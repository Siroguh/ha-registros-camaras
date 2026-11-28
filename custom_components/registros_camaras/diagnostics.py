"""Minimal allowlisted diagnostics; never serialize entry data or client objects."""


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "entry_version": entry.version,
        "last_update_success": coordinator.last_update_success,
        "api_version": coordinator.data.raw["apiVersion"],
        "synchronization": coordinator.data.raw["status"],
        "revision": coordinator.data.revision,
        "chambers": len(coordinator.data.locations),
        "outstanding_tasks": len(coordinator.data.tasks),
        "updates": coordinator.updates.diagnostics(),
    }
