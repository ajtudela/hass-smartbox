"""Tests for the part-2 adapter: fan-out routing + typed accessors.

The fan-out and accessors are HA-free (plain pytest); the entity base's
availability rendering is tested without a hass fixture (the base reads
the engine store directly and Entity construction needs no harness).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from smartbox.core.engine import SmartboxEngine
from smartbox.core.state import (
    Change,
    DeviceChange,
    DeviceRemoved,
    NodeAdded,
    NodeChange,
    NodeRemoved,
    NodeState,
    node_key,
)
from smartbox.protocol.events import Snapshot, Update
from smartbox.protocol.values import celsius_to_node_units

from custom_components.smartbox.adapter import (
    EngineChangeFanOut,
    SmartboxAdapterEntity,
    extra_options_temps_in_celsius,
    extra_temp_from_node_units,
    node_boost_params,
    node_extra_options,
    node_max_stemp_limit,
    node_power_limit,
    node_runback_limits,
    node_runback_params,
    node_setup_bool,
    node_setup_float,
    node_units,
)
from custom_components.smartbox.const import (
    CONF_DISPLAY_ENTITY_PICTURES,
    DEFAULT_BOOST_TEMP,
    DEFAULT_BOOST_TIME,
)
from custom_components.smartbox.services import ServiceTargets

DEV = "dev1"
KEY = node_key("htr", 1)


async def _run(engine: SmartboxEngine, change: Change) -> None:
    """Deliver one change through the engine's real notify path."""
    # _notify is the engine's subscriber pump; fan-out routing (not the
    # ingestion path) is under test here.
    await engine._notify([change])


# --- fan-out ---


@pytest.mark.asyncio
async def test_fanout_routes_matching_node_change() -> None:
    engine = SmartboxEngine()
    fanout = EngineChangeFanOut(engine)
    hits: list[Any] = []
    misses: list[Any] = []
    fanout.register_node(DEV, KEY, hits.append)
    fanout.register_node(DEV, node_key("htr", 2), misses.append)

    change = NodeChange(DEV, KEY, "status", NodeState())
    await _run(engine, change)

    assert hits == [change]
    assert misses == []


async def test_fanout_registration_change_during_dispatch_does_not_skip() -> None:
    """Dispatch iterates a snapshot of the registrations.

    A callback may register or unregister (an entity added or removed from a
    state-changed listener); iterating the live list would shift it under the
    loop and silently skip the next registration.
    """
    engine = SmartboxEngine()
    fanout = EngineChangeFanOut(engine)
    calls: list[str] = []
    unregister_second: list[Any] = []
    dropped: list[bool] = []

    def first(_change: Change) -> None:
        calls.append("first")
        if not dropped:
            dropped.append(True)
            unregister_second[0]()  # drops the SECOND registration mid-dispatch

    fanout.register_node(DEV, KEY, first)
    unregister_second.append(
        fanout.register_node(DEV, KEY, lambda _change: calls.append("second"))
    )

    change = NodeChange(DEV, KEY, "status", NodeState())
    await _run(engine, change)

    # the snapshot still had two entries, so the second one ran even though
    # the first callback removed it from the live list
    assert calls == ["first", "second"]

    # and a registration made during dispatch is served by the NEXT change
    fanout.register_node(DEV, KEY, lambda _change: calls.append("third"))
    await _run(engine, change)
    assert calls == ["first", "second", "first", "third"]


@pytest.mark.asyncio
async def test_fanout_device_registration_skips_node_changes() -> None:
    engine = SmartboxEngine()
    fanout = EngineChangeFanOut(engine)
    hits: list[Any] = []
    fanout.register_device(DEV, hits.append)

    node_change = NodeChange(DEV, KEY, "status", NodeState())
    dev_change = DeviceChange(DEV, "away", NodeState())
    await _run(engine, node_change)
    await _run(engine, dev_change)

    assert hits == [dev_change]


@pytest.mark.asyncio
async def test_fanout_node_registrations_receive_device_changes() -> None:
    """A node entity hears its device's changes.

    Device slots render inside node entities - climate's PRESET_AWAY reads the
    device away flag - so routing device changes to device registrations only
    left those entities stale until an unrelated node frame arrived.
    """
    engine = SmartboxEngine()
    fanout = EngineChangeFanOut(engine)
    same_device: list[Any] = []
    other_device: list[Any] = []
    device_reg: list[Any] = []
    fanout.register_node(DEV, KEY, same_device.append)
    fanout.register_node("dev2", KEY, other_device.append)
    fanout.register_device(DEV, device_reg.append)

    dev_change = DeviceChange(DEV, "away", NodeState())
    await _run(engine, dev_change)

    assert same_device == [dev_change]
    assert device_reg == [dev_change]
    assert other_device == []

    # ...and a node change still reaches only that node's registration
    same_device.clear()
    device_reg.clear()
    await _run(engine, NodeChange(DEV, KEY, "status", NodeState()))
    assert len(same_device) == 1
    assert device_reg == []  # the device registration skips node changes


@pytest.mark.asyncio
async def test_fanout_unregister_stops_delivery() -> None:
    engine = SmartboxEngine()
    fanout = EngineChangeFanOut(engine)
    hits: list[Any] = []
    unregister = fanout.register_node(DEV, KEY, hits.append)
    unregister()

    await _run(engine, NodeChange(DEV, KEY, "status", NodeState()))

    assert hits == []


@pytest.mark.asyncio
async def test_fanout_ignores_membership_changes() -> None:
    """Additions and removals are the watcher's, never entity state."""
    engine = SmartboxEngine()
    fanout = EngineChangeFanOut(engine)
    node_hits: list[Any] = []
    device_hits: list[Any] = []
    fanout.register_node(DEV, KEY, node_hits.append)
    fanout.register_device(DEV, device_hits.append)

    await _run(engine, NodeAdded(DEV, KEY))
    await _run(engine, NodeRemoved(DEV, KEY))
    await _run(engine, DeviceRemoved(DEV))

    assert node_hits == []
    assert device_hits == []


# --- typed accessors (fixture ground truth: htr_status_boost_off.json) ---


def _node_state(
    *,
    setup: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
) -> NodeState:
    return NodeState(status=status or {}, setup=setup or {}, prog={})


def test_node_units() -> None:
    assert node_units(None) is None
    assert node_units(NodeState(status={"units": "C"})) == "C"
    assert node_units(NodeState(status={"units": "F"})) == "F"
    assert node_units(NodeState(status={"units": 5})) is None  # not a string
    assert node_units(NodeState(status={})) is None


def test_node_setup_bool() -> None:
    state = _node_state(setup={"window_mode_enabled": True})
    assert node_setup_bool(state, "window_mode_enabled") is True
    assert node_setup_bool(state, "true_radiant_enabled") is None
    assert node_setup_bool(None, "window_mode_enabled") is None


def test_node_setup_float_stringified() -> None:
    state = _node_state(setup={"away_offset": "2.0", "max_stemp_limit": None})
    assert node_setup_float(state, "away_offset") == 2.0
    # None wire value tolerated (old float(None) tolerance).
    assert node_setup_float(state, "max_stemp_limit") is None
    assert node_setup_float(state, "missing") is None
    assert node_setup_float(None, "away_offset") is None


def test_node_max_stemp_limit_zero_sentinel() -> None:
    # "0.0" is the disabled sentinel -> None (api-contract live fixtures).
    assert node_max_stemp_limit(_node_state(setup={"max_stemp_limit": "0.0"})) is None
    assert node_max_stemp_limit(_node_state(setup={"max_stemp_limit": 0.0})) is None
    assert node_max_stemp_limit(_node_state(setup={"max_stemp_limit": "12.5"})) == 12.5
    assert node_max_stemp_limit(_node_state(setup={})) is None
    assert node_max_stemp_limit(None) is None


# --- boost / runback / node power-limit decoding ---


def test_node_power_limit_reads_numbers_and_strings() -> None:
    assert node_power_limit(_node_state(setup={"power_limit": 2000})) == 2000
    assert node_power_limit(_node_state(setup={"power_limit": "4000"})) == 4000
    assert node_power_limit(_node_state(setup={"power_limit": "0"})) == 0
    # Malformed server data reads as unknown: entity properties never raise.
    assert node_power_limit(_node_state(setup={"power_limit": "junk"})) is None
    assert node_power_limit(_node_state(setup={"power_limit": None})) is None
    assert node_power_limit(None) is None


def test_node_extra_options_reads_only_objects() -> None:
    options = {"version": 2, "boost_temp": 21.0}
    assert node_extra_options(_node_state(setup={"extra_options": options})) == options
    assert node_extra_options(_node_state(setup={"extra_options": "junk"})) == {}
    assert node_extra_options(_node_state(setup={})) == {}
    assert node_extra_options(None) == {}


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (2, True),
        (2.0, True),
        ("2", True),
        ("2.5", True),
        (1, False),
        ("1.9", False),
        (True, False),
        (None, False),
        ("junk", False),
        # Non-finite versions are malformed, not a Celsius marker.
        (float("inf"), False),
        (float("nan"), False),
    ],
)
def test_extra_options_temps_in_celsius_reads_the_version(version, expected) -> None:
    state = _node_state(setup={"extra_options": {"version": version}})
    assert extra_options_temps_in_celsius(state) is expected
    assert extra_options_temps_in_celsius(None) is False


def test_node_boost_params_fall_back_to_the_shared_defaults() -> None:
    assert node_boost_params(_node_state()) == (
        DEFAULT_BOOST_TEMP,
        float(DEFAULT_BOOST_TIME),
    )
    fahrenheit = _node_state(status={"units": "F"})
    assert node_boost_params(fahrenheit) == (
        celsius_to_node_units(DEFAULT_BOOST_TEMP, "F"),
        float(DEFAULT_BOOST_TIME),
    )


def test_node_boost_params_tolerate_malformed_wire_values() -> None:
    state = _node_state(
        setup={"extra_options": {"boost_temp": "junk", "boost_time": "junk"}}
    )
    assert node_boost_params(state) == (None, None)


def test_extra_temp_from_node_units_encodes_version_two_as_celsius() -> None:
    """The write side mirrors node_boost_params for version >= 2."""
    celsius = _node_state(status={"units": "C"})
    fahrenheit_v2 = _node_state(
        status={"units": "F"}, setup={"extra_options": {"version": 2}}
    )
    fahrenheit_legacy = _node_state(
        status={"units": "F"}, setup={"extra_options": {"version": 1}}
    )
    assert extra_temp_from_node_units(celsius, 21.0) == 21.0
    assert extra_temp_from_node_units(fahrenheit_v2, 70.0) == 21.0
    assert extra_temp_from_node_units(fahrenheit_legacy, 70.0) == 70.0
    assert extra_temp_from_node_units(None, 21.0) == 21.0


def test_node_boost_temp_converts_celsius_extra_options_to_node_units() -> None:
    state = _node_state(
        status={"units": "F"},
        setup={"extra_options": {"version": 2, "boost_temp": 21.0, "boost_time": 90}},
    )
    assert node_boost_params(state) == (celsius_to_node_units(21.0, "F"), 90.0)


def test_node_runback_params_fall_back_to_status_and_defaults() -> None:
    assert node_runback_params(_node_state(status={"stemp": "19.0"})) == (
        19.0,
        float(DEFAULT_BOOST_TIME),
    )
    assert node_runback_params(None) == (None, float(DEFAULT_BOOST_TIME))


def test_node_runback_params_read_extra_options_and_tolerate_malformed() -> None:
    configured = _node_state(
        setup={"extra_options": {"default_runback_temp": "21.5", "boost_time": 45}}
    )
    assert node_runback_params(configured) == (21.5, 45.0)
    malformed = _node_state(
        status={"stemp": "junk"},
        setup={"extra_options": {"boost_time": "junk"}},
    )
    assert node_runback_params(malformed) == (None, None)


def test_node_runback_limits_prefer_extra_options_then_setup() -> None:
    configured = _node_state(
        status={"ice_temp": "7.0"},
        setup={
            "extra_options": {"setback_temp": "12.0", "max_runback_temp": "25.0"},
            "max_runback_temp": "30.0",
        },
    )
    assert node_runback_limits(configured) == (12.0, 25.0)
    fallback = _node_state(
        status={"ice_temp": "7.0"}, setup={"max_runback_temp": "30.0"}
    )
    assert node_runback_limits(fallback) == (7.0, 30.0)


def test_node_runback_limits_tolerate_malformed_wire_values() -> None:
    malformed = _node_state(
        status={"ice_temp": "junk"}, setup={"max_runback_temp": "junk"}
    )
    assert node_runback_limits(malformed) == (None, None)
    assert node_runback_limits(None) == (None, None)


# --- entity base availability rendering (no hass fixture needed) ---


def _entity(engine: SmartboxEngine) -> SmartboxAdapterEntity:
    runtime = SimpleNamespace(engine=engine, services=ServiceTargets())
    return SmartboxAdapterEntity(runtime, EngineChangeFanOut(engine), DEV, KEY)


def _device_entity(engine: SmartboxEngine) -> SmartboxAdapterEntity:
    runtime = SimpleNamespace(engine=engine, services=ServiceTargets())
    return SmartboxAdapterEntity(runtime, EngineChangeFanOut(engine), DEV)


def test_device_level_entity_reads_no_node_state() -> None:
    """The base addresses a node only when it has a node key."""
    entity = _device_entity(SmartboxEngine())
    assert entity._node_state() is None
    assert entity._device_state() is None
    assert entity.available is False


async def test_entity_removal_without_registration_is_a_no_op() -> None:
    """Unloading an entity that never subscribed must not raise."""
    await _entity(SmartboxEngine()).async_will_remove_from_hass()


def test_display_entity_picture_option_sets_the_reseller_icon() -> None:
    entity = _entity(SmartboxEngine())
    entry = SimpleNamespace(options={CONF_DISPLAY_ENTITY_PICTURES: True})
    reseller = SimpleNamespace(web_url="https://portal.test/")

    entity._apply_display_entity_picture(entry, reseller)

    assert entity.entity_picture == "https://portal.test/img/favicon.ico"


def test_entity_availability_unknown_renders_unavailable() -> None:
    engine = SmartboxEngine()
    entity = _entity(engine)
    assert entity.available is False


async def test_entity_availability_follows_node_state() -> None:
    engine = SmartboxEngine()
    entity = _entity(engine)
    # Snapshot ingestion leaves the availability slot UNKNOWN (no status
    # in the node entry) -> the entity renders unavailable.
    await engine.handle_event(Snapshot(payload=_snapshot_with_node()))
    node = engine.node(DEV, KEY)
    assert node is not None  # guard: the fixture must create the node
    assert node.available is None  # unknown, not False: nobody said "lost"
    assert entity.available is False
    # A non-lost status frame marks the node available immediately
    # (public ingestion path - no store poke needed).
    await engine.handle_event(
        Update(dev_id=DEV, path="/htr/1/status", body={"mtemp": "190"}),
    )
    assert entity.available is True


def _snapshot_with_node() -> list[dict[str, Any]]:
    """Minimal all_data nesting holding just the DEV/KEY node.

    The node entry uses the WIRE key ``type``: the engine reads ``type``/``addr``,
    so a fixture with the wrong key creates no node and the availability
    assertions below cannot fail.
    """
    return [
        {
            "id": "h1",
            "devs": [
                {
                    "dev_id": DEV,
                    "devData": {
                        "nodes": [{"type": "htr", "addr": 1}],
                    },
                },
            ],
        },
    ]
