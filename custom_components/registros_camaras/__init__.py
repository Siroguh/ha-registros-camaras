from homeassistant.const import CONF_API_KEY, CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RegistrosClient
from .bridge import async_register
from .const import DOMAIN
from .coordinator import RegistrosConfigEntry, RegistrosCoordinator
from .notifications import async_setup_notifications

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.BUTTON]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    await async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: RegistrosConfigEntry) -> bool:
    client = RegistrosClient(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_API_KEY]
    )
    coordinator = RegistrosCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload))
    await async_setup_notifications(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RegistrosConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload(hass: HomeAssistant, entry: RegistrosConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
