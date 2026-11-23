"""Thermostat (``thm``) climate property and write-path tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.components.climate.const import DEFAULT_MAX_TEMP, DEFAULT_MIN_TEMP
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_conversion import TemperatureConverter
import pytest
from smartbox.core.engine import SmartboxEngine
from smartbox.core.state import DeviceState, NodeState, WorldState, node_key
from smartbox.protocol import NodeRef

from custom_components.smartbox.climate import (
    SmartboxHeater,
    SmartboxThermostat,
    _build_node_climate,
)
from custom_components.smartbox.sensor import ThermostatScheduleSensor
from custom_components.smartbox.switch import AntiGrippageSwitch
from tests.mocks import get_entity_id_from_unique_id
from tests.stack import seed_stack

DEV = "device_1"
ADDR = 0

STATUS = {
    "sync_status": "ok",
    "low_batt": False,
    "mode": "off",
    "active": False,
    "units": "C",
    "stemp": "10.0",
    "mtemp": "19.8",
    "locked": False,
}

SETUP = {
    "type": "heat",
    "control_mode": 2,
    "units": "C",
    "away_offset": "0.0",
}


class _NoopFanout:
    """Fan-out double for property tests (no engine subscription)."""

    def register_node(self, *_args):
        return lambda: None


class _Host:
    """Write host double: records posts and serves the GET half of a write."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    async def post(self, path: str, body: dict) -> None:
        self.posts.append((path, dict(body)))

    async def get_node_status(self, dev_id: str, node) -> dict:  # noqa: ARG002
        return {}

    async def get_node_setup(self, dev_id: str, node) -> dict:  # noqa: ARG002
        return {"units": "C"}

    async def get_node_prog(self, dev_id: str, node) -> dict:  # noqa: ARG002
        return {"prog": {}}


def _node() -> MagicMock:
    """Build a wire-faithful thermostat node stub for platform-level tests."""
    node = MagicMock()
    node.node_type = "thm"
    node.name = "Thermostat"
    node.node_id = f"{DEV}_{ADDR}"
    node.addr = ADDR
    node.dev_id = DEV
    node.setup = dict(SETUP)
    node.reseller = SimpleNamespace(web_url="https://web_url/", name="Test API name")
    node.home_id = "home_1"
    node.pid = "0109"
    node.hw_version = "hw"
    node.supported_wire_type = True
    node.heater_node = False
    node.boost_available = False
    return node


def _thermostat(status=None, setup=None, *, host=None) -> SmartboxThermostat:
    """Build a thermostat over a hand-built engine world."""
    status = dict(STATUS if status is None else status)
    setup = dict(SETUP if setup is None else setup)
    engine = SmartboxEngine(host or _Host())
    key = node_key("thm", ADDR)
    device = DeviceState(
        dev_id=DEV,
        away={},
        node_states={key: NodeState(status=status, setup=setup)},
    )
    object.__setattr__(engine, "_world", WorldState(devices={DEV: device}))
    node = _node()
    node.setup = setup
    return SmartboxThermostat(
        node, MagicMock(), SimpleNamespace(engine=engine), _NoopFanout()
    )


def test_climate_platform_builds_a_thermostat() -> None:
    """The climate platform chooses SmartboxThermostat for a thm node."""
    runtime = SimpleNamespace(services=SimpleNamespace(climate=[]))
    groups = _build_node_climate(_node(), MagicMock(), runtime, _NoopFanout())
    entity = groups[0][0][0]
    assert isinstance(entity, SmartboxThermostat)
    assert runtime.services.climate == [entity]


def test_climate_platform_builds_a_heater_for_thm_28() -> None:
    """PID 28 is the htr-family floor thermostat and uses the heater surface."""
    node = _node()
    node.pid = "0128"
    node.thermostat_surface = False
    node.heater_surface = True
    node.boost_available = False
    runtime = SimpleNamespace(services=SimpleNamespace(climate=[]))
    groups = _build_node_climate(node, MagicMock(), runtime, _NoopFanout())
    entity = groups[0][0][0]
    assert isinstance(entity, SmartboxHeater)
    assert not isinstance(entity, SmartboxThermostat)


def test_off_thermostat_has_no_target_and_no_presets() -> None:
    thermostat = _thermostat()
    assert thermostat.hvac_modes == [HVACMode.HEAT, HVACMode.AUTO, HVACMode.OFF]
    assert thermostat.hvac_mode == HVACMode.OFF
    assert thermostat.hvac_action == HVACAction.OFF
    assert thermostat.target_temperature is None
    assert thermostat.current_temperature == 19.8
    assert thermostat.min_temp == 7
    assert thermostat.max_temp == 35
    assert thermostat.preset_modes == []
    assert thermostat.preset_mode is None


def test_manual_heat_thermostat() -> None:
    thermostat = _thermostat(
        {**STATUS, "mode": "manual", "stemp": "20.5", "active": True}
    )
    assert thermostat.hvac_mode == HVACMode.HEAT
    assert thermostat.hvac_action == HVACAction.HEATING
    assert thermostat.target_temperature == 20.5
    attributes = thermostat.extra_state_attributes
    assert attributes["load_type"] == "heat"
    assert "locked" not in attributes
    assert "low_battery" not in attributes


def test_cold_load_reports_cool() -> None:
    thermostat = _thermostat(
        {**STATUS, "mode": "manual", "active": True},
        {**SETUP, "type": "cold"},
    )
    assert thermostat.hvac_modes == [HVACMode.COOL, HVACMode.AUTO, HVACMode.OFF]
    assert thermostat.hvac_mode == HVACMode.COOL
    assert thermostat.hvac_action == HVACAction.COOLING
    assert thermostat.extra_state_attributes["load_type"] == "cold"


def test_auto_thermostat_is_idle_when_not_active() -> None:
    thermostat = _thermostat({**STATUS, "mode": "auto"})
    assert thermostat.hvac_mode == HVACMode.AUTO
    assert thermostat.hvac_action == HVACAction.IDLE


def test_auto_thermostat_is_heating_when_active() -> None:
    thermostat = _thermostat({**STATUS, "mode": "auto", "active": True})
    assert thermostat.hvac_action == HVACAction.HEATING


def test_auto_thermostat_truthy_non_boolean_active_is_not_heating() -> None:
    thermostat = _thermostat({**STATUS, "mode": "auto", "active": 1})
    assert thermostat.hvac_action == HVACAction.IDLE


def test_thermostat_range_converts_for_fahrenheit() -> None:
    thermostat = _thermostat({**STATUS, "units": "F", "stemp": "68.0", "mtemp": "70.0"})
    assert thermostat.min_temp == TemperatureConverter.convert(
        DEFAULT_MIN_TEMP, UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT
    )
    assert thermostat.max_temp == TemperatureConverter.convert(
        DEFAULT_MAX_TEMP, UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT
    )
    assert thermostat.target_temperature_step == 1.0


async def test_setpoint_and_mode_writes_use_the_thm_status_path() -> None:
    host = _Host()
    thermostat = _thermostat({**STATUS, "mode": "manual", "active": True}, host=host)
    try:
        await thermostat.async_set_temperature(temperature=21.5)
        await thermostat.async_set_hvac_mode(HVACMode.COOL)

        assert host.posts[0][0].endswith(f"devs/{DEV}/thm/{ADDR}/status")
        assert host.posts[0][1] == {"stemp": "21.5", "units": "C"}
        assert host.posts[1][0].endswith(f"devs/{DEV}/thm/{ADDR}/status")
        assert host.posts[1][1] == {"mode": "manual"}
    finally:
        await thermostat._runtime.engine.stop()


async def test_turn_off_writes_off() -> None:
    host = _Host()
    thermostat = _thermostat({**STATUS, "mode": "auto", "active": True}, host=host)
    try:
        await thermostat.async_turn_off()
        assert host.posts[-1][1] == {"mode": "off"}
    finally:
        await thermostat._runtime.engine.stop()


async def test_turn_on_writes_auto_like_the_app_on_mode() -> None:
    host = _Host()
    thermostat = _thermostat({**STATUS, "mode": "off", "active": False}, host=host)
    try:
        await thermostat.async_turn_on()
        # ThermostatController's ON maps to getDefaultMode(); AUTO is offered.
        assert host.posts[-1][1] == {"mode": "auto"}
    finally:
        await thermostat._runtime.engine.stop()


async def test_turn_on_cold_load_writes_auto() -> None:
    host = _Host()
    thermostat = _thermostat(
        {**STATUS, "mode": "off", "active": False},
        {**SETUP, "type": "cold"},
        host=host,
    )
    try:
        await thermostat.async_turn_on()
        assert host.posts[-1][1] == {"mode": "auto"}
    finally:
        await thermostat._runtime.engine.stop()


async def test_set_temperature_without_a_value_is_a_noop() -> None:
    host = _Host()
    thermostat = _thermostat(host=host)
    try:
        await thermostat.async_set_temperature()
        assert host.posts == []
    finally:
        await thermostat._runtime.engine.stop()


def test_load_type_falls_back_to_the_discovery_setup() -> None:
    thermostat = _thermostat(setup={})
    thermostat._node.setup = {"type": "cold"}
    assert thermostat._load_type == "cold"


async def test_modified_auto_span_does_not_change_the_thm_setpoint_body() -> None:
    host = _Host()
    thermostat = _thermostat(
        {**STATUS, "mode": "auto", "active": False},
        {**SETUP, "modified_auto_span": 30},
        host=host,
    )
    try:
        await thermostat.async_set_temperature(temperature=21.0)
        assert host.posts[-1][1] == {"stemp": "21.0", "units": "C"}
    finally:
        await thermostat._runtime.engine.stop()


def test_blank_status_has_no_action_or_attributes() -> None:
    thermostat = _thermostat({})
    assert thermostat.hvac_action is None
    assert thermostat.extra_state_attributes == {}


def test_optional_attributes_and_control_mode() -> None:
    status = {
        key: value for key, value in STATUS.items() if key not in ("locked", "low_batt")
    }
    thermostat = _thermostat(status, {**SETUP, "control_mode": 2})
    assert thermostat.extra_state_attributes == {
        "load_type": "heat",
        "control_mode": 2,
    }


async def test_preset_mode_is_rejected() -> None:
    thermostat = _thermostat()
    with pytest.raises(HomeAssistantError) as excinfo:
        await thermostat.async_set_preset_mode("eco")
    assert excinfo.value.translation_key == "preset_mode_unsupported"


def test_extra_attributes_without_control_mode() -> None:
    setup = {key: value for key, value in SETUP.items() if key != "control_mode"}
    thermostat = _thermostat({**STATUS, "mode": "manual"}, setup)
    attributes = thermostat.extra_state_attributes
    assert attributes["load_type"] == "heat"
    assert "control_mode" not in attributes


@pytest.fixture
def units() -> str:
    """Pin the platform test world to Celsius."""
    return "C"


THM_ADDR = 2


def _add_thm_node(mock_smartbox) -> None:
    """Add a thm node to the mock world before config-entry setup."""
    mock_smartbox.add_node(
        "device_1",
        {
            "type": "thm",
            "addr": THM_ADDR,
            "name": "Thermostat",
            "installed": True,
            "product_id": "product_id_thm",
            "fw_version": "1.0",
            "serial_id": "serial_id_thm",
        },
        setup={
            "type": "heat",
            "control_mode": 2,
            "units": "C",
            "away_offset": "0.0",
            # Heater-only keys the thermostat write path must ignore.
            "modified_auto_span": 30,
            "away_mode": 0,
            "factory_options": {"boost_config": 3},
        },
        status={
            "sync_status": "ok",
            "mode": "off",
            "active": False,
            "units": "C",
            "stemp": "10.0",
            "mtemp": "19.8",
            "locked": False,
            "low_batt": False,
        },
        prog={},
    )


async def test_thermostat_platform_entity_writes_and_entity_shape(
    hass, mock_smartbox, config_entry
):
    """The HA entity uses the thm status path and skips heater-only entities."""
    _add_thm_node(mock_smartbox)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)

    entity_id = get_entity_id_from_unique_id(
        hass, "climate", f"device_1_{THM_ADDR}_thermostat"
    )
    state = hass.states.get(entity_id)
    assert state.state == "off"
    assert state.attributes["hvac_modes"] == ["heat", "auto", "off"]
    assert state.attributes["current_temperature"] == 19.8

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            "sensor", "smartbox", f"device_1_{THM_ADDR}_schedule"
        )
        is None
    )
    assert (
        registry.async_get_entity_id(
            "sensor", "smartbox", f"device_1_{THM_ADDR}_boost_end_time"
        )
        is None
    )
    assert (
        registry.async_get_entity_id("switch", "smartbox", f"device_1_{THM_ADDR}_boost")
        is None
    )
    assert (
        registry.async_get_entity_id(
            "number", "smartbox", f"device_1_{THM_ADDR}_config_boost_temperature"
        )
        is None
    )
    assert (
        registry.async_get_entity_id(
            "number", "smartbox", f"device_1_{THM_ADDR}_config_boost_duration"
        )
        is None
    )
    assert get_entity_id_from_unique_id(
        hass, "binary_sensor", f"device_1_{THM_ADDR}_low_battery"
    )

    # The heater schedule service must reject a thermostat target.
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "smartbox",
            "set_schedule",
            {ATTR_ENTITY_ID: [entity_id], "schedule": {"0": [1] * 24}},
            blocking=True,
        )
    assert err.value.translation_key == "schedule_wrong_target"

    await hass.services.async_call(
        "climate",
        "turn_on",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    posts = [(path, body) for method, path, body in fake.requests if method == "POST"]
    assert posts[-1][0].endswith(f"/thm/{THM_ADDR}/status")
    assert posts[-1][1] == {"mode": "auto"}

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: 21.0},
        blocking=True,
    )
    posts = [(path, body) for method, path, body in fake.requests if method == "POST"]
    assert posts[-1][0].endswith(f"/thm/{THM_ADDR}/status")
    assert posts[-1][1] == {"stemp": "21.0", "units": "C"}


async def test_boost_mode_skips_thermostats_in_a_mixed_target(
    hass, mock_smartbox, config_entry
):
    """A device target with a full-boost heater and a thermostat writes the heater."""
    mock_smartbox._node_info["device_1"][0]["product_id"] = "01340000"
    mock_smartbox._node_setup["device_1"][0]["factory_options"]["boost_config"] = 3
    _add_thm_node(mock_smartbox)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, _fake = await seed_stack(hass, mock_smartbox)

    heater_id = get_entity_id_from_unique_id(hass, "climate", "device_1_0_thermostat")
    thermostat_id = get_entity_id_from_unique_id(
        hass, "climate", f"device_1_{THM_ADDR}_thermostat"
    )
    entities = {entity.entity_id: entity for entity in runtime.services.climate}
    heater = entities[heater_id]
    thermostat = entities[thermostat_id]
    heater.async_set_boost_mode = AsyncMock()
    thermostat.async_set_boost_mode = AsyncMock()

    await hass.services.async_call(
        "smartbox",
        "set_boost_mode",
        {ATTR_ENTITY_ID: [heater_id, thermostat_id], "mode": "runback"},
        blocking=True,
    )
    heater.async_set_boost_mode.assert_awaited_once()
    thermostat.async_set_boost_mode.assert_not_awaited()


def _thermostat_schedule_sensor(prog):
    sensor = object.__new__(ThermostatScheduleSensor)
    sensor._node = SimpleNamespace(
        node_type="thm",
        addr=0,
        pid="0109",
        version={"fw_version": "1.9"},
        setup={"units": "C"},
        status={},
    )
    sensor._node_state = lambda: NodeState(prog=prog, status={"units": "C"})
    sensor._dev_id = DEV
    sensor._runtime = MagicMock()
    sensor._unsub_boundary = None
    return sensor


def test_thermostat_schedule_state_uses_the_current_segment(monkeypatch):
    """The segment end is m; the state is the temperature in force now."""
    prog = {"1": [{"m": 360, "t": "19.0"}, {"m": 1320, "t": "17.0"}]}
    sensor = _thermostat_schedule_sensor(prog)
    monkeypatch.setattr(
        "custom_components.smartbox.sensor.dt_util.now",
        lambda: datetime(2026, 11, 23, 8, 0, tzinfo=UTC),  # Monday 08:00
    )
    assert sensor.native_value == 17.0
    assert sensor.extra_state_attributes["schedule"] == prog
    monkeypatch.setattr(
        "custom_components.smartbox.sensor.dt_util.now",
        lambda: datetime(2026, 11, 23, 2, 0, tzinfo=UTC),
    )
    assert sensor.native_value == 19.0
    monkeypatch.setattr(
        "custom_components.smartbox.sensor.dt_util.now",
        lambda: datetime(2026, 11, 23, 23, 0, tzinfo=UTC),
    )
    assert sensor.native_value == 17.0


def test_thermostat_schedule_accepts_numeric_string_minutes(monkeypatch):
    """A server-reformatted ``m`` string still drives the state."""
    sensor = _thermostat_schedule_sensor({"1": [{"m": "360", "t": "19.0"}]})
    monkeypatch.setattr(
        "custom_components.smartbox.sensor.dt_util.now",
        lambda: datetime(2026, 11, 23, 2, 0, tzinfo=UTC),
    )
    assert sensor.native_value == 19.0


def test_thermostat_schedule_before_first_segment_uses_first_temperature(monkeypatch):
    """At 02:00 the 00:00-06:00 segment applies, not yesterday's last one."""
    sensor = _thermostat_schedule_sensor(
        {"1": [{"m": 360, "t": "19.0"}, {"m": 1320, "t": "17.0"}]}
    )
    monkeypatch.setattr(
        "custom_components.smartbox.sensor.dt_util.now",
        lambda: datetime(2026, 11, 23, 5, 59, tzinfo=UTC),
    )
    assert sensor.native_value == 19.0
    monkeypatch.setattr(
        "custom_components.smartbox.sensor.dt_util.now",
        lambda: datetime(2026, 11, 23, 6, 0, tzinfo=UTC),
    )
    assert sensor.native_value == 17.0


def test_thermostat_schedule_1440_boundary_is_next_midnight(monkeypatch):
    """A 1440 segment ends at midnight rather than constructing hour 24."""
    sensor = _thermostat_schedule_sensor({"1": [{"m": 1440, "t": "19.0"}]})
    now = datetime(2026, 11, 23, 8, 0, tzinfo=UTC)
    assert sensor._next_change(now) == datetime(2026, 11, 24, 0, 0, tzinfo=UTC)


def test_thermostat_schedule_empty_day_falls_back_to_previous_day(monkeypatch):
    """A day with no segments keeps the previous day's final temperature."""
    sensor = _thermostat_schedule_sensor({"0": [{"m": 1320, "t": "15.0"}], "1": []})
    monkeypatch.setattr(
        "custom_components.smartbox.sensor.dt_util.now",
        lambda: datetime(2026, 11, 23, 2, 0, tzinfo=UTC),
    )
    assert sensor.native_value == 15.0


async def test_thermostat_schedule_write_uses_the_thermostat_op():
    prog = {"1": [{"m": 360, "t": 19.0}]}
    sensor = _thermostat_schedule_sensor(prog)
    sensor._runtime.engine.set_thermostat_prog = AsyncMock()
    sensor.async_write_ha_state = MagicMock()
    await sensor.async_set_thermostat_schedule(prog)
    sensor._runtime.engine.set_thermostat_prog.assert_awaited_once_with(
        DEV, NodeRef("thm", "0"), prog, "C"
    )


async def test_anti_grippage_switch_reads_and_writes_the_setup_string():
    switch = object.__new__(AntiGrippageSwitch)
    switch._node = SimpleNamespace(setup={"anti_grippage": "on"})
    switch._node_state = lambda: NodeState(setup={"anti_grippage": "on"})
    switch._dev_id = DEV
    switch._ref = NodeRef("thm", "0")
    switch._runtime = MagicMock()
    switch._runtime.engine.set_setup_value = AsyncMock()
    assert switch.is_on is True
    await switch.async_turn_off()
    switch._runtime.engine.set_setup_value.assert_awaited_once_with(
        DEV, NodeRef("thm", "0"), "anti_grippage", "off"
    )


def test_thermostat_schedule_rearms_on_a_pushed_change():
    """A programme frame re-arms the boundary timer."""
    sensor = _thermostat_schedule_sensor({"1": [{"m": 360, "t": "19.0"}]})
    sensor._track_next_change = MagicMock()
    sensor.async_write_ha_state = MagicMock()
    sensor._async_change(MagicMock())
    sensor._track_next_change.assert_called_once_with()


def test_thermostat_schedule_arms_next_midnight_for_the_new_day():
    """A new day's first segment takes force at midnight."""
    sensor = _thermostat_schedule_sensor(
        {"0": [{"m": 1320, "t": "17.0"}], "1": [{"m": 360, "t": "19.0"}]}
    )
    now = datetime(2026, 11, 22, 23, 0, tzinfo=UTC)  # Sunday 23:00
    assert sensor._next_change(now) == datetime(2026, 11, 23, 0, 0, tzinfo=UTC)
    monkeypatch_now = datetime(2026, 11, 23, 0, 5, tzinfo=UTC)
    assert sensor._scheduled_temperature(monkeypatch_now) == 19.0
