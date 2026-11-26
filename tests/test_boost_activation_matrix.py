"""Activation request-shape matrix.

The surface tests say which controls exist; this matrix says what each
activation sends. It pins the intended plan for every family and boost config,
including the config-1/2 legacy paths and the storage `acm` case.
"""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.exceptions import ServiceValidationError
import pytest
from smartbox.core.state import NodeState

from custom_components.smartbox.boost_activation import (
    ActivationPlan,
    plan_boost,
    plan_runback,
)
from custom_components.smartbox.boost_prefs import NodeBoostPrefs
from custom_components.smartbox.identity import SmartboxDeviceInfo, SmartboxNodeInfo

RESELLER = SimpleNamespace(web_url="https://example/", name="Test")
DEVICE = SmartboxDeviceInfo(
    dev_id="dev",
    name="Box",
    model_id=None,
    sw_version=None,
    serial_number=None,
    home_id="home",
    reseller=RESELLER,
    box_power_limit_available=True,
)


class _Prefs:
    def __init__(self, **values: float | None) -> None:
        self._node = NodeBoostPrefs(**values)

    def get(self, node_id: str) -> NodeBoostPrefs:  # noqa: ARG002
        return self._node


def _world(
    pid: str,
    setup: dict,
    status: dict | None = None,
    node_type: str = "htr",
) -> tuple[SmartboxNodeInfo, NodeState, _Prefs]:
    node = SmartboxNodeInfo(
        device=DEVICE,
        node_info={"type": node_type, "addr": 0, "name": "Node"},
        status=status or {"units": "C", "stemp": "20.0"},
        setup=setup,
        version={"pid": pid, "fw_version": "1.9"},
    )
    state = NodeState(
        status=dict(node.status), setup=dict(node.setup), version=dict(node.version)
    )
    return node, state, _Prefs()


LEGACY = True
ENDPOINT = False
FULL = {"factory_options": {"boost_config": 3}}
TEMP = {"boost_modes_available": ["temp_and_time"]}
FIXED = {"boost_modes_available": ["max_temp_fixed_time"]}
BOTH = {"boost_modes_available": ["temp_and_time", "max_temp_fixed_time"]}


@pytest.mark.parametrize(
    ("pid", "setup", "status", "prefs", "expected"),
    [
        pytest.param(
            "01320000",
            {"factory_options": {"boost_config": 2}},
            {"units": "C", "stemp": "20.0"},
            {},
            ActivationPlan(None, 21.0, 60, legacy=ENDPOINT),
            id="helki-config2-device-defaults",
        ),
        pytest.param(
            "011A0000",
            FULL,
            {"units": "C", "stemp": "20.0"},
            {},
            ActivationPlan(None, 21.0, 60, legacy=LEGACY),
            id="triac-full-legacy-override",
        ),
        pytest.param(
            "014C0000",
            {"factory_options": {"boost_config": 1}},
            {"units": "C", "stemp": "20.0"},
            {},
            ActivationPlan(None, None, None, legacy=ENDPOINT),
            id="atc-config1-fixed",
        ),
        pytest.param(
            "014C0000",
            {"factory_options": {"boost_config": 2}},
            {"units": "C", "stemp": "20.0"},
            {},
            ActivationPlan(None, 21.0, 60, legacy=ENDPOINT),
            id="atc-config2-temp-time",
        ),
        pytest.param(
            "01170000",
            {"factory_options": {"boost_config": 2}},
            {"units": "C", "stemp": "20.0"},
            {},
            ActivationPlan(None, None, None, legacy=LEGACY),
            id="haverland-fixed-time-at-config2",
        ),
        pytest.param(
            "011D0000",
            FULL,
            {"units": "C", "stemp": "20.0"},
            {},
            ActivationPlan("max_temp_fixed_time", None, None, legacy=ENDPOINT),
            id="haverland-wifi-full-fixed-endpoint",
        ),
        pytest.param(
            "01340000",
            FULL,
            {"units": "C", "stemp": "20.0", **TEMP},
            {},
            ActivationPlan("temp_and_time", 21.0, 60, legacy=ENDPOINT),
            id="wifi-full-temp-and-time-device-defaults",
        ),
        pytest.param(
            "01340000",
            FULL,
            {"units": "C", "stemp": "20.0", **BOTH},
            {"boost_temp_c": 23.0, "boost_time": 120},
            ActivationPlan("temp_and_time", 23.0, 120, legacy=ENDPOINT),
            id="wifi-full-temp-and-time-ha-preferences",
        ),
        pytest.param(
            "01340000",
            FULL,
            {"units": "C", "stemp": "20.0", **FIXED},
            {},
            ActivationPlan("max_temp_fixed_time", None, None, legacy=ENDPOINT),
            id="wifi-full-fixed-only",
        ),
        pytest.param(
            "081C0000",
            {
                "factory_options": {"boost_config": 1},
                "extra_options": {"boost_temp": "20.0", "boost_time": 60},
            },
            {"units": "C", "stemp": "20.0"},
            {},
            ActivationPlan(None, 20.0, 60, legacy=ENDPOINT),
            id="storage-config1-keeps-parameters",
        ),
        pytest.param(
            "081C0000",
            {
                "factory_options": {"boost_config": 2},
                "extra_options": {"boost_temp": "20.0", "boost_time": 60},
            },
            {"units": "C", "stemp": "20.0"},
            {},
            ActivationPlan(None, 20.0, 60, legacy=ENDPOINT),
            id="helki-storage-boost",
        ),
        pytest.param(
            "081C0000",
            {
                "factory_options": {"boost_config": 3},
                "extra_options": {"boost_temp": "20.0", "boost_time": 60},
            },
            {"units": "C", "stemp": "20.0"},
            {},
            ActivationPlan(None, 20.0, 60, legacy=False),
            id="storage-config3-stays-on-boost",
        ),
        pytest.param(
            "082E0000",
            {
                "factory_options": {"boost_config": 3},
                "extra_options": {"boost_temp": "20.0", "boost_time": 60},
            },
            {"units": "C", "stemp": "20.0"},
            {},
            ActivationPlan(None, 20.0, 60, legacy=False),
            id="storage-config3-2e-stays-on-boost",
        ),
    ],
)
def test_boost_request_shape(pid, setup, status, prefs, expected) -> None:
    """The planner produces the exact legacy or /boost_mode request."""
    node_type = "acm" if pid[2:4] in {"1C", "2E", "14", "20", "25", "2A"} else "htr"
    node, state, _store = _world(pid, setup, status, node_type)
    store = _Prefs(**prefs)
    assert plan_boost(node, state, store) == expected


@pytest.mark.parametrize("status", [{}, {"boost_modes_available": []}])
def test_full_boost_without_a_mode_is_refused(status) -> None:
    """No advertised basic style is an error, not a guessed temp-and-time write."""
    node, state, store = _world(
        "01340000", FULL, {"units": "C", "stemp": "20.0", **status}
    )
    with pytest.raises(ServiceValidationError) as err:
        plan_boost(node, state, store)
    assert err.value.translation_key == "boost_mode_unavailable"


@pytest.mark.parametrize(
    ("prefs", "key"),
    [
        ({"boost_time": 90}, "boost_duration_step"),
        ({"boost_temp_c": 50.0}, "boost_temperature_out_of_range"),
    ],
)
def test_boost_parameters_are_validated(prefs, key) -> None:
    """Off-grid duration and out-of-range temperature are refused."""
    node, state, store = _world(
        "01340000", FULL, {"units": "C", "stemp": "20.0", **TEMP}
    )
    store = _Prefs(**prefs)
    with pytest.raises(ServiceValidationError) as err:
        plan_boost(node, state, store)
    assert err.value.translation_key == key


def test_runback_request_shape() -> None:
    """Runback carries its own temperature and duration."""
    node, state, _store = _world(
        "01340000",
        FULL,
        {
            "units": "C",
            "stemp": "20.0",
            "boost_modes_available": ["temp_and_time", "runback"],
        },
    )
    store = _Prefs(runback_temp_c=18.0, runback_time=60)
    assert plan_runback(node, state, store) == ActivationPlan(
        "runback", 18.0, 60, legacy=ENDPOINT
    )


def test_runback_requires_the_advertised_mode() -> None:
    """Runback is refused when the endpoint does not list it."""
    node, state, store = _world("01340000", FULL, {"units": "C", **TEMP})
    with pytest.raises(ServiceValidationError) as err:
        plan_runback(node, state, store)
    assert err.value.translation_key == "runback_requires_full_boost"


def test_boost_malformed_device_values_are_refused() -> None:
    """A present-but-unreadable device value is not guessed."""
    setup = {
        "factory_options": {"boost_config": 2},
        "extra_options": {"boost_temp": "bad", "boost_time": 60},
    }
    node, state, _store = _world("01320000", setup, {"units": "C"})
    with pytest.raises(ServiceValidationError) as err:
        plan_boost(node, state, _Prefs())
    assert err.value.translation_key == "boost_parameters_required"


@pytest.mark.parametrize(
    ("prefs", "key"),
    [
        ({"boost_time": 2000}, "boost_duration_out_of_range"),
        ({"runback_time": 2000}, "runback_duration_out_of_range"),
    ],
)
def test_duration_ceiling_is_validated(prefs, key) -> None:
    """Both boost and runback duration caps are enforced."""
    node, state, _store = _world(
        "01340000",
        FULL,
        {
            "units": "C",
            "stemp": "20.0",
            "boost_modes_available": ["temp_and_time", "runback"],
        },
    )
    store = _Prefs(**prefs)
    with pytest.raises(ServiceValidationError) as err:
        if "runback_time" in prefs:
            plan_runback(node, state, store)
        else:
            plan_boost(node, state, store)
    assert err.value.translation_key == key


def test_runback_falls_back_to_controller_bounds_and_default_time() -> None:
    """No status limits: the controller range and 30-minute default apply."""
    node, state, _store = _world(
        "01340000",
        FULL,
        {"units": "C", "stemp": "20.0", "boost_modes_available": ["runback"]},
    )
    assert plan_runback(node, state, _Prefs()) == ActivationPlan(
        "runback", 20.0, 30, legacy=ENDPOINT
    )


def test_runback_max_time_comes_from_extra_options() -> None:
    """The app's max_runback_time caps the runback duration."""
    node, state, _store = _world(
        "01340000",
        {**FULL, "extra_options": {"max_runback_time": 90}},
        {"units": "C", "stemp": "20.0", "boost_modes_available": ["runback"]},
    )
    store = _Prefs(runback_time=120)
    with pytest.raises(ServiceValidationError) as err:
        plan_runback(node, state, store)
    assert err.value.translation_key == "runback_duration_out_of_range"


def test_fahrenheit_preferences_are_converted_to_node_units() -> None:
    """The stored Celsius preference is presented in the node's scale."""
    node, state, _store = _world(
        "01340000",
        FULL,
        {"units": "F", "stemp": "68.0", "boost_modes_available": ["temp_and_time"]},
    )
    plan = plan_boost(node, state, _Prefs(boost_temp_c=23.0, boost_time=60))
    assert plan.temperature == 73.0


def test_runback_low_max_time_is_clamped() -> None:
    """A max_runback_time below the floor cannot invert the valid range."""
    node, state, _store = _world(
        "01340000",
        {**FULL, "extra_options": {"max_runback_time": 20}},
        {"units": "C", "stemp": "20.0", "boost_modes_available": ["runback"]},
    )
    assert plan_runback(node, state, _Prefs()) == ActivationPlan(
        "runback", 20.0, 30, legacy=ENDPOINT
    )
