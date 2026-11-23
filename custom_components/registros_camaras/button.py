from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .api import ApiError
from .entity import CameraEntity, add_cameras

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    add_cameras(
        entry,
        async_add_entities,
        lambda camera: [
            ConfirmButton(entry.runtime_data, camera, "confirm", "Confirmar cambio pendiente")
        ],
    )


class ConfirmButton(CameraEntity, ButtonEntity):
    @property
    def available(self):
        return super().available and bool(self.camera.get("pendiente"))

    async def async_press(self):
        pending = (self.camera or {}).get("pendiente")
        if not pending:
            raise HomeAssistantError("No hay propuesta pendiente")
        try:
            await self.coordinator.async_operation(
                "confirm_change", {"location_id": self.location_id, "proposal_id": pending["id"]}
            )
        except ApiError as err:
            raise HomeAssistantError(str(err)) from err
