from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RegistrosCoordinator


class CameraEntity(CoordinatorEntity[RegistrosCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator, camera, key, name):
        super().__init__(coordinator)
        self.location_id = camera["ubicacionId"]
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{self.location_id}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.config_entry.unique_id}_{self.location_id}")},
            name=camera["nombre"],
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="Registros Cámaras",
            model="Cámara lógica",
            via_device=(DOMAIN, coordinator.config_entry.unique_id),
        )

    @property
    def camera(self):
        return next(
            (
                c
                for c in self.coordinator.dashboard["camaras"]
                if c["ubicacionId"] == self.location_id
            ),
            None,
        )

    @property
    def available(self):
        return super().available and self.camera is not None

    @property
    def extra_state_attributes(self):
        return {"location_id": self.location_id, "entry_id": self.coordinator.config_entry.entry_id}


def add_cameras(entry, async_add_entities, factory):
    known = set()

    def discover():
        new = []
        for camera in entry.runtime_data.dashboard["camaras"]:
            if camera["ubicacionId"] not in known:
                known.add(camera["ubicacionId"])
                new.extend(factory(camera))
        if new:
            async_add_entities(new)

    discover()
    entry.async_on_unload(entry.runtime_data.async_add_listener(discover))
