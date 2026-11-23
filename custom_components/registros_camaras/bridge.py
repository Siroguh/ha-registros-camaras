from typing import Any

import probatio as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from .api import ApiError
from .const import DOMAIN

OPERATIONS = [
    "snapshot",
    "lots",
    "movement_options",
    "create_lot",
    "register_movement",
    "set_manual",
    "confirm_change",
]
WRITES = ["create_lot", "register_movement", "set_manual", "confirm_change"]


def coordinator(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if not entry or entry.domain != DOMAIN or entry.state != ConfigEntryState.LOADED:
        raise HomeAssistantError("Configura y conecta primero la integración Registros Cámaras")
    return entry.runtime_data


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/entries"})
@websocket_api.require_admin
@websocket_api.async_response
async def entries(hass, connection, msg):
    connection.send_result(
        msg["id"],
        [
            {"id": e.entry_id, "title": e.title, "ready": e.state == ConfigEntryState.LOADED}
            for e in hass.config_entries.async_entries(DOMAIN)
        ],
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/operation",
        vol.Required("entry_id"): str,
        vol.Required("operation"): vol.In(OPERATIONS),
        vol.Optional("data", default={}): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def operation(hass, connection, msg):
    try:
        runtime = coordinator(hass, msg["entry_id"])
        if msg["operation"] == "snapshot":
            await runtime.async_request_refresh()
            if not runtime.last_update_success:
                raise HomeAssistantError(
                    "La API no está disponible. Revisa la conexión de la integración."
                )
            result = runtime.dashboard
        else:
            result = await runtime.async_operation(msg["operation"], msg["data"])
        connection.send_result(msg["id"], result)
    except (ApiError, HomeAssistantError) as err:
        connection.send_error(msg["id"], "request_failed", str(err))


async def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, entries)
    websocket_api.async_register_command(hass, operation)

    async def handle(call: ServiceCall) -> None:
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if not user or not user.is_admin:
                raise Unauthorized()
        try:
            await coordinator(hass, call.data["entry_id"]).async_operation(
                call.service, dict(call.data)
            )
        except ApiError as err:
            raise HomeAssistantError(str(err)) from err

    for name in WRITES:
        fields: dict[Any, Any] = {vol.Required("entry_id"): str}
        if name in {"set_manual", "confirm_change"}:
            fields[vol.Required("location_id")] = str
        if name == "register_movement":
            fields[vol.Required("lot_id")] = str
        if name == "confirm_change":
            fields[vol.Required("proposal_id")] = str
        else:
            fields[vol.Required("payload")] = dict
        hass.services.async_register(DOMAIN, name, handle, schema=vol.Schema(fields))
