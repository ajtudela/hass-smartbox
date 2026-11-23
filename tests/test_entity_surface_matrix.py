"""Entity-surface matrix per controller family.

Each row builds one node through the real platform builders and asserts the
exact domains and unique-id suffixes. A second matrix covers the box-level
entities, and an attribute snapshot covers the options, bounds and current
states of representative families. This catches a family silently gaining or
losing a surface or wiring a profile field into the wrong entity; the
remaining per-property behaviour lives in the platform suites.
"""

from __future__ import annotations

from dataclasses import replace
import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from smartbox.core.engine import SmartboxEngine
from smartbox.core.state import DeviceState, NodeState, WorldState, node_key

from custom_components.smartbox.binary_sensor import (
    _build_device_binary_sensors,
    _build_node_binary_sensors,
)
from custom_components.smartbox.boost_prefs import NodeBoostPrefs
from custom_components.smartbox.climate import _build_node_climate
from custom_components.smartbox.identity import SmartboxDeviceInfo, SmartboxNodeInfo
from custom_components.smartbox.number import (
    _build_device_entities,
    _build_node_numbers,
)
from custom_components.smartbox.select import _build_node_selects
from custom_components.smartbox.sensor import _build_device_sensors, _build_node_sensors
from custom_components.smartbox.services import ServiceTargets
from custom_components.smartbox.switch import (
    _build_device_switches,
    _build_node_switches,
)

RESELLER = SimpleNamespace(web_url="https://example/", name="Test")
PLATFORMS = (
    ("climate", _build_node_climate),
    ("sensor", _build_node_sensors),
    ("number", _build_node_numbers),
    ("switch", _build_node_switches),
    ("select", _build_node_selects),
    ("binary_sensor", _build_node_binary_sensors),
)
PLATFORMS_DEVICE = (
    ("switch", _build_device_switches),
    ("number", _build_device_entities),
    ("sensor", _build_device_sensors),
    ("binary_sensor", _build_device_binary_sensors),
)

BASE_SENSORS = ["duty_cycle", "power", "schedule", "temperature", "total_consumption"]
BASE_NUMBERS = [
    "away_offset",
    "max_temperature",
    "prog_comfort_temperature",
    "prog_eco_temperature",
    "prog_frost_temperature",
]
PROG_NUMBERS = [
    "prog_comfort_temperature",
    "prog_eco_temperature",
    "prog_frost_temperature",
]
BOOST_NUMBERS = ["config_boost_duration", "config_boost_temperature"]
BOOST_SENSORS = ["duty_cycle", "power", "schedule", "temperature", "total_consumption"]
LOCK_SWITCHES = ["lock"]


def _device(box_power_limit_available: bool = True) -> SmartboxDeviceInfo:
    return SmartboxDeviceInfo(
        dev_id="dev",
        name="Box",
        model_id=None,
        sw_version=None,
        serial_number=None,
        home_id="home",
        reseller=RESELLER,
        box_power_limit_available=box_power_limit_available,
    )


def _node(node_type, pid, fw, setup, status, box=True) -> SmartboxNodeInfo:
    return SmartboxNodeInfo(
        device=_device(box),
        node_info={"type": node_type, "addr": 0, "name": "Node"},
        status=status,
        setup=setup,
        version={"pid": pid, "fw_version": fw},
    )


def _runtime(node: SmartboxNodeInfo | None = None) -> SimpleNamespace:
    """Build a runtime whose engine sees the node's discovery setup/status."""
    engine = SmartboxEngine()
    if node is not None:
        key = node_key(node.node_type, node.addr)
        device = DeviceState(
            dev_id=node.dev_id,
            away={"away": False, "enabled": False, "forced": False},
            node_states={
                key: NodeState(
                    status=dict(node.status),
                    setup=dict(node.setup),
                    version=dict(node.version),
                )
            },
        )
        object.__setattr__(engine, "_world", WorldState(devices={node.dev_id: device}))
    return SimpleNamespace(
        engine=engine,
        services=ServiceTargets(),
        client=MagicMock(),
        boost_prefs=_NullPrefs(),
    )


class _NullPrefs:
    """Empty per-node boost preferences for construction-only tests."""

    def get(self, node_id: str) -> NodeBoostPrefs:  # noqa: ARG002
        return NodeBoostPrefs()


def _runtime_for_device(device: SmartboxDeviceInfo) -> SimpleNamespace:
    """Build a runtime whose engine sees the box's away/power/connected state."""
    engine = SmartboxEngine()
    world_device = DeviceState(
        dev_id=device.dev_id,
        connected=True,
        away={"away": False, "enabled": True, "forced": False},
        power_limit=6000,
        node_states={},
    )
    object.__setattr__(
        engine, "_world", WorldState(devices={device.dev_id: world_device})
    )
    return SimpleNamespace(
        engine=engine,
        services=ServiceTargets(),
        client=MagicMock(),
        boost_prefs=_NullPrefs(),
    )


def _surface(node: SmartboxNodeInfo) -> dict[str, list[str]]:
    runtime = _runtime(node)
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.options = {}
    fanout = SimpleNamespace(register_node=lambda *_args: lambda: None)
    surface: dict[str, list[str]] = {}
    for domain, builder in PLATFORMS:
        keys = [
            entity.unique_id.removeprefix(f"{node.node_id}_")
            for entities, _ in builder(node, entry, runtime, fanout)
            for entity in entities
        ]
        if keys:
            surface[domain] = sorted(keys)
    return surface


def _device_surface(device: SmartboxDeviceInfo) -> dict[str, list[str]]:
    runtime = _runtime_for_device(device)
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.options = {}
    fanout = SimpleNamespace(register_node=lambda *_args: lambda: None)
    surface: dict[str, list[str]] = {}
    for domain, builder in PLATFORMS_DEVICE:
        keys = [
            entity.unique_id.removeprefix(f"{device.dev_id}_")
            for entities, _ in builder(device, entry, runtime, fanout)
            for entity in entities
        ]
        if keys:
            surface[domain] = sorted(keys)
    return surface


def _as_value(value: object) -> object:
    """Render enums, datetimes and mode lists as stable JSON-ish values."""
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, (dt.datetime, dt.date)):
        return "<timestamp>"
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return value


def _device_class(entity) -> str | None:
    value = getattr(entity, "device_class", None)
    return None if value is None else str(getattr(value, "value", value))


def _number_spec(entity) -> tuple[dict[str, object], object]:
    spec: dict[str, object] = {}
    for attr, name in (
        ("native_min_value", "min"),
        ("native_max_value", "max"),
        ("native_step", "step"),
    ):
        value = getattr(entity, attr, None)
        if value is not None:
            spec[name] = _as_value(value)
    unit = getattr(entity, "native_unit_of_measurement", None)
    if unit is not None:
        spec["unit"] = str(unit)
    if (device_class := _device_class(entity)) is not None:
        spec["device_class"] = device_class
    return spec, entity.native_value


def _climate_spec(entity) -> tuple[dict[str, object], object]:
    spec: dict[str, object] = {}
    for attr in ("hvac_modes", "preset_modes", "swing_modes", "fan_modes"):
        value = getattr(entity, attr, None)
        if value:
            spec[attr] = _as_value(value)
    for attr in ("min_temp", "max_temp", "target_temp_step"):
        value = getattr(entity, attr, None)
        if value is not None:
            spec[attr] = _as_value(value)
    preset = getattr(entity, "preset_mode", None)
    if preset is not None:
        spec["preset"] = _as_value(preset)
    return spec, getattr(entity, "hvac_mode", None)


def _select_spec(entity) -> tuple[dict[str, object], object]:
    spec: dict[str, object] = {}
    options = getattr(entity, "options", None)
    if options is not None:
        spec["options"] = _as_value(options)
    return spec, getattr(entity, "current_option", None)


def _switch_spec(entity) -> tuple[dict[str, object], object]:
    spec: dict[str, object] = {}
    if (device_class := _device_class(entity)) is not None:
        spec["device_class"] = device_class
    category = getattr(entity, "entity_category", None)
    if category is not None:
        spec["entity_category"] = str(getattr(category, "value", category))
    return spec, entity.is_on


def _binary_sensor_spec(entity) -> tuple[dict[str, object], object]:
    spec: dict[str, object] = {}
    if (device_class := _device_class(entity)) is not None:
        spec["device_class"] = device_class
    return spec, entity.is_on


def _sensor_spec(entity) -> tuple[dict[str, object], object]:
    spec: dict[str, object] = {}
    if (device_class := _device_class(entity)) is not None:
        spec["device_class"] = device_class
    unit = getattr(entity, "native_unit_of_measurement", None)
    if unit is not None:
        spec["unit"] = str(unit)
    state_class = getattr(entity, "state_class", None)
    if state_class is not None:
        spec["state_class"] = str(getattr(state_class, "value", state_class))
    return spec, getattr(entity, "native_value", None)


_SPEC_BUILDERS = {
    "number": _number_spec,
    "climate": _climate_spec,
    "select": _select_spec,
    "switch": _switch_spec,
    "binary_sensor": _binary_sensor_spec,
    "sensor": _sensor_spec,
}


def _entity_spec(domain: str, entity) -> dict[str, object]:
    """Return the static options, bounds and current state of one entity."""
    spec, state = _SPEC_BUILDERS[domain](entity)
    if state is not None:
        spec["state"] = _as_value(state)
    return spec


def _attributes(node: SmartboxNodeInfo) -> dict[str, dict[str, object]]:
    """Return every entity's options, bounds and current state for one node."""
    runtime = _runtime(node)
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.options = {}
    fanout = SimpleNamespace(register_node=lambda *_args: lambda: None)
    snapshot: dict[str, dict[str, object]] = {}
    for domain, builder in PLATFORMS:
        for entities, _ in builder(node, entry, runtime, fanout):
            for entity in entities:
                key = entity.unique_id.removeprefix(f"{node.node_id}_")
                snapshot[f"{domain}.{key}"] = _entity_spec(domain, entity)
    return snapshot


def _device_attributes(
    device: SmartboxDeviceInfo,
) -> dict[str, dict[str, object]]:
    """Return every box-level entity's attributes for one device."""
    runtime = _runtime_for_device(device)
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.options = {}
    fanout = SimpleNamespace(register_node=lambda *_args: lambda: None)
    snapshot: dict[str, dict[str, object]] = {}
    for domain, builder in PLATFORMS_DEVICE:
        for entities, _ in builder(device, entry, runtime, fanout):
            for entity in entities:
                key = entity.unique_id.removeprefix(f"{device.dev_id}_")
                snapshot[f"{domain}.{key}"] = _entity_spec(domain, entity)
    return snapshot


def _states(node: SmartboxNodeInfo) -> dict[str, object]:
    """Return every non-None state, with the climate preset split out."""
    runtime = _runtime(node)
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.options = {}
    fanout = SimpleNamespace(register_node=lambda *_args: lambda: None)
    states: dict[str, object] = {}
    for domain, builder in PLATFORMS:
        for entities, _ in builder(node, entry, runtime, fanout):
            for entity in entities:
                name = f"{domain}.{entity.unique_id.removeprefix(f'{node.node_id}_')}"
                if domain == "climate":
                    hvac_mode = getattr(entity, "hvac_mode", None)
                    if hvac_mode is not None:
                        states[name] = _as_value(hvac_mode)
                    preset = getattr(entity, "preset_mode", None)
                    if preset is not None:
                        states[f"{name}.preset"] = _as_value(preset)
                    continue
                if domain == "select":
                    value = getattr(entity, "current_option", None)
                elif domain in ("switch", "binary_sensor"):
                    value = entity.is_on
                else:
                    value = getattr(entity, "native_value", None)
                if value is not None:
                    states[name] = _as_value(value)
    return states


CASES = [
    pytest.param(
        "base_0402",
        _node(
            "htr",
            "04020000",
            "1.9",
            {
                "units": "C",
                "away_offset": "2.0",
                "max_stemp_limit": "30.0",
                "priority": 1,
                "control_mode": 0,
                "factory_options": {
                    "window_mode_available": True,
                    "true_radiant_available": True,
                    "boost_config": 0,
                },
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": BASE_SENSORS,
            "number": BASE_NUMBERS,
            "switch": [
                "lock",
                "max_temperature_limit",
                "true_radiant",
                "window_mode",
            ],
            "select": ["control_mode", "priority"],
            "binary_sensor": ["window_detection"],
        },
        id="base-0402",
    ),
    pytest.param(
        "helki_0132",
        _node(
            "htr",
            "01320000",
            "1.8.0",
            {
                "units": "C",
                "away_offset": "2.0",
                "max_stemp_limit": "30.0",
                "power": "2000.0",
                "extra_options": {"user_duty_limit": 0},
                "factory_options": {
                    "window_mode_available": True,
                    "true_radiant_available": True,
                    "boost_config": 2,
                    "lst_available": True,
                    "lst_value": 25,
                    "bbc_available": True,
                    "bbc_value": 50,
                    "super_lock_available": True,
                },
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BASE_SENSORS],
            "number": [
                "away_offset",
                *BOOST_NUMBERS,
                "max_temperature",
                *PROG_NUMBERS,
            ],
            "switch": ["boost", "max_temperature_limit", "true_radiant", "window_mode"],
            "select": ["duty_limit", "lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="helki-0132",
    ),
    pytest.param(
        "helki_0132_bool_lock",
        _node(
            "htr",
            "01320000",
            "1.8.0",
            {
                "units": "C",
                "away_offset": "2.0",
                "max_stemp_limit": "30.0",
                "power": "2000.0",
                "extra_options": {"user_duty_limit": 0},
                "factory_options": {
                    "window_mode_available": True,
                    "true_radiant_available": True,
                    "boost_config": 2,
                    "lst_available": True,
                    "lst_value": 25,
                    "bbc_available": True,
                    "bbc_value": 50,
                },
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BASE_SENSORS],
            "number": [
                "away_offset",
                *BOOST_NUMBERS,
                "max_temperature",
                *PROG_NUMBERS,
            ],
            "switch": [
                "boost",
                "lock",
                "max_temperature_limit",
                "true_radiant",
                "window_mode",
            ],
            "select": ["duty_limit"],
            "binary_sensor": ["window_detection"],
        },
        id="helki-0132-bool-lock",
    ),
    pytest.param(
        "tevolve_011A",
        _node(
            "htr",
            "011A0000",
            "1.9",
            {"units": "C", "factory_options": {"boost_config": 3}},
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BOOST_SENSORS],
            "number": [*BOOST_NUMBERS, *PROG_NUMBERS],
            "switch": ["boost", "easy_mode", "lock", "runback_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="tevolve-011A",
    ),
    pytest.param(
        "tevolve_0134",
        _node(
            "htr",
            "01340000",
            "1.9",
            {"units": "C", "factory_options": {"boost_config": 2}},
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BOOST_SENSORS],
            "number": [*BOOST_NUMBERS, *PROG_NUMBERS],
            "switch": ["boost", "easy_mode", "runback_mode"],
            "select": ["lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="tevolve-0134",
    ),
    pytest.param(
        "atc_014C",
        _node(
            "htr",
            "014C0000",
            "1.9",
            {"units": "C", "factory_options": {"boost_config": 2}},
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BOOST_SENSORS],
            "number": [*BOOST_NUMBERS, *PROG_NUMBERS],
            "switch": ["boost", "runback_mode"],
            "select": ["lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="atc-014C",
    ),
    pytest.param(
        "haverland_0117",
        _node(
            "htr",
            "01170000",
            "1.9",
            {
                "units": "C",
                "max_stemp_limit": "30.0",
                "factory_options": {"boost_config": 1},
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BOOST_SENSORS],
            "number": ["max_temperature", *PROG_NUMBERS],
            "switch": ["boost", "lock", "max_temperature_limit"],
            "binary_sensor": ["window_detection"],
        },
        id="haverland-0117",
    ),
    pytest.param(
        "haverland_011D",
        _node(
            "htr",
            "011D0000",
            "1.9",
            {
                "units": "C",
                "max_stemp_limit": "30.0",
                "factory_options": {"boost_config": 1},
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": BASE_SENSORS,
            "number": PROG_NUMBERS,
            "select": ["lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="haverland-011D",
    ),
    pytest.param(
        "haverland_011D_config0",
        _node(
            "htr",
            "011D0000",
            "1.9",
            {
                "units": "C",
                "max_stemp_limit": "30.0",
                "factory_options": {"boost_config": 0},
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BOOST_SENSORS],
            "number": PROG_NUMBERS,
            "switch": ["boost"],
            "select": ["lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="haverland-011D-config0",
    ),
    pytest.param(
        "htr_mod_010A",
        _node(
            "htr_mod",
            "010A0000",
            "1.8.0",
            {"units": "C", "factory_options": {"boost_config": 2}},
            {"units": "C", "mode": "auto"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["power", "schedule", "temperature", "total_consumption"],
            "switch": LOCK_SWITCHES,
            "binary_sensor": ["window_detection"],
        },
        id="htr-mod-010A",
    ),
    pytest.param(
        "storage_081C",
        _node(
            "acm",
            "081C0000",
            "1.8.0",
            {
                "units": "C",
                "operational_mode": 1,
                "factory_options": {"boost_config": 2},
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": [
                "boost_end_time",
                "charge_level",
                "power",
                "schedule",
                "target_charge",
                "temperature",
                "total_consumption",
            ],
            "number": [*BOOST_NUMBERS, *PROG_NUMBERS],
            "switch": ["boost"],
            "select": ["lock_mode"],
            "binary_sensor": ["charging", "window_detection"],
        },
        id="storage-081C",
    ),
    pytest.param(
        "storage_0814",
        _node(
            "acm",
            "08140000",
            "1.9",
            {"units": "C", "factory_options": {"ui_mode": 1, "boost_config": 0}},
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": [
                "charge_level",
                "power",
                "schedule",
                "target_charge",
                "temperature",
                "total_consumption",
            ],
            "number": PROG_NUMBERS,
            "select": ["lock_mode", "storage_ui_mode"],
            "binary_sensor": ["charging", "window_detection"],
        },
        id="storage-0814",
    ),
    pytest.param(
        "storage_0814_classic_flags",
        _node(
            "acm",
            "08140000",
            "1.9",
            {
                "units": "C",
                "away_offset": "2.0",
                "factory_options": {
                    "ui_mode": 1,
                    "boost_config": 0,
                    "window_mode_available": True,
                    "true_radiant_available": True,
                },
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": [
                "charge_level",
                "power",
                "schedule",
                "target_charge",
                "temperature",
                "total_consumption",
            ],
            "number": ["away_offset", *PROG_NUMBERS],
            "select": ["lock_mode", "storage_ui_mode"],
            "binary_sensor": ["charging", "window_detection"],
        },
        id="storage-0814-classic-flags",
    ),
    pytest.param(
        "water_082A",
        _node(
            "acm",
            "082A0000",
            "1.9",
            {"units": "C", "factory_options": {"ui_mode": 0, "boost_config": 0}},
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": [
                "charge_level",
                "power",
                "target_charge",
                "temperature",
                "total_consumption",
            ],
            "select": ["lock_mode"],
            "binary_sensor": [
                "water_fully_ready",
                "water_ready",
                "window_detection",
            ],
        },
        id="water-082A",
    ),
    pytest.param(
        "water_082A_settings",
        _node(
            "acm",
            "082A0000",
            "1.9",
            {
                "units": "C",
                "away_offset": "2.0",
                "priority": 1,
                "control_mode": 0,
                "factory_options": {
                    "ui_mode": 0,
                    "boost_config": 0,
                    "window_mode_available": True,
                    "true_radiant_available": True,
                },
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": [
                "charge_level",
                "power",
                "target_charge",
                "temperature",
                "total_consumption",
            ],
            "select": ["lock_mode", "priority"],
            "binary_sensor": [
                "water_fully_ready",
                "water_ready",
                "window_detection",
            ],
        },
        id="water-082A-settings",
    ),
    pytest.param(
        "thermostat_0104",
        _node(
            "thm",
            "01040000",
            "1.9",
            {"units": "C", "type": "heat", "anti_grippage": "on"},
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["temperature", "thermostat_schedule"],
            "switch": ["anti_grippage"],
            "binary_sensor": ["low_battery"],
        },
        id="thermostat-0104",
    ),
    pytest.param(
        "thm28_0128",
        _node(
            "thm",
            "01280000",
            "1.9",
            {"units": "C", "factory_options": {"boost_config": 2}},
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", "schedule", "temperature"],
            "number": [*BOOST_NUMBERS, *PROG_NUMBERS],
            "switch": ["boost", "easy_mode", "lock", "runback_mode"],
            "binary_sensor": ["low_battery", "window_detection"],
        },
        id="thm-28",
    ),
    pytest.param(
        "pmo_0103",
        _node(
            "pmo",
            "01030000",
            "1.9",
            {"units": "C", "power_limit": 1500},
            {"units": "C"},
        ),
        {
            "sensor": ["power", "total_consumption"],
            "number": ["node_power_limit"],
            "switch": ["no_node_power_limit"],
        },
        id="pmo-0103",
    ),
    pytest.param(
        "base_0402_nocaps",
        _node(
            "htr",
            "04020000",
            "1.9",
            {
                "units": "C",
                "factory_options": {
                    "window_mode_available": False,
                    "true_radiant_available": False,
                    "boost_config": 0,
                },
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": BASE_SENSORS,
            "number": PROG_NUMBERS,
            "switch": LOCK_SWITCHES,
            "binary_sensor": ["window_detection"],
        },
        id="base-0402-nocaps",
    ),
    pytest.param(
        "base_0402_boxless",
        _node(
            "htr",
            "04020000",
            "1.9",
            {
                "units": "C",
                "away_offset": "2.0",
                "max_stemp_limit": "30.0",
                "priority": 1,
                "control_mode": 0,
                "factory_options": {"boost_config": 0},
            },
            {"units": "C"},
            box=False,
        ),
        {
            "climate": ["thermostat"],
            "sensor": BASE_SENSORS,
            "number": BASE_NUMBERS,
            "switch": ["lock", "max_temperature_limit"],
            "select": ["control_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="base-0402-boxless",
    ),
    pytest.param(
        "helki_0132_config0",
        _node(
            "htr",
            "01320000",
            "1.8.0",
            {
                "units": "C",
                "away_offset": "2.0",
                "max_stemp_limit": "30.0",
                "power": "2000.0",
                "extra_options": {"user_duty_limit": 0},
                "factory_options": {
                    "window_mode_available": True,
                    "true_radiant_available": True,
                    "boost_config": 0,
                    "lst_available": True,
                    "lst_value": 25,
                    "bbc_available": True,
                    "bbc_value": 50,
                    "super_lock_available": True,
                },
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": BASE_SENSORS,
            "number": BASE_NUMBERS,
            "switch": ["max_temperature_limit", "true_radiant", "window_mode"],
            "select": ["duty_limit", "lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="helki-0132-config0",
    ),
    pytest.param(
        "tevolve_0134_config0",
        _node(
            "htr",
            "01340000",
            "1.9",
            {"units": "C", "factory_options": {"boost_config": 0}},
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": BOOST_SENSORS,
            "number": PROG_NUMBERS,
            "switch": ["easy_mode"],
            "select": ["lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="tevolve-0134-config0",
    ),
    pytest.param(
        "tevolve_0134_full_temp_and_runback",
        _node(
            "htr",
            "01340000",
            "1.9",
            {
                "units": "C",
                "factory_options": {"boost_config": 3},
                "extra_options": {"boost_temp": "21.0", "boost_time": 60},
            },
            {
                "units": "C",
                "stemp": "20.0",
                "boost_modes_available": ["temp_and_time", "runback"],
            },
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BASE_SENSORS],
            "number": [
                "config_boost_duration",
                "config_boost_temperature",
                "config_runback_duration",
                "config_runback_temperature",
                *PROG_NUMBERS,
            ],
            "switch": ["boost", "easy_mode"],
            "select": ["lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="tevolve-0134-full-temp-and-runback",
    ),
    pytest.param(
        "tevolve_0134_full_fixed_only",
        _node(
            "htr",
            "01340000",
            "1.9",
            {"units": "C", "factory_options": {"boost_config": 3}},
            {
                "units": "C",
                "stemp": "20.0",
                "boost_modes_available": ["max_temp_fixed_time"],
            },
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BASE_SENSORS],
            "number": PROG_NUMBERS,
            "switch": ["boost", "easy_mode"],
            "select": ["lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="tevolve-0134-full-fixed-only",
    ),
    pytest.param(
        "tevolve_0134_full_no_modes",
        _node(
            "htr",
            "01340000",
            "1.9",
            {"units": "C", "factory_options": {"boost_config": 3}},
            {"units": "C", "stemp": "20.0", "boost_modes_available": []},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BASE_SENSORS],
            "number": PROG_NUMBERS,
            "switch": ["boost", "easy_mode"],
            "select": ["lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="tevolve-0134-full-no-modes",
    ),
    pytest.param(
        "thm28_0128_nopobes",
        _node(
            "thm",
            "01280000",
            "1.9",
            {
                "units": "C",
                "extra_options": {"sensor_mode": 3},
                "factory_options": {"boost_config": 2},
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["temperature"],
            "switch": ["lock"],
            "binary_sensor": ["low_battery", "window_detection"],
        },
        id="thm-28-nopobes",
    ),
    pytest.param(
        "haverland_011D_fw16",
        _node(
            "htr",
            "011D0000",
            "1.6.0",
            {
                "units": "C",
                "max_stemp_limit": "30.0",
                "factory_options": {"boost_config": 1},
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": BASE_SENSORS,
            "number": PROG_NUMBERS,
            "switch": ["lock"],
            "binary_sensor": ["window_detection"],
        },
        id="haverland-011D-fw16",
    ),
    pytest.param(
        "tevolve_0127",
        _node(
            "htr",
            "01270000",
            "1.9",
            {
                "units": "C",
                "away_offset": "2.0",
                "extra_options": {"pir_enabled": True},
                "factory_options": {"boost_config": 2},
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": BASE_SENSORS,
            "number": [
                "away_offset",
                "prog_comfort_temperature",
                "prog_eco_temperature",
            ],
            "switch": ["easy_mode", "lock"],
            "binary_sensor": ["pir_presence", "window_detection"],
        },
        id="tevolve-0127",
    ),
    pytest.param(
        "climastar_013D",
        _node(
            "htr",
            "013D0000",
            "1.9",
            {
                "units": "C",
                "max_stemp_limit": "30.0",
                "priority": 1,
                "control_mode": 0,
                "extra_options": {"user_duty_limit": 0},
                "factory_options": {
                    "boost_config": 2,
                    "window_mode_available": True,
                    "true_radiant_available": True,
                },
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BASE_SENSORS],
            "number": [*BOOST_NUMBERS, "max_temperature", *PROG_NUMBERS],
            "switch": [
                "boost",
                "lock",
                "max_temperature_limit",
                "true_radiant",
                "window_mode",
            ],
            "select": ["control_mode", "priority"],
            "binary_sensor": ["window_detection"],
        },
        id="climastar-013D",
    ),
    pytest.param(
        "haverland_0147",
        _node(
            "htr",
            "01470000",
            "1.9",
            {
                "units": "C",
                "max_stemp_limit": "30.0",
                "factory_options": {"boost_config": 1},
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": BASE_SENSORS,
            "number": ["max_temperature", *PROG_NUMBERS],
            "switch": ["max_temperature_limit"],
            "select": ["lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="haverland-0147",
    ),
    pytest.param(
        "lhz_relay_0136_config0",
        _node(
            "htr",
            "01360000",
            "1.9",
            {
                "units": "C",
                "away_offset": "2.0",
                "max_stemp_limit": "30.0",
                "power": "2000.0",
                "extra_options": {"user_duty_limit": 0},
                "factory_options": {
                    "window_mode_available": True,
                    "true_radiant_available": True,
                    "boost_config": 0,
                    "lst_available": True,
                    "lst_value": 25,
                    "bbc_available": True,
                    "bbc_value": 50,
                    "super_lock_available": True,
                },
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BASE_SENSORS],
            "number": BASE_NUMBERS,
            "switch": [
                "boost",
                "max_temperature_limit",
                "true_radiant",
                "window_mode",
            ],
            "select": ["duty_limit", "lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="lhz-relay-0136-config0",
    ),
    pytest.param(
        "lhz_wifi_0138",
        _node(
            "htr",
            "01380000",
            "1.9",
            {
                "units": "C",
                "away_offset": "2.0",
                "max_stemp_limit": "30.0",
                "power": "2000.0",
                "extra_options": {"user_duty_limit": 0},
                "factory_options": {
                    "window_mode_available": True,
                    "true_radiant_available": True,
                    "boost_config": 2,
                    "lst_available": True,
                    "lst_value": 25,
                    "bbc_available": True,
                    "bbc_value": 50,
                    "super_lock_available": True,
                },
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": ["boost_end_time", *BASE_SENSORS],
            "number": ["away_offset", *BOOST_NUMBERS, "max_temperature", *PROG_NUMBERS],
            "switch": [
                "boost",
                "max_temperature_limit",
                "true_radiant",
                "window_mode",
            ],
            "select": ["duty_limit", "lock_mode"],
            "binary_sensor": ["window_detection"],
        },
        id="lhz-wifi-0138",
    ),
    pytest.param(
        "tevolve_relay_011E",
        _node(
            "htr",
            "011E0000",
            "1.9",
            {
                "units": "C",
                "away_offset": "2.0",
                "max_stemp_limit": "30.0",
                "factory_options": {"boost_config": 0},
            },
            {"units": "C"},
        ),
        {
            "climate": ["thermostat"],
            "sensor": BASE_SENSORS,
            "number": BASE_NUMBERS,
            "switch": ["lock", "max_temperature_limit"],
            "binary_sensor": ["window_detection"],
        },
        id="tevolve-relay-011E",
    ),
]

SNAPSHOTS = {
    "base-0402": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 1.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.schedule": {"device_class": "enum"},
        "number.away_offset": {
            "min": 0.0,
            "max": 5.0,
            "step": 0.5,
            "unit": "°C",
            "state": 2.0,
        },
        "number.max_temperature": {
            "min": 16.0,
            "max": 30.0,
            "step": 1.0,
            "unit": "°C",
            "device_class": "temperature",
            "state": 30.0,
        },
        "number.prog_frost_temperature": {
            "min": 0.0,
            "max": 29.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 0.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 1.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.window_mode": {"entity_category": "config", "state": False},
        "switch.true_radiant": {"entity_category": "config", "state": False},
        "switch.lock": {"entity_category": "config", "state": False},
        "switch.max_temperature_limit": {"entity_category": "config", "state": True},
        "select.priority": {"options": ["low", "medium", "high"]},
        "select.control_mode": {
            "options": [
                "PID (15 min. period)",
                "PID (30 min. period)",
                "Hysteresis (0.2 °C)",
                "Hysteresis (0.3 °C)",
                "Hysteresis (0.5 °C)",
                "Hysteresis (0.7 °C)",
            ],
            "state": "Hysteresis (0.2 °C)",
        },
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "helki-0132": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 1.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.config_boost_temperature": {
            "min": 5.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
            "state": 21.0,
        },
        "number.config_boost_duration": {
            "min": 60,
            "max": 1440.0,
            "step": 60,
            "unit": "min",
            "device_class": "duration",
            "state": 60.0,
        },
        "number.away_offset": {
            "min": 0.0,
            "max": 5.0,
            "step": 0.5,
            "unit": "°C",
            "state": 2.0,
        },
        "number.max_temperature": {
            "min": 16.0,
            "max": 30.0,
            "step": 1.0,
            "unit": "°C",
            "device_class": "temperature",
            "state": 30.0,
        },
        "number.prog_frost_temperature": {
            "min": 0.0,
            "max": 29.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 0.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 1.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.window_mode": {"entity_category": "config", "state": False},
        "switch.true_radiant": {"entity_category": "config", "state": False},
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.max_temperature_limit": {"entity_category": "config", "state": True},
        "select.duty_limit": {"options": ["Off", "LST", "Baby Care"], "state": "Off"},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "helki-0132-bool-lock": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 1.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.config_boost_temperature": {
            "min": 5.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
            "state": 21.0,
        },
        "number.config_boost_duration": {
            "min": 60,
            "max": 1440.0,
            "step": 60,
            "unit": "min",
            "device_class": "duration",
            "state": 60.0,
        },
        "number.away_offset": {
            "min": 0.0,
            "max": 5.0,
            "step": 0.5,
            "unit": "°C",
            "state": 2.0,
        },
        "number.max_temperature": {
            "min": 16.0,
            "max": 30.0,
            "step": 1.0,
            "unit": "°C",
            "device_class": "temperature",
            "state": 30.0,
        },
        "number.prog_frost_temperature": {
            "min": 0.0,
            "max": 29.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 0.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 1.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.window_mode": {"entity_category": "config", "state": False},
        "switch.true_radiant": {"entity_category": "config", "state": False},
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.lock": {"entity_category": "config", "state": False},
        "switch.max_temperature_limit": {"entity_category": "config", "state": True},
        "select.duty_limit": {"options": ["Off", "LST", "Baby Care"], "state": "Off"},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "tevolve-011A": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.config_boost_temperature": {
            "min": 5.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
            "state": 21.0,
        },
        "number.config_boost_duration": {
            "min": 60,
            "max": 1440.0,
            "step": 60,
            "unit": "min",
            "device_class": "duration",
            "state": 60.0,
        },
        "number.prog_frost_temperature": {
            "min": 7.0,
            "max": 34.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 7.5,
            "max": 34.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 8.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.easy_mode": {"entity_category": "config", "state": False},
        "switch.runback_mode": {"entity_category": "config", "state": False},
        "switch.lock": {"entity_category": "config", "state": False},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "tevolve-0134": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.config_boost_temperature": {
            "min": 5.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
            "state": 21.0,
        },
        "number.config_boost_duration": {
            "min": 60,
            "max": 1440.0,
            "step": 60,
            "unit": "min",
            "device_class": "duration",
            "state": 60.0,
        },
        "number.prog_frost_temperature": {
            "min": 7.0,
            "max": 34.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 7.5,
            "max": 34.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 8.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.easy_mode": {"entity_category": "config", "state": False},
        "switch.runback_mode": {"entity_category": "config", "state": False},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "atc-014C": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 5.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.config_boost_temperature": {
            "min": 5.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
            "state": 21.0,
        },
        "number.config_boost_duration": {
            "min": 60,
            "max": 120.0,
            "step": 60,
            "unit": "min",
            "device_class": "duration",
            "state": 60.0,
        },
        "number.prog_frost_temperature": {
            "min": 5.0,
            "max": 10.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 5.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 6.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.runback_mode": {"entity_category": "config", "state": False},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "haverland-0117": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 5.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.max_temperature": {
            "min": 16.0,
            "max": 30.0,
            "step": 1.0,
            "unit": "°C",
            "device_class": "temperature",
            "state": 30.0,
        },
        "number.prog_frost_temperature": {
            "min": 5.0,
            "max": 7.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 7.0,
            "max": 28.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 9.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.lock": {"entity_category": "config", "state": False},
        "switch.max_temperature_limit": {"entity_category": "config", "state": True},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "haverland-011D": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 5.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.schedule": {"device_class": "enum"},
        "number.prog_frost_temperature": {
            "min": 5.0,
            "max": 7.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 7.0,
            "max": 28.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 9.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "haverland-011D-config0": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 5.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.prog_frost_temperature": {
            "min": 5.0,
            "max": 7.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 7.0,
            "max": 28.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 9.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.boost": {"entity_category": "config", "state": False},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "htr-mod-010A": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": [
                "away",
                "activity",
                "comfort",
                "eco",
                "frost",
                "self_learn",
                "schedule",
            ],
            "min_temp": 5.0,
            "max_temp": 28.0,
            "preset": "schedule",
            "state": "auto",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.schedule": {"device_class": "enum"},
        "switch.lock": {"entity_category": "config", "state": False},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "storage-081C": {
        "climate.thermostat": {
            "hvac_modes": ["auto", "heat", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 5.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.charge_level": {
            "device_class": "battery",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.target_charge": {"unit": "%", "state_class": "measurement"},
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.config_boost_temperature": {
            "min": 5.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
            "state": 21.0,
        },
        "number.config_boost_duration": {
            "min": 60,
            "max": 1440.0,
            "step": 60,
            "unit": "min",
            "device_class": "duration",
            "state": 60.0,
        },
        "number.prog_frost_temperature": {
            "min": 5.0,
            "max": 29.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 5.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 6.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.boost": {"entity_category": "config", "state": False},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
        "binary_sensor.charging": {"device_class": "battery_charging"},
    },
    "storage-0814": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 3.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.charge_level": {
            "device_class": "battery",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.target_charge": {"unit": "%", "state_class": "measurement"},
        "sensor.schedule": {"device_class": "enum"},
        "number.prog_frost_temperature": {
            "min": 3.0,
            "max": 29.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 3.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 4.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "select.storage_ui_mode": {
            "options": ["Classic combined", "Classic accumulator"],
            "state": "Classic combined",
        },
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
        "binary_sensor.charging": {"device_class": "battery_charging"},
    },
    "storage-0814-classic-flags": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 3.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.charge_level": {
            "device_class": "battery",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.target_charge": {"unit": "%", "state_class": "measurement"},
        "sensor.schedule": {"device_class": "enum"},
        "number.away_offset": {
            "min": 0.0,
            "max": 5.0,
            "step": 0.5,
            "unit": "°C",
            "state": 2.0,
        },
        "number.prog_frost_temperature": {
            "min": 3.0,
            "max": 29.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 3.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 4.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "select.storage_ui_mode": {
            "options": ["Classic combined", "Classic accumulator"],
            "state": "Classic combined",
        },
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
        "binary_sensor.charging": {"device_class": "battery_charging"},
    },
    "water-082A": {
        "climate.thermostat": {
            "hvac_modes": ["auto", "heat", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 3.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.charge_level": {
            "device_class": "battery",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.target_charge": {"unit": "%", "state_class": "measurement"},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
        "binary_sensor.water_ready": {},
        "binary_sensor.water_fully_ready": {},
    },
    "water-082A-settings": {
        "climate.thermostat": {
            "hvac_modes": ["auto", "heat", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 3.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.charge_level": {
            "device_class": "battery",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.target_charge": {"unit": "%", "state_class": "measurement"},
        "select.priority": {"options": ["low", "medium", "high"]},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
        "binary_sensor.water_ready": {},
        "binary_sensor.water_fully_ready": {},
    },
    "thermostat-0104": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "min_temp": 7.0,
            "max_temp": 35.0,
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.thermostat_schedule": {"device_class": "temperature", "unit": "C"},
        "switch.anti_grippage": {"entity_category": "config", "state": True},
        "binary_sensor.low_battery": {"device_class": "battery"},
    },
    "thm-28": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.config_boost_temperature": {
            "min": 5.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
            "state": 21.0,
        },
        "number.config_boost_duration": {
            "min": 60,
            "max": 1440.0,
            "step": 60,
            "unit": "min",
            "device_class": "duration",
            "state": 60.0,
        },
        "number.prog_frost_temperature": {
            "min": 7.0,
            "max": 34.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 7.5,
            "max": 34.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 8.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.easy_mode": {"entity_category": "config", "state": False},
        "switch.runback_mode": {"entity_category": "config", "state": False},
        "switch.lock": {"entity_category": "config", "state": False},
        "binary_sensor.window_detection": {"device_class": "window"},
        "binary_sensor.low_battery": {"device_class": "battery"},
    },
    "pmo-0103": {
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "number.node_power_limit": {
            "min": 0.0,
            "max": 60000,
            "unit": "W",
            "state": 1500,
        },
        "switch.no_node_power_limit": {"entity_category": "config", "state": False},
    },
    "base-0402-nocaps": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 1.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.schedule": {"device_class": "enum"},
        "number.prog_frost_temperature": {
            "min": 0.0,
            "max": 29.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 0.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 1.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.lock": {"entity_category": "config", "state": False},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "base-0402-boxless": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 1.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.schedule": {"device_class": "enum"},
        "number.away_offset": {
            "min": 0.0,
            "max": 5.0,
            "step": 0.5,
            "unit": "°C",
            "state": 2.0,
        },
        "number.max_temperature": {
            "min": 16.0,
            "max": 30.0,
            "step": 1.0,
            "unit": "°C",
            "device_class": "temperature",
            "state": 30.0,
        },
        "number.prog_frost_temperature": {
            "min": 0.0,
            "max": 29.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 0.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 1.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.lock": {"entity_category": "config", "state": False},
        "switch.max_temperature_limit": {"entity_category": "config", "state": True},
        "select.control_mode": {
            "options": [
                "PID (15 min. period)",
                "PID (30 min. period)",
                "Hysteresis (0.2 °C)",
                "Hysteresis (0.3 °C)",
                "Hysteresis (0.5 °C)",
                "Hysteresis (0.7 °C)",
            ],
            "state": "Hysteresis (0.2 °C)",
        },
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "helki-0132-config0": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 1.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.schedule": {"device_class": "enum"},
        "number.away_offset": {
            "min": 0.0,
            "max": 5.0,
            "step": 0.5,
            "unit": "°C",
            "state": 2.0,
        },
        "number.max_temperature": {
            "min": 16.0,
            "max": 30.0,
            "step": 1.0,
            "unit": "°C",
            "device_class": "temperature",
            "state": 30.0,
        },
        "number.prog_frost_temperature": {
            "min": 0.0,
            "max": 29.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 0.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 1.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.window_mode": {"entity_category": "config", "state": False},
        "switch.true_radiant": {"entity_category": "config", "state": False},
        "switch.max_temperature_limit": {"entity_category": "config", "state": True},
        "select.duty_limit": {"options": ["Off", "LST", "Baby Care"], "state": "Off"},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "tevolve-0134-config0": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.schedule": {"device_class": "enum"},
        "number.prog_frost_temperature": {
            "min": 7.0,
            "max": 34.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 7.5,
            "max": 34.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 8.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.easy_mode": {"entity_category": "config", "state": False},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "tevolve-0134-full-temp-and-runback": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "runback", "home"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.config_boost_temperature": {
            "min": 5.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
            "state": 21.0,
        },
        "number.config_boost_duration": {
            "min": 60,
            "max": 1440.0,
            "step": 60,
            "unit": "min",
            "device_class": "duration",
            "state": 60.0,
        },
        "number.config_runback_temperature": {
            "min": 7.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
            "state": 20.0,
        },
        "number.config_runback_duration": {
            "min": 30,
            "max": 1440.0,
            "step": 30.0,
            "unit": "min",
            "device_class": "duration",
            "state": 30.0,
        },
        "number.prog_frost_temperature": {
            "min": 7.0,
            "max": 34.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 7.5,
            "max": 34.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 8.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.easy_mode": {"entity_category": "config", "state": False},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "tevolve-0134-full-fixed-only": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.prog_frost_temperature": {
            "min": 7.0,
            "max": 34.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 7.5,
            "max": 34.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 8.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.easy_mode": {"entity_category": "config", "state": False},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "tevolve-0134-full-no-modes": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.prog_frost_temperature": {
            "min": 7.0,
            "max": 34.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 7.5,
            "max": 34.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 8.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.easy_mode": {"entity_category": "config", "state": False},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "thm-28-nopobes": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "switch.lock": {"entity_category": "config", "state": False},
        "binary_sensor.window_detection": {"device_class": "window"},
        "binary_sensor.low_battery": {"device_class": "battery"},
    },
    "haverland-011D-fw16": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 5.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.schedule": {"device_class": "enum"},
        "number.prog_frost_temperature": {
            "min": 5.0,
            "max": 7.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 7.0,
            "max": 28.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 9.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.lock": {"entity_category": "config", "state": False},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "tevolve-0127": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 7.0,
            "max_temp": 35.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.schedule": {"device_class": "enum"},
        "number.away_offset": {
            "min": 0.0,
            "max": 5.0,
            "step": 0.5,
            "unit": "°C",
            "state": 2.0,
        },
        "number.prog_eco_temperature": {
            "min": 7.5,
            "max": 31.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 11.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.easy_mode": {"entity_category": "config", "state": False},
        "switch.lock": {"entity_category": "config", "state": False},
        "binary_sensor.window_detection": {"device_class": "window"},
        "binary_sensor.pir_presence": {"device_class": "occupancy"},
    },
    "climastar-013D": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 1.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.config_boost_temperature": {
            "min": 5.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
            "state": 21.0,
        },
        "number.config_boost_duration": {
            "min": 60,
            "max": 300.0,
            "step": 60,
            "unit": "min",
            "device_class": "duration",
            "state": 60.0,
        },
        "number.max_temperature": {
            "min": 16.0,
            "max": 30.0,
            "step": 1.0,
            "unit": "°C",
            "device_class": "temperature",
            "state": 30.0,
        },
        "number.prog_frost_temperature": {
            "min": 0.0,
            "max": 29.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 0.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 1.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.window_mode": {"entity_category": "config", "state": False},
        "switch.true_radiant": {"entity_category": "config", "state": False},
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.lock": {"entity_category": "config", "state": False},
        "switch.max_temperature_limit": {"entity_category": "config", "state": True},
        "select.priority": {"options": ["low", "medium", "high"]},
        "select.control_mode": {
            "options": [
                "PID",
                "Hysteresis (0.2 °C)",
                "Hysteresis (0.3 °C)",
                "Hysteresis (0.5 °C)",
                "Hysteresis (0.7 °C)",
            ],
            "state": "Hysteresis (0.2 °C)",
        },
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "haverland-0147": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 5.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.schedule": {"device_class": "enum"},
        "number.max_temperature": {
            "min": 16.0,
            "max": 30.0,
            "step": 1.0,
            "unit": "°C",
            "device_class": "temperature",
            "state": 30.0,
        },
        "number.prog_frost_temperature": {
            "min": 5.0,
            "max": 7.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 7.0,
            "max": 28.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 9.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.max_temperature_limit": {"entity_category": "config", "state": True},
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "lhz-relay-0136-config0": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 1.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.away_offset": {
            "min": 0.0,
            "max": 5.0,
            "step": 0.5,
            "unit": "°C",
            "state": 2.0,
        },
        "number.max_temperature": {
            "min": 16.0,
            "max": 30.0,
            "step": 1.0,
            "unit": "°C",
            "device_class": "temperature",
            "state": 30.0,
        },
        "number.prog_frost_temperature": {
            "min": 0.0,
            "max": 29.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 0.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 1.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.window_mode": {"entity_category": "config", "state": False},
        "switch.true_radiant": {"entity_category": "config", "state": False},
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.max_temperature_limit": {"entity_category": "config", "state": True},
        "select.duty_limit": {
            "options": ["Off", "LST", "Baby Care", "70 %", "80 %", "90 %"],
            "state": "Off",
        },
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "lhz-wifi-0138": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "boost", "home"],
            "min_temp": 1.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.boost_end_time": {"device_class": "timestamp"},
        "sensor.schedule": {"device_class": "enum"},
        "number.config_boost_temperature": {
            "min": 5.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
            "state": 21.0,
        },
        "number.config_boost_duration": {
            "min": 60,
            "max": 1440.0,
            "step": 60,
            "unit": "min",
            "device_class": "duration",
            "state": 60.0,
        },
        "number.away_offset": {
            "min": 0.0,
            "max": 5.0,
            "step": 0.5,
            "unit": "°C",
            "state": 2.0,
        },
        "number.max_temperature": {
            "min": 16.0,
            "max": 30.0,
            "step": 1.0,
            "unit": "°C",
            "device_class": "temperature",
            "state": 30.0,
        },
        "number.prog_frost_temperature": {
            "min": 0.0,
            "max": 29.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 0.5,
            "max": 29.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 1.0,
            "max": 30.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.window_mode": {"entity_category": "config", "state": False},
        "switch.true_radiant": {"entity_category": "config", "state": False},
        "switch.boost": {"entity_category": "config", "state": False},
        "switch.max_temperature_limit": {"entity_category": "config", "state": True},
        "select.duty_limit": {
            "options": ["Off", "LST", "Baby Care", "70 %", "80 %", "90 %"],
            "state": "Off",
        },
        "select.lock_mode": {"options": ["Off", "Local lock", "Remote lock"]},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
    "tevolve-relay-011E": {
        "climate.thermostat": {
            "hvac_modes": ["heat", "auto", "off"],
            "preset_modes": ["away", "home"],
            "min_temp": 1.0,
            "max_temp": 30.0,
            "preset": "home",
        },
        "sensor.temperature": {
            "device_class": "temperature",
            "unit": "°C",
            "state_class": "measurement",
        },
        "sensor.power": {
            "device_class": "power",
            "unit": "W",
            "state_class": "measurement",
            "state": 0,
        },
        "sensor.total_consumption": {
            "device_class": "energy",
            "unit": "Wh",
            "state_class": "total_increasing",
        },
        "sensor.duty_cycle": {
            "device_class": "power_factor",
            "unit": "%",
            "state_class": "measurement",
        },
        "sensor.schedule": {"device_class": "enum"},
        "number.away_offset": {
            "min": 0.0,
            "max": 5.0,
            "step": 0.5,
            "unit": "°C",
            "state": 2.0,
        },
        "number.max_temperature": {
            "min": 16.0,
            "max": 35.0,
            "step": 1.0,
            "unit": "°C",
            "device_class": "temperature",
            "state": 30.0,
        },
        "number.prog_frost_temperature": {
            "min": 0.0,
            "max": 34.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_eco_temperature": {
            "min": 0.5,
            "max": 34.5,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "number.prog_comfort_temperature": {
            "min": 1.0,
            "max": 35.0,
            "step": 0.5,
            "unit": "°C",
            "device_class": "temperature",
        },
        "switch.lock": {"entity_category": "config", "state": False},
        "switch.max_temperature_limit": {"entity_category": "config", "state": True},
        "binary_sensor.window_detection": {"device_class": "window"},
    },
}
CASES_BY_ID = {case.id: case.values[1] for case in CASES}

STATE_CASES = [
    pytest.param(
        replace(
            CASES_BY_ID["helki-0132"],
            status={
                "units": "C",
                "stemp": "21.0",
                "boost": True,
                "boost_mode": "temp_and_time",
                "boost_end_min": 600,
                "boost_end_day": 0,
                "mode": "auto",
                "active": True,
                "power": "1500",
                "duty": "100",
            },
        ),
        {
            "climate.thermostat.preset": "boost",
            "switch.boost": True,
            "select.duty_limit": "Off",
            "sensor.boost_end_time": "<timestamp>",
            "sensor.duty_cycle": 100.0,
        },
        id="helki-boost-active",
    ),
    pytest.param(
        replace(
            CASES_BY_ID["tevolve-011A"],
            status={
                "units": "C",
                "stemp": "20.0",
                "runback": True,
                "easy": False,
                "mode": "auto",
                "active": True,
                "power": "1000",
            },
        ),
        {
            "climate.thermostat.preset": "home",
            "switch.runback_mode": True,
            "switch.easy_mode": False,
        },
        id="triac-runback-flag",
    ),
    pytest.param(
        replace(
            CASES_BY_ID["tevolve-0134-full-temp-and-runback"],
            status={
                "units": "C",
                "stemp": "20.0",
                "boost": True,
                "boost_mode": "runback",
                "boost_end_min": 600,
                "boost_end_day": 0,
                "mode": "auto",
                "boost_modes_available": ["temp_and_time", "runback"],
                "active": True,
                "power": "800",
            },
        ),
        {
            "climate.thermostat.preset": "runback",
            "switch.boost": True,
            "number.config_runback_temperature": 20.0,
            "number.config_runback_duration": 30.0,
        },
        id="full-runback-active",
    ),
    pytest.param(
        replace(
            CASES_BY_ID["storage-0814"],
            status={
                "units": "C",
                "stemp": "20.0",
                "charging": True,
                "charge_level": "50",
                "target_charge": "80",
                "mode": "auto",
                "power": "1200",
            },
        ),
        {
            "binary_sensor.charging": True,
            "sensor.charge_level": 1000,
            "select.storage_ui_mode": "Classic combined",
        },
        id="storage-charging",
    ),
]


DEVICE_ATTRIBUTES = {
    "box-with-power-limit": {
        "switch.away_status": {"state": False},
        "switch.no_power_limit": {"entity_category": "config", "state": False},
        "number.power_limit": {"min": 0.0, "max": 60000, "unit": "W", "state": 6000},
        "sensor.rtc_drift": {"unit": "s", "state_class": "measurement"},
        "binary_sensor.connected": {"device_class": "connectivity", "state": True},
    },
    "boxless": {
        "switch.away_status": {"state": False},
        "sensor.rtc_drift": {"unit": "s", "state_class": "measurement"},
        "binary_sensor.connected": {"device_class": "connectivity", "state": True},
    },
}

DEVICE_CASES = [
    pytest.param(
        True,
        {
            "switch": ["away_status", "no_power_limit"],
            "number": ["power_limit"],
            "sensor": ["rtc_drift"],
            "binary_sensor": ["connected"],
        },
        id="box-with-power-limit",
    ),
    pytest.param(
        False,
        {
            "switch": ["away_status"],
            "sensor": ["rtc_drift"],
            "binary_sensor": ["connected"],
        },
        id="boxless",
    ),
]


@pytest.mark.parametrize(("name", "node", "expected"), CASES)
def test_entity_surface_matrix(name, node, expected) -> None:
    """The domains and unique keys for one controller family."""
    assert name  # keeps the case name visible in a failure
    assert _surface(node) == expected


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        pytest.param(p.values[1], SNAPSHOTS[p.id], id=p.id)
        for p in CASES
        if p.id in SNAPSHOTS
    ],
)
def test_entity_attribute_matrix(node, expected) -> None:
    """Options, bounds and current states for representative families."""
    assert _attributes(node) == expected


@pytest.mark.parametrize(("node", "expected"), STATE_CASES)
def test_entity_state_matrix(node, expected) -> None:
    """A live frame maps to the expected presets, flags and values."""
    states = _states(node)
    for key, value in expected.items():
        assert states.get(key) == value, key


@pytest.mark.parametrize(("box_power_limit_available", "expected"), DEVICE_CASES)
def test_device_surface_matrix(box_power_limit_available, expected) -> None:
    """The box-level domains and unique keys for both box capabilities."""
    assert _device_surface(_device(box_power_limit_available)) == expected


@pytest.mark.parametrize(
    ("box_power_limit_available", "expected"),
    [(case.values[0], DEVICE_ATTRIBUTES[case.id]) for case in DEVICE_CASES],
    ids=[case.id for case in DEVICE_CASES],
)
def test_device_attribute_matrix(box_power_limit_available, expected) -> None:
    """The box-level options, bounds and states for both box capabilities."""
    device = _device(box_power_limit_available)
    assert _device_attributes(device) == expected
