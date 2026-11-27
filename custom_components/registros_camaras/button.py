"""Refresh action; business confirmations require explicit IDs and revisions."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from .entity import RegistrosEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([RefreshButton(entry.runtime_data)])


class RefreshButton(RegistrosEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator, "refresh")

    @property
    def available(self):
        return True

    async def async_press(self):
        await self.coordinator.async_request_refresh()
