"""Registros Cámaras 2 integration, based on custom-components/blueprint."""

from homeassistant.const import CONF_API_KEY, CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import RegistrosClient
from .const import CONF_CENTRE_ID, DEFAULT_CENTRE, DOMAIN
from .coordinator import RegistrosConfigEntry, RegistrosCoordinator
from .services import async_register_services, async_unload_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: RegistrosConfigEntry) -> bool:
    if entry.data.get("legacy_auth"):
        raise ConfigEntryAuthFailed("Configure the new server URL and a native Better Auth API key")
    client = RegistrosClient(
        async_get_clientsession(hass),
        entry.data[CONF_URL],
        entry.data[CONF_API_KEY],
        entry.data[CONF_CENTRE_ID],
    )
    coordinator = RegistrosCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    entry.async_on_unload(entry.add_update_listener(async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RegistrosConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    async_unload_services(hass, entry)
    return True


async def async_reload(hass: HomeAssistant, entry: RegistrosConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: RegistrosConfigEntry) -> bool:
    if entry.version > 2:
        return False
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry,
            version=2,
            data={
                **entry.data,
                CONF_CENTRE_ID: DEFAULT_CENTRE,
                "legacy_auth": True,
            },
        )
    return True
