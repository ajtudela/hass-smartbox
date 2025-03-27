from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartbox import (
    APIUnavailableError,
    InvalidAuthError,
    SmartboxError,
)
from custom_components.smartbox.config_flow import SmartboxConfigFlow

from .const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    MOCK_SESSION_CONFIG,
    MOCK_SMARTBOX_CONFIG,
)


async def test_show_form(hass: HomeAssistant) -> None:
    """Test that the form is served with no input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_integration_already_exists(
    hass: HomeAssistant, mock_smartbox, reseller
) -> None:
    """Test we only allow a single config flow."""
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data=MOCK_SMARTBOX_CONFIG[DOMAIN],
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data=MOCK_SMARTBOX_CONFIG[DOMAIN],
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_option_flow(hass: HomeAssistant, config_entry) -> None:
    """Test config flow options."""
    valid_option = {
        "history_consumption": "off",
    }
    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "options"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=valid_option
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    for k, v in MOCK_SESSION_CONFIG[DOMAIN].items():
        assert config_entry.options[k] == v


async def test_step_reauth(hass: HomeAssistant, mock_smartbox, reseller) -> None:
    """Test the reauth flow."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        data=MOCK_SMARTBOX_CONFIG[DOMAIN],
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "new_password",
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"

    assert len(hass.config_entries.async_entries()) == 1
    assert entry.data[CONF_PASSWORD] == "new_password"
    await hass.async_block_till_done()


async def test_async_step_user_show_form(hass: HomeAssistant) -> None:
    """Test that the form is served with no input."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    result = await flow.async_step_user(user_input=None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_async_step_user_cannot_connect(hass: HomeAssistant) -> None:
    """Test handling cannot connect error."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    with patch(
        "custom_components.smartbox.config_flow.create_smartbox_session_from_entry",
        side_effect=APIUnavailableError("Cannot connect"),
    ):
        result = await flow.async_step_user(user_input=MOCK_SMARTBOX_CONFIG[DOMAIN])
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert flow.context["title_placeholders"]["error"] == "Cannot connect"


async def test_async_step_user_invalid_auth(hass: HomeAssistant) -> None:
    """Test handling invalid auth error."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    with patch(
        "custom_components.smartbox.config_flow.create_smartbox_session_from_entry",
        side_effect=InvalidAuthError("Invalid auth"),
    ):
        result = await flow.async_step_user(user_input=MOCK_SMARTBOX_CONFIG[DOMAIN])
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert flow.context["title_placeholders"]["error"] == "Invalid auth"


async def test_async_step_user_unknown_error(hass: HomeAssistant) -> None:
    """Test handling unknown error."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    with patch(
        "custom_components.smartbox.config_flow.create_smartbox_session_from_entry",
        side_effect=SmartboxError("Unknown error"),
    ):
        result = await flow.async_step_user(user_input=MOCK_SMARTBOX_CONFIG[DOMAIN])
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
    assert flow.context["title_placeholders"]["error"] == "Unknown error"


async def test_async_step_reauth_confirm_show_form(hass: HomeAssistant) -> None:
    """Test that the reauth confirm form is served with no input."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    flow.current_user_inputs = MOCK_SMARTBOX_CONFIG[DOMAIN]
    result = await flow.async_step_reauth_confirm(user_input=None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {}


async def test_async_step_reauth_confirm_invalid_auth(
    hass: HomeAssistant,
) -> None:
    """Test handling invalid auth error during reauth confirm."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    flow.current_user_inputs = MOCK_SMARTBOX_CONFIG[DOMAIN]
    with patch(
        "custom_components.smartbox.config_flow.create_smartbox_session_from_entry",
        side_effect=InvalidAuthError("Invalid auth"),
    ):
        result = await flow.async_step_reauth_confirm(
            user_input={
                CONF_USERNAME: "user@email.com",
                CONF_PASSWORD: "new_password",
            }
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert flow.context["title_placeholders"]["error"] == "Invalid auth"


async def test_async_step_reauth_confirm_unknown_error(
    hass: HomeAssistant,
) -> None:
    """Test handling unknown error during reauth confirm."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    flow.current_user_inputs = MOCK_SMARTBOX_CONFIG[DOMAIN]
    with patch(
        "custom_components.smartbox.config_flow.create_smartbox_session_from_entry",
        side_effect=SmartboxError("Unknown error"),
    ):
        result = await flow.async_step_reauth_confirm(
            user_input={
                CONF_USERNAME: "user@email.com",
                CONF_PASSWORD: "new_password",
            }
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
    assert flow.context["title_placeholders"]["error"] == "Unknown error"


async def test_async_step_reauth_confirm_api_unavailable(
    hass: HomeAssistant,
) -> None:
    """Test handling api unavailable error during reauth confirm."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    flow.current_user_inputs = MOCK_SMARTBOX_CONFIG[DOMAIN]
    with patch(
        "custom_components.smartbox.config_flow.create_smartbox_session_from_entry",
        side_effect=APIUnavailableError("Cannot connect"),
    ):
        result = await flow.async_step_reauth_confirm(
            user_input={
                CONF_USERNAME: "user@email.com",
                CONF_PASSWORD: "new_password",
            }
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert flow.context["title_placeholders"]["error"] == "Cannot connect"
