"""Real HA setup, entities, availability, services and reloads."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockUser

from custom_components.registros_camaras.api import CannotConnect, decode_snapshot
from custom_components.registros_camaras.const import DEFAULT_CENTRE, DOMAIN
from custom_components.registros_camaras.diagnostics import async_get_config_entry_diagnostics
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er


def entity_id(hass, entry, platform, location, key):
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{location}_{key}"
    )


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_entities_fail_closed_and_discover_new_chambers(hass, entry, client, raw):
    await setup(hass, entry)
    quantity = entity_id(hass, entry, "sensor", "cam-1", "quantity")
    assert hass.states.get(quantity).state == "45"
    temp = entity_id(hass, entry, "sensor", "cam-1", "temperature_min")
    assert hass.states.get(temp).state == "2"
    assert hass.states.get(temp).attributes["unit_of_measurement"] == "°C"
    assert hass.states.get(entity_id(hass, entry, "sensor", "centre", "due_tasks")).state == "1"
    assert (
        hass.states.get(entity_id(hass, entry, "binary_sensor", "cam-1", "proposal_overdue")).state
        == "on"
    )
    coordinator = entry.runtime_data
    pending = deepcopy(raw)
    pending.update(status="pending", chambers=[], tasks=[])
    client.return_value = decode_snapshot(pending, DEFAULT_CENTRE)
    await coordinator.async_refresh()
    assert hass.states.get(quantity).state == "unavailable"
    client.side_effect = CannotConnect("Disconnected")
    await coordinator.async_refresh()
    assert hass.states.get(quantity).state == "unavailable"
    assert (
        hass.states.get(entity_id(hass, entry, "binary_sensor", "centre", "connection")).state
        == "off"
    )
    client.side_effect = None
    raw["locations"].append({**raw["locations"][0], "id": "cam-2", "name": "Cámara 2"})
    client.return_value = decode_snapshot(raw, DEFAULT_CENTRE)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id(hass, entry, "sensor", "cam-2", "quantity")).state == "0"
    raw["locations"][0]["archived"] = True
    client.return_value = decode_snapshot(raw, DEFAULT_CENTRE)
    await coordinator.async_refresh()
    assert hass.states.get(quantity).state == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass.services.has_service(DOMAIN, "confirm_proposal")


async def test_services_preserve_receipt_and_retry_identity(hass, entry, client):
    await setup(hass, entry)
    command = {
        "config_entry_id": entry.entry_id,
        "command_id": "00000000-0000-4000-8000-000000000123",
        "expected_revision": 7,
        "location_id": "cam-1",
        "proposal_id": "proposal:7",
    }
    with patch.object(
        entry.runtime_data.client, "command", new=AsyncMock(return_value={"version": 8})
    ) as send:
        client.side_effect = CannotConnect("Poll failed after acceptance")
        for _ in range(2):
            result = await hass.services.async_call(
                DOMAIN, "confirm_proposal", command, blocking=True, return_response=True
            )
            assert result == {"version": 8}
        assert send.call_args_list[0] == send.call_args_list[1]
        assert send.call_args.args[1]["expected_revision"] == 7
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "get_snapshot",
            {"config_entry_id": "missing"},
            blocking=True,
            return_response=True,
        )
    client.side_effect = None
    result = await hass.services.async_call(
        DOMAIN,
        "get_snapshot",
        {"config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )
    assert result["revision"] == 7
    diagnostic = await async_get_config_entry_diagnostics(hass, entry)
    assert "test-key" not in str(diagnostic) and "registros.example" not in str(diagnostic)
    assert "lot-1" not in str(diagnostic)
    old_id = entity_id(hass, entry, "sensor", "cam-1", "quantity")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entity_id(hass, entry, "sensor", "cam-1", "quantity") == old_id


async def test_admin_gate_rejects_reads_and_writes_before_client(hass, entry, client):
    await setup(hass, entry)
    user = MockUser().add_to_hass(hass)
    client.reset_mock()
    with patch.object(entry.runtime_data.client, "command", new=AsyncMock()) as send:
        for service, data in [
            ("get_snapshot", {}),
            (
                "create_lot",
                {
                    "command_id": "00000000-0000-4000-8000-000000000123",
                    "expected_revision": 7,
                    "code": "A",
                    "quantity": 2,
                    "started_on": "2026-11-27",
                },
            ),
        ]:
            with pytest.raises(HomeAssistantError):
                await hass.services.async_call(
                    DOMAIN,
                    service,
                    {"config_entry_id": entry.entry_id, **data},
                    blocking=True,
                    return_response=True,
                    context=Context(user_id=user.id),
                )
        send.assert_not_called()
    client.assert_not_called()


async def test_actions_exist_when_first_connection_fails(hass, entry, client):
    client.side_effect = CannotConnect("Offline")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert hass.services.has_service(DOMAIN, "get_snapshot")
    with pytest.raises(HomeAssistantError, match="loaded"):
        await hass.services.async_call(
            DOMAIN,
            "get_snapshot",
            {"config_entry_id": entry.entry_id},
            blocking=True,
            return_response=True,
        )


async def test_task_revision_zero_reaches_api_unchanged(hass, entry, client):
    await setup(hass, entry)
    with patch.object(entry.runtime_data.client, "command", new=AsyncMock(return_value={})) as send:
        await hass.services.async_call(
            DOMAIN,
            "complete_task",
            {
                "config_entry_id": entry.entry_id,
                "command_id": "00000000-0000-4000-8000-000000000123",
                "expected_revision": 7,
                "task_id": "task",
                "task_revision": 0,
                "result": {},
            },
            blocking=True,
            return_response=True,
        )
        assert send.call_args.args[1]["task_revision"] == 0


async def test_snapshot_cache_bounded_writes_flush_latest_and_restore_offline(
    hass, entry, client, raw, stream_manifest
):
    await setup(hass, entry)
    updates = entry.runtime_data.updates
    with patch.object(updates.spool, "cache", wraps=updates.spool.cache) as cache:
        for revision in range(8, 21):
            raw["revision"] = revision
            client.return_value = decode_snapshot(deepcopy(raw), DEFAULT_CENTRE)
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
        assert entry.runtime_data.data.revision == 20
        assert updates.spool.data["snapshot"]["revision"] == 7
        assert cache.await_count == 0
        # Wake the bounded cache writer at its next eligible interval.
        updates.snapshot_next_write = 0
        updates.write_ready.set()
        await hass.async_block_till_done()
        assert updates.spool.data["snapshot"]["revision"] == 20
        assert cache.await_count == 1
        raw["revision"] = 21
        client.return_value = decode_snapshot(deepcopy(raw), DEFAULT_CENTRE)
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert cache.await_count == 1
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert cache.await_count == 2
    assert updates.snapshot_timer is None
    client.side_effect = CannotConnect()
    stream_manifest.side_effect = CannotConnect()
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.runtime_data.data.revision == 21
    assert not entry.runtime_data.last_update_success
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_snapshot_cache_does_not_hide_accepted_command(hass, entry, client, raw):
    await setup(hass, entry)
    updates = entry.runtime_data.updates
    updates.snapshot_next_write = 0
    raw["revision"] = 8
    client.return_value = decode_snapshot(raw, DEFAULT_CENTRE)
    command = {
        "config_entry_id": entry.entry_id,
        "command_id": "00000000-0000-4000-8000-000000000123",
        "expected_revision": 7,
        "location_id": "cam-1",
        "proposal_id": "proposal:7",
    }
    with (
        patch.object(
            entry.runtime_data.client, "command", new=AsyncMock(return_value={"version": 8})
        ),
        patch.object(updates.spool, "cache", side_effect=HomeAssistantError("cache write failed")),
    ):
        result = await hass.services.async_call(
            DOMAIN, "confirm_proposal", command, blocking=True, return_response=True
        )
        await hass.async_block_till_done()
        assert result == {"version": 8}
        assert updates.snapshot_pending["revision"] == 8
        assert updates.spool.data["snapshot"]["revision"] == 7
        assert updates.cache_error == "Dashboard cache pending (HomeAssistantError)"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert updates.spool.data["snapshot"]["revision"] == 8


async def test_native_root_device_id_preserves_entity_identity_on_reload(
    hass, entry, client, caplog
):
    from homeassistant.helpers import device_registry as dr

    await setup(hass, entry)
    registry = dr.async_get(hass)
    root = registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    chamber = registry.async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}_cam-1"), entry.entry_id
    )
    assert chamber.via_device_id == root.id
    before = entity_id(hass, entry, "sensor", "cam-1", "quantity")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entity_id(hass, entry, "sensor", "cam-1", "quantity") == before
    assert (
        registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id).id
        == root.id
    )
    assert "deprecated `via_device`" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)
