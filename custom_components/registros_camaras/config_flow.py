from collections.abc import Mapping
from typing import Any

import probatio

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import AuthenticationError, CannotConnect, IncompatibleApi, RegistrosClient, normalize_url
from .const import DOMAIN


class RegistrosConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._async_configure("user", user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_configure("reauth_confirm", user_input, self._get_reauth_entry())

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_configure("reconfigure", user_input, self._get_reconfigure_entry())

    async def _async_configure(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        entry: ConfigEntry | None = None,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = dict(entry.data) if entry else {}
            data.update(user_input)
            if entry and not user_input.get(CONF_API_KEY, "").strip():
                data[CONF_API_KEY] = entry.data[CONF_API_KEY]
            try:
                data[CONF_URL] = normalize_url(data[CONF_URL])
                data[CONF_API_KEY] = data[CONF_API_KEY].strip()
                if not data[CONF_API_KEY].startswith("rc_"):
                    raise AuthenticationError("Use an application integration key")
                client = RegistrosClient(
                    async_get_clientsession(self.hass), data[CONF_URL], data[CONF_API_KEY]
                )
                status = await client.async_get_status()
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except IncompatibleApi:
                errors["base"] = "incompatible_api"
            else:
                await self.async_set_unique_id(status.account_id)
                if entry:
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(entry, data_updates=data)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=status.name, data=data)

        fields = {}
        if step_id != "reauth_confirm":
            fields[probatio.Required(CONF_URL)] = TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            )
        key_field = probatio.Optional if step_id == "reconfigure" else probatio.Required
        fields[key_field(CONF_API_KEY)] = TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
        schema = probatio.Schema(fields)
        if entry and step_id == "reconfigure":
            schema = self.add_suggested_values_to_schema(schema, {CONF_URL: entry.data[CONF_URL]})
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)
