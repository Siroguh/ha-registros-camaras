"""All selected HA reports -> durable outbox -> versioned Alchemy ingestion API."""

import asyncio
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import hashlib
import json
import logging
import math
import random
from typing import TYPE_CHECKING
from uuid import uuid4

from homeassistant.const import CONF_URL
from homeassistant.core import Event, HassJob, HomeAssistant, callback
from homeassistant.helpers import entity_registry
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)

from .api import CannotConnect, IncompatibleApi, RegistrosClient, normalize_url
from .const import CONF_CENTRE_ID, DOMAIN
from .update_store import MAX_REPORTS, MEMORY_REPORTS, UpdateStore

if TYPE_CHECKING:
    from .coordinator import RegistrosConfigEntry

_LOGGER = logging.getLogger(__name__)


def reading(assignment: dict, state, at: datetime, mode: str) -> dict:
    """Copy immediately: HA mutates State.last_reported on equal-value reports."""
    try:
        value = float(state.state)
    except ValueError, TypeError:
        value = math.nan
    return {
        "assignmentId": assignment["id"],
        "sourceEventId": at.astimezone(UTC).isoformat()
        + ":change:"
        + hashlib.sha256(
            json.dumps([state.state, dict(state.attributes)], sort_keys=True, default=str).encode()
        ).hexdigest(),
        "observedAt": int(at.timestamp() * 1000),
        "value": value if math.isfinite(value) else None,
        "unit": str(state.attributes.get("unit_of_measurement", ""))[:20],
        "status": (
            "valid"
            if math.isfinite(value)
            else "unavailable"
            if state.state == "unavailable"
            else "invalid"
        ),
        "mode": mode,
    }


class Updates:
    def __init__(
        self, hass: HomeAssistant, entry: RegistrosConfigEntry, client: RegistrosClient
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.spool = UpdateStore(hass, entry.entry_id)
        self.pending: deque[tuple[str, dict]] = deque()
        self.assignments: list[dict] = []
        self.unsubscribers: list[Callable[[], None]] = []
        self.tasks: list[asyncio.Task] = []
        self.write_ready = asyncio.Event()
        self.send_ready = asyncio.Event()
        self.stop_ready = asyncio.Event()
        self.stopping = False
        self.paused = False
        self.error: str | None = None
        self.recovery_error: str | None = None
        self.queue_size = 0
        self.unrecoverable_reports = 0
        self.started_at = int(datetime.now(UTC).timestamp() * 1000)
        self.prepared = False
        self.change_counts: dict[str, tuple[datetime, dict[str, int]]] = {}

    async def prepare(self) -> None:
        if self.prepared:
            return
        await self.spool.initialize()
        destination = hashlib.sha256(
            json.dumps(
                [normalize_url(self.entry.data[CONF_URL]), self.entry.data[CONF_CENTRE_ID]]
            ).encode()
        ).hexdigest()
        await self.spool.bind(destination)
        self.prepared = True
        self.queue_size = await self.spool.size()
        self.paused = self.queue_size >= MAX_REPORTS
        if "manifest" in self.spool.data:
            # Install listeners before either HTTP request can wait on an offline server.
            await self.apply_manifest(self.spool.data["manifest"])

    async def start(self) -> None:
        await self.prepare()
        self.queue_size = await self.spool.size()
        self.paused = self.queue_size >= MAX_REPORTS
        try:
            await self.refresh()
        except CannotConnect:
            if "manifest" not in self.spool.data:
                raise
            await self.apply_manifest(self.spool.data["manifest"])
            self.recovery_error = "Offline: capturing with last verified sensor mappings"
        for name, coroutine in (
            ("writer", self.write_loop()),
            ("sender", self.send_loop()),
            ("reconciliation", self.reconcile_loop()),
        ):
            self.tasks.append(
                self.entry.async_create_background_task(
                    self.hass,
                    coroutine,
                    f"registros updates {name}",
                    eager_start=False,
                )
            )
        self.entry.async_on_unload(self.hass.async_add_shutdown_job(HassJob(self.stop)))
        self.send_ready.set()

    def unsubscribe(self) -> None:
        for unsubscribe in self.unsubscribers:
            unsubscribe()
        self.unsubscribers.clear()

    async def stop(self) -> None:
        self.stopping = True
        self.unsubscribe()
        self.stop_ready.set()
        self.write_ready.set()
        self.send_ready.set()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.pending:
            await self.spool.append(list(self.pending), shutdown=True)
            self.pending.clear()

    async def wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stop_ready.wait(), seconds)
        except TimeoutError:
            pass

    async def refresh(self) -> None:
        manifest = await self.client.sensor_stream()
        await self.apply_manifest(manifest)
        await self.spool.cache(manifest=manifest)

    async def apply_manifest(self, manifest: dict) -> None:
        sources = {
            url.rstrip("/")
            for url in (self.hass.config.external_url, self.hass.config.internal_url)
            if url
        }
        assignments = [a for a in manifest["assignments"] if a["source"].rstrip("/") in sources]
        if manifest["assignments"] and not assignments:
            self.recovery_error = (
                "HA internal/external URL does not match a configured sensor source"
            )
        registry = entity_registry.async_get(self.hass)
        if any(
            (registered := registry.async_get(a["entityId"])) is not None
            and registered.platform == DOMAIN
            for a in assignments
        ):
            raise IncompatibleApi("Registros output entities cannot be sensor inputs")
        for assignment in assignments:
            await self.spool.ensure_cursor(
                assignment["id"],
                max(assignment["historyFrom"], self.started_at - 10000),
            )
        self.assignments = assignments
        self.subscribe()

    def subscribe(self) -> None:
        self.unsubscribe()
        if self.paused or self.stopping:
            return
        entities = {a["entityId"] for a in self.assignments if a["endsAt"] is None}
        if entities:
            self.unsubscribers = [
                async_track_state_change_event(self.hass, entities, self.capture),
                async_track_state_report_event(self.hass, entities, self.capture),
            ]

    @callback
    def capture(self, event: Event) -> None:
        state = event.data.get("new_state")
        if state is None:
            return
        at = event.data.get("last_reported", state.last_updated)
        for assignment in self.assignments:
            if assignment["entityId"] != state.entity_id or assignment["endsAt"] is not None:
                continue
            row = reading(assignment, state, at, "live")
            if "last_reported" in event.data:
                # Equal reports are not Recorder rows; even coincident timestamps are distinct.
                row["sourceEventId"] += f":report:{uuid4()}"
            else:
                previous_at, counts = self.change_counts.get(assignment["id"], (at, {}))
                if previous_at != at:
                    counts = {}
                key = row["sourceEventId"]
                occurrence = counts.get(key, 0)
                counts[key] = occurrence + 1
                self.change_counts[assignment["id"]] = (at, counts)
                if occurrence:
                    row["sourceEventId"] += f":occurrence:{occurrence}"
            if row["observedAt"] < assignment["startsAt"]:
                continue
            if len(self.pending) >= MEMORY_REPORTS:
                self.paused = True
                self.error = "Local buffer full: capture paused; equal-value reports may be missing"
                self.unrecoverable_reports += 1
                self.unsubscribe()
                _LOGGER.error(self.error)
                break
            self.pending.append((assignment["source"], row))
        self.write_ready.set()

    async def write_loop(self) -> None:
        while not self.stopping:
            await self.write_ready.wait()
            self.write_ready.clear()
            while self.pending and not self.stopping:
                rows = list(self.pending)[:1000]
                try:
                    await self.spool.append(rows)
                except Exception as err:
                    # Never remove a report when persistence fails.
                    self.error = f"Local persistence failed ({type(err).__name__})"
                    _LOGGER.error(self.error)
                    await self.wait(30)
                    continue
                for _ in rows:
                    self.pending.popleft()
                self.send_ready.set()
                if self.paused and await self.spool.size() < MAX_REPORTS:
                    self.paused = False
                    self.subscribe()

    async def send_loop(self) -> None:
        retry = 1.0
        while not self.stopping:
            await self.send_ready.wait()
            self.send_ready.clear()
            await self.wait(2)
            while not self.stopping:
                try:
                    batch = await self.spool.next_batch(self.client.centre_id)
                    self.queue_size = await self.spool.size()
                    if self.queue_size >= MAX_REPORTS:
                        self.paused = True
                        self.unsubscribe()
                        self.recovery_error = (
                            "HA storage queue full: capture paused; reports may be missing"
                        )
                    elif self.paused and not self.pending:
                        self.paused = False
                        self.subscribe()
                    if batch is None:
                        break
                    await self.client.push_updates(batch)
                    await self.spool.acknowledge(batch["commandId"])
                    retry = 1.0
                    self.error = None
                except Exception as err:
                    self.error = f"Update delivery pending ({type(err).__name__})"
                    await self.wait(retry + random.uniform(0, retry / 4))
                    retry = min(300, retry * 2)

    async def recover(self) -> bool:
        """Recorder supplements the outbox; it cannot reconstruct identical reports."""
        if "recorder" not in self.hass.config.components:
            self.recovery_error = (
                "Recorder unavailable; only persisted live reports can be recovered"
            )
            return False
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.history import get_significant_states

        until = int(datetime.now(UTC).timestamp() * 1000) - 10000
        recorder = get_instance(self.hass)
        # Freeze the window first, then synchronize the writer before querying readers.
        await recorder.async_block_till_done()
        more = False
        if await self.spool.size() >= MAX_REPORTS:
            return False
        for assignment in self.assignments:
            if self.stopping:
                return False
            target = min(until, assignment["endsAt"] or until)
            initial = max(assignment["historyFrom"], self.started_at - 10000)
            start = await self.spool.cursor(assignment["id"], initial)
            # A bounded window per assignment/pass keeps recovery and live ingestion responsive.
            end = min(target, start + 300000)
            if end <= start:
                continue
            more = more or end < target
            groups = await recorder.async_add_executor_job(
                lambda a=assignment, s=start, e=end: get_significant_states(
                    self.hass,
                    datetime.fromtimestamp(s / 1000, UTC) - timedelta(milliseconds=1),
                    datetime.fromtimestamp(e / 1000, UTC),
                    [a["entityId"]],
                    significant_changes_only=False,
                    include_start_time_state=False,
                    minimal_response=False,
                    no_attributes=False,
                )
            )
            rows = []
            counts: dict[str, int] = {}
            for state in groups.get(assignment["entityId"], []):
                if not start <= int(state.last_updated.timestamp() * 1000) < end:
                    continue
                row = reading(assignment, state, state.last_updated, "history")
                key = row["sourceEventId"]
                occurrence = counts.get(key, 0)
                counts[key] = occurrence + 1
                if occurrence:
                    row["sourceEventId"] += f":occurrence:{occurrence}"
                rows.append((assignment["source"], row))
            await self.spool.append(rows, (assignment["id"], end))
            if rows:
                self.send_ready.set()
        self.recovery_error = None
        return more

    async def reconcile_loop(self) -> None:
        next_refresh = self.hass.loop.time() + 300
        while not self.stopping:
            more = False
            try:
                more = await self.recover()
            except Exception as err:
                self.recovery_error = f"Recorder recovery pending ({type(err).__name__})"
            await self.wait(0.1 if more else 300)
            if not self.stopping and self.hass.loop.time() >= next_refresh:
                try:
                    await self.refresh()
                except Exception as err:
                    self.recovery_error = f"Sensor mapping refresh pending ({type(err).__name__})"
                next_refresh = self.hass.loop.time() + 300

    def diagnostics(self) -> dict:
        return {
            "queued": self.queue_size,
            "queue_capacity": MAX_REPORTS,
            "awaiting_local_persistence": len(self.pending),
            "capture_paused": self.paused,
            "delivery_error": self.error,
            "recovery_error": self.recovery_error,
            "capture_overflows": self.unrecoverable_reports,
        }
