from homeassistant.core import HomeAssistant

from .coordinator import RegistrosConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: RegistrosConfigEntry
) -> dict[str, object]:
    coordinator = entry.runtime_data
    return {
        "connected": coordinator.last_update_success,
        "api_version": coordinator.data.api_version,
        "capabilities": list(coordinator.data.capabilities),
    }
