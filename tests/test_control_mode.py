"""Control-mode select tests for the decoded vendor-app catalogue."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from smartbox.core.engine import SmartboxEngine
from smartbox.core.state import DeviceState, NodeState, WorldState, node_key

from custom_components.smartbox import control_mode
from custom_components.smartbox.control_mode import (
    HTR_TRIAC_UI,
    MODE_LABELS_C,
    STORAGE_WIRE,
    _choices,
    control_mode_available,
    control_mode_choices,
    control_mode_label,
)
from custom_components.smartbox.product import product_code
from custom_components.smartbox.select import ControlMode

DEV = "device_1"
ADDR = 3
PID = "0402"  # LHZ/EHC (04) + HELKI_RELAY_HEATER (02)
SETUP = {"control_mode": 5, "priority": "low"}


class _NoopFanout:
    """Fan-out double for property tests (no engine subscription)."""

    def register_node(self, *_args):
        return lambda: None


class _Host:
    """Setup-write host double."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    async def post(self, path: str, body: dict) -> None:
        self.posts.append((path, dict(body)))

    async def get_node_setup(self, dev_id: str, node) -> dict:  # noqa: ARG002
        return dict(SETUP)


def _entity(host=None, setup=None) -> ControlMode:
    setup = dict(SETUP if setup is None else setup)
    engine = SmartboxEngine(host or _Host())
    key = node_key("htr", ADDR)
    device = DeviceState(
        dev_id=DEV,
        away={},
        node_states={key: NodeState(status={}, setup=dict(setup))},
    )
    object.__setattr__(engine, "_world", WorldState(devices={DEV: device}))
    node = MagicMock()
    node.node_type = "htr"
    node.name = "Heater"
    node.node_id = f"{DEV}_{ADDR}"
    node.addr = ADDR
    node.dev_id = DEV
    node.pid = PID
    node.setup = dict(setup)
    node.status = {}
    node.reseller = SimpleNamespace(web_url="https://web_url/", name="Test API name")
    node.home_id = "home_1"
    node.hw_version = "1.1"
    node.supported_wire_type = True
    node.heater_node = True
    node.boost_available = False
    return ControlMode(node, MagicMock(), SimpleNamespace(engine=engine), _NoopFanout())


def test_confirmed_lhz_relay_htr_map_matches_the_live_cycle() -> None:
    choices = control_mode_choices(PID, "htr", SETUP, "C")
    assert choices is not None
    assert choices == (
        ("PID (15 min. period)", 5),
        ("PID (30 min. period)", 6),
        ("Hysteresis (0.2 °C)", 0),
        ("Hysteresis (0.3 °C)", 1),
        ("Hysteresis (0.5 °C)", 2),
        ("Hysteresis (0.7 °C)", 3),
    )
    assert control_mode_label(choices, 5) == "PID (15 min. period)"


def test_triac_htr_offers_plain_pid_at_wire_4() -> None:
    choices = control_mode_choices("0401", "htr", {"control_mode": 4}, "C")
    assert choices == (
        ("PID", 4),
        ("Hysteresis (0.2 °C)", 0),
        ("Hysteresis (0.3 °C)", 1),
        ("Hysteresis (0.5 °C)", 2),
        ("Hysteresis (0.7 °C)", 3),
    )


def test_haverland_htr_mod_is_triac_and_maps_wire_4_to_pid() -> None:
    choices = control_mode_choices("040A", "htr_mod", {"control_mode": 4}, "C")
    assert choices is not None
    assert control_mode_label(choices, 4) == "PID"


def test_default_relay_htr_map_is_used_for_unlisted_lhz_code() -> None:
    choices = control_mode_choices("040D", "htr", {"control_mode": 5}, "C")
    assert choices is not None
    assert control_mode_label(choices, 5) == "PID (15 min. period)"
    assert control_mode_label(choices, 6) == "PID (30 min. period)"


def test_storage_family_starts_with_off_at_wire_zero() -> None:
    choices = control_mode_choices("041C", "acm", {"control_mode": 5}, "C")
    assert choices == (
        ("PID (15 min. period)", 5),
        ("PID (30 min. period)", 6),
        ("Hysteresis (0.2 °C)", 1),
        ("Hysteresis (0.3 °C)", 2),
        ("Hysteresis (0.5 °C)", 3),
        ("Hysteresis (0.7 °C)", 4),
    )


def test_relay_sensor_mode_two_offers_only_hysteresis() -> None:
    setup = {"control_mode": 1, "extra_options": {"sensor_mode": 2}}
    choices = control_mode_choices(PID, "htr", setup, "C")
    assert choices == (
        ("Hysteresis (0.2 °C)", 0),
        ("Hysteresis (0.3 °C)", 1),
        ("Hysteresis (0.5 °C)", 2),
        ("Hysteresis (0.7 °C)", 3),
    )
    # The hysteresis-only product map stays available even when the stored
    # PID index is outside it; the entity renders that value as unknown.
    stored_pid = control_mode_choices(
        PID, "htr", {"control_mode": 5, "extra_options": {"sensor_mode": 2}}, "C"
    )
    assert stored_pid == choices
    assert control_mode_label(stored_pid, 5) is None


def test_fahrenheit_labels_follow_the_vendor_fahrenheit_map() -> None:
    choices = control_mode_choices(PID, "htr", {**SETUP, "units": "F"}, "F")
    assert choices is not None
    assert control_mode_label(choices, 0) == "Hysteresis (0.4 °F)"
    assert control_mode_label(choices, 5) == "PID (15 min. period)"


def test_thermostat_family_is_withheld() -> None:
    assert control_mode_choices("0404", "thm", {"control_mode": 2}, "C") is None
    assert control_mode_choices("0409", "thm", {"control_mode": 2}, "C") is None
    # An unlisted THM code is withheld by integration choice.
    assert control_mode_choices("0499", "thm", {"control_mode": 2}, "C") is None


def test_explicit_htr_family_on_a_thm_node_is_offered() -> None:
    """Code 28 is an htr-family floor thermostat in the app."""
    choices = control_mode_choices("0428", "thm", {"control_mode": 0}, "C")
    assert choices is not None
    assert control_mode_label(choices, 0) == "Hysteresis (0.2 °C)"
    assert control_mode_available("0428", "thm", {"control_mode": 0}, "C")


def test_product_code_and_label_none_paths() -> None:
    assert product_code("12") is None
    choices = control_mode_choices(PID, "htr", SETUP, "C")
    assert choices is not None
    assert control_mode_label(choices, None) is None
    assert control_mode_label(choices, 99) is None


def test_ui_mode_missing_from_wire_returns_none() -> None:
    # A triac UI offers PID, which the OFF-first storage wire does not contain.
    assert _choices(HTR_TRIAC_UI, STORAGE_WIRE, MODE_LABELS_C) is None


def test_choices_none_path_in_control_mode_choices(monkeypatch) -> None:
    monkeypatch.setattr(
        control_mode, "_ui_for_actuation", lambda _actuation, _sensor: ("OFF",)
    )
    assert control_mode_choices(PID, "htr", {"control_mode": 0}, "C") is None


def test_unoffered_stored_index_keeps_the_catalogue() -> None:
    # Relay htr wire 4 is plain PID, which the relay UI does not offer.
    choices = control_mode_choices(PID, "htr", {"control_mode": 4}, "C")
    assert choices is not None
    assert control_mode_label(choices, 4) is None
    assert control_mode_available(PID, "htr", {"control_mode": 4}, "C")

    # Storage wire 0 is OFF, which the inherited UI does not offer.
    choices = control_mode_choices("041C", "acm", {"control_mode": 0}, "C")
    assert choices is not None
    assert control_mode_label(choices, 0) is None
    assert control_mode_available("041C", "acm", {"control_mode": 0}, "C")

    # Non-heating node types and missing products have no catalogue.
    assert control_mode_choices(PID, "pmo", SETUP, "C") is None
    assert control_mode_choices(None, "htr", SETUP, "C") is None
    assert not control_mode_available(PID, "pmo", SETUP, "C")
    assert control_mode_available(PID, "htr", {"control_mode": 5}, "C")


def test_unlisted_htr_code_uses_the_factory_default() -> None:
    choices = control_mode_choices("0499", "htr", {"control_mode": 4}, "C")
    assert choices is not None
    assert control_mode_label(choices, 4) == "PID"


def test_unlisted_acm_code_uses_the_htr_default() -> None:
    choices = control_mode_choices("040D", "acm", {"control_mode": 0}, "C")
    assert choices is not None
    # The factory default is the htr wire array: hysteresis 0.2 is wire 0,
    # while the storage OFF-first array would put it at 1.
    assert control_mode_label(choices, 0) == "Hysteresis (0.2 °C)"


def test_storage_family_requires_relay_actuation(monkeypatch) -> None:
    monkeypatch.setitem(control_mode.ACTUATION, "1C", "TRIAC")
    assert control_mode_choices("041C", "acm", {"control_mode": 5}, "C") is None


def test_control_mode_available_app_gates() -> None:
    """The app hides the setting on Elnur water/classic storage products."""
    assert not control_mode_available("042A", "acm", {"control_mode": 5}, "C")
    assert not control_mode_available(
        "0414", "htr", {"control_mode": 5, "factory_options": {"ui_mode": 1}}, "C"
    )
    assert not control_mode_available(
        "0414", "htr", {"control_mode": 5, "factory_options": {"ui_mode": 2}}, "C"
    )
    assert control_mode_available(
        "0414", "htr", {"control_mode": 5, "factory_options": {"ui_mode": 0}}, "C"
    )
    # The setting must be present for the entity gate.
    assert not control_mode_available(PID, "htr", {}, "C")


def test_select_options_and_current_option() -> None:
    select = _entity()
    assert select.options == [
        "PID (15 min. period)",
        "PID (30 min. period)",
        "Hysteresis (0.2 °C)",
        "Hysteresis (0.3 °C)",
        "Hysteresis (0.5 °C)",
        "Hysteresis (0.7 °C)",
    ]
    assert select.current_option == "PID (15 min. period)"


def test_select_keeps_options_when_the_stored_index_is_unoffered() -> None:
    select = _entity(setup={"control_mode": 4})
    assert select.options == [
        "PID (15 min. period)",
        "PID (30 min. period)",
        "Hysteresis (0.2 °C)",
        "Hysteresis (0.3 °C)",
        "Hysteresis (0.5 °C)",
        "Hysteresis (0.7 °C)",
    ]
    assert select.current_option is None


async def test_select_writes_the_wire_index() -> None:
    host = _Host()
    select = _entity(host)
    try:
        await select.async_select_option("Hysteresis (0.7 °C)")
        assert host.posts[-1][0].endswith(f"devs/{DEV}/htr/{ADDR}/setup")
        assert host.posts[-1][1]["control_mode"] == 3
        assert host.posts[-1][1]["priority"] == "low"
    finally:
        await select._runtime.engine.stop()


def test_unlisted_acm_defaults_to_the_storage_controller():
    """The app's factory default for an unlisted acm is HelkiStorage."""
    choices = control_mode_choices("0899", "acm", {"control_mode": 0}, "C")
    assert choices is not None
    # Storage relay UI keeps PID_15 as the first offered label, with the
    # storage wire array's OFF at index 0 still excluded.
    assert choices[0][0] == "PID (15 min. period)"
    assert control_mode_choices("0899", "thm", {"control_mode": 0}, "C") is None
