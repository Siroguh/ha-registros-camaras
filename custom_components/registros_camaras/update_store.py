"""Versioned HA-native storage for pending sensor reports and delivery receipts."""

import asyncio
from copy import deepcopy
import math
from pathlib import Path
from uuid import uuid4

from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

MAX_REPORTS = 50000
MEMORY_REPORTS = 10000
SEGMENT_REPORTS = 1000
MAX_INCOMPLETE_REPORTS = 1000


def incomplete_history(row: dict) -> bool:
    return row["mode"] == "history" and row["status"] == "valid" and not row["unit"]


class IncompleteHistoryFull(HomeAssistantError):
    """The incomplete-history reserve cannot accept this recovery window."""


def identity(source: str, row: dict) -> tuple[str, str, str]:
    return source, row["assignmentId"], row["sourceEventId"]


class UpdateStore:
    """Use Store's atomic files, versioning, executor and final-write lifecycle."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.key = f"registros_camaras.updates.{entry_id}"
        self.store = self.make_store()
        self.lock = asyncio.Lock()
        self.data = {"rows": [], "batch": None, "cursors": {}, "commit": None}
        self.segments: list[tuple[dict, list]] = []
        self.final_pending = False
        self.garbage: set[str] = set()
        self.cache_key: str | None = None
        self.cached: dict = {}

    def make_store(self, key: str | None = None) -> Store:
        return Store(
            self.hass,
            1,
            key or self.key,
            private=True,
            atomic_writes=True,
            serialize_in_event_loop=False,
        )

    async def initialize(self) -> None:
        value = await self.store.async_load()
        self.garbage = await self.hass.async_add_executor_job(self.stored_keys)
        if value is None and self.garbage:
            raise HomeAssistantError("Sensor queue index missing; stored files require recovery")
        if value is not None:
            if not isinstance(value, dict):
                raise HomeAssistantError("Invalid stored sensor update queue")
            if "segments" in value:
                rows = []
                for segment in value["segments"]:
                    items = await self.make_store(segment["key"]).async_load()
                    if not isinstance(items, list):
                        raise HomeAssistantError("Missing sensor update segment")
                    self.segments.append((segment, items))
                    rows.extend(items[segment["start"] :])
                value = {**value, "rows": rows}
            if not (
                isinstance(value, dict)
                and isinstance(value.get("rows"), list)
                and isinstance(value.get("cursors"), dict)
                and "batch" in value
            ):
                raise HomeAssistantError("Invalid stored sensor update queue")
            self.data = value
            self.cache_key = value.get("cache_key")
            if self.cache_key:
                cached = await self.make_store(self.cache_key).async_load()
                if isinstance(cached, dict):
                    self.cached = cached
                    self.data = {**value, **cached}
                else:
                    # The queue is authoritative; this optional cache can be fetched online.
                    self.cache_key = None
        await self.cleanup()

    def stored_keys(self) -> set[str]:
        root = Path(self.store.path).parent
        return {path.name for path in root.glob(f"{self.key}.segment.*")} | {
            f"{self.key}.files/{path.name}"
            for path in (root / f"{self.key}.files").glob("*")
            if path.name.startswith(("segment.", "cache."))
        }

    async def cleanup(self) -> None:
        self.garbage.difference_update(d["key"] for d, _ in self.segments)
        self.garbage.discard(self.cache_key)
        for key in list(self.garbage):
            try:
                await self.make_store(key).async_remove()
            except OSError:
                continue
            self.garbage.remove(key)

    async def save(self, data: dict) -> None:
        if self.hass.state is CoreState.stopping:
            # FINAL_WRITE must commit segments before their metadata, not concurrently.
            self.data = data
            if not self.final_pending:
                self.final_pending = True
                self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_FINAL_WRITE, self.final_write)
            return
        task = self.hass.async_create_task(self.commit(data), "registros durable write")
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # Keep the lock even if shutdown cancels a previously cancelled writer.
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def final_write(self, _) -> None:
        async with self.lock:
            await self.save(self.data)
            self.final_pending = False

    async def verified_save(self, store: Store, data) -> None:
        await store.async_save(data)
        if await self.make_store(store.key).async_load() != data:
            raise HomeAssistantError("Sensor update queue was not persisted")

    async def commit(self, data: dict) -> None:
        """Immutable bounded segments, then one atomic metadata commit (rows + cursor + ACK)."""
        remaining = {identity(*item): item for item in data["rows"]}
        known = set()
        segments = []
        for descriptor, items in self.segments:
            start = descriptor["start"]
            while start < len(items) and identity(*items[start]) not in remaining:
                start += 1
            if start < len(items):
                active = [item for item in items[start:] if identity(*item) in remaining]
                if len(active) != len(items) - start or any(
                    item != remaining[identity(*item)] for item in active
                ):
                    items = [remaining[identity(*item)] for item in active]
                    descriptor = {"key": f"{self.key}.files/segment.{uuid4()}", "start": 0}
                    self.garbage.add(descriptor["key"])
                    await self.verified_save(self.make_store(descriptor["key"]), items)
                    start = 0
                segments.append(({**descriptor, "start": start}, items))
                known.update(identity(*item) for item in items[start:])
        added: dict[str, list] = {}
        for item in data["rows"]:
            if identity(*item) not in known:
                added.setdefault(item[0], []).append(item)
        for source, items in added.items():
            # Copy only the incomplete tail of this source; ACK never rewrites samples.
            tail = next(
                (i for i in range(len(segments) - 1, -1, -1) if segments[i][1][0][0] == source),
                None,
            )
            if tail is not None:
                descriptor, prior = segments[tail]
                active = prior[descriptor["start"] :]
                if len(active) < SEGMENT_REPORTS:
                    segments.pop(tail)
                    items = [*active, *items]
            for offset in range(0, len(items), SEGMENT_REPORTS):
                chunk = items[offset : offset + SEGMENT_REPORTS]
                descriptor = {"key": f"{self.key}.files/segment.{uuid4()}", "start": 0}
                self.garbage.add(descriptor["key"])
                await self.verified_save(self.make_store(descriptor["key"]), chunk)
                segments.append((descriptor, chunk))
        cached = {k: data[k] for k in ("snapshot", "manifest") if k in data}
        cache_key = self.cache_key
        if cached != self.cached:
            cache_key = f"{self.key}.files/cache.{uuid4()}" if cached else None
            if cache_key:
                self.garbage.add(cache_key)
                await self.verified_save(self.make_store(cache_key), cached)
        metadata = {
            k: v
            for k, v in data.items()
            if k not in {"rows", "segments", "snapshot", "manifest", "cache_key"}
        }
        if cache_key:
            metadata["cache_key"] = cache_key
        metadata.update(commit=str(uuid4()), segments=[d for d, _ in segments])
        await self.verified_save(self.store, metadata)
        self.garbage.update(d["key"] for d, _ in self.segments)
        if self.cache_key:
            self.garbage.add(self.cache_key)
        self.cache_key = cache_key
        self.cached = cached
        self.segments = segments
        self.data = data
        await self.cleanup()

    async def bind(self, destination: str) -> None:
        async with self.lock:
            previous = self.data.get("destination")
            if previous is not None and previous != destination:
                if self.data["rows"] or self.data["batch"] is not None:
                    raise HomeAssistantError(
                        "Pending sensor storage belongs to another destination"
                    )
                await self.save(
                    {"rows": [], "batch": None, "cursors": {}, "destination": destination}
                )
            if previous is None:
                await self.save({**self.data, "destination": destination})

    async def cache(self, **values) -> None:
        async with self.lock:
            await self.save({**self.data, **deepcopy(values)})

    async def append(
        self,
        rows: list[tuple[str, dict]],
        cursor: tuple[str, int] | None = None,
        *,
        shutdown: bool = False,
    ) -> None:
        async with self.lock:
            known = {identity(source, row): row for source, row in self.data["rows"]}
            added = []
            replacements = {}
            for source, row in rows:
                if row["value"] is not None and not math.isfinite(row["value"]):
                    raise ValueError("Non-finite sensor reading")
                key = identity(source, row)
                if key in known:
                    original = {k: v for k, v in known[key].items() if k != "mode"}
                    if original != {k: v for k, v in row.items() if k != "mode"}:
                        prior = known[key]

                        def comparable(value):
                            return {k: v for k, v in value.items() if k not in {"mode", "unit"}}

                        incomplete = prior if prior["mode"] == "history" else row
                        live = row if row["mode"] == "live" else prior
                        if not (
                            incomplete["mode"] == "history"
                            and incomplete["unit"] == ""
                            and live["mode"] == "live"
                            and comparable(prior) == comparable(row)
                        ):
                            raise HomeAssistantError("Conflicting sensor report identity")
                        if prior["mode"] == "history":
                            replacements[key] = deepcopy(live)
                            known[key] = replacements[key]
                    continue
                known[key] = row
                added.append([source, deepcopy(row)])
            # Reserve enough space to flush the bounded RAM buffer during unload.
            capacity = MAX_REPORTS + (MEMORY_REPORTS if shutdown else 0)
            incomplete = sum(incomplete_history(row) for row in known.values())
            if len(known) - incomplete > capacity:
                raise HomeAssistantError("Sensor update queue capacity reached")
            # Incomplete history cannot consume live delivery capacity. Preserve any
            # larger pre-upgrade reserve, but never grow it or advance a failed cursor.
            prior_incomplete = sum(incomplete_history(row) for _, row in self.data["rows"])
            if incomplete > max(MAX_INCOMPLETE_REPORTS, prior_incomplete):
                raise IncompleteHistoryFull("Incomplete sensor history capacity reached")
            cursors = {**self.data["cursors"]}
            if cursor:
                cursors[cursor[0]] = max(cursors.get(cursor[0], 0), cursor[1])
            if added or replacements or cursors != self.data["cursors"]:
                await self.save(
                    {
                        **self.data,
                        "rows": [
                            [source, replacements.get(identity(source, row), row)]
                            for source, row in [*self.data["rows"], *added]
                        ],
                        "cursors": cursors,
                    }
                )

    async def cursor(self, assignment_id: str, initial: int) -> int:
        return self.data["cursors"].get(assignment_id, initial)

    async def ensure_cursor(self, assignment_id: str, initial: int) -> None:
        async with self.lock:
            if assignment_id not in self.data["cursors"]:
                await self.save(
                    {**self.data, "cursors": {**self.data["cursors"], assignment_id: initial}}
                )

    async def next_batch(self, centre_id: str) -> dict | None:
        async with self.lock:
            if self.data["batch"] is not None:
                return deepcopy(self.data["batch"])
            if not self.data["rows"]:
                return None
            # Do not freeze a valid historical value without the unit needed to interpret it.
            eligible = [
                (origin, row) for origin, row in self.data["rows"] if not incomplete_history(row)
            ]
            if not eligible:
                return None
            source = eligible[0][0]
            rows = [row for origin, row in eligible if origin == source][:100]
            batch = {
                "apiVersion": 1,
                "commandId": str(uuid4()),
                "centreId": centre_id,
                "source": source,
                "readings": rows,
            }
            await self.save({**self.data, "batch": batch})
            return deepcopy(batch)

    async def acknowledge(self, command_id: str) -> None:
        async with self.lock:
            batch = self.data["batch"]
            if batch is None or batch["commandId"] != command_id:
                raise ValueError("Unexpected acknowledgement")
            accepted = {identity(batch["source"], row) for row in batch["readings"]}
            rows = [item for item in self.data["rows"] if identity(*item) not in accepted]
            await self.save({**self.data, "batch": None, "rows": rows})

    async def size(self) -> int:
        return len(self.data["rows"])

    async def deliverable_size(self) -> int:
        return sum(not incomplete_history(row) for _, row in self.data["rows"])
