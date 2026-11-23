from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import UnitOfTemperature

from .entity import CameraEntity, add_cameras

PARALLEL_UPDATES = 0
FIELDS = {
    "temperaturaMin": "Temperatura mínima vigente",
    "temperaturaMax": "Temperatura máxima vigente",
    "humedadMin": "Humedad mínima vigente",
    "humedadMax": "Humedad máxima vigente",
    "origen": "Origen de consigna",
    "manualHasta": "Fin de consigna manual",
}


async def async_setup_entry(hass, entry, async_add_entities):
    add_cameras(
        entry,
        async_add_entities,
        lambda camera: [
            CameraSensor(entry.runtime_data, camera, key, name) for key, name in FIELDS.items()
        ],
    )


class CameraSensor(CameraEntity, SensorEntity):
    def __init__(self, coordinator, camera, key, name):
        super().__init__(coordinator, camera, key, name)
        self.key = key
        if key.startswith("temperatura"):
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        elif key.startswith("humedad"):
            self._attr_device_class = SensorDeviceClass.HUMIDITY
            self._attr_native_unit_of_measurement = "%"
        elif key == "manualHasta":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        camera = self.camera or {}
        if self.key == "manualHasta":
            return datetime.fromisoformat(camera[self.key]) if camera.get(self.key) else None
        if self.key == "origen":
            return camera.get(self.key)
        return (camera.get("consigna") or {}).get(self.key)
