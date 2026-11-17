import base64
import contextlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from smartbox.protocol.auth import TokenBundle
from smartbox.protocol.client import SmartboxClient
from smartbox.protocol.reseller import SmartboxReseller

from tests.fake_transport import FakeTransport

from .const import (
    CONF_USERNAME,
    DOMAIN,
    MOCK_SMARTBOX_CONFIG,
    MOCK_SMARTBOX_DEVICE_INFO,
    MOCK_SMARTBOX_DEVICE_POWER,
    MOCK_SMARTBOX_HOME_INFO,
    MOCK_SMARTBOX_NODE_AWAY,
    MOCK_SMARTBOX_NODE_INFO,
    MOCK_SMARTBOX_NODE_PROG,
    MOCK_SMARTBOX_NODE_SETUP,
    MOCK_SMARTBOX_NODE_STATUS,
)
from .mocks import MOCK_DEVICE_RTC, MockSmartbox
from .test_utils import simple_celsius_to_fahrenheit

if TYPE_CHECKING:
    from collections.abc import Generator

    from homeassistant.core import HomeAssistant

    from tests.fake_api import FakeSmartbox

pytest_plugins = "pytest_homeassistant_custom_component"


# Test initialization must ensure custom_components are enabled
# but we can't autouse a simple fixture for that since the recorder
# need to be initialized first.
# Building a hass costs ~25 ms (setup plus teardown), so tests whose
# fixture closure never reaches `hass` skip creating one at all.
@pytest.fixture(autouse=True)
def auto_enable(request: pytest.FixtureRequest):
    if "recorder_mock" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
    if "hass" not in request.fixturenames:
        return
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


@pytest.fixture(autouse=True)
def fast_availability_windows(monkeypatch):
    """Deterministic node-availability windows in tests.

    The runtime builder reads these module globals at engine
    construction, so patch the integration module's copies: every
    engine built afterwards uses the tiny windows.
    """
    monkeypatch.setattr("custom_components.smartbox.SMARTBOX_UNAVAILABLE_DELAY", 0.05)
    monkeypatch.setattr(
        "custom_components.smartbox.SMARTBOX_WRITE_CONFIRM_TIMEOUT", 0.05
    )


@pytest.fixture(autouse=True)
def fast_membership_removals(monkeypatch):
    """Confirm membership removals immediately in tests.

    The production grace keeps a ten-minute confirmation window; the suite
    shrinks it so a removal task settles inside ``async_block_till_done``.
    Tests that need the real grace patch it back locally.
    """
    monkeypatch.setattr(
        "custom_components.smartbox.membership._REMOVAL_GRACE_SECONDS", 0.0
    )
    monkeypatch.setattr(
        "custom_components.smartbox.membership._REMOVAL_RETRY_SECONDS",
        (0.0, 0.0, 0.0),
    )


@pytest.fixture(autouse=True)
def fast_membership_refreshes(monkeypatch):
    """Skip the refresh retry backoff in tests.

    The bounded retries exist to survive transient REST failures; sleeping
    5 s and 15 s between attempts only pads a test that announces a node the
    identity read has not caught up with. The retry test rebinds the real
    values and a recording sleep.
    """
    monkeypatch.setattr(
        "custom_components.smartbox.membership._REFRESH_BACKOFF_SECONDS",
        (0.0, 0.0, 0.0),
    )


def _celsius_delta_to_fahrenheit(delta_c: float) -> float:
    """Convert a temperature DELTA (not an absolute value) to °F."""
    return delta_c * 9.0 / 5.0


def _get_node_status(units: str) -> dict:
    data = deepcopy(MOCK_SMARTBOX_NODE_STATUS)
    if units == "F":
        for dev_id in data:
            for i, _ in enumerate(data[dev_id]):
                for key in [
                    "mtemp",
                    "stemp",
                    "comfort_temp",
                    "ice_temp",
                    "eco_temp",
                    "comf_temp",
                ]:
                    if key in data[dev_id][i]:
                        temp_c = float(MOCK_SMARTBOX_NODE_STATUS[dev_id][i][key])
                        temp_f: float = simple_celsius_to_fahrenheit(temp_c)
                        data[dev_id][i][key] = str(round(temp_f, 1))
                # A delta is scaled by 9/5 only; adding 32 would turn a 3 °C
                # offset into 37.4 °F and mask the delta handling in entities.
                if "eco_offset" in data[dev_id][i]:
                    delta_c = float(MOCK_SMARTBOX_NODE_STATUS[dev_id][i]["eco_offset"])
                    data[dev_id][i]["eco_offset"] = str(
                        round(_celsius_delta_to_fahrenheit(delta_c), 1)
                    )
                data[dev_id][i]["units"] = "F"
    return data


def _get_node_setup(units: str) -> dict:
    data = deepcopy(MOCK_SMARTBOX_NODE_SETUP)
    if units == "F":
        # The OFFSET follows the node's unit (the app's own rule), so convert
        # it to stay scale-coherent in Fahrenheit runs. max_stemp_limit stays
        # in CELSIUS: that field is Celsius on the wire regardless of the
        # node's unit (docs/api-contract.md §5.2/§7 item 2), and the adapter
        # converts it for the entities.
        for dev_id in data:
            for i, _ in enumerate(data[dev_id]):
                key = "away_offset"
                if key in data[dev_id][i]:
                    delta_c = float(MOCK_SMARTBOX_NODE_SETUP[dev_id][i][key])
                    data[dev_id][i][key] = str(
                        round(_celsius_delta_to_fahrenheit(delta_c), 1)
                    )
                # ``extra_options.boost_temp`` is a temperature in the node's
                # own scale, not a delta.
                extra = data[dev_id][i].get("extra_options")
                if isinstance(extra, dict) and "boost_temp" in extra:
                    extra["boost_temp"] = str(
                        round(
                            simple_celsius_to_fahrenheit(float(extra["boost_temp"])),
                            1,
                        )
                    )
    return data


def _make_jwt(user_id: str = "user_under_test") -> str:
    """Build a minimal JWT carrying a userId claim (fake-token use)."""
    payload = json.dumps({"userId": user_id}).encode()
    segment = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    return f"hdr.{segment}.sig"


def _stub_token_bundle():
    return TokenBundle(
        access_token=_make_jwt(),
        refresh_token="refresh_token",
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )


def _stub_transport(token_source, api_host, **kwargs) -> FakeTransport:
    """Stands in for WsUserTransport: FakeTransport, no network."""
    return FakeTransport()


class _WriteRouter:
    """Routes the client's POSTs to the FakeSmartbox the test seeds.

    The fake models the server; the client stays the transport to it, so the
    engine's write host is the real client and production carries no seam for
    swapping it. ``stack._seed_stack`` fills the host in once the test has
    prepared the mock world, so mutations made in the test body land in the
    fake.
    """

    def __init__(self) -> None:
        self.host: FakeSmartbox | None = None

    async def post(self, path, body) -> None:
        assert self.host is not None, "seed the fake write host before writing"
        await self.host.post(path, body)


def _identity_client_seams(mock_smartbox: MockSmartbox, router: _WriteRouter):
    """Patch SmartboxClient's reads and POSTs onto the mock world / fake.

    The real setup discovers device/node identity with plain REST reads;
    the mock world serves them (no network). POSTs are routed to the test's
    FakeSmartbox (the server-side fake) so writes mutate it and are asserted
    through ``fake.requests``.
    """
    return (
        patch.object(
            SmartboxClient,
            "post",
            new=AsyncMock(side_effect=router.post),
        ),
        patch.object(
            SmartboxClient,
            "get_grouped_devices",
            new=AsyncMock(side_effect=mock_smartbox.get_grouped_devices),
        ),
        patch.object(
            SmartboxClient,
            "get_power_limit",
            new=AsyncMock(side_effect=mock_smartbox.get_power_limit),
        ),
        patch.object(
            SmartboxClient,
            "get_dev_data",
            new=AsyncMock(side_effect=mock_smartbox.get_dev_data),
        ),
        patch.object(
            SmartboxClient,
            "get_nodes",
            new=AsyncMock(side_effect=mock_smartbox.get_nodes),
        ),
        patch.object(
            SmartboxClient,
            "get_node_status",
            new=AsyncMock(side_effect=mock_smartbox.get_status),
        ),
        patch.object(
            SmartboxClient,
            "get_node_setup",
            new=AsyncMock(side_effect=mock_smartbox.get_node_setup),
        ),
        patch.object(
            SmartboxClient,
            "get_node_version",
            new=AsyncMock(side_effect=mock_smartbox.get_node_version),
        ),
        # The clock-drift sensor's slow RTC poll.
        patch.object(
            SmartboxClient,
            "get_rtc_time",
            new=AsyncMock(return_value=dict(MOCK_DEVICE_RTC)),
        ),
        # The PMO power sensor's poll.
        patch.object(
            SmartboxClient,
            "get_node_power",
            new=AsyncMock(side_effect=mock_smartbox.get_node_power),
        ),
        # The consumption sensor's statistics import fetches samples at
        # entity add: serve none (individual tests patch their own).
        patch.object(
            SmartboxClient,
            "get_node_samples",
            new=AsyncMock(return_value=[]),
        ),
    )


def _new_stack_seams():
    """Seam patches that let the REAL new-stack builder run offline.

    The token grant returns a fake bundle, the capability probe
    succeeds, and the transport is a FakeTransport (run() parks like
    the real one; close() releases it).
    """
    return (
        patch(
            "smartbox.protocol.auth.TokenManager.get_token",
            new=AsyncMock(return_value=_stub_token_bundle()),
        ),
        patch(
            "custom_components.smartbox.check_ws_user_support",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.smartbox.WsUserTransport",
            _stub_transport,
        ),
    )


def _make_mock_smartbox(units: str, start_available=True) -> MockSmartbox:
    return MockSmartbox(
        mock_config=MOCK_SMARTBOX_CONFIG,
        mock_home_info=MOCK_SMARTBOX_HOME_INFO,
        mock_device_info=MOCK_SMARTBOX_DEVICE_INFO,
        mock_node_info=MOCK_SMARTBOX_NODE_INFO,
        mock_node_setup=_get_node_setup(units),
        mock_node_prog=deepcopy(MOCK_SMARTBOX_NODE_PROG),
        mock_node_away=MOCK_SMARTBOX_NODE_AWAY,
        mock_device_power=MOCK_SMARTBOX_DEVICE_POWER,
        mock_node_status=_get_node_status(units),
        start_available=start_available,
    )


@pytest.fixture(params=["C", "F"])
def units(request: pytest.FixtureRequest) -> str:
    """Node temperature scale the mock world uses.

    Both scales by default. A module whose behavior does not depend on the
    display unit overrides this fixture with a single scale, which drops its
    C/F duplicates from the suite.
    """
    return request.param


@pytest.fixture
def mock_smartbox(units, reseller):
    mock_smartbox = _make_mock_smartbox(units)
    router = _WriteRouter()
    mock_smartbox.write_router = router
    with contextlib.ExitStack() as stack:
        for seam in (
            *_new_stack_seams(),
            *_identity_client_seams(mock_smartbox, router),
        ):
            stack.enter_context(seam)
        yield mock_smartbox


@pytest.fixture
def mock_smartbox_unavailable(units, reseller):
    mock_smartbox = _make_mock_smartbox(units, start_available=False)
    router = _WriteRouter()
    mock_smartbox.write_router = router
    with contextlib.ExitStack() as stack:
        for seam in (
            *_new_stack_seams(),
            *_identity_client_seams(mock_smartbox, router),
        ):
            stack.enter_context(seam)
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
def mock_validate_credentials():
    """Patch the config flow's protocol-layer validation (success)."""
    with patch(
        "custom_components.smartbox.config_flow.async_validate_credentials",
        new=AsyncMock(),
    ) as mock:
        yield mock


@pytest.fixture
def reseller(mocker):
    return mocker.patch(
        "smartbox.protocol.reseller.AvailableResellers.resellers",
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
