from collections.abc import Generator
from copy import deepcopy
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest
from const import (
    CONF_USERNAME,
    DOMAIN,
    MOCK_SMARTBOX_CONFIG,
    MOCK_SMARTBOX_DEVICE_INFO,
    MOCK_SMARTBOX_NODE_INFO,
    MOCK_SMARTBOX_NODE_SETUP,
    MOCK_SMARTBOX_NODE_STATUS,
)
from homeassistant.core import HomeAssistant
from mocks import MockSmartbox
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_utils import simple_celsius_to_fahrenheit

pytest_plugins = "pytest_homeassistant_custom_component"


# This fixture enables loading custom integrations in all tests.
# Remove to enable selective use of this fixture
@pytest.fixture(name="auto_enable_custom_integrations", autouse=True)
def auto_enable_custom_integrations(hass: Any, enable_custom_integrations: Any) -> None:  # noqa: F811
    """Enable custom integrations defined in the test dir."""


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


def _get_node_status(units: str) -> Dict[str, Any]:
    data = deepcopy(MOCK_SMARTBOX_NODE_STATUS)
    if units == "F":
        for dev_id in data:
            for i, _ in enumerate(data[dev_id]):
                for key in ["mtemp", "stemp", "comfort_temp", "eco_offset", "ice_temp"]:
                    if key in data[dev_id][i]:
                        temp_c = float(MOCK_SMARTBOX_NODE_STATUS[dev_id][i][key])
                        temp_f: float = simple_celsius_to_fahrenheit(temp_c)
                        data[dev_id][i][key] = str(round(temp_f, 1))
                data[dev_id][i]["units"] = "F"
    return data


@pytest.fixture(params=["C", "F"])
def mock_smartbox(request):
    mock_smartbox = MockSmartbox(
        MOCK_SMARTBOX_CONFIG,
        MOCK_SMARTBOX_DEVICE_INFO,
        MOCK_SMARTBOX_NODE_INFO,
        deepcopy(MOCK_SMARTBOX_NODE_SETUP),
        _get_node_status(request.param),
    )

    with patch(
        "custom_components.smartbox.Session",
        autospec=True,
        side_effect=mock_smartbox.get_mock_session,
    ):
        with patch(
            "smartbox.update_manager.SocketSession",
            autospec=True,
            side_effect=mock_smartbox.get_mock_socket,
        ):
            yield mock_smartbox


@pytest.fixture(params=["C", "F"])
def mock_smartbox_unavailable(request):
    mock_smartbox = MockSmartbox(
        MOCK_SMARTBOX_CONFIG,
        MOCK_SMARTBOX_DEVICE_INFO,
        MOCK_SMARTBOX_NODE_INFO,
        deepcopy(MOCK_SMARTBOX_NODE_SETUP),
        _get_node_status(request.param),
        False,
    )

    with patch(
        "custom_components.smartbox.Session",
        autospec=True,
        side_effect=mock_smartbox.get_mock_session,
    ):
        with patch(
            "smartbox.update_manager.SocketSession",
            autospec=True,
            side_effect=mock_smartbox.get_mock_socket,
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
