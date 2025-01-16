from unittest.mock import AsyncMock

from const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    MOCK_SESSION_CONFIG,
    MOCK_SMARTBOX_CONFIG,
)
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartbox.const import DOMAIN


async def test_show_form(hass: HomeAssistant) -> None:
    """Test that the form is served with no input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_integration_already_exists(hass: HomeAssistant, mock_smartbox) -> None:
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


async def test_form(
    hass: HomeAssistant, mock_setup_entry: AsyncMock, mock_smartbox
) -> None:
    """Test we get the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {}
    data = {
        "api_name": "test_api_name_1",
        CONF_USERNAME: MOCK_SMARTBOX_CONFIG[DOMAIN][CONF_USERNAME],
        "password": "test_password_1",
        "basic_auth_creds": "test_basic_auth_creds",
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        data,
    )
    await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == MOCK_SMARTBOX_CONFIG[DOMAIN][CONF_USERNAME]
    assert result["data"] == data
    assert len(mock_setup_entry.mock_calls) == 1


async def test_option_flow(hass: HomeAssistant, config_entry) -> None:
    """Test config flow options."""
    valid_option = {
        "session_retry_attempts": 7,
        "session_backoff_factor": 0.4,
        "socket_reconnect_attempts": 6,
        "socket_backoff_factor": 0.5,
    }
    result = await hass.config_entries.options.async_init(config_entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "session_options"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=valid_option
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    for k, v in MOCK_SESSION_CONFIG[DOMAIN].items():
        assert config_entry.options[k] == v


async def test_step_reauth(hass: HomeAssistant, mock_smartbox) -> None:
    """Test the reauth flow."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@email.com",
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
