"""Real HA setup, entities, availability, services and reloads."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.registros_camaras.api import CannotConnect, decode_snapshot
from custom_components.registros_camaras.const import DEFAULT_CENTRE, DOMAIN
from custom_components.registros_camaras.diagnostics import async_get_config_entry_diagnostics
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
    assert not hass.services.has_service(DOMAIN, "confirm_proposal")


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
