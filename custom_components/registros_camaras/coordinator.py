from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiError, AuthenticationError, IntegrationStatus, RegistrosClient
from .const import DOMAIN, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)

type RegistrosConfigEntry = ConfigEntry[RegistrosCoordinator]


class RegistrosCoordinator(DataUpdateCoordinator[IntegrationStatus]):
    config_entry: RegistrosConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: RegistrosConfigEntry, client: RegistrosClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
            always_update=False,
        )
        self.client = client

    async def _async_update_data(self) -> IntegrationStatus:
        try:
            status = await self.client.async_get_status()
            if status.account_id != self.config_entry.unique_id:
                raise ConfigEntryAuthFailed("The API key belongs to a different account")
            return status
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed("The integration key is invalid or revoked") from err
        except ApiError as err:
            raise UpdateFailed(str(err)) from err
