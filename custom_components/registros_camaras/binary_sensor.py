"""Connection, synchronization, pending proposals and conflicts."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .entity import RegistrosEntity, discover


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities([ConnectionSensor(coordinator)])
    discover(
        coordinator,
        async_add_entities,
        lambda location: [
            CameraBinarySensor(coordinator, key, location)
            for key in ("proposal_pending", "proposal_overdue", "conflict", "manual_active")
        ],
    )


class ConnectionSensor(RegistrosEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator, "connection")

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self):
        return {
            **super().extra_state_attributes,
            "synchronization": self.coordinator.data.raw["status"],
        }


class CameraBinarySensor(RegistrosEntity, BinarySensorEntity):
    def __init__(self, coordinator, key, location):
        super().__init__(coordinator, key, location)
        self.key = key
        if key in {"conflict", "proposal_overdue"}:
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self):
        chamber = self.chamber
        if self.key == "conflict":
            return chamber["status"] == "conflict"
        if self.key == "manual_active":
            return chamber["manual"] is not None
        proposal = chamber["proposal"]
        return proposal is not None and (self.key == "proposal_pending" or proposal["overdue"])
