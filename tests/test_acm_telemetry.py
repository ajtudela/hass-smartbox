"""Storage-heater telemetry and live-flag entity tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from smartbox.core.engine import SmartboxEngine
from smartbox.core.state import DeviceState, NodeState, WorldState, node_key

from custom_components.smartbox.binary_sensor import (
    Charging,
    LowBattery,
    PirPresence,
    WindowDetection,
    _build_node_binary_sensors,
)
from custom_components.smartbox.const import (
    ENERGY_NODE_TYPES,
    PRODUCT_CODE_WATER_STORAGE,
    SmartboxNodeType,
)
from custom_components.smartbox.sensor import (
    TargetChargeSensor,
    _build_node_sensors,
    _format_charging_days,
    _format_charging_slot,
)

DEV = "device_1"
ADDR = 1

STATUS = {
    "sync_status": "ok",
    "target_charge_per": 80,
    "charge_level": 40,
    "charging": True,
    "window_open": False,
    "french_rule_pir_state": True,
    "low_batt": False,
}

SETUP = {
    "charging_conf": {
        "slot_1": {"start": 420, "end": 540},
        "slot_2": {"start": 0, "end": 0},
        "active_days": [True, True, False, True, False, False, True],
    }
}


class _NoopFanout:
    """Fan-out double for property tests (no engine subscription)."""

    def register_node(self, *_args):
        return lambda: None


def _node(node_type: str = "acm") -> MagicMock:
    node = MagicMock()
    node.node_type = node_type
    node.name = "Node"
    node.node_id = f"{DEV}_{ADDR}"
    node.addr = ADDR
    node.dev_id = DEV
    node.setup = SETUP
    node.status = STATUS
    node.reseller = SimpleNamespace(web_url="https://web_url/", name="Test API name")
    node.home_id = "home_1"
    node.pid = "0109"
    node.hw_version = "hw"
    node.supported_wire_type = node_type in {t.value for t in SmartboxNodeType}
    node.heater_node = node_type in {"htr", "htr_mod", "acm"}
    node.heater_surface = node_type in {"htr", "htr_mod", "acm"}
    node.thermostat_surface = node_type == "thm"
    node.boost_available = False
    return node


def _entity(cls, status=None, setup=None, *, node_type="acm"):
    """Build one node entity over a hand-built engine world."""
    status = dict(STATUS if status is None else status)
    setup = dict(SETUP if setup is None else setup)
    engine = SmartboxEngine()
    key = node_key(node_type, ADDR)
    device = DeviceState(
        dev_id=DEV,
        away={},
        node_states={key: NodeState(status=status, setup=setup)},
    )
    object.__setattr__(engine, "_world", WorldState(devices={DEV: device}))
    return cls(
        _node(node_type), MagicMock(), SimpleNamespace(engine=engine), _NoopFanout()
    )


def test_target_charge_and_charging_schedule_attributes() -> None:
    sensor = _entity(TargetChargeSensor)
    assert sensor.native_value == 80
    assert sensor.extra_state_attributes == {
        "charging_slot_1": "07:00-09:00",
        "charging_slot_2": "disabled",
        "charging_days": ["sun", "mon", "wed", "sat"],
    }


def test_target_charge_skips_malformed_slots_and_days() -> None:
    setup = {
        "charging_conf": {
            "slot_1": {"start": "x", "end": 9},
            "slot_2": {"start": 9, "end": 9},
            "active_days": [True],
        }
    }
    sensor = _entity(TargetChargeSensor, setup=setup)
    attributes = sensor.extra_state_attributes
    assert "charging_slot_1" not in attributes
    assert attributes["charging_slot_2"] == "disabled"
    assert "charging_days" not in attributes


def test_target_charge_renders_overnight_slots() -> None:
    setup = {
        "charging_conf": {
            "slot_1": {"start": 1320, "end": 720},
            "slot_2": {"start": 1320, "end": 0},
        }
    }
    sensor = _entity(TargetChargeSensor, setup=setup)
    assert sensor.extra_state_attributes == {
        "charging_slot_1": "22:00-12:00",
        "charging_slot_2": "22:00-00:00",
    }


def test_live_flags() -> None:
    assert _entity(Charging).is_on is True
    assert _entity(WindowDetection).is_on is False
    assert _entity(PirPresence).is_on is True
    assert _entity(LowBattery, node_type="thm").is_on is False


def test_live_flag_is_none_before_status() -> None:
    assert _entity(PirPresence, status={}).is_on is None
    assert _entity(TargetChargeSensor, status={}).native_value is None


def test_live_flags_use_the_apps_strict_true_comparison() -> None:
    assert _entity(WindowDetection, status={"window_open": "false"}).is_on is False
    assert _entity(Charging, status={"charging": 0}).is_on is False
    assert _entity(PirPresence, status={"french_rule_pir_state": "true"}).is_on is False


def test_unknown_wire_type_gets_no_binary_sensors() -> None:
    node = _node("towel_rail_heater")
    assert (
        _build_node_binary_sensors(node, MagicMock(), _runtime_for(node), _NoopFanout())
        == []
    )


def test_charging_helpers_reject_malformed_schedule() -> None:
    assert _format_charging_slot({}, "slot_1") is None
    assert _format_charging_slot({"slot_1": {"start": "7", "end": 8}}, "slot_1") is None
    assert (
        _format_charging_slot({"slot_1": {"start": 9, "end": 9}}, "slot_1")
        == "disabled"
    )
    assert (
        _format_charging_slot({"slot_1": {"start": 60, "end": 1440}}, "slot_1")
        == "01:00-00:00"
    )
    assert (
        _format_charging_slot({"slot_1": {"start": True, "end": 8}}, "slot_1") is None
    )
    assert (
        _format_charging_slot({"slot_1": {"start": 1440, "end": 0}}, "slot_1") is None
    )
    assert (
        _format_charging_slot({"slot_1": {"start": 0, "end": 1441}}, "slot_1") is None
    )
    assert _format_charging_days({"active_days": [True]}) is None


def test_thermostat_gets_temperature_and_its_own_schedule_but_no_energy() -> None:
    runtime = SimpleNamespace(
        services=SimpleNamespace(schedule=[], thermostat_schedule=[])
    )
    groups = _build_node_sensors(_node("thm"), MagicMock(), runtime, _NoopFanout())
    classes = {type(entity).__name__ for entities, _ in groups for entity in entities}
    assert classes == {"TemperatureSensor", "ThermostatScheduleSensor"}
    assert runtime.services.schedule == []
    assert len(runtime.services.thermostat_schedule) == 1


def test_thm_28_gets_the_heater_schedule_not_the_thermostat_one() -> None:
    """The Tevolve floor thermostat uses the heater int-slot programme."""
    node = _node("thm")
    node.pid = "0128"
    node.heater_surface = True
    node.thermostat_surface = False
    runtime = SimpleNamespace(
        services=SimpleNamespace(schedule=[], thermostat_schedule=[])
    )
    groups = _build_node_sensors(node, MagicMock(), runtime, _NoopFanout())
    classes = {type(entity).__name__ for entities, _ in groups for entity in entities}
    assert classes == {"TemperatureSensor", "ScheduleSensor"}
    assert len(runtime.services.schedule) == 1
    assert runtime.services.thermostat_schedule == []


def test_water_storage_has_no_generic_heater_schedule() -> None:
    """2A uses ON/OFF_AUTO profiles this integration does not model."""
    node = _node("acm")
    node.pid = "082A"
    runtime = SimpleNamespace(
        services=SimpleNamespace(
            schedule=[], thermostat_schedule=[], charging=[], boost=[]
        )
    )
    groups = _build_node_sensors(node, MagicMock(), runtime, _NoopFanout())
    classes = {type(entity).__name__ for entities, _ in groups for entity in entities}
    assert "ScheduleSensor" not in classes
    assert runtime.services.schedule == []


def test_unknown_wire_type_gets_no_sensors() -> None:
    runtime = SimpleNamespace(
        services=SimpleNamespace(schedule=[], thermostat_schedule=[])
    )
    assert (
        _build_node_sensors(
            _node("towel_rail_heater"), MagicMock(), runtime, _NoopFanout()
        )
        == []
    )


def test_energy_node_types_exclude_thermostats() -> None:
    assert SmartboxNodeType.THM not in ENERGY_NODE_TYPES
    assert SmartboxNodeType.HTR in ENERGY_NODE_TYPES
    assert SmartboxNodeType.ACM in ENERGY_NODE_TYPES
    assert SmartboxNodeType.PMO in ENERGY_NODE_TYPES


def _runtime_for(node: MagicMock) -> SimpleNamespace:
    """Build a runtime whose engine world holds this node's status."""
    engine = SmartboxEngine()
    key = node_key(node.node_type, ADDR)
    device = DeviceState(
        dev_id=DEV,
        away={},
        node_states={key: NodeState(status=dict(node.status), setup=dict(node.setup))},
    )
    object.__setattr__(engine, "_world", WorldState(devices={DEV: device}))
    return SimpleNamespace(engine=engine)


def test_thermostat_binary_sensor_gating() -> None:
    node = _node("thm")
    # batt_level/error_code are not app-backed and presence is a panel-touch
    # artifact; only the generic low_batt shell field is honoured, and the
    # entity exists before the field arrives.
    node.status = {
        "presence": True,
        "error_code": 0,
        "batt_level": 6,
    }
    node.setup = {}
    groups = _build_node_binary_sensors(
        node, MagicMock(), _runtime_for(node), _NoopFanout()
    )
    classes = {type(entity).__name__ for entities, _ in groups for entity in entities}
    assert classes == {"LowBattery"}


def test_storage_heater_binary_sensor_gating() -> None:
    node = _node("acm")
    node.status = {"french_rule_pir_state": False, "charging": True}
    node.setup = {"extra_options": {"pir_enabled": True}}
    groups = _build_node_binary_sensors(
        node, MagicMock(), _runtime_for(node), _NoopFanout()
    )
    classes = {type(entity).__name__ for entities, _ in groups for entity in entities}
    assert classes == {"WindowDetection", "PirPresence", "Charging"}


def test_water_storage_gets_level_binaries_instead_of_charging() -> None:
    node = _node("acm")
    node.get_model_code.return_value = PRODUCT_CODE_WATER_STORAGE
    node.setup = {}

    node.status = {"charging": True, "charge_level": 50}
    groups = _build_node_binary_sensors(
        node, MagicMock(), _runtime_for(node), _NoopFanout()
    )
    entities = {
        type(entity).__name__: entity for entities, _ in groups for entity in entities
    }
    assert set(entities) == {"WindowDetection", "WaterReady", "WaterFullyReady"}
    assert entities["WaterReady"].is_on is True
    assert entities["WaterFullyReady"].is_on is False

    node.status = {"charging": False, "charge_level": 100}
    groups = _build_node_binary_sensors(
        node, MagicMock(), _runtime_for(node), _NoopFanout()
    )
    entities = {
        type(entity).__name__: entity for entities, _ in groups for entity in entities
    }
    assert entities["WaterReady"].is_on is False
    assert entities["WaterFullyReady"].is_on is True

    # No status body and a body without charge_level both render unknown.
    for status in ({}, {"charging": False}):
        node.status = status
        groups = _build_node_binary_sensors(
            node, MagicMock(), _runtime_for(node), _NoopFanout()
        )
        entities = {
            type(entity).__name__: entity
            for entities, _ in groups
            for entity in entities
        }
        assert entities["WaterReady"].is_on is None
        assert entities["WaterFullyReady"].is_on is None


def test_pir_presence_needs_the_setup_gate() -> None:
    node = _node("htr")
    node.status = {"french_rule_pir_state": True}
    node.setup = {"extra_options": {"pir_enabled": False}}
    groups = _build_node_binary_sensors(
        node, MagicMock(), _runtime_for(node), _NoopFanout()
    )
    classes = {type(entity).__name__ for entities, _ in groups for entity in entities}
    assert "PirPresence" not in classes
    assert "LowBattery" not in classes
