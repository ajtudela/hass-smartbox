"""Config flow for Smartbox."""

import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    FlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from smartbox import AvailableResailers

from . import (
    APIUnavailableError,
    InvalidAuthError,
    SmartboxConfigEntry,
    SmartboxError,
    create_smartbox_session_from_entry,
)
from .const import (
    CONF_API_NAME,
    CONF_DISPLAY_ENTITY_PICTURES,
    CONF_HISTORY_CONSUMPTION,
    CONF_PASSWORD,
    CONF_SESSION_BACKOFF_FACTOR,
    CONF_SESSION_RETRY_ATTEMPTS,
    CONF_SOCKET_BACKOFF_FACTOR,
    CONF_SOCKET_RECONNECT_ATTEMPTS,
    CONF_USERNAME,
    DEFAULT_SESSION_RETRY_ATTEMPTS,
    DEFAULT_SOCKET_BACKOFF_FACTOR,
    DOMAIN,
    HistoryConsumptionStatus,
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

OPTIONS_DATA_SCHEMA = {
    vol.Required(CONF_HISTORY_CONSUMPTION): SelectSelector(
        SelectSelectorConfig(
            options=[e.value for e in HistoryConsumptionStatus],
            mode=SelectSelectorMode.DROPDOWN,
        )
    ),
    vol.Required(CONF_DISPLAY_ENTITY_PICTURES, default=False): BooleanSelector(),
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
        vol.Required(CONF_API_NAME): SelectSelector(
            SelectSelectorConfig(
                options=[
                    SelectOptionDict(value=resailer.api_url, label=resailer.name)
                    for resailer in AvailableResailers.resailers.values()
                ],
                mode=SelectSelectorMode.DROPDOWN,
            )
        ),
        **LOGIN_DATA_SCHEMA,
    },
)


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
            except APIUnavailableError as ex:
                errors["base"] = "cannot_connect"
                placeholders["error"] = str(ex)
            except InvalidAuthError as ex:
                errors["base"] = "invalid_auth"
                placeholders["error"] = str(ex)
            except SmartboxError as ex:
                errors["base"] = "unknown"
                placeholders["error"] = str(ex)
            else:
                await self.async_set_unique_id(
                    f"{AvailableResailers(api_url=user_input[CONF_API_NAME]).api_url}_{user_input[CONF_USERNAME]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{AvailableResailers(api_url=user_input[CONF_API_NAME]).name} {user_input[CONF_USERNAME]}",
                    data=user_input,
                )
        context = dict(self.context)
        context["title_placeholders"] = placeholders
        self.context = context
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
            except APIUnavailableError as ex:
                errors["base"] = "cannot_connect"
                placeholders["error"] = str(ex)
            except InvalidAuthError as ex:
                errors["base"] = "invalid_auth"
                placeholders["error"] = str(ex)
            except SmartboxError as ex:
                errors["base"] = "unknown"
                placeholders["error"] = str(ex)
            else:
                await self.async_set_unique_id(
                    f"{AvailableResailers(api_url=user_input[CONF_API_NAME]).api_url}_{user_input[CONF_USERNAME]}"
                )
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
        config_entry: SmartboxConfigEntry,
    ) -> OptionsFlow:
        """Create the options flow."""
        return OptionsFlowHandler(config_entry=config_entry)


class OptionsFlowHandler(OptionsFlow):
    """Options flow."""

    def __init__(self, config_entry: SmartboxConfigEntry) -> None:
        """Initialisation of class."""
        self.config_entry_options = config_entry.options

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Manage the Netatmo options."""
        return await self.async_step_options()

    async def async_step_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title=None, data=user_input)

        return self.async_show_form(
            step_id="options",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(OPTIONS_DATA_SCHEMA), self.config_entry_options
            ),
        )
