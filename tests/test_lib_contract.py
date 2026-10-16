"""Contract tests pinning the smartbox library surface this integration relies on.

These guard lock-step evolution with the sibling repository (../smartbox):
if a library change breaks something the integration depends on, they fail
here before the integration is bumped. They run against whatever `smartbox`
is installed in the environment — the uv.lock pin (PyPI) or the editable
sibling checkout (see scripts/dev-lib-link.sh).
"""

import inspect

import pytest
import smartbox
from smartbox import (
    AsyncSmartboxSession,
    AvailableResellers,
    SmartboxNodeType,
    UpdateManager,
)
from smartbox.error import APIUnavailableError, InvalidAuthError, SmartboxError

# Session methods the integration calls (models.py, __init__.py, config_flow.py).
SESSION_METHODS = (
    "get_devices",
    "get_homes",
    "get_nodes",
    "get_device_connected",
    "get_device_away_status",
    "set_device_away_status",
    "get_device_power_limit",
    "set_device_power_limit",
    "get_node_samples",
    "get_node_status",
    "set_node_status",
    "get_node_setup",
    "set_node_setup",
    "get_node_version",
    # Schedule surface (smartbox 2.6.0).
    "get_node_prog",
    "set_node_prog",
)

# UpdateManager subscriptions wired in SmartboxDevice (models.py).
SUBSCRIPTION_METHODS = (
    "subscribe_to_device_connected",
    "subscribe_to_device_away_status",
    "subscribe_to_node_setup",
    "subscribe_to_device_power_limit",
    "subscribe_to_node_status",
    # Schedule surface (smartbox 2.6.0).
    "subscribe_to_node_prog",
)


def test_package_exports() -> None:
    """Names imported by the integration resolve from the top-level package."""
    assert AsyncSmartboxSession is not None
    assert UpdateManager is not None
    assert SmartboxNodeType is not None
    assert AvailableResellers is not None


def test_smartbox_node_type_members() -> None:
    """Node types the integration dispatches on keep their lowercase values."""
    assert SmartboxNodeType.HTR.value == "htr"
    assert SmartboxNodeType.ACM.value == "acm"
    assert SmartboxNodeType.HTR_MOD.value == "htr_mod"
    assert SmartboxNodeType.PMO.value == "pmo"


def test_error_surface() -> None:
    """Auth/unavailable errors stay importable, catchable exceptions.

    Documented library hierarchy (not pinned negatively on purpose):
    SmartboxError(Exception), InvalidAuthError(Exception),
    APIUnavailableError(aiohttp.ClientConnectionError).
    """
    assert issubclass(SmartboxError, Exception)
    assert issubclass(InvalidAuthError, Exception)
    assert issubclass(APIUnavailableError, Exception)


def test_validation_error_contract() -> None:
    """Model-mode (raw_response=False) payload drift raises a SmartboxError.

    Pinned for smartbox >= 2.6.0 (skipped against older libraries, which
    leaked pydantic's ValidationError). Raw mode (the integration's
    default) never validates, so it never raises this.
    """
    validation_error = getattr(smartbox, "SmartboxValidationError", None)
    if validation_error is None:
        pytest.skip("smartbox < 2.6.0: no SmartboxValidationError")
    assert issubclass(validation_error, SmartboxError)


def test_async_session_surface() -> None:
    """Every session call the integration makes exists, is async, same ctor."""
    init_params = inspect.signature(AsyncSmartboxSession.__init__).parameters
    assert {"username", "password", "websession"} <= set(init_params)

    for name in SESSION_METHODS:
        method = getattr(AsyncSmartboxSession, name, None)
        assert callable(method), f"AsyncSmartboxSession.{name} missing"
        assert inspect.iscoroutinefunction(method), f"{name} must be async"

    # Dict responses (raw_response=True) are a relied-on semantic.
    assert init_params["raw_response"].default is True

    # PMO power limit is fetched per node via the optional `node` argument.
    power_params = inspect.signature(
        AsyncSmartboxSession.get_device_power_limit
    ).parameters
    assert power_params["node"].default is None


def test_update_manager_surface() -> None:
    """UpdateManager keeps the constructor/subscription model models.py uses."""
    init_params = list(inspect.signature(UpdateManager.__init__).parameters)
    assert init_params[:3] == ["self", "session", "device_id"]

    for name in (*SUBSCRIPTION_METHODS, "run", "cancel"):
        assert callable(getattr(UpdateManager, name, None)), (
            f"UpdateManager.{name} missing"
        )


def test_subscription_callbacks() -> None:
    """Subscription methods keep an explicit callback parameter."""
    for name in SUBSCRIPTION_METHODS:
        params = inspect.signature(getattr(UpdateManager, name)).parameters
        assert "callback" in params, f"{name} lost its callback parameter"


def test_availability_surface() -> None:
    """Node-availability surface (smartbox >= 2.6.1) stays usable.

    Skip-guarded so the suite also stays green against older library
    installs (e.g. after scripts/dev-lib-link.sh --unlink with a
    pre-2.6.1 uv.lock pin; see memory-bank/program.md). The release pin
    itself is smartbox 2.6.1, so this normally runs unskipped.
    """
    if not hasattr(UpdateManager, "expect_write_confirmation"):
        pytest.skip("smartbox < 2.6.1: no availability surface")
    params = inspect.signature(UpdateManager.__init__).parameters
    assert params["write_confirm_timeout"].default > 0
    assert params["unavailable_delay"].default > 0
    assert callable(UpdateManager.get_node_availability)
    # subscribe_to_node_availability keeps an explicit callback parameter,
    # like every other subscription wired in models.py.
    sub_params = inspect.signature(
        UpdateManager.subscribe_to_node_availability,
    ).parameters
    assert "callback" in sub_params
    expect_params = inspect.signature(
        UpdateManager.expect_write_confirmation,
    ).parameters
    assert {"node", "written", "kind"} <= set(expect_params)
    assert expect_params["kind"].default == "status"
