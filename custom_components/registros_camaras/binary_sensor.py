from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RegistrosConfigEntry, RegistrosCoordinator
from .entity import CameraEntity, add_cameras

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RegistrosConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([ConnectionSensor(entry.runtime_data)])
    add_cameras(
        entry,
        async_add_entities,
        lambda camera: [PendingSensor(entry.runtime_data, camera, "pending", "Cambio pendiente")],
    )


class PendingSensor(CameraEntity, BinarySensorEntity):
    @property
    def is_on(self):
        return bool((self.camera or {}).get("pendiente"))

    @property
    def extra_state_attributes(self):
        return {**super().extra_state_attributes, "pending": (self.camera or {}).get("pendiente")}


class ConnectionSensor(CoordinatorEntity[RegistrosCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_is_on = True

    def __init__(self, coordinator: RegistrosCoordinator) -> None:
        super().__init__(coordinator)
        status = coordinator.data
        self._attr_unique_id = f"{status.account_id}_connection"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, status.account_id)},
            name=status.name,
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Registros Cámaras",
            model="REST API",
            sw_version=f"API {status.api_version}",
            configuration_url=coordinator.client.base_url,
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return {
            "api_version": self.coordinator.data.api_version,
            "capabilities": list(self.coordinator.data.capabilities),
        }
