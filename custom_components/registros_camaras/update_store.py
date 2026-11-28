"""Versioned HA-native storage for pending sensor reports and delivery receipts."""

import asyncio
from copy import deepcopy
import math
from uuid import uuid4

from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

MAX_REPORTS = 50000
MEMORY_REPORTS = 10000


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

    def make_store(self) -> Store:
        return Store(
            self.hass,
            1,
            self.key,
            private=True,
            atomic_writes=True,
            serialize_in_event_loop=False,
        )

    async def initialize(self) -> None:
        value = await self.store.async_load()
        if value is not None:
            if not (
                isinstance(value, dict)
                and isinstance(value.get("rows"), list)
                and isinstance(value.get("cursors"), dict)
                and "batch" in value
            ):
                raise HomeAssistantError("Invalid stored sensor update queue")
            self.data = value

    async def save(self, data: dict) -> None:
        data = {**data, "commit": str(uuid4())}
        await self.store.async_save(data)
        if self.hass.state is CoreState.stopping:
            # Store retains this complete snapshot for HA's FINAL_WRITE event.
            # Delivery has already stopped, so this cannot acknowledge a server batch.
            self.data = data
            return
        # Store logs some write errors instead of propagating them. A fresh Store
        # verifies the committed snapshot before we forget any in-memory reports.
        if await self.make_store().async_load() != data:
            raise HomeAssistantError("Sensor update queue was not persisted")
        self.data = data

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
            for source, row in rows:
                if row["value"] is not None and not math.isfinite(row["value"]):
                    raise ValueError("Non-finite sensor reading")
                key = identity(source, row)
                if key in known:
                    original = {k: v for k, v in known[key].items() if k != "mode"}
                    if original != {k: v for k, v in row.items() if k != "mode"}:
                        raise HomeAssistantError("Conflicting sensor report identity")
                    continue
                known[key] = row
                added.append([source, deepcopy(row)])
            # Reserve enough space to flush the bounded RAM buffer during unload.
            capacity = MAX_REPORTS + (MEMORY_REPORTS if shutdown else 0)
            if len(self.data["rows"]) + len(added) > capacity:
                raise HomeAssistantError("Sensor update queue capacity reached")
            cursors = {**self.data["cursors"]}
            if cursor:
                cursors[cursor[0]] = max(cursors.get(cursor[0], 0), cursor[1])
            if added or cursors != self.data["cursors"]:
                await self.save(
                    {**self.data, "rows": [*self.data["rows"], *added], "cursors": cursors}
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
            source = self.data["rows"][0][0]
            rows = [row for origin, row in self.data["rows"] if origin == source][:100]
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
