from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, PropertyMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from smartbox.protocol import (
    AuthError,
    SmartboxError,
    TokenBundle,
    TransportUnavailableError,
    TransportUnsupportedError,
)
from smartbox.protocol.reseller import SmartboxReseller

from custom_components.smartbox import config_flow as config_flow_mod
from custom_components.smartbox.config_flow import SmartboxConfigFlow
from custom_components.smartbox.token_store import token_store_for

from .const import (
    CONF_API_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    MOCK_SMARTBOX_CONFIG,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_TOKEN_GRANT = "custom_components.smartbox.config_flow.TokenManager.get_token"
_VALIDATE_CREDENTIALS = (
    "custom_components.smartbox.config_flow.async_validate_credentials"
)
_WS_PROBE = "custom_components.smartbox.config_flow.check_ws_user_support"
_JWT = "hdr.eyJ1c2VySWQiOiJ1In0.sig"


@pytest.fixture
def units():
    """Pin the mock world to Celsius: the config flow reads no node values."""
    return "C"


def _stub_token_bundle() -> TokenBundle:
    return TokenBundle(
        access_token=_JWT,
        refresh_token="rt",
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )


async def test_show_form(hass: HomeAssistant) -> None:
    """Test that the form is served with no input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_integration_already_exists(
    hass: HomeAssistant, mock_smartbox, reseller, mock_validate_credentials
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
    # Assert the persisted options, not a loop over an empty fixture mapping:
    # returning data={} from the flow would otherwise pass silently.
    assert config_entry.options["history_consumption"] == "off"
    assert config_entry.options["reseller_entity"] is False
    assert config_entry.options["timedelta_update_power"] == 60

    # A zero interval would make async_track_time_interval re-arm at loop
    # time and busy-loop REST reads, so the schema must reject it.
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={**valid_option, "timedelta_update_power": 0},
        )


async def test_step_reauth(
    hass: HomeAssistant, mock_smartbox, reseller, mock_validate_credentials
) -> None:
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


async def test_reconfigure_updates_credentials_and_identity(
    hass: HomeAssistant, mock_smartbox, reseller
) -> None:
    """Reconfigure replaces the account data and keeps the same entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        title="Test API name user@email.com",
        data={
            CONF_API_NAME: "test_api_name_1",
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "old_password",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    with patch(_VALIDATE_CREDENTIALS, new=AsyncMock()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_NAME: "test_api_name_1",
                CONF_USERNAME: "new@email.com",
                CONF_PASSWORD: "new_password",
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_USERNAME] == "new@email.com"
    assert entry.data[CONF_PASSWORD] == "new_password"
    assert entry.unique_id == "test_api_name_1_new@email.com"
    assert entry.title == "Test API name new@email.com"


async def test_reconfigure_same_account_still_succeeds(
    hass: HomeAssistant, mock_smartbox, reseller
) -> None:
    """Submitting the current account must not abort against its own entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        data={
            CONF_API_NAME: "test_api_name_1",
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "old_password",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    with patch(_VALIDATE_CREDENTIALS, new=AsyncMock()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_NAME: "test_api_name_1",
                CONF_USERNAME: "user@email.com",
                CONF_PASSWORD: "new_password",
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.unique_id == "test_api_name_1_user@email.com"


async def test_reconfigure_to_an_existing_account_aborts(
    hass: HomeAssistant, mock_smartbox, reseller
) -> None:
    """Reconfiguring onto another entry's account is refused."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        data={
            CONF_API_NAME: "test_api_name_1",
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "old_password",
        },
    )
    entry.add_to_hass(hass)
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_other@email.com",
        data={
            CONF_API_NAME: "test_api_name_1",
            CONF_USERNAME: "other@email.com",
            CONF_PASSWORD: "other_password",
        },
    )
    other.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    with patch(_VALIDATE_CREDENTIALS, new=AsyncMock()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_NAME: "test_api_name_1",
                CONF_USERNAME: "other@email.com",
                CONF_PASSWORD: "new_password",
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_USERNAME] == "user@email.com"


async def test_reconfigure_invalid_auth_reshows_the_form(
    hass: HomeAssistant, mock_smartbox, reseller
) -> None:
    """A failed validation keeps the reconfigure form open with its error."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        data={
            CONF_API_NAME: "test_api_name_1",
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "old_password",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    with patch(
        _VALIDATE_CREDENTIALS,
        new=AsyncMock(side_effect=AuthError("bad credentials")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_NAME: "test_api_name_1",
                CONF_USERNAME: "user@email.com",
                CONF_PASSWORD: "wrong_password",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "old_password"


async def test_reconfigure_blank_password_reshows_a_form_error(
    hass: HomeAssistant, mock_smartbox, reseller
) -> None:
    """Blank credentials are a form error, not an uncaught ValueError."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        data={
            CONF_API_NAME: "test_api_name_1",
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "old_password",
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_API_NAME: "test_api_name_1",
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "old_password"


async def test_reconfigure_schedules_reload_for_a_non_loaded_entry(
    hass: HomeAssistant, mock_smartbox, reseller
) -> None:
    """A failed entry has no update listener, so the flow schedules the reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        data={
            CONF_API_NAME: "test_api_name_1",
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "old_password",
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    with (
        patch(_VALIDATE_CREDENTIALS, new=AsyncMock()),
        patch.object(hass.config_entries, "async_schedule_reload") as reload,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_NAME: "test_api_name_1",
                CONF_USERNAME: "user@email.com",
                CONF_PASSWORD: "new_password",
            },
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    reload.assert_called_once_with(entry.entry_id)


async def test_reconfigure_refuses_a_populated_account_switch(
    hass: HomeAssistant, mock_smartbox, reseller
) -> None:
    """An entry with entities is not silently pointed at another account."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        data={
            CONF_API_NAME: "test_api_name_1",
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "old_password",
        },
    )
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        "device_1_0_power",
        config_entry=entry,
        suggested_object_id="device_1_0_power",
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    with patch(_VALIDATE_CREDENTIALS, new=AsyncMock()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_NAME: "test_api_name_1",
                CONF_USERNAME: "other@email.com",
                CONF_PASSWORD: "new_password",
            },
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "account_mismatch"
    assert entry.data[CONF_USERNAME] == "user@email.com"


async def test_async_step_user_show_form(hass: HomeAssistant) -> None:
    """Test that the form is served with no input."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    result = await flow.async_step_user(user_input=None)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_async_step_user_cannot_connect(hass: HomeAssistant, reseller) -> None:
    """Test handling cannot connect error."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    with patch(
        _TOKEN_GRANT,
        new=AsyncMock(side_effect=TransportUnavailableError("Cannot connect")),
    ):
        result = await flow.async_step_user(user_input=MOCK_SMARTBOX_CONFIG[DOMAIN])
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert flow.context["title_placeholders"]["error"] == "Cannot connect"


async def test_async_step_user_invalid_auth(hass: HomeAssistant, reseller) -> None:
    """Test handling invalid auth error."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    with patch(
        _TOKEN_GRANT,
        new=AsyncMock(side_effect=AuthError("Invalid auth")),
    ):
        result = await flow.async_step_user(user_input=MOCK_SMARTBOX_CONFIG[DOMAIN])
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert flow.context["title_placeholders"]["error"] == "Invalid auth"


async def test_async_step_user_unknown_error(hass: HomeAssistant, reseller) -> None:
    """Test handling unknown error."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    with patch(
        _TOKEN_GRANT,
        new=AsyncMock(side_effect=SmartboxError("Unknown error")),
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
    hass: HomeAssistant, reseller
) -> None:
    """Test handling invalid auth error during reauth confirm."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    flow.current_user_inputs = MOCK_SMARTBOX_CONFIG[DOMAIN]
    with patch(
        _TOKEN_GRANT,
        new=AsyncMock(side_effect=AuthError("Invalid auth")),
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
    hass: HomeAssistant, reseller
) -> None:
    """Test handling unknown error during reauth confirm."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    flow.current_user_inputs = MOCK_SMARTBOX_CONFIG[DOMAIN]
    with patch(
        _TOKEN_GRANT,
        new=AsyncMock(side_effect=SmartboxError("Unknown error")),
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
    hass: HomeAssistant, reseller
) -> None:
    """Test handling api unavailable error during reauth confirm."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    flow.current_user_inputs = MOCK_SMARTBOX_CONFIG[DOMAIN]
    with patch(
        _TOKEN_GRANT,
        new=AsyncMock(side_effect=TransportUnavailableError("Cannot connect")),
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


async def test_async_step_user_unsupported(hass: HomeAssistant, reseller) -> None:
    """A deterministic probe rejection surfaces the 'unsupported' error."""
    flow = SmartboxConfigFlow()
    flow.hass = hass
    with patch(
        _VALIDATE_CREDENTIALS,
        new=AsyncMock(side_effect=TransportUnsupportedError("404")),
    ):
        result = await flow.async_step_user(user_input=MOCK_SMARTBOX_CONFIG[DOMAIN])
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "unsupported"}
    assert flow.context["title_placeholders"]["error"] == "404"


async def test_validate_credentials_runs_grant_then_ws_user_probe(
    hass: HomeAssistant, reseller
) -> None:
    """The real validation is one password grant + the one-shot ws_user probe.

    The flow steps mock this helper, so its own body (the probe call in
    particular) needs a direct test.
    """
    probe = AsyncMock()
    with (
        patch(_TOKEN_GRANT, new=AsyncMock(return_value=_stub_token_bundle())),
        patch(_WS_PROBE, probe),
    ):
        await config_flow_mod.async_validate_credentials(
            hass, MOCK_SMARTBOX_CONFIG[DOMAIN]
        )
    probe.assert_awaited_once()
    # the probe gets the granted token + the host the vendor apps dial (the
    # reseller row is the brand key, not a host)
    assert probe.await_args.args[0] == _stub_token_bundle().access_token
    assert probe.await_args.args[1] == "https://users-api.helki.com"


async def test_validate_credentials_probe_rejection_propagates(
    hass: HomeAssistant, reseller
) -> None:
    """A deterministic probe rejection reaches the flow step unmapped."""
    with (
        patch(_TOKEN_GRANT, new=AsyncMock(return_value=_stub_token_bundle())),
        patch(
            _WS_PROBE,
            new=AsyncMock(side_effect=TransportUnsupportedError("404")),
        ),
        pytest.raises(TransportUnsupportedError),
    ):
        await config_flow_mod.async_validate_credentials(
            hass, MOCK_SMARTBOX_CONFIG[DOMAIN]
        )


async def test_async_step_reauth_confirm_unsupported_reshows_reauth_step(
    hass: HomeAssistant, reseller
) -> None:
    """A failed reauth validation re-shows the reauth step, not the user step.

    The user step would abort on the entry's own unique id (it creates an
    entry), so a hop there after a failure would leave the user with no way
    to finish the reauth flow.
    """
    flow = SmartboxConfigFlow()
    flow.hass = hass
    flow.current_user_inputs = MOCK_SMARTBOX_CONFIG[DOMAIN]
    with patch(
        _VALIDATE_CREDENTIALS,
        new=AsyncMock(side_effect=TransportUnsupportedError("404")),
    ):
        result = await flow.async_step_reauth_confirm(
            user_input={
                CONF_USERNAME: "user@email.com",
                CONF_PASSWORD: "new_password",
            }
        )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "unsupported"}
    assert flow.context["title_placeholders"]["error"] == "404"


async def test_reauth_recovers_after_a_failed_attempt(
    hass: HomeAssistant, reseller
) -> None:
    """A failed reauth attempt must leave the user able to finish the flow.

    The failure re-shows the reauth step; a following correct submission has
    to complete (the user step would abort on the entry's own unique id).
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        data={
            CONF_API_NAME: "test_api_name_1",
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "old_password",
        },
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    # first attempt: transient failure, the flow stays on the reauth step
    with patch(
        _VALIDATE_CREDENTIALS,
        new=AsyncMock(side_effect=TransportUnavailableError("down")),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_USERNAME: "user@email.com",
                CONF_PASSWORD: "new_password",
            },
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "cannot_connect"}

    # second attempt: correct credentials complete the flow
    with patch(_VALIDATE_CREDENTIALS, new=AsyncMock()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_USERNAME: "user@email.com",
                CONF_PASSWORD: "new_password",
            },
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new_password"


async def test_reauth_with_a_changed_account_aborts(
    hass: HomeAssistant, reseller
) -> None:
    """Reauthing as a different account must not move the entry silently."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        data={
            CONF_API_NAME: "test_api_name_1",
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "old_password",
        },
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch(_VALIDATE_CREDENTIALS, new=AsyncMock()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_USERNAME: "other@email.com",
                CONF_PASSWORD: "new_password",
            },
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_auth"
    assert entry.data["username"] == "user@email.com"
    assert entry.data["password"] == "old_password"


async def test_step_reauth_aliased_reseller_resolves_unique_id(
    hass: HomeAssistant, mock_validate_credentials
) -> None:
    """Reauth on an ALIASED reseller: unique_id uses the RESOLVED api_url.

    api-technoterm resolves to api-lhz (reseller-table alias, pinned by
    test_lib_contract) - the entry unique_id must be ``api-lhz_<user>``
    byte-matching the released integration, so the reauth does NOT abort
    with invalid_auth (unique-id mismatch).
    """
    table = {
        "api-technoterm": SmartboxReseller(
            name="Technotherm",
            api_url="api-lhz",
            basic_auth="test_credentials",
            serial_id=16,
            web_url="https://web_url/",
        ),
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="api-lhz_user@email.com",
        data={
            CONF_API_NAME: "api-technoterm",
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "old_password",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "smartbox.protocol.reseller.AvailableResellers.resellers",
        new_callable=PropertyMock,
        return_value=table,
    ):
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
    assert result["reason"] == "reauth_successful"  # NOT invalid_auth
    assert entry.unique_id == "api-lhz_user@email.com"
    assert entry.data["password"] == "new_password"


async def test_reconfigure_loaded_entry_reloads_once(
    hass: HomeAssistant, mock_smartbox
) -> None:
    """A credential change on a live entry is reloaded by its listener once."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_test_username_1",
        data=MOCK_SMARTBOX_CONFIG[DOMAIN],
        title="Test API name test_username_1",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    before = entry.runtime_data
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    with (
        patch(_VALIDATE_CREDENTIALS, new=AsyncMock()),
        patch.object(hass.config_entries, "async_schedule_reload") as schedule,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_NAME: "test_api_name_1",
                CONF_USERNAME: "test_username_1",
                CONF_PASSWORD: "new_password",
            },
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    schedule.assert_not_called()
    assert entry.runtime_data is not before


# --- stored tokens are dropped when the credentials change ---


async def test_reauth_clears_the_stored_token(
    hass: HomeAssistant, mock_smartbox, reseller, mock_validate_credentials
) -> None:
    """A refreshed password must mint its own session, not resume the old one."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        data=MOCK_SMARTBOX_CONFIG[DOMAIN],
    )
    entry.add_to_hass(hass)
    store = token_store_for(hass, entry.entry_id, entry.data)
    await store.async_save(_stub_token_bundle())

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_USERNAME: "user@email.com", CONF_PASSWORD: "new_password"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert await token_store_for(hass, entry.entry_id, entry.data).async_load() is None


async def test_reconfigure_clears_the_stored_token(
    hass: HomeAssistant, mock_smartbox, reseller
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        title="Test API name user@email.com",
        data={
            CONF_API_NAME: "test_api_name_1",
            CONF_USERNAME: "user@email.com",
            CONF_PASSWORD: "old_password",
        },
    )
    entry.add_to_hass(hass)
    store = token_store_for(hass, entry.entry_id, entry.data)
    await store.async_save(_stub_token_bundle())

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    with patch(_VALIDATE_CREDENTIALS, new=AsyncMock()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={
                CONF_API_NAME: "test_api_name_1",
                CONF_USERNAME: "user@email.com",
                CONF_PASSWORD: "new_password",
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert await token_store_for(hass, entry.entry_id, entry.data).async_load() is None


async def test_failed_reauth_keeps_the_stored_token(
    hass: HomeAssistant, mock_smartbox, reseller
) -> None:
    """A typo must not discard the session the entry already had."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test_api_name_1_user@email.com",
        data=MOCK_SMARTBOX_CONFIG[DOMAIN],
    )
    entry.add_to_hass(hass)
    store = token_store_for(hass, entry.entry_id, entry.data)
    await store.async_save(_stub_token_bundle())

    result = await entry.start_reauth_flow(hass)
    with patch(_VALIDATE_CREDENTIALS, new=AsyncMock(side_effect=AuthError("bad"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_USERNAME: "user@email.com", CONF_PASSWORD: "wrong"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    loaded = await token_store_for(hass, entry.entry_id, entry.data).async_load()
    assert loaded is not None
