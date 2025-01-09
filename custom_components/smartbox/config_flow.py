"""Config flow for Smartbox."""

from collections.abc import Mapping
import copy
import logging
from typing import Any

import requests
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    FlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import create_smartbox_session_from_entry
from .const import (
    CONF_API_NAME,
    CONF_BASIC_AUTH_CREDS,
    CONF_PASSWORD,
    CONF_SESSION_BACKOFF_FACTOR,
    CONF_SESSION_RETRY_ATTEMPTS,
    CONF_SOCKET_BACKOFF_FACTOR,
    CONF_SOCKET_RECONNECT_ATTEMPTS,
    CONF_USERNAME,
    CONF_X_REFERER,
    CONF_X_SERIALID,
    DEFAULT_SESSION_RETRY_ATTEMPTS,
    DEFAULT_SOCKET_BACKOFF_FACTOR,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
LOGIN_DATA_SCHEMA = {
    vol.Required(CONF_USERNAME): TextSelector(
        TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
    ),
    vol.Required(CONF_PASSWORD): TextSelector(
        TextSelectorConfig(
            type=TextSelectorType.PASSWORD,
            autocomplete="current-password",
        )
    ),
}

SESSION_DATA_SCHEMA = {
    vol.Required(
        CONF_SESSION_RETRY_ATTEMPTS,
        default=DEFAULT_SESSION_RETRY_ATTEMPTS,
    ): cv.positive_int,
    vol.Required(
        CONF_SESSION_BACKOFF_FACTOR,
        default=DEFAULT_SOCKET_BACKOFF_FACTOR,
    ): cv.small_float,
    vol.Required(CONF_SOCKET_RECONNECT_ATTEMPTS, default=3): cv.positive_int,
    vol.Required(CONF_SOCKET_BACKOFF_FACTOR, default=0.1): cv.small_float,
}

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_NAME): str,
        **LOGIN_DATA_SCHEMA,
        vol.Required(CONF_BASIC_AUTH_CREDS): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_X_REFERER): str,
        vol.Required(CONF_X_SERIALID): str,
        # **SESSION_DATA_SCHEMA,
    },
)


def add_suggested_values_to_schema(
    data_schema: vol.Schema, suggested_values: Mapping[str, Any]
) -> vol.Schema:
    """Make a copy of the schema, populated with suggested values.

    For each schema marker matching items in `suggested_values`,
    the `suggested_value` will be set. The existing `suggested_value` will
    be left untouched if there is no matching item.
    """
    schema = {}
    for key, val in data_schema.schema.items():
        new_key = key
        if key in suggested_values and isinstance(key, vol.Marker):
            # Copy the marker to not modify the flow schema
            new_key = copy.copy(key)
            new_key.description = {"suggested_value": suggested_values[key]}
        schema[new_key] = val
    _LOGGER.debug("add_suggested_values_to_schema: schema=%s", schema)
    return vol.Schema(schema)


class ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for test."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            try:
                await create_smartbox_session_from_entry(self.hass, user_input)
            except requests.exceptions.ConnectionError as ex:
                errors["base"] = "cannot_connect"
                placeholders["error"] = str(ex)
            except Exception as ex:
                errors["base"] = "invalid_auth"
                placeholders["error"] = str(ex)
            except Exception as ex:
                errors["base"] = "unknown"
                placeholders["base"] = str(ex)
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME], data=user_input
                )
        self.context["title_placeholders"] = placeholders
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        self.current_user_inputs = entry_data.copy()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Dialog that informs the user that reauth is required."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            user_input = {**self.current_user_inputs, **user_input}
            try:
                await create_smartbox_session_from_entry(self.hass, user_input)
            except Exception as ex:
                errors["base"] = "invalid_auth"
                placeholders["error"] = str(ex)
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME])
                self._abort_if_unique_id_mismatch(reason="invalid_auth")
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates=user_input,
                )
            return await self.async_step_user(user_input=user_input)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(LOGIN_DATA_SCHEMA),
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler(config_entry=config_entry)


class OptionsFlowHandler(OptionsFlow):
    """Options flow."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initilisation of class."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Manage the Netatmo options."""
        return await self.async_step_session_options()

    async def async_step_session_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title=None, data=user_input)

        return self.async_show_form(
            step_id="session_options",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(SESSION_DATA_SCHEMA), self.config_entry.options
            ),
        )
