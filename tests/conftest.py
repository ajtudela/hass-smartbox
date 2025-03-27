from collections.abc import Generator
from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from smartbox.reseller import SmartboxReseller

from .const import (
    CONF_USERNAME,
    DOMAIN,
    MOCK_SMARTBOX_CONFIG,
    MOCK_SMARTBOX_DEVICE_INFO,
    MOCK_SMARTBOX_DEVICE_POWER,
    MOCK_SMARTBOX_HOME_INFO,
    MOCK_SMARTBOX_NODE_AWAY,
    MOCK_SMARTBOX_NODE_INFO,
    MOCK_SMARTBOX_NODE_SETUP,
    MOCK_SMARTBOX_NODE_STATUS,
)
from .mocks import MockSmartbox
from .test_utils import simple_celsius_to_fahrenheit

pytest_plugins = "pytest_homeassistant_custom_component"


# Test initialization must ensure custom_components are enabled
# but we can't autouse a simple fixture for that since the recorder
# need to be initialized first
@pytest.fixture(autouse=True)
def auto_enable(request: pytest.FixtureRequest):
    if "recorder_mock" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
    hass = request.getfixturevalue("hass")
    hass.data.pop("custom_components")


# This fixture is used to prevent HomeAssistant from attempting to create and
# dismiss persistent notifications. These calls would fail without this fixture
# since the persistent_notification integration is never loaded during a test.
@pytest.fixture(name="skip_notifications", autouse=True)
def skip_notifications_fixture():
    """Skip notification calls."""
    with (
        patch("homeassistant.components.persistent_notification.async_create"),
        patch("homeassistant.components.persistent_notification.async_dismiss"),
    ):
        yield


def _get_node_status(units: str) -> dict[str, Any]:
    data = deepcopy(MOCK_SMARTBOX_NODE_STATUS)
    if units == "F":
        for dev_id in data:
            for i, _ in enumerate(data[dev_id]):
                for key in [
                    "mtemp",
                    "stemp",
                    "comfort_temp",
                    "eco_offset",
                    "ice_temp",
                ]:
                    if key in data[dev_id][i]:
                        temp_c = float(MOCK_SMARTBOX_NODE_STATUS[dev_id][i][key])
                        temp_f: float = simple_celsius_to_fahrenheit(temp_c)
                        data[dev_id][i][key] = str(round(temp_f, 1))
                data[dev_id][i]["units"] = "F"
    return data


@pytest.fixture(params=["C", "F"])
def mock_smartbox(request):
    mock_smartbox = MockSmartbox(
        mock_config=MOCK_SMARTBOX_CONFIG,
        mock_home_info=MOCK_SMARTBOX_HOME_INFO,
        mock_device_info=MOCK_SMARTBOX_DEVICE_INFO,
        mock_node_info=MOCK_SMARTBOX_NODE_INFO,
        mock_node_setup=deepcopy(MOCK_SMARTBOX_NODE_SETUP),
        mock_node_away=MOCK_SMARTBOX_NODE_AWAY,
        mock_device_power=MOCK_SMARTBOX_DEVICE_POWER,
        mock_node_status=_get_node_status(request.param),
    )

    with (
        patch(
            "custom_components.smartbox.AsyncSmartboxSession",
            autospec=True,
            side_effect=mock_smartbox.get_mock_session,
        ),
        patch(
            "smartbox.update_manager.SocketSession",
            autospec=True,
            side_effect=mock_smartbox.get_mock_socket,
        ),
    ):
        yield mock_smartbox


@pytest.fixture(params=["C", "F"])
def mock_smartbox_unavailable(request):
    mock_smartbox = MockSmartbox(
        mock_config=MOCK_SMARTBOX_CONFIG,
        mock_home_info=MOCK_SMARTBOX_HOME_INFO,
        mock_device_info=MOCK_SMARTBOX_DEVICE_INFO,
        mock_node_info=MOCK_SMARTBOX_NODE_INFO,
        mock_node_setup=deepcopy(MOCK_SMARTBOX_NODE_SETUP),
        mock_node_away=MOCK_SMARTBOX_NODE_AWAY,
        mock_device_power=MOCK_SMARTBOX_DEVICE_POWER,
        mock_node_status=_get_node_status(request.param),
        start_available=False,
    )

    with (
        patch(
            "custom_components.smartbox.AsyncSmartboxSession",
            autospec=True,
            side_effect=mock_smartbox.get_mock_session,
        ),
        patch(
            "smartbox.update_manager.SocketSession",
            autospec=True,
            side_effect=mock_smartbox.get_mock_socket,
        ),
    ):
        yield mock_smartbox


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Override async_setup_entry."""
    with patch(
        "custom_components.smartbox.async_setup_entry", return_value=True
    ) as mock_setup_entry:
        yield mock_setup_entry


@pytest.fixture(name="config_entry")
def mock_config_entry_fixture(hass: HomeAssistant) -> MockConfigEntry:
    """Mock a config entry."""
    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data=MOCK_SMARTBOX_CONFIG[DOMAIN],
        title=MOCK_SMARTBOX_CONFIG[DOMAIN][CONF_USERNAME],
    )
    mock_entry.add_to_hass(hass)

    return mock_entry


@pytest.fixture
def mock_create_smartbox_session():
    with patch(
        "custom_components.smartbox.config_flow.create_smartbox_session_from_entry",
        return_value=AsyncMock(),
    ) as mock:
        yield mock


@pytest.fixture
def mock_get_devices(mock_devices):
    with patch("custom_components.smartbox.get_devices", return_value=mock_devices):
        yield


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.check_refresh_auth = AsyncMock()
    return session


@pytest.fixture
def mock_devices():
    device = AsyncMock()
    device.dev_id = "device_1"
    device.get_nodes = AsyncMock(return_value=[])
    return [device]


@pytest.fixture
def reseller(mocker):
    return mocker.patch(
        "smartbox.reseller.AvailableResellers.resellers",
        new_callable=mocker.PropertyMock,
        return_value={
            "test_api_name_1": SmartboxReseller(
                name="Test API name",
                api_url="test_api_name_1",
                basic_auth="test_credentials",
                serial_id=10,
                web_url="https://web_url/",
            )
        },
    )
