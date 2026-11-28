"""Durability, unchanged reports, and real HA event semantics."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.registros_camaras.api import CannotConnect, IncompatibleApi, RegistrosClient
from custom_components.registros_camaras.update_store import UpdateStore
from custom_components.registros_camaras.updates import Updates, reading
from homeassistant.helpers.storage import Store

_STORE_LOAD = Store._async_load
_STORE_WRITE = Store._async_write_data
_STORE_REMOVE = Store.async_remove


@pytest.fixture
def mock_recorder_before_hass(request):
    if "real_recorder" in request.node.name:
        request.getfixturevalue("recorder_db_url")


@pytest.fixture
def real_storage(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    with (
        patch.object(Store, "_async_load", _STORE_LOAD),
        patch.object(Store, "_async_write_data", _STORE_WRITE),
        patch.object(Store, "async_remove", _STORE_REMOVE),
    ):
        yield


ASSIGNMENT = {
    "id": "temperature-1",
    "source": "https://home.example",
    "entityId": "sensor.temperature",
    "locationId": "chamber-1",
    "metric": "temperature",
    "position": 1,
    "startsAt": 0,
    "endsAt": None,
    "historyFrom": 0,
}


def sample(n=0):
    return {
        "assignmentId": ASSIGNMENT["id"],
        "sourceEventId": f"2026-11-28T00:00:00.{n:06d}+00:00",
        "observedAt": 1795824000000,
        "mode": "live",
        "value": 20,
        "unit": "°C",
        "status": "valid",
    }


async def test_native_store_retains_equal_reports_and_batch_identity_after_restart(hass):
    spool = UpdateStore(hass, "test")
    await spool.initialize()
    await spool.append([(ASSIGNMENT["source"], sample(n)) for n in range(102)])
    batch = await spool.next_batch("centre")
    assert len(batch["readings"]) == 100
    assert len({r["sourceEventId"] for r in batch["readings"]}) == 100
    restored = UpdateStore(hass, "test")
    await restored.initialize()
    assert await restored.next_batch("centre") == batch
    with pytest.raises(ValueError):
        await restored.acknowledge("wrong-id")
    assert await restored.size() == 102
    # Simulates a lost response after the server committed: retry the exact original command.
    assert await restored.next_batch("centre") == batch
    await restored.acknowledge(batch["commandId"])
    assert await restored.size() == 2
    assert (await restored.next_batch("centre"))["commandId"] != batch["commandId"]


async def test_reconciliation_cursor_and_samples_commit_together(hass):
    spool = UpdateStore(hass, "test")
    await spool.initialize()
    await spool.append([(ASSIGNMENT["source"], sample())], ("temperature-1", 50))
    assert await spool.cursor("temperature-1", 0) == 50
    with pytest.raises(ValueError):
        await spool.append(
            [
                (ASSIGNMENT["source"], sample(1)),
                (ASSIGNMENT["source"], {**sample(2), "value": float("nan")}),
            ],
            ("temperature-1", 100),
        )
    assert await spool.size() == 1
    assert await spool.cursor("temperature-1", 0) == 50
    await spool.append(
        [(ASSIGNMENT["source"], {**sample(), "mode": "history"})], ("temperature-1", 40)
    )
    assert await spool.size() == 1
    assert (await spool.next_batch("centre"))["readings"][0]["mode"] == "live"
    assert await spool.cursor("temperature-1", 0) == 50


async def test_native_store_write_failure_cannot_acknowledge_or_advance_cursor(hass, real_storage):
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.util.file import WriteError

    store = UpdateStore(hass, "failed-write")
    await store.initialize()
    await store.append([(ASSIGNMENT["source"], sample())], ("temperature-1", 50))
    batch = await store.next_batch("centre")
    with patch.object(store.store, "_async_write_data", side_effect=WriteError("disk failed")):
        with pytest.raises(HomeAssistantError, match="not persisted"):
            await store.acknowledge(batch["commandId"])
        with pytest.raises(HomeAssistantError, match="not persisted"):
            await store.append([(ASSIGNMENT["source"], sample(2))], ("temperature-1", 100))
    restored = UpdateStore(hass, "failed-write")
    await restored.initialize()
    assert await restored.size() == 1
    assert await restored.cursor("temperature-1", 0) == 50
    assert await restored.next_batch("centre") == batch


async def test_shutdown_uses_homeassistant_final_write(hass, entry):
    from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
    from homeassistant.core import CoreState

    updates = Updates(hass, entry, AsyncMock())
    await updates.spool.initialize()
    updates.pending.append((ASSIGNMENT["source"], sample()))
    hass.set_state(CoreState.stopping)
    await updates.stop()
    hass.set_state(CoreState.final_write)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()
    restored = UpdateStore(hass, entry.entry_id)
    await restored.initialize()
    assert await restored.size() == 1
    hass.set_state(CoreState.not_running)


@pytest.mark.parametrize("damage", ["corrupt", "missing"])
async def test_missing_index_preserves_segments_for_recovery(hass, real_storage, damage):
    from pathlib import Path

    from homeassistant.exceptions import HomeAssistantError

    spool = UpdateStore(hass, "damaged-index")
    await spool.initialize()
    await spool.append([(ASSIGNMENT["source"], sample())])
    batch = await spool.next_batch("centre")
    index = Path(spool.store.path)
    original = await hass.async_add_executor_job(index.read_bytes)
    segment = Path(spool.make_store(spool.segments[0][0]["key"]).path)
    if damage == "corrupt":
        await hass.async_add_executor_job(index.write_text, "{")
    else:
        await hass.async_add_executor_job(index.unlink)
    for _ in range(2):
        with pytest.raises(HomeAssistantError, match="require recovery"):
            await UpdateStore(hass, "damaged-index").initialize()
        assert await hass.async_add_executor_job(segment.exists)
    await hass.async_add_executor_job(index.write_bytes, original)
    restored = UpdateStore(hass, "damaged-index")
    await restored.initialize()
    assert await restored.next_batch("centre") == batch


async def test_repeated_empty_queues_keep_store_keys_and_cache_writes_bounded(hass, real_storage):
    from homeassistant.helpers.storage import get_internal_store_manager

    spool = UpdateStore(hass, "repeated-drain")
    await spool.initialize()
    snapshot = {"tasks": [{"id": str(n), "description": "x" * 300} for n in range(1500)]}
    await spool.cache(snapshot=snapshot, manifest={"assignments": [ASSIGNMENT]})
    manager = get_internal_store_manager(hass)
    before = set(manager._invalidated)
    sizes = []
    original = Store._write_prepared_data

    def measure(store, mode, data):
        sizes.append(len(data))
        original(store, mode, data)

    with patch.object(Store, "_write_prepared_data", measure):
        for n in range(100):
            await spool.append([(ASSIGNMENT["source"], sample(n))])
            batch = await spool.next_batch("centre")
            await spool.acknowledge(batch["commandId"])
        await spool.cache(snapshot=snapshot)
    assert manager._invalidated == before
    assert max(sizes) < 4000
    assert sum(sizes) < 400_000
    assert not spool.segments
    restored = UpdateStore(hass, "repeated-drain")
    await restored.initialize()
    assert restored.data["snapshot"] == snapshot
    assert await restored.size() == 0


async def test_legacy_segments_and_inline_cache_upgrade_atomically(hass, real_storage):
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.util.file import WriteError

    spool = UpdateStore(hass, "legacy-segments")
    legacy_key = f"{spool.key}.segment.old"
    await spool.make_store(legacy_key).async_save([[ASSIGNMENT["source"], sample()]])
    await spool.store.async_save(
        {
            "segments": [{"key": legacy_key, "start": 0}],
            "batch": None,
            "cursors": {},
            "snapshot": {"revision": 1},
            "manifest": {"assignments": [ASSIGNMENT]},
        }
    )
    await spool.initialize()
    assert spool.data["snapshot"] == {"revision": 1}
    with patch.object(spool.store, "_async_write_data", side_effect=WriteError("disk failed")):
        with pytest.raises(HomeAssistantError, match="not persisted"):
            await spool.cache(snapshot={"revision": 2})
    restored = UpdateStore(hass, "legacy-segments")
    await restored.initialize()
    assert restored.data["snapshot"] == {"revision": 1}
    assert await restored.size() == 1
    await restored.cache(snapshot={"revision": 2})
    metadata = await restored.store.async_load()
    assert "snapshot" not in metadata
    assert metadata["cache_key"]
    reopened = UpdateStore(hass, "legacy-segments")
    await reopened.initialize()
    assert reopened.data["snapshot"] == {"revision": 2}
    assert (await reopened.next_batch("centre"))["readings"] == [sample()]


async def test_real_ha_changed_and_identical_reports_are_copied_without_coalescing(hass, entry):
    client = AsyncMock()
    updates = Updates(hass, entry, client)
    updates.assignments = [ASSIGNMENT]
    updates.subscribe()
    try:
        hass.states.async_set("sensor.temperature", "20", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        hass.states.async_set("sensor.temperature", "20", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        hass.states.async_set("sensor.temperature", "21", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert [r[1]["value"] for r in updates.pending] == [20, 20, 21]
        assert len({r[1]["sourceEventId"] for r in updates.pending}) == 3
        snapshot = list(updates.pending)
        hass.states.async_set("sensor.temperature", "21", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert list(updates.pending)[:3] == snapshot
        hass.states.async_set("sensor.unselected", "20")
        await hass.async_block_till_done()
        assert len(updates.pending) == 4
    finally:
        updates.unsubscribe()


async def test_batch_ack_is_verified_before_local_removal():
    client = RegistrosClient(AsyncMock(), "https://registros.example", "key", "centre")
    batch = {"commandId": "batch-id"}
    with patch.object(client, "request", new=AsyncMock(return_value={"commandId": "batch-id"})):
        with pytest.raises(IncompatibleApi):
            await client.push_updates(batch)
    with patch.object(
        client,
        "request",
        new=AsyncMock(
            return_value={
                "commandId": "batch-id",
                "stream": "ReadingIngestion/batch-id",
                "eventId": "ReadingIngestion/batch-id:1",
                "version": 1,
            }
        ),
    ):
        await client.push_updates(batch)


async def test_stream_and_push_use_real_http_contract(aiohttp_client, socket_enabled):
    from aiohttp import web

    payloads = []
    manifest = {
        "apiVersion": 1,
        "revision": 1,
        "assignments": [ASSIGNMENT],
        "maxBatchSize": 100,
        "maxBatchDelayMillis": 2000,
    }

    async def stream(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        return web.json_response(manifest)

    async def push(request):
        payload = await request.json()
        payloads.append(payload)
        command_id = payload["commandId"]
        return web.json_response(
            {
                "commandId": command_id,
                "stream": f"ReadingIngestion/{command_id}",
                "eventId": f"ReadingIngestion/{command_id}:1",
                "version": 1,
            }
        )

    app = web.Application()
    app.router.add_get("/api/environment/streams/centre", stream)
    app.router.add_post("/api/environment/updates", push)
    http = await aiohttp_client(app)
    client = RegistrosClient(http.session, str(http.make_url("/")), "test-key", "centre")
    assert (await client.sensor_stream())["assignments"] == [ASSIGNMENT]
    batch = {
        "apiVersion": 1,
        "commandId": "stable-id",
        "centreId": "centre",
        "source": ASSIGNMENT["source"],
        "readings": [sample(1), sample(2)],
    }
    await client.push_updates(batch)
    await client.push_updates(batch)
    assert payloads == [batch, batch]
    manifest["assignments"] = [ASSIGNMENT, ASSIGNMENT]
    with pytest.raises(IncompatibleApi):
        await client.sensor_stream()


async def test_unchanged_reports_keep_microseconds_and_original_freshness(hass):
    hass.states.async_set("sensor.temperature", "20", {"unit_of_measurement": "°C"})
    state = hass.states.get("sensor.temperature")
    at = datetime(2026, 11, 28, microsecond=1, tzinfo=UTC)
    first = reading(ASSIGNMENT, state, at, "live")
    second = reading(ASSIGNMENT, state, at + timedelta(microseconds=1), "live")
    assert first["observedAt"] == second["observedAt"]
    assert first["sourceEventId"] != second["sourceEventId"]


async def test_unload_flushes_pending_reports_for_restart(hass, entry):
    updates = Updates(hass, entry, AsyncMock())
    await updates.spool.initialize()
    updates.pending.append((ASSIGNMENT["source"], sample()))
    await updates.stop()
    assert not updates.pending
    assert await updates.spool.size() == 1


async def test_failed_unload_retries_flush_on_homeassistant_shutdown(
    hass, entry, client, stream_manifest, real_storage
):
    from homeassistant.exceptions import HomeAssistantError

    hass.config.external_url = ASSIGNMENT["source"]
    stream_manifest.return_value["assignments"] = [ASSIGNMENT]
    assert await hass.config_entries.async_setup(entry.entry_id)
    updates = entry.runtime_data.updates
    with patch.object(
        updates.spool, "append", side_effect=HomeAssistantError("temporary disk failure")
    ):
        hass.states.async_set(ASSIGNMENT["entityId"], "20", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert not await hass.config_entries.async_unload(entry.entry_id)
        assert len(updates.pending) == 1
    await hass.async_stop()
    restored = UpdateStore(hass, entry.entry_id)
    await restored.initialize()
    assert await restored.size() == 1
    assert (await restored.next_batch("centre"))["readings"][0]["value"] == 20


@pytest.mark.parametrize("offline", [False, True])
async def test_missing_cache_recovers_online_without_changing_pending_batch(
    hass, entry, client, stream_manifest, real_storage, offline
):
    hass.config.external_url = ASSIGNMENT["source"]
    stream_manifest.return_value["assignments"] = [ASSIGNMENT]
    assert await hass.config_entries.async_setup(entry.entry_id)
    updates = entry.runtime_data.updates
    hass.states.async_set(ASSIGNMENT["entityId"], "20", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    batch = await updates.spool.next_batch("centre")
    cursors = dict(updates.spool.data["cursors"])
    assert await hass.config_entries.async_unload(entry.entry_id)
    await updates.spool.make_store(updates.spool.cache_key).async_remove()
    if offline:
        client.side_effect = CannotConnect()
        stream_manifest.side_effect = CannotConnect()
        assert not await hass.config_entries.async_setup(entry.entry_id)
        hass.states.async_set(ASSIGNMENT["entityId"], "21", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        restored = UpdateStore(hass, entry.entry_id)
        await restored.initialize()
        assert await restored.size() == 1
        assert await restored.next_batch("centre") == batch
        client.side_effect = None
        stream_manifest.side_effect = None
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    restored = entry.runtime_data.updates.spool
    assert await restored.next_batch("centre") == batch
    assert restored.data["cursors"] == cursors
    assert "manifest" in restored.data and "snapshot" in restored.data
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_full_queue_reserves_shutdown_space_without_losing_buffer(hass, entry):
    from homeassistant.exceptions import HomeAssistantError

    updates = Updates(hass, entry, AsyncMock())
    await updates.spool.initialize()
    with (
        patch("custom_components.registros_camaras.update_store.MAX_REPORTS", 2),
        patch("custom_components.registros_camaras.update_store.MEMORY_REPORTS", 2),
    ):
        await updates.spool.append([(ASSIGNMENT["source"], sample(n)) for n in range(2)])
        updates.pending.extend((ASSIGNMENT["source"], sample(n)) for n in range(2, 4))
        with pytest.raises(HomeAssistantError, match="capacity"):
            await updates.spool.append(list(updates.pending))
        await updates.stop()
        restored = UpdateStore(hass, entry.entry_id)
        await restored.initialize()
        assert await restored.size() == 4
        batch = await restored.next_batch("centre")
        assert [row["sourceEventId"] for row in batch["readings"]] == [
            sample(n)["sourceEventId"] for n in range(4)
        ]
        with pytest.raises(HomeAssistantError, match="capacity"):
            await restored.append([(ASSIGNMENT["source"], sample(4))], shutdown=True)
        await restored.acknowledge(batch["commandId"])
        await restored.append([(ASSIGNMENT["source"], sample(4))])
        assert await restored.size() == 1


async def test_restart_drains_shutdown_reserve_before_resubscribing(
    hass, entry, client, stream_manifest
):
    hass.config.external_url = ASSIGNMENT["source"]
    stream_manifest.return_value["assignments"] = [ASSIGNMENT]
    store = UpdateStore(hass, entry.entry_id)
    await store.initialize()
    await store.append([(ASSIGNMENT["source"], sample(n)) for n in range(3)])
    with patch("custom_components.registros_camaras.updates.MAX_REPORTS", 2):
        assert await hass.config_entries.async_setup(entry.entry_id)
        updates = entry.runtime_data.updates
        assert updates.paused
        assert not updates.unsubscribers
        hass.states.async_set(ASSIGNMENT["entityId"], "20", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert not updates.pending
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert await updates.spool.size() == 3


async def test_manifest_storage_failure_does_not_kill_reconciliation(hass, entry):
    from homeassistant.exceptions import HomeAssistantError

    updates = Updates(hass, entry, AsyncMock())

    async def wait(_):
        if refresh.await_count == 2:
            updates.stopping = True

    with (
        patch.object(updates, "recover", new=AsyncMock(return_value=False)),
        patch.object(
            updates, "refresh", new=AsyncMock(side_effect=[HomeAssistantError("disk full"), None])
        ) as refresh,
        patch.object(updates, "wait", side_effect=wait),
        patch.object(hass.loop, "time", side_effect=[0, 301, 301, 602, 602]),
    ):
        await updates.reconcile_loop()
    assert refresh.await_count == 2


async def test_native_entry_unload_joins_transport_and_removes_subscriptions(
    hass, entry, client, stream_manifest
):
    hass.config.external_url = ASSIGNMENT["source"]
    stream_manifest.return_value["assignments"] = [ASSIGNMENT]
    assert await hass.config_entries.async_setup(entry.entry_id)
    updates = entry.runtime_data.updates
    hass.states.async_set(ASSIGNMENT["entityId"], "20", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert all(task.done() for task in updates.tasks)
    assert not updates.tasks[0].cancelled()
    restored = UpdateStore(hass, entry.entry_id)
    await restored.initialize()
    assert await restored.size() == 1
    hass.states.async_set(ASSIGNMENT["entityId"], "21", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert not updates.pending


async def test_sender_retries_identical_batch_after_lost_response(hass, entry):
    client = AsyncMock()
    client.centre_id = "centre"
    updates = Updates(hass, entry, client)
    await updates.spool.initialize()
    await updates.spool.append([(ASSIGNMENT["source"], sample())])
    acknowledged = asyncio.Event()
    original_ack = updates.spool.acknowledge

    async def ack(command_id):
        await original_ack(command_id)
        updates.stopping = True
        acknowledged.set()

    client.push_updates.side_effect = [CannotConnect("response lost"), None]
    updates.send_ready.set()
    with (
        patch.object(updates.spool, "acknowledge", side_effect=ack),
        patch.object(updates, "wait", new=AsyncMock()),
    ):
        task = asyncio.create_task(updates.send_loop())
        try:
            await asyncio.wait_for(acknowledged.wait(), 5)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert client.push_updates.call_count == 2
    assert client.push_updates.call_args_list[0] == client.push_updates.call_args_list[1]
    assert await updates.spool.size() == 0


async def test_recorder_overlap_is_deduplicated_and_cursor_commits_with_rows(hass, entry):
    from homeassistant.core import State

    updates = Updates(hass, entry, AsyncMock())
    updates.assignments = [ASSIGNMENT]
    await updates.spool.initialize()
    at = datetime.now(UTC) - timedelta(seconds=20)
    start = int(at.timestamp() * 1000) - 1000
    await updates.spool.ensure_cursor(ASSIGNMENT["id"], start)
    state = State("sensor.temperature", "20", {"unit_of_measurement": "°C"}, last_updated=at)
    await updates.spool.append([(ASSIGNMENT["source"], reading(ASSIGNMENT, state, at, "live"))])
    hass.config.components.add("recorder")
    recorder = AsyncMock()
    recorder.queue_task = lambda task: task.future.set_result(None)
    recorder.async_add_executor_job.side_effect = lambda work: work()
    with (
        patch("homeassistant.components.recorder.get_instance", return_value=recorder),
        patch(
            "homeassistant.components.recorder.history.get_significant_states",
            return_value={"sensor.temperature": [state]},
        ) as history,
    ):
        await updates.recover()
    assert history.call_args.kwargs["include_start_time_state"] is False
    assert history.call_args.kwargs["significant_changes_only"] is False
    assert await updates.spool.size() == 1
    assert await updates.spool.cursor(ASSIGNMENT["id"], 0) > start
    batch = await updates.spool.next_batch("centre")
    assert batch["readings"][0]["mode"] == "live"


async def test_coincident_equal_reports_never_collapse(hass, entry):
    from homeassistant.core import Event, State

    updates = Updates(hass, entry, AsyncMock())
    updates.assignments = [ASSIGNMENT]
    at = datetime.now(UTC)
    state = State("sensor.temperature", "20", {"unit_of_measurement": "°C"}, last_updated=at)
    event = Event(
        "state_reported", {"entity_id": state.entity_id, "new_state": state, "last_reported": at}
    )
    updates.capture(event)
    updates.capture(event)
    assert len(updates.pending) == 2
    first, second = [row for _, row in updates.pending]
    assert first["observedAt"] == second["observedAt"]
    assert first["sourceEventId"] != second["sourceEventId"]


@pytest.mark.parametrize(
    "interval,expected_samples,expected_batches",
    [(0, 0, 0), (60, 36, 1), (10, 216, 6), (1, 2160, 30)],
)
async def test_36_sensor_workload_preserves_every_report_with_bounded_batches(
    hass, interval, expected_samples, expected_batches
):
    spool = UpdateStore(hass, "workload")
    await spool.initialize()
    sent = []
    batches = 0
    for second in range(60):
        if interval and second % interval == 0:
            await spool.append(
                [
                    (ASSIGNMENT["source"], {**sample(second), "assignmentId": f"sensor-{n}"})
                    for n in range(36)
                ]
            )
        if second % 2 == 1:
            while batch := await spool.next_batch("centre"):
                assert len(batch["readings"]) <= 100
                sent.extend(batch["readings"])
                batches += 1
                await spool.acknowledge(batch["commandId"])
    assert len(sent) == expected_samples
    assert batches == expected_batches
    assert await spool.size() == 0


async def test_shutdown_joins_blocked_executor_before_final_snapshot(
    hass, entry, client, stream_manifest, real_storage
):
    import threading

    from homeassistant.core import CoreState
    from homeassistant.helpers.storage import Store

    hass.config.external_url = ASSIGNMENT["source"]
    stream_manifest.return_value["assignments"] = [ASSIGNMENT]
    assert await hass.config_entries.async_setup(entry.entry_id)
    updates = entry.runtime_data.updates
    entered = asyncio.Event()
    release = threading.Event()
    original = Store._write_prepared_data
    blocked = False

    def write(store, mode, data):
        nonlocal blocked
        if "/segment." in store.key and not blocked:
            blocked = True
            hass.loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
        original(store, mode, data)

    with patch.object(Store, "_write_prepared_data", write):
        hass.states.async_set(ASSIGNMENT["entityId"], "20", {"unit_of_measurement": "°C"})
        await asyncio.wait_for(entered.wait(), 5)
        hass.states.async_set(ASSIGNMENT["entityId"], "21", {"unit_of_measurement": "°C"})
        hass.set_state(CoreState.running)
        stopping = asyncio.create_task(hass.async_stop())
        try:
            await asyncio.wait_for(updates.stop_ready.wait(), 5)
            assert not stopping.done()
        finally:
            release.set()
        await asyncio.wait_for(stopping, 10)
    restored = UpdateStore(hass, entry.entry_id)
    await restored.initialize()
    batch = await restored.next_batch(entry.data["centre_id"])
    assert [row["value"] for row in batch["readings"]] == [20, 21]
    assert all(task.done() for task in updates.tasks)
    assert not updates.tasks[0].cancelled()


async def test_coincident_changes_and_recorder_overlap_keep_every_occurrence(hass, entry):
    updates = Updates(hass, entry, AsyncMock())
    await updates.spool.initialize()
    updates.assignments = [ASSIGNMENT]
    updates.subscribe()
    at = datetime.now(UTC) - timedelta(seconds=20)
    states = []
    try:
        with patch("homeassistant.core.time.time", return_value=at.timestamp()):
            for value in (20, 21, 20):
                hass.states.async_set(
                    ASSIGNMENT["entityId"], str(value), {"unit_of_measurement": "°C"}
                )
                await hass.async_block_till_done()
                states.append(hass.states.get(ASSIGNMENT["entityId"]))
        hass.states.async_set(ASSIGNMENT["entityId"], "22", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        rows = list(updates.pending)
        assert len({r["sourceEventId"] for _, r in rows}) == 4
        assert len({r["observedAt"] for _, r in rows[:3]}) == 1
        await updates.spool.append(rows)
        await updates.spool.ensure_cursor(ASSIGNMENT["id"], int(at.timestamp() * 1000) - 1000)
        hass.config.components.add("recorder")
        recorder = AsyncMock()
        recorder.queue_task = lambda task: task.future.set_result(None)
        recorder.async_add_executor_job.side_effect = lambda work: work()
        with (
            patch("homeassistant.components.recorder.get_instance", return_value=recorder),
            patch(
                "homeassistant.components.recorder.history.get_significant_states",
                return_value={ASSIGNMENT["entityId"]: states},
            ),
        ):
            await updates.recover()
        assert await updates.spool.size() == 4
        assert [r["value"] for r in (await updates.spool.next_batch("centre"))["readings"]] == [
            20,
            21,
            20,
            22,
        ]
    finally:
        updates.unsubscribe()


async def test_manifest_removes_last_local_subscription(hass, entry):
    hass.config.external_url = ASSIGNMENT["source"]
    client = AsyncMock()
    updates = Updates(hass, entry, client)
    await updates.prepare()
    client.sensor_stream.return_value = {"assignments": [ASSIGNMENT]}
    await updates.refresh()
    assert updates.unsubscribers
    client.sensor_stream.return_value = {
        "assignments": [{**ASSIGNMENT, "source": "https://other.example"}]
    }
    await updates.refresh()
    assert updates.assignments == []
    assert updates.unsubscribers == []
    hass.states.async_set(ASSIGNMENT["entityId"], "20")
    await hass.async_block_till_done()
    assert not updates.pending


async def test_offline_restart_captures_equal_reports_then_delivers(
    hass, entry, client, stream_manifest
):
    hass.config.external_url = ASSIGNMENT["source"]
    stream_manifest.return_value["assignments"] = [ASSIGNMENT]
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert await hass.config_entries.async_unload(entry.entry_id)
    client.side_effect = CannotConnect()
    stream_manifest.side_effect = CannotConnect()
    assert await hass.config_entries.async_setup(entry.entry_id)
    updates = entry.runtime_data.updates
    assert not entry.runtime_data.last_update_success
    assert updates.unsubscribers
    for _ in range(2):
        hass.states.async_set(ASSIGNMENT["entityId"], "20", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
    assert await updates.spool.size() == 2
    client.side_effect = None
    stream_manifest.side_effect = None
    await entry.runtime_data.async_refresh()
    await updates.refresh()
    assert entry.runtime_data.last_update_success
    await updates.stop()
    updates.stopping = False
    delivered = []

    async def push(batch):
        delivered.extend(batch["readings"])
        updates.stopping = True

    with (
        patch.object(updates.client, "push_updates", side_effect=push),
        patch.object(updates, "wait", new=AsyncMock()),
    ):
        updates.send_ready.set()
        await updates.send_loop()
    assert len(delivered) == 2
    assert len({r["sourceEventId"] for r in delivered}) == 2
    assert await updates.spool.size() == 0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_full_queue_drain_has_bounded_write_volume_and_restarts(hass, real_storage):
    from homeassistant.helpers.storage import Store

    spool = UpdateStore(hass, "full-queue")
    await spool.initialize()
    snapshot = {"tasks": [{"id": str(n), "description": "x" * 300} for n in range(1500)]}
    await spool.cache(snapshot=snapshot, manifest={"assignments": [ASSIGNMENT]})
    sizes = []
    original = Store._write_prepared_data

    def measure(store, mode, data):
        sizes.append(len(data))
        original(store, mode, data)

    with patch.object(Store, "_write_prepared_data", measure):
        await spool.append([(ASSIGNMENT["source"], sample(n)) for n in range(50000)])
        fill_bytes = sum(sizes)
        sizes.clear()
        count = 0
        while batch := await spool.next_batch("centre"):
            if count == 0:
                spool = UpdateStore(hass, "full-queue")
                await spool.initialize()
                assert spool.data["snapshot"] == snapshot
                assert await spool.next_batch("centre") == batch
            count += len(batch["readings"])
            await spool.acknowledge(batch["commandId"])
    assert count == 50000
    assert max(sizes) < 128000
    assert sum(sizes) < 32_000_000
    print(
        f"Full queue: fill={fill_bytes} bytes, drain={sum(sizes)} bytes, "
        f"max commit={max(sizes)} bytes"
    )


async def test_recovery_waits_for_real_recorder_commit(
    recorder_db_url, hass, entry, async_setup_recorder_instance, tmp_path
):
    recorder = await async_setup_recorder_instance(
        hass,
        {
            "db_url": f"sqlite:///{tmp_path / 'recorder.db'}",
            "commit_interval": 60,
        },
    )
    updates = Updates(hass, entry, AsyncMock())
    await updates.spool.initialize()
    updates.assignments = [ASSIGNMENT]
    at = datetime.now(UTC)
    await updates.spool.ensure_cursor(ASSIGNMENT["id"], int(at.timestamp() * 1000) - 1000)
    with patch("homeassistant.core.time.time", return_value=at.timestamp()):
        hass.states.async_set(ASSIGNMENT["entityId"], "20", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    await recorder.async_block_till_done()
    with patch(
        "homeassistant.core.time.time", return_value=(at + timedelta(seconds=1)).timestamp()
    ):
        hass.states.async_set(ASSIGNMENT["entityId"], "21", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    await recorder.async_add_executor_job(recorder.block_till_done)
    assert recorder._event_session_has_pending_writes
    with patch("custom_components.registros_camaras.updates.datetime") as clock:
        clock.now.return_value = at + timedelta(seconds=20)
        clock.fromtimestamp.side_effect = datetime.fromtimestamp
        await updates.recover()
    batch = await updates.spool.next_batch("centre")
    assert [r["value"] for r in batch["readings"]] == [20, 21]
    assert all(r["mode"] == "history" for r in batch["readings"])
    restored = UpdateStore(hass, entry.entry_id)
    await restored.initialize()
    assert await restored.cursor(ASSIGNMENT["id"], 0) == int(
        (at + timedelta(seconds=10)).timestamp() * 1000
    )


async def test_shutdown_timeout_flushes_ram_with_http_and_executor_blocked(
    hass, entry, client, stream_manifest, real_storage
):
    import threading

    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    hass.config.external_url = ASSIGNMENT["source"]
    stream_manifest.return_value["assignments"] = [ASSIGNMENT]
    http_entered = asyncio.Event()
    writer_entered = asyncio.Event()
    stopped = asyncio.Event()
    release = threading.Event()
    original_write = Store._write_prepared_data
    original_wait = Updates.wait

    async def slow_http(_):
        http_entered.set()
        await asyncio.Event().wait()

    async def wait(updates, seconds):
        await original_wait(updates, 0 if seconds == 2 else seconds)

    def write(store, mode, data):
        if "/segment." in store.key:
            hass.loop.call_soon_threadsafe(writer_entered.set)
            assert release.wait(5)
        original_write(store, mode, data)

    async def on_stop(_):
        stopped.set()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_stop)
    with (
        patch.object(RegistrosClient, "push_updates", side_effect=slow_http),
        patch.object(Updates, "wait", wait),
        patch("homeassistant.core.STOPPING_STAGE_SHUTDOWN_TIMEOUT", 0.05),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        hass.states.async_set(ASSIGNMENT["entityId"], "20", {"unit_of_measurement": "°C"})
        await asyncio.wait_for(http_entered.wait(), 5)
        with patch.object(Store, "_write_prepared_data", write):
            hass.states.async_set(ASSIGNMENT["entityId"], "21", {"unit_of_measurement": "°C"})
            await asyncio.wait_for(writer_entered.wait(), 5)
            hass.states.async_set(ASSIGNMENT["entityId"], "22", {"unit_of_measurement": "°C"})
            stopping = asyncio.create_task(hass.async_stop())
            try:
                await asyncio.wait_for(stopped.wait(), 5)
            finally:
                release.set()
            await asyncio.wait_for(stopping, 10)
    restored = UpdateStore(hass, entry.entry_id)
    await restored.initialize()
    assert [r["value"] for _, r in restored.data["rows"]] == [20, 21, 22]
    assert (await restored.next_batch(entry.data["centre_id"]))["readings"][0]["value"] == 20


async def test_real_recorder_excluded_attributes_keep_live_identity(
    recorder_db_url, hass, entry, async_setup_recorder_instance, tmp_path
):
    recorder = await async_setup_recorder_instance(
        hass,
        {
            "db_url": f"sqlite:///{tmp_path / 'recorder.db'}",
            "commit_interval": 60,
        },
    )
    updates = Updates(hass, entry, AsyncMock())
    await updates.spool.initialize()
    updates.assignments = [ASSIGNMENT]
    updates.subscribe()
    at = datetime.now(UTC)
    try:
        with patch("homeassistant.core.time.time", return_value=at.timestamp()):
            for value in (20, 21, 20):
                hass.states.async_set(
                    ASSIGNMENT["entityId"],
                    str(value),
                    {
                        "unit_of_measurement": "°C",
                        "attribution": "Excluded by Recorder",
                    },
                )
                await hass.async_block_till_done()
        await recorder.async_block_till_done()
        await updates.spool.append(list(updates.pending))
        await updates.spool.ensure_cursor(ASSIGNMENT["id"], int(at.timestamp() * 1000) - 1000)
        with patch("custom_components.registros_camaras.updates.datetime") as clock:
            clock.now.return_value = at + timedelta(seconds=20)
            clock.fromtimestamp.side_effect = datetime.fromtimestamp
            await updates.recover()
        assert await updates.spool.size() == 3
        batch = await updates.spool.next_batch("centre")
        assert [r["value"] for r in batch["readings"]] == [20, 21, 20]
        assert all(r["mode"] == "live" for r in batch["readings"])
    finally:
        updates.unsubscribe()


async def test_real_recorder_dequeued_event_waits_for_commit_barrier(
    recorder_db_url, hass, entry, async_setup_recorder_instance, tmp_path
):
    import threading

    from homeassistant.const import EVENT_STATE_CHANGED

    recorder = await async_setup_recorder_instance(
        hass,
        {
            "db_url": f"sqlite:///{tmp_path / 'recorder.db'}",
            "commit_interval": 60,
        },
    )
    updates = Updates(hass, entry, AsyncMock())
    await updates.spool.initialize()
    updates.assignments = [ASSIGNMENT]
    at = datetime.now(UTC)
    start = int(at.timestamp() * 1000) - 1000
    await updates.spool.ensure_cursor(ASSIGNMENT["id"], start)
    await recorder.async_block_till_done()
    entered = asyncio.Event()
    queued = asyncio.Event()
    release = threading.Event()
    original = recorder._process_one_event
    original_queue = recorder.queue_task

    def process(event):
        if (
            event.event_type == EVENT_STATE_CHANGED
            and event.data.get("entity_id") == ASSIGNMENT["entityId"]
        ):
            hass.loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
        original(event)

    def enqueue(task):
        queued.set()
        original_queue(task)

    with patch.object(recorder, "_process_one_event", process):
        hass.states.async_set(ASSIGNMENT["entityId"], "21", {"unit_of_measurement": "°C"})
        await asyncio.wait_for(entered.wait(), 5)
        assert recorder._queue.empty()
        assert not recorder._event_session_has_pending_writes
        with (
            patch.object(recorder, "queue_task", enqueue),
            patch("custom_components.registros_camaras.updates.datetime") as clock,
        ):
            clock.now.return_value = at + timedelta(seconds=20)
            clock.fromtimestamp.side_effect = datetime.fromtimestamp
            recovery = asyncio.create_task(updates.recover())
            try:
                await asyncio.wait_for(queued.wait(), 5)
                assert not recovery.done()
                assert await updates.spool.cursor(ASSIGNMENT["id"], 0) == start
            finally:
                release.set()
            await asyncio.wait_for(recovery, 5)
    batch = await updates.spool.next_batch("centre")
    assert [r["value"] for r in batch["readings"]] == [21]


@pytest.mark.parametrize("history_first", [False, True])
async def test_real_recorder_missing_unit_preserves_live_payload_and_cursor(
    recorder_db_url,
    hass,
    entry,
    async_setup_recorder_instance,
    tmp_path,
    real_storage,
    history_first,
):
    await async_setup_recorder_instance(
        hass,
        {
            "db_url": f"sqlite:///{tmp_path / 'recorder.db'}",
            "commit_interval": 60,
        },
    )
    updates = Updates(hass, entry, AsyncMock())
    await updates.spool.initialize()
    updates.assignments = [ASSIGNMENT]
    updates.subscribe()
    at = datetime.now(UTC)
    try:
        hass.states.async_set(
            ASSIGNMENT["entityId"], "20", {"unit_of_measurement": "°C", "large": "x" * 17000}
        )
        await hass.async_block_till_done()
        live = list(updates.pending)
        if not history_first:
            await updates.spool.append(live)
            batch = await updates.spool.next_batch("centre")
        await updates.spool.ensure_cursor(ASSIGNMENT["id"], int(at.timestamp() * 1000) - 1000)
        with patch("custom_components.registros_camaras.updates.datetime") as clock:
            clock.now.return_value = at + timedelta(seconds=20)
            clock.fromtimestamp.side_effect = datetime.fromtimestamp
            await updates.recover()
        if history_first:
            assert updates.spool.data["rows"][0][1]["unit"] == ""
            assert await updates.spool.next_batch("centre") is None
            await updates.spool.append(live)
            batch = await updates.spool.next_batch("centre")
        assert await updates.spool.size() == 1
        assert await updates.spool.next_batch("centre") == batch
        assert batch["readings"] == [live[0][1]]
        restored = UpdateStore(hass, entry.entry_id)
        await restored.initialize()
        assert await restored.next_batch("centre") == batch
        assert restored.data["rows"][0][1] == live[0][1]
        assert await restored.cursor(ASSIGNMENT["id"], 0) == int(
            (at + timedelta(seconds=10)).timestamp() * 1000
        )
    finally:
        updates.unsubscribe()


async def test_incomplete_history_does_not_block_other_reports_or_weaken_conflicts(
    hass, real_storage
):
    from homeassistant.exceptions import HomeAssistantError

    spool = UpdateStore(hass, "missing-unit")
    await spool.initialize()
    await spool.append(
        [
            (ASSIGNMENT["source"], {**sample(), "mode": "history", "unit": ""}),
            (ASSIGNMENT["source"], sample(1)),
        ]
    )
    batch = await spool.next_batch("centre")
    assert batch["readings"] == [sample(1)]
    await spool.acknowledge(batch["commandId"])
    restored = UpdateStore(hass, "missing-unit")
    await restored.initialize()
    assert await restored.size() == 1
    with pytest.raises(HomeAssistantError, match="Conflicting"):
        await restored.append([(ASSIGNMENT["source"], {**sample(), "value": 99})])
    await restored.append([(ASSIGNMENT["source"], sample())])
    batch = await restored.next_batch("centre")
    assert batch["readings"] == [sample()]
    await restored.append([(ASSIGNMENT["source"], {**sample(), "mode": "history", "unit": ""})])
    assert await restored.next_batch("centre") == batch
