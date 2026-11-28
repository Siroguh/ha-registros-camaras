"""Configuration and native Better Auth keys, including legacy upgrade."""

from hashlib import sha256
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.registros_camaras import async_migrate_entry
from custom_components.registros_camaras.api import (
    AuthenticationError,
    CannotConnect,
    IncompatibleApi,
)
from custom_components.registros_camaras.const import DEFAULT_CENTRE, DOMAIN
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

DATA = {"url": "https://registros.example/", "api_key": "native-key", "centre_id": DEFAULT_CENTRE}


async def test_user_and_duplicate(hass, client):
    with patch("custom_components.registros_camaras.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["api_key"] == "native-key"
        assert result["data"]["url"] == "https://registros.example"
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data=DATA
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AuthenticationError(), "invalid_auth"),
        (CannotConnect(), "cannot_connect"),
        (IncompatibleApi(), "incompatible_api"),
    ],
)
async def test_flow_errors(hass, client, error, code):
    client.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=DATA
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": code}


async def test_server_without_sensor_stream_is_rejected_during_configuration(
    hass, client, stream_manifest
):
    stream_manifest.side_effect = IncompatibleApi("Sensor stream unavailable")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=DATA
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "incompatible_api"}


async def test_reauth_and_legacy_migration(hass, client):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="legacy-account",
        data={"url": "https://old.example", "api_key": "old-key"},
    )
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2 and entry.data["legacy_auth"]
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    with patch("custom_components.registros_camaras.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "url": "https://new.example",
                "api_key": "new-native-key",
                "centre_id": "another-centre",
            },
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.data["url"] == "https://new.example"
        assert entry.data.get("legacy_auth") is not True
        assert entry.data["centre_id"] == "another-centre"
        assert entry.unique_id == sha256(b"https://new.example|another-centre").hexdigest()
        await hass.async_block_till_done()


async def test_reconfigure_moves_identity_and_rejects_duplicate_destination(hass, client):
    with patch("custom_components.registros_camaras.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data=DATA
        )
        entry = result["result"]
        await hass.async_block_till_done()
        changed = {**DATA, "url": "https://other.example"}
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}, data=changed
        )
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        duplicate = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data=changed
        )
        assert duplicate["reason"] == "already_configured"
        released = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}, data=DATA
        )
        assert released["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        blocked = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id}, data=DATA
        )
        assert blocked["reason"] == "already_configured"
        assert entry.data["url"] == changed["url"]
