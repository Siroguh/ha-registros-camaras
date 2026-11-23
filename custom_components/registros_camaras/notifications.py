import logging

from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .api import ApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def async_setup_notifications(hass, entry):
    service = entry.options.get("notify_service", "")
    if not service:
        return
    sent: dict[str, str] = {}
    running = False

    async def notify():
        nonlocal running
        if running or not entry.runtime_data.last_update_success:
            return
        running = True
        try:
            for camera in entry.runtime_data.dashboard["camaras"]:
                pending = camera["pendiente"]
                location = camera["ubicacionId"]
                tag = f"{DOMAIN}_{entry.entry_id}_{location}"
                if not pending:
                    if sent.pop(location, None) and service == "persistent_notification":
                        persistent_notification.async_dismiss(hass, tag)
                    continue
                if sent.get(location) == pending["id"]:
                    continue
                target = pending["propuesta"]
                values = (
                    f"{target['temperaturaMin']}–{target['temperaturaMax']} °C / "
                    f"{target['humedadMin']}–{target['humedadMax']} %"
                    if target
                    else "Retirar consigna automática"
                )
                message = f"{values}. {pending['motivo']}"
                try:
                    if service == "persistent_notification":
                        persistent_notification.async_create(
                            hass,
                            message + " Confirma desde la tarjeta o el botón de la cámara.",
                            title=camera["nombre"],
                            notification_id=tag,
                        )
                    else:
                        await hass.services.async_call(
                            "notify",
                            service,
                            {
                                "title": camera["nombre"],
                                "message": message,
                                "data": {
                                    "tag": tag,
                                    "actions": [
                                        {
                                            "action": f"rc:{entry.entry_id}:{pending['id']}",
                                            "title": "Confirmar esta propuesta",
                                        }
                                    ],
                                },
                            },
                            blocking=True,
                        )
                    sent[location] = pending["id"]
                except HomeAssistantError:
                    _LOGGER.warning("No se pudo entregar una notificación de consigna")
        finally:
            running = False

    @callback
    def updated():
        entry.async_create_background_task(hass, notify(), "registros-notification")

    async def action(event):
        value = event.data.get("action", "")
        prefix = f"rc:{entry.entry_id}:"
        if not isinstance(value, str) or not value.startswith(prefix):
            return
        proposal = value[len(prefix) :]
        camera = next(
            (
                c
                for c in entry.runtime_data.dashboard["camaras"]
                if (c.get("pendiente") or {}).get("id") == proposal
            ),
            None,
        )
        try:
            if not camera:
                raise HomeAssistantError("Esta propuesta ya no está pendiente. Revisa la cámara.")
            await entry.runtime_data.async_operation(
                "confirm_change", {"location_id": camera["ubicacionId"], "proposal_id": proposal}
            )
        except (ApiError, HomeAssistantError) as err:
            persistent_notification.async_create(
                hass,
                str(err),
                title="No se confirmó el cambio",
                notification_id=f"{DOMAIN}_confirmation_error_{entry.entry_id}",
            )

    entry.async_on_unload(hass.bus.async_listen("mobile_app_notification_action", action))
    entry.async_on_unload(entry.runtime_data.async_add_listener(updated))
    updated()
