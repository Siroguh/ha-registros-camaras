"""Shared polling, following custom-components/blueprint's coordinator pattern."""

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiError, AuthenticationError, RegistrosClient, Snapshot
from .const import DOMAIN, UPDATE_INTERVAL_SECONDS
from .updates import Updates

type RegistrosConfigEntry = ConfigEntry[RegistrosCoordinator]


class RegistrosCoordinator(DataUpdateCoordinator[Snapshot]):
    config_entry: RegistrosConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: RegistrosConfigEntry, client: RegistrosClient
    ) -> None:
        super().__init__(
            hass,
            logging.getLogger(__name__),
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.client = client
        self.updates = Updates(hass, entry, client)

    async def _async_update_data(self) -> Snapshot:
        try:
            return await self.client.snapshot()
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed("API key rejected") from err
        except ApiError as err:
            raise UpdateFailed(str(err)) from err
