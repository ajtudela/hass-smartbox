"""Climate platform.

The mock world is delivered to the engine as a ws snapshot through the
runtime's FakeTransport, and writes go through a FakeSmartbox write host
(the fake stack's in-memory server whose request log is observable).
User-visible state is asserted through the entity states, with the
confirming ok frames a live device pushes delivered explicitly (the fake
pushes none itself).

Assertion scope: internal-cache assertions are intentionally absent (the
store is the cache), the setpoint quantization is floor (not round, which
the wire's device-side quantization matches), and availability after a
write follows the write-confirmation rule (a node whose confirming frame
never arrives goes Unavailable).
"""

from __future__ import annotations

import logging
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.climate import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.components.climate.const import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    DOMAIN as CLIMATE_DOMAIN,
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_HOME,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_LOCKED,
    ATTR_TEMPERATURE,
    ENTITY_MATCH_ALL,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_UNAVAILABLE,
    UnitOfTemperature,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_component import async_update_entity
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter
import pytest
from smartbox.core.engine import SmartboxEngine
from smartbox.core.state import NodeState, node_key
from smartbox.protocol.client import NodeRef
from smartbox.protocol.events import Snapshot, Update
from smartbox.protocol.writes import VALID_BOOST_MODES

from custom_components.smartbox.adapter import node_boost_params
from custom_components.smartbox.boost_prefs import NodeBoostPrefs
from custom_components.smartbox.climate import SmartboxHeater
from custom_components.smartbox.const import (
    DOMAIN,
    PRESET_ADVANCED_SLOT,
    PRESET_FROST,
    PRESET_RUNBACK,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
    SERVICE_SET_BOOST_MODE,
    SmartboxNodeType,
)
from custom_components.smartbox.mapping import _warn_unknown, get_hvac_mode
from tests.mocks import (
    get_away_status_switch_entity_id,
    get_climate_entity_id,
    get_climate_entity_name,
    get_entity_id_from_unique_id,
    get_node_unique_id,
    get_object_id,
    is_heater_node,
)
from tests.stack import (
    NoopFanout,
    confirm_write,
    engine_with_node,
    push_status,
    ref,
    seed_stack,
)
from tests.test_utils import (
    armed_deadlines,
    assert_no_log_errors,
    convert_temp,
    max_limit_value,
    round_temp,
    until,
)

_LOGGER = logging.getLogger(__name__)

DEV = "device_1"
KEY = node_key("htr", 0)

# Literal wire mode -> HA state, deliberately NOT the production mapping: a
# test that computes its expectation with the function under test cannot
# fail when that function is wrong.
_HVAC_STATE_FOR_MODE = {
    "off": "off",
    "manual": "heat",
    "auto": "auto",
    "modified_auto": "auto",
    "self_learn": "auto",
    "presence": "auto",
}


@pytest.fixture
async def smartbox_stack(hass, mock_smartbox, config_entry):
    """Set up a loaded entry with the engine store seeded."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return await seed_stack(hass, mock_smartbox)


@pytest.mark.parametrize(("locked", "expected"), [(0, False), (1, True)])
def test_locked_attribute_normalizes_int(locked, expected):
    """An acm-style 0/1 locked value becomes a real bool on climate."""
    heater = object.__new__(SmartboxHeater)
    heater._node = SimpleNamespace(node_type=SmartboxNodeType.HTR)
    heater._node_state = lambda: NodeState(status={"locked": locked})
    assert heater.extra_state_attributes[ATTR_LOCKED] is expected


def _check_state(hass, mock_node, mock_node_status, state):
    assert state.state == get_hvac_mode(mock_node["type"], mock_node_status)
    assert state.attributes[ATTR_LOCKED] is bool(mock_node_status["locked"])
    if (
        mock_node["type"] == SmartboxNodeType.HTR_MOD
        and "selected_temp" in mock_node_status
    ):
        assert state.attributes["selected_temp"] == mock_node_status["selected_temp"]
    else:
        assert "selected_temp" not in state.attributes

    assert round_temp(hass, state.attributes[ATTR_CURRENT_TEMPERATURE]) == round_temp(
        hass,
        convert_temp(hass, mock_node_status["units"], float(mock_node_status["mtemp"])),
    )
    # ATTR_TEMPERATURE actually stores the target temperature. htr_mod AUTO
    # derives it from the programme (covered by its own test); this helper
    # only has the status frame, so skip that one comparison.
    if (
        mock_node["type"] == SmartboxNodeType.HTR_MOD
        and mock_node_status.get("mode") == "auto"
    ):
        return
    if mock_node["type"] == SmartboxNodeType.HTR_MOD:
        if mock_node_status["selected_temp"] == "comfort":
            target_temp = float(mock_node_status["comfort_temp"])
        elif mock_node_status["selected_temp"] == "eco":
            target_temp = float(mock_node_status["comfort_temp"]) - float(
                mock_node_status["eco_offset"]
            )
        elif mock_node_status["selected_temp"] == "ice":
            target_temp = float(mock_node_status["ice_temp"])
        else:
            msg = f"Unknown selected_temp value {mock_node_status['selected_temp']}"
            raise ValueError(msg)
    else:
        target_temp = float(mock_node_status["stemp"])
    assert round_temp(hass, state.attributes[ATTR_TEMPERATURE]) == round_temp(
        hass,
        convert_temp(hass, mock_node_status["units"], target_temp),
    )


async def test_basic_climate(hass, smartbox_stack, mock_smartbox, caplog):
    runtime, fake = smartbox_stack
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        dev_id = mock_device["dev_id"]
        for mock_node in await mock_smartbox.session.get_nodes(dev_id):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)
            state = hass.states.get(entity_id)

            # check basic properties
            assert state.object_id.startswith(
                get_object_id(get_climate_entity_name(mock_node))
            )
            unique_id = get_node_unique_id(mock_device, mock_node, "thermostat")
            assert entity_id == get_entity_id_from_unique_id(
                hass, CLIMATE_DOMAIN, unique_id
            )
            assert state.name == mock_node["name"]
            assert state.attributes[ATTR_FRIENDLY_NAME] == mock_node["name"]

            mock_node_status = await fake.get_node_status(dev_id, ref(mock_node))
            _check_state(hass, mock_node, mock_node_status, state)

            # the transport is supervised (the old socket.run await)
            assert runtime.engine.supervision_task is not None

            # mtemp bump arrives as a websocket status frame
            frame = dict(mock_node_status)
            frame["mtemp"] = str(float(mock_node_status["mtemp"]) + 1)
            await push_status(hass, runtime, dev_id, mock_node, frame)
            new_state = hass.states.get(entity_id)
            assert (
                new_state.attributes[ATTR_CURRENT_TEMPERATURE]
                != state.attributes[ATTR_CURRENT_TEMPERATURE]
            )
            _check_state(hass, mock_node, frame, new_state)

    # Make sure we don't log any errors during setup
    assert_no_log_errors(caplog)


async def test_unavailable(hass, smartbox_stack, mock_smartbox):
    runtime, fake = smartbox_stack
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        dev_id = mock_device["dev_id"]
        for mock_node in await mock_smartbox.session.get_nodes(dev_id):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)
            state = hass.states.get(entity_id)

            mock_node_status = await fake.get_node_status(dev_id, ref(mock_node))
            _check_state(hass, mock_node, mock_node_status, state)

            # lost frame with no ok follow-up within the (patched) window
            await push_status(hass, runtime, dev_id, mock_node, {"sync_status": "lost"})
            await until(
                lambda eid=entity_id: hass.states.get(eid).state == STATE_UNAVAILABLE
            )
            state = hass.states.get(entity_id)
            assert state.state == STATE_UNAVAILABLE

            # recovery: an ok frame (mtemp bumped, like the old helper)
            ok_status = await fake.get_node_status(dev_id, ref(mock_node))
            ok_status = dict(ok_status)
            ok_status["mtemp"] = str(float(ok_status["mtemp"]) + 1)
            await push_status(hass, runtime, dev_id, mock_node, ok_status)
            state = hass.states.get(entity_id)
            _check_state(hass, mock_node, ok_status, state)


async def test_lost_frame_renders_without_raising(
    hass, smartbox_stack, mock_smartbox, caplog
):
    """A bare lost status frame renders None properties, no tracebacks.

    The store replaces the status slot with {"sync_status": "lost"}
    verbatim; the required-key property reads must not raise (the fan-out
    delivery logged engine-level "Change subscriber raised" errors for
    every lost frame). With this fixed, the sensor round's dropped
    no-log-errors assertion (tests/test_sensor.py, lost-frame
    section) can be restored.
    """
    runtime, fake = smartbox_stack

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)

    # The frame delivery (and the availability flip at the window's end)
    # both re-render the entity against the lost body.
    await push_status(
        hass, runtime, mock_device["dev_id"], mock_node, {"sync_status": "lost"}
    )
    await until(lambda eid=entity_id: hass.states.get(eid).state == STATE_UNAVAILABLE)

    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    # Recovery: an ok frame restores the rendered state.
    ok_status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    await push_status(hass, runtime, mock_device["dev_id"], mock_node, ok_status)
    state = hass.states.get(entity_id)
    _check_state(hass, mock_node, ok_status, state)

    assert_no_log_errors(caplog)


async def test_persistent_lost_marks_node_unavailable(
    hass, smartbox_stack, mock_smartbox
):
    """A lost frame never followed by ok -> node entities Unavailable.

    Node-availability feature (smartbox 2.6.1): the device-level connected
    flag stays true, but the node is unreachable after a bare
    {"sync_status": "lost"} frame with no ok follow-up (live probes,
    docs/api-contract.md §6.3, 2026-09-30). The confirming ok frame (or any
    later ok frame) marks it available again.
    """
    runtime, fake = smartbox_stack

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)
    ok_status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    expected = _HVAC_STATE_FOR_MODE[ok_status["mode"]]
    assert hass.states.get(entity_id).state == expected

    # Bare lost frame; no ok confirmation follows within the (test-patched)
    # unavailability delay -> Unavailable.
    await push_status(
        hass, runtime, mock_device["dev_id"], mock_node, {"sync_status": "lost"}
    )
    await until(lambda eid=entity_id: hass.states.get(eid).state == STATE_UNAVAILABLE)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    # Recovery: an ok frame marks the node available again.
    await push_status(hass, runtime, mock_device["dev_id"], mock_node, ok_status)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == expected


async def test_write_on_lost_node_stays_unavailable(
    hass, smartbox_stack, mock_smartbox
):
    """A direct write on a node already marked lost stays Unavailable.

    The lost frame is the availability evidence; the write timeout verifies
    with a GET, gets no data from this host, and leaves the node unavailable
    until an ok frame.
    """
    runtime, fake = smartbox_stack

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)
    ok_status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    expected = _HVAC_STATE_FOR_MODE[ok_status["mode"]]
    assert hass.states.get(entity_id).state == expected

    # Node goes dead (lost frame); then a user command is sent anyway.
    await push_status(
        hass, runtime, mock_device["dev_id"], mock_node, {"sync_status": "lost"}
    )
    await until(lambda eid=entity_id: hass.states.get(eid).state == STATE_UNAVAILABLE)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    climate_entity = hass.data["entity_components"]["climate"].get_entity(entity_id)
    # HA's service targeting silently DROPS unavailable entities
    # (helpers/service.py: "if not entity.available: continue"), so a user
    # command never reaches the handler while the node is Unavailable.
    # Invoke the entity method directly to exercise the real write path.
    await climate_entity.async_set_hvac_mode(HVACMode.OFF)
    # The verification GET returns no evidence -> still Unavailable.
    await until(lambda: not armed_deadlines(runtime.engine, "write"))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    # Power comes back: ok frame -> Available again, on its real mode.
    await push_status(hass, runtime, mock_device["dev_id"], mock_node, ok_status)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == expected


async def test_write_on_live_node_stays_available(hass, smartbox_stack, mock_smartbox):
    """A write followed by the confirming ok frame never false-flips.

    The core's optimistic merge shows the write immediately; the
    confirming frame cancels the pending confirmation, so the node stays
    available past the (patched) confirm window.
    """
    runtime, fake = smartbox_stack

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)
    ok_status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    assert hass.states.get(entity_id).state == _HVAC_STATE_FOR_MODE[ok_status["mode"]]

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_HVAC_MODE: HVACMode.OFF, ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == HVACMode.OFF  # optimistic merge, immediate

    await confirm_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
    # the confirming frame cancels the pending confirmation (bounded poll)
    await until(lambda: not armed_deadlines(runtime.engine, "write"))
    state = hass.states.get(entity_id)
    assert state.state == HVACMode.OFF  # write applied, not flipped dead


async def test_hvac_write_confirming_frame_reaches_entity(
    hass, smartbox_stack, mock_smartbox, caplog
):
    """Off -> heat -> off desync regression (live finding, 2026-09-28).

    The store forwards every DISTINCT value, so a write updates the entity
    immediately and the value-equal confirming frame is deduped - but the
    availability plumbing still processes it (stays available).
    """
    runtime, fake = smartbox_stack

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)

    # Known starting point: off
    off_status = dict(await fake.get_node_status(mock_device["dev_id"], ref(mock_node)))
    off_status["mode"] = "off"
    await push_status(hass, runtime, mock_device["dev_id"], mock_node, off_status)
    assert hass.states.get(entity_id).state == HVACMode.OFF

    # off -> heat: the entity must follow immediately from the optimistic
    # merge, without waiting for the websocket to confirm
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: HVACMode.HEAT},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == HVACMode.HEAT

    # The confirming frame for the write equals the optimistically merged
    # store value; it must confirm the write (not desync the entity)
    confirmed = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    await push_status(hass, runtime, mock_device["dev_id"], mock_node, confirmed)
    assert hass.states.get(entity_id).state == HVACMode.HEAT
    _check_state(hass, mock_node, confirmed, hass.states.get(entity_id))

    # ... and turning off must work from there
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == HVACMode.OFF

    assert_no_log_errors(caplog)


async def test_target_temperature_mirrors_backend_on_mode_change(
    hass, smartbox_stack, mock_smartbox, caplog
):
    """Mode changes mirror the backend frames (E7 decision, 2026-09-28).

    HA does not assume setpoints: a mode write shows the backend's own
    intermediate view (the stale pre-switch stemp, delivered via the
    optimistic merge) and resolves to the device's value when the server
    feeds the device state back. Nothing is held or suppressed: every ok
    frame is applied and dispatched (E3).
    """
    runtime, fake = smartbox_stack

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)
    status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    units = status["units"]
    stale_stemp = status["stemp"]

    # Start off: no set point attribute (display semantics matching the app)
    off_status = dict(status)
    off_status["mode"] = "off"
    await push_status(hass, runtime, mock_device["dev_id"], mock_node, off_status)
    state = hass.states.get(entity_id)
    assert state.state == HVACMode.OFF
    # HA renders the key with a None value when there is no set point
    assert state.attributes[ATTR_TEMPERATURE] is None

    # Off -> heat: the backend's intermediate view (mode applied, stale
    # pre-switch stemp) is mirrored as-is, without assuming anything
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: HVACMode.HEAT},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == HVACMode.HEAT
    assert round_temp(hass, state.attributes[ATTR_TEMPERATURE]) == round_temp(
        hass, convert_temp(hass, units, float(stale_stemp))
    )

    # The device reports its re-selected manual set point: the entity
    # follows the backend frame
    manual_status = dict(
        await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    )
    manual_status["stemp"] = "21.5"
    await push_status(hass, runtime, mock_device["dev_id"], mock_node, manual_status)
    state = hass.states.get(entity_id)
    assert state.state == HVACMode.HEAT
    assert round_temp(hass, state.attributes[ATTR_TEMPERATURE]) == round_temp(
        hass, convert_temp(hass, units, 21.5)
    )

    # ... and turning off again works end to end
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: HVACMode.OFF},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == HVACMode.OFF
    assert state.attributes[ATTR_TEMPERATURE] is None

    assert_no_log_errors(caplog)


async def test_set_temperature_in_auto_engages_modified_auto(
    hass, smartbox_stack, mock_smartbox, caplog
):
    """Setpoint inside the running programme engages modified_auto (E5).

    HA must send the app's body {"stemp", "units", "mode": "modified_auto"}
    when the node runs its programme (and its setup advertises
    modified_auto_span): the plain setpoint write is stored and applied but
    the override is dropped at the next programme transition (mode stays
    auto); the app's body keeps the override and reverts with
    {"mode": "auto"} (live-probed 2026-09-28, see docs/api-contract.md §5.1).
    """
    runtime, fake = smartbox_stack

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)
    status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    units = status["units"]

    # Advertise the modified_auto feature the way the live units do: a
    # setup frame carries the FULL merged setup (wire semantics).
    setup = dict(await fake.get_node_setup(mock_device["dev_id"], ref(mock_node)))
    setup["modified_auto_span"] = 6
    await runtime.transport.deliver_update(
        mock_device["dev_id"],
        f"/{mock_node['type']}/{mock_node['addr']}/setup",
        setup,
    )
    await hass.async_block_till_done()

    # Setpoint grid step matches the app: 0.5 for Celsius, 1.0 for
    # Fahrenheit (user-verified against the app, 2026-09-28)
    assert hass.states.get(entity_id).attributes["target_temp_step"] == (
        0.5 if units == "C" else 1.0
    )

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: 21.5},
        blocking=True,
    )
    await hass.async_block_till_done()

    # The write is the app-shaped modified_auto body (observable on the
    # fake's request log); the device reports the override back.
    posts = [(m, p, b) for m, p, b in fake.requests if m == "POST" and "/status" in p]
    assert len(posts) == 1
    # HA passes the service temperature in display units; the integration
    # sends device units ([F] family: display °C, devices °F)
    device_temp = TemperatureConverter.convert(
        21.5,
        hass.config.units.temperature_unit,
        UnitOfTemperature.CELSIUS if units == "C" else UnitOfTemperature.FAHRENHEIT,
    )
    # The client quantizes before the POST: Celsius onto the 0.5 grid (21.5
    # is on it), Fahrenheit to whole degrees (the app's integral grid) - both
    # formatted with one decimal (the app's toFixed(1) shape).
    posted = (
        str(device_temp) if units == "C" else f"{math.floor(device_temp + 0.5):.1f}"
    )
    assert posts[0][2] == {
        "stemp": posted,
        "units": units,
        "mode": "modified_auto",
    }

    # The device reports the override back: HA shows AUTO with the override
    override = dict(await fake.get_node_status(mock_device["dev_id"], ref(mock_node)))
    override["mode"] = "modified_auto"
    override["stemp"] = posted
    await push_status(hass, runtime, mock_device["dev_id"], mock_node, override)
    state = hass.states.get(entity_id)
    assert state.state == HVACMode.AUTO
    assert round_temp(hass, state.attributes[ATTR_TEMPERATURE]) == round_temp(
        hass, convert_temp(hass, units, float(posted))
    )
    # The explicit revert renders AUTO too
    revert = dict(await fake.get_node_status(mock_device["dev_id"], ref(mock_node)))
    revert["mode"] = "auto"
    await push_status(hass, runtime, mock_device["dev_id"], mock_node, revert)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == HVACMode.AUTO

    assert_no_log_errors(caplog)


def _check_not_away_preset(node_type, status, preset_mode):
    if status.get("boost", False) is True:
        assert preset_mode == PRESET_BOOST
    elif node_type == SmartboxNodeType.HTR_MOD:
        if status["mode"] == "auto":
            assert preset_mode == PRESET_SCHEDULE
        elif status["mode"] == "manual":
            if status["selected_temp"] == "comfort":
                assert preset_mode == PRESET_COMFORT
            elif status["selected_temp"] == "eco":
                assert preset_mode == PRESET_ECO
            elif status["selected_temp"] == "ice":
                assert preset_mode == PRESET_FROST
            else:
                pytest.fail(f"Unexpected selected_temp {status['selected_temp']}")
        elif status["mode"] == "self_learn":
            assert preset_mode == PRESET_SELF_LEARN
        elif status["mode"] == "presence":
            assert preset_mode == PRESET_ACTIVITY
        else:
            pytest.fail(f"Unknown smartbox node mode {status['mode']}")
    else:
        assert preset_mode == PRESET_HOME


async def test_away(hass, smartbox_stack, mock_smartbox):
    runtime, _fake = smartbox_stack
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    mock_device_1 = (await mock_smartbox.session.get_devices())[0]
    # Away is a device-level frame (the old dev_data update)
    await runtime.transport.deliver_update(
        mock_device_1["dev_id"], "/mgr/away_status", {"away": True}
    )
    await hass.async_block_till_done()
    # check all device_1's climate entities are away but device_2's are not
    for mock_node in await mock_smartbox.session.get_nodes(mock_device_1["dev_id"]):
        entity_id = get_climate_entity_id(mock_node)
        await async_update_entity(hass, entity_id)
        state = hass.states.get(entity_id)
        assert state.attributes[ATTR_PRESET_MODE] == PRESET_AWAY
    # but all device_2's should still be home
    mock_device_2 = (await mock_smartbox.session.get_devices())[1]
    for mock_node in await mock_smartbox.session.get_nodes(mock_device_2["dev_id"]):
        if not is_heater_node(mock_node):
            continue
        entity_id = get_climate_entity_id(mock_node)
        await async_update_entity(hass, entity_id)
        state = hass.states.get(entity_id)
        mock_node_status = await fake_status_of(mock_smartbox, mock_device_2, mock_node)
        _check_not_away_preset(
            mock_node["type"],
            mock_node_status,
            state.attributes[ATTR_PRESET_MODE],
        )


async def test_away_switch_flips_the_climate_presets_without_a_forced_update(
    hass, smartbox_stack, mock_smartbox
):
    """Flipping the box Away switch re-renders the climate cards on its own.

    Two traps hide this. ``test_away`` above forces ``async_update_entity``
    before asserting, which recomputes the state from the store; and it uses
    device_1, which the mock world already has away, so its frame is absorbed by
    the store's dedup. This test drives the real user action on the device that
    starts at home and asserts the state HA holds straight afterwards, so it
    fails if device changes reach device-level registrations only.
    """
    _runtime, _fake = smartbox_stack
    home_device = (await mock_smartbox.session.get_devices())[1]
    assert (await mock_smartbox.session.get_device_away_status(home_device["dev_id"]))[
        "away"
    ] is False
    switch_id = get_away_status_switch_entity_id(home_device)
    entity_ids = [
        get_climate_entity_id(node)
        for node in await mock_smartbox.session.get_nodes(home_device["dev_id"])
        if is_heater_node(node)
    ]
    assert entity_ids  # guard: the assertions below must not be vacuous
    for entity_id in entity_ids:
        assert hass.states.get(entity_id).attributes[ATTR_PRESET_MODE] != PRESET_AWAY

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {"entity_id": switch_id}, blocking=True
    )
    await hass.async_block_till_done()

    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        assert state.attributes[ATTR_PRESET_MODE] == PRESET_AWAY


async def fake_status_of(mock_smartbox, device, node):
    """Return the seeded (session-copy) status for a mock node."""
    return await mock_smartbox.session.get_status(device["dev_id"], node)


async def test_away_preset(hass, smartbox_stack, mock_smartbox):
    _runtime, _fake = smartbox_stack
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    mock_device_1 = (await mock_smartbox.session.get_devices())[0]
    mock_device_1_node_0 = (
        await mock_smartbox.session.get_nodes(mock_device_1["dev_id"])
    )[0]
    entity_id_device_1_node_0 = get_climate_entity_id(mock_device_1_node_0)

    # Set a node on device_1 away via preset
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {
            ATTR_PRESET_MODE: PRESET_AWAY,
            ATTR_ENTITY_ID: entity_id_device_1_node_0,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    # check all device_1's climate entities are away but device_2's are not
    for mock_node in await mock_smartbox.session.get_nodes(mock_device_1["dev_id"]):
        entity_id = get_climate_entity_id(mock_node)
        await async_update_entity(hass, entity_id)
        state = hass.states.get(entity_id)
        assert state.attributes[ATTR_PRESET_MODE] == PRESET_AWAY
    # but all device_2's should still be home
    mock_device_2 = (await mock_smartbox.session.get_devices())[1]
    for mock_node in await mock_smartbox.session.get_nodes(mock_device_2["dev_id"]):
        if not is_heater_node(mock_node):
            continue
        entity_id = get_climate_entity_id(mock_node)
        await async_update_entity(hass, entity_id)
        state = hass.states.get(entity_id)
        mock_node_status = await fake_status_of(mock_smartbox, mock_device_2, mock_node)
        _check_not_away_preset(
            mock_node["type"],
            mock_node_status,
            state.attributes[ATTR_PRESET_MODE],
        )

    # Set a node on device_1 back to home (it's not an htr_mod device,
    # otherwise PRESET_HOME would be invalid)
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {
            ATTR_PRESET_MODE: PRESET_HOME,
            ATTR_ENTITY_ID: entity_id_device_1_node_0,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    # test nothing is now away
    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            mock_node_status = await fake_status_of(
                mock_smartbox, mock_device, mock_node
            )
            _check_not_away_preset(
                mock_node["type"],
                mock_node_status,
                state.attributes[ATTR_PRESET_MODE],
            )


# --- htr_mod presets (device_2) ---


async def _preset_write(
    hass, runtime, fake, mock_smartbox, device_index, node_index, preset
):
    """Set one preset and confirm the write frame; return the fake status."""
    mock_device = (await mock_smartbox.session.get_devices())[device_index]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[
        node_index
    ]
    entity_id = get_climate_entity_id(mock_node)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {
            ATTR_PRESET_MODE: preset,
            ATTR_ENTITY_ID: entity_id,
        },
        blocking=True,
    )
    await hass.async_block_till_done()
    await confirm_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
    state = hass.states.get(entity_id)
    status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    return state, status


async def test_htr_mod_presets(hass, smartbox_stack, mock_smartbox):
    """Every htr_mod preset writes its mode/selected_temp and renders back."""
    runtime, fake = smartbox_stack
    assert DOMAIN in hass.config.components

    # (node index on device_2, preset, wire mode, selected_temp)
    cases = (
        (1, PRESET_SCHEDULE, "auto", None),
        (1, PRESET_SELF_LEARN, "self_learn", None),
        (1, PRESET_ACTIVITY, "presence", None),
        (5, PRESET_COMFORT, "manual", "comfort"),
        (1, PRESET_ECO, "manual", "eco"),
        (5, PRESET_FROST, "manual", "ice"),
    )
    for node_index, preset, wire_mode, selected_temp in cases:
        state, status = await _preset_write(
            hass, runtime, fake, mock_smartbox, 1, node_index, preset
        )
        state = await _refresh(hass, state.entity_id)
        assert state.attributes[ATTR_PRESET_MODE] == preset
        assert status["mode"] == wire_mode
        assert status["on"] is True
        if selected_temp is not None:
            assert status["selected_temp"] == selected_temp


async def _refresh(hass, entity_id):
    """Force one entity poll + return the fresh state."""
    await async_update_entity(hass, entity_id)
    return hass.states.get(entity_id)


async def _push_haverland_pid(hass, runtime, dev_id, node):
    """Deliver the version frame that carries a Haverland product code.

    The htr_mod setpoint policy is gated on ``pid[2:4]`` (0A = heater module),
    so a test node needs the version slot a live box pushes.
    """
    await runtime.transport.deliver_update(
        dev_id, f"/{node['type']}/{node['addr']}/version", {"pid": "010A0000"}
    )
    await hass.async_block_till_done()


async def test_htr_mod_climate_grid_and_profile_limits(
    hass, smartbox_stack, mock_smartbox
):
    """htr_mod advertises the app's whole-degree grid and per-profile range."""

    def node_bound(celsius: float, unit: str) -> float:
        """Return the bound the entity reports (whole degrees on F nodes)."""
        value = TemperatureConverter.convert(celsius, UnitOfTemperature.CELSIUS, unit)
        return round(value) if unit is UnitOfTemperature.FAHRENHEIT else value

    runtime, fake = smartbox_stack
    assert DOMAIN in hass.config.components
    nodes = await mock_smartbox.session.get_nodes("device_2")
    # (addr, selected profile in the fixture, Celsius range)
    # Comfort and eco share the union; ice is the slot's own value.
    expectations = (
        (1, "comfort", (10.0, 28.0)),
        (2, "eco", (5.0, 28.0)),  # AUTO in the fixture; global htr_mod bounds
        (3, "ice", (7.0, 7.0)),
    )
    for addr in (1, 2, 3):
        await _push_haverland_pid(hass, runtime, "device_2", nodes[addr])
    entities = {entity.entity_id: entity for entity in runtime.services.climate}
    for addr, selected, (min_c, max_c) in expectations:
        node = nodes[addr]
        status = await fake.get_node_status("device_2", ref(node))
        assert status["selected_temp"] == selected
        entity = entities[get_climate_entity_id(node)]
        unit = (
            UnitOfTemperature.CELSIUS
            if status["units"] == "C"
            else UnitOfTemperature.FAHRENHEIT
        )
        if selected == "ice":
            expected = (float(status["ice_temp"]),) * 2
        else:
            expected = tuple(node_bound(value, unit) for value in (min_c, max_c))
        assert entity.target_temperature_step == 1.0
        assert entity.min_temp == pytest.approx(expected[0])
        assert entity.max_temp == pytest.approx(expected[1])
        if status["mode"] == "auto":
            # The programme-derived AUTO target is covered separately; this
            # test is the profile-bound grid.
            continue
        if selected == "eco":
            # comfort 24.3 - offset 4: inside the union, reported as stored.
            eco = float(status["comfort_temp"]) - float(status["eco_offset"])
            assert entity.native_target_temperature == pytest.approx(eco)
        else:
            key = "comfort_temp" if selected == "comfort" else "ice_temp"
            assert entity.native_target_temperature == pytest.approx(float(status[key]))


async def test_htr_mod_target_clamps_into_the_union(
    hass, smartbox_stack, mock_smartbox
):
    """Both ends clamp: a stale comfort above 28 or below 10 reports the bound."""
    runtime, fake = smartbox_stack
    nodes = await mock_smartbox.session.get_nodes("device_2")
    node = nodes[1]  # selected comfort, comfort_temp "20.3"
    await _push_haverland_pid(hass, runtime, "device_2", node)
    entity = {entity.entity_id: entity for entity in runtime.services.climate}[
        get_climate_entity_id(node)
    ]
    status = await fake.get_node_status("device_2", ref(node))
    celsius = status["units"] == "C"
    cases = (
        (30.0 if celsius else 90.0, entity.max_temp),
        (9.0 if celsius else 45.0, entity.min_temp),
    )
    for stored, bound in cases:
        body = dict(status)
        body["comfort_temp"] = f"{stored:.1f}"
        await push_status(hass, runtime, "device_2", node, body)
        assert entity.native_target_temperature == pytest.approx(bound)


async def test_htr_mod_non_haverland_product_keeps_the_generic_grid(
    hass, smartbox_stack, mock_smartbox
):
    """A product code the app routes elsewhere keeps the default setpoint grid."""
    runtime, fake = smartbox_stack
    assert DOMAIN in hass.config.components
    nodes = await mock_smartbox.session.get_nodes("device_2")
    node = nodes[1]  # selected comfort, comfort_temp "20.3"
    await runtime.transport.deliver_update(
        "device_2", f"/{node['type']}/{node['addr']}/version", {"pid": "08010000"}
    )
    await hass.async_block_till_done()
    status = await fake.get_node_status("device_2", ref(node))
    entities = {entity.entity_id: entity for entity in runtime.services.climate}
    entity = entities[get_climate_entity_id(node)]
    unit = (
        UnitOfTemperature.CELSIUS
        if status["units"] == "C"
        else UnitOfTemperature.FAHRENHEIT
    )
    assert entity.target_temperature_step == (
        0.5 if unit is UnitOfTemperature.CELSIUS else 1.0
    )
    assert entity.min_temp == pytest.approx(
        TemperatureConverter.convert(1.0, UnitOfTemperature.CELSIUS, unit)
    )
    assert entity.max_temp == pytest.approx(
        TemperatureConverter.convert(30.0, UnitOfTemperature.CELSIUS, unit)
    )
    # No profile range and no clamp: the stored device value is reported.
    assert entity.native_target_temperature == pytest.approx(
        float(status["comfort_temp"])
    )


@pytest.mark.parametrize("target", [9, 29])
async def test_htr_mod_out_of_range_target_is_rejected_by_ha(
    hass, smartbox_stack, mock_smartbox, units, target
):
    """HA validates the service call against the union range before the write."""
    if units != "C":
        pytest.skip("the fixture range is expressed in Celsius")
    runtime, _fake = smartbox_stack
    assert DOMAIN in hass.config.components
    nodes = await mock_smartbox.session.get_nodes("device_2")
    node = nodes[1]  # comfort profile: the union is 10-28 C
    await _push_haverland_pid(hass, runtime, "device_2", node)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_TEMPERATURE: target, ATTR_ENTITY_ID: get_climate_entity_id(node)},
            blocking=True,
        )
    assert err.value.translation_key == "temp_out_of_range"


async def _write_htr_mod_setpoint(hass, runtime, fake, node, value):
    """Set a temperature on an htr_mod node and read back the stored status."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_TEMPERATURE: value, ATTR_ENTITY_ID: get_climate_entity_id(node)},
        blocking=True,
    )
    await hass.async_block_till_done()
    await confirm_write(hass, runtime, fake, "device_2", node)
    return await fake.get_node_status("device_2", ref(node))


async def test_htr_mod_setpoint_crosses_comfort_to_eco(
    hass, smartbox_stack, mock_smartbox, units
):
    """A target at or below 18 in comfort selects eco, moving only what it must."""
    if units != "C":
        pytest.skip("the fixture range is expressed in Celsius")
    runtime, fake = smartbox_stack
    nodes = await mock_smartbox.session.get_nodes("device_2")
    node = nodes[1]  # comfort, comfort_temp 20.3, eco_offset 4
    await _push_haverland_pid(hass, runtime, "device_2", node)
    status = await _write_htr_mod_setpoint(hass, runtime, fake, node, 17.0)
    assert status["selected_temp"] == "eco"
    # 20.3 snaps to 20; a 3 C gap is below the floor, so comfort rises to 21.
    assert status["comfort_temp"] == "21.0"
    assert status["eco_offset"] == "4.0"
    assert status["mode"] == "manual"


async def test_htr_mod_setpoint_crosses_eco_to_comfort(
    hass, smartbox_stack, mock_smartbox, units
):
    """A target above 18 in eco selects comfort and keeps the stored offset."""
    if units != "C":
        pytest.skip("the fixture range is expressed in Celsius")
    runtime, fake = smartbox_stack
    nodes = await mock_smartbox.session.get_nodes("device_2")
    node = nodes[5]  # eco, comfort_temp 24.3, eco_offset 4
    await _push_haverland_pid(hass, runtime, "device_2", node)
    status = await _write_htr_mod_setpoint(hass, runtime, fake, node, 21.0)
    assert status["selected_temp"] == "comfort"
    assert status["comfort_temp"] == "21.0"
    # The comfort branch keeps the stored offset exactly as the device reports it.
    assert status["eco_offset"] == "4"
    assert status["mode"] == "manual"


async def test_htr_mod_eco_setpoint_keeps_the_stored_comfort(
    hass, smartbox_stack, mock_smartbox, units
):
    """A reachable eco target changes only the offset, not the comfort value."""
    if units != "C":
        pytest.skip("the fixture range is expressed in Celsius")
    runtime, fake = smartbox_stack
    nodes = await mock_smartbox.session.get_nodes("device_2")
    node = nodes[1]  # comfort, comfort_temp 20.3, eco_offset 4
    await _push_haverland_pid(hass, runtime, "device_2", node)
    status = await _write_htr_mod_setpoint(hass, runtime, fake, node, 15.0)
    assert status["selected_temp"] == "eco"
    # Only the snap to a whole degree moves comfort; the gap fits at 5 C.
    assert status["comfort_temp"] == "20.0"
    assert status["eco_offset"] == "5.0"


async def test_htr_mod_temperature_write_follows_the_app_grid(
    hass, smartbox_stack, mock_smartbox, units
):
    """htr_mod setpoints land on whole degrees in the app's key set."""
    if units != "C":
        pytest.skip("the app's setpoint constants are Celsius")
    runtime, fake = smartbox_stack
    assert DOMAIN in hass.config.components
    nodes = await mock_smartbox.session.get_nodes("device_2")

    async def set_temp(node, value):
        await _push_haverland_pid(hass, runtime, "device_2", node)
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_TEMPERATURE: value, ATTR_ENTITY_ID: get_climate_entity_id(node)},
            blocking=True,
        )
        await hass.async_block_till_done()
        await confirm_write(hass, runtime, fake, "device_2", node)
        return await fake.get_node_status("device_2", ref(node))

    # Comfort profile: the target stays put, snapped to the whole-degree grid.
    status = await set_temp(nodes[1], 21.0)
    assert status["selected_temp"] == "comfort"
    assert status["comfort_temp"] == "21.0"
    assert status["mode"] == "manual"

    # Eco profile: 17 C against a stored comfort of 24 places the offset.
    status = await set_temp(nodes[5], 17.0)
    assert status["selected_temp"] == "eco"
    assert status["comfort_temp"] == "24.0"
    assert status["eco_offset"] == "7.0"
    assert status["mode"] == "manual"

    # A setpoint on a programmed node leaves the programme, as the app does.
    status = await set_temp(nodes[2], 17.0)  # auto mode in the fixture
    assert status["mode"] == "manual"
    assert status["selected_temp"] == "eco"


async def _write_all_hvac(hass, runtime, fake, mock_smartbox, hvac_mode):
    """Match-all hvac write + the confirming frames for every heater."""
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_HVAC_MODE: hvac_mode, ATTR_ENTITY_ID: ENTITY_MATCH_ALL},
        blocking=True,
    )
    await hass.async_block_till_done()
    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if is_heater_node(mock_node):
                await confirm_write(
                    hass, runtime, fake, mock_device["dev_id"], mock_node
                )


async def _assert_all_heaters(hass, fake, mock_smartbox, hvac_mode: str) -> None:
    """After a match-all write: every heater's wire + rendered state."""
    wire_mode = {HVACMode.AUTO: "auto", HVACMode.HEAT: "manual", HVACMode.OFF: "off"}[
        hvac_mode
    ]
    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)
            state = await _refresh(hass, entity_id)
            assert state.state == hvac_mode
            status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
            if mock_node["type"] == SmartboxNodeType.HTR_MOD:
                # The htr_mod off write carries {"on": False} only (no mode)
                assert status["on"] is (hvac_mode != HVACMode.OFF)
                if hvac_mode != HVACMode.OFF:
                    assert status["mode"] == wire_mode
            else:
                assert status["mode"] == wire_mode


async def test_set_hvac_mode(hass, smartbox_stack, mock_smartbox):
    runtime, fake = smartbox_stack
    assert DOMAIN in hass.config.components

    for hvac_mode in (HVACMode.AUTO, HVACMode.HEAT, HVACMode.OFF):
        await _write_all_hvac(hass, runtime, fake, mock_smartbox, hvac_mode)
        await _assert_all_heaters(hass, fake, mock_smartbox, hvac_mode)


async def test_set_target_temp(hass, smartbox_stack, mock_smartbox):
    runtime, fake = smartbox_stack
    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)

            state = hass.states.get(entity_id)
            mock_node_status = await fake.get_node_status(
                mock_device["dev_id"], ref(mock_node)
            )
            old_target_temp = state.attributes[ATTR_CURRENT_TEMPERATURE]
            if (
                mock_node["type"] == SmartboxNodeType.HTR_MOD
                and mock_node_status["selected_temp"] == "ice"
            ):
                # We can't set temperatures in ice mode
                with pytest.raises(ValueError) as e_info:
                    await hass.services.async_call(
                        CLIMATE_DOMAIN,
                        SERVICE_SET_TEMPERATURE,
                        {
                            ATTR_TEMPERATURE: old_target_temp + 1,
                            ATTR_ENTITY_ID: entity_id,
                        },
                        blocking=True,
                    )
                assert "Can't set temperature" in e_info.exconly()
            else:
                sent = old_target_temp + 1
                await hass.services.async_call(
                    CLIMATE_DOMAIN,
                    SERVICE_SET_TEMPERATURE,
                    {
                        ATTR_TEMPERATURE: sent,
                        ATTR_ENTITY_ID: entity_id,
                    },
                    blocking=True,
                )
                await hass.async_block_till_done()
                await confirm_write(
                    hass, runtime, fake, mock_device["dev_id"], mock_node
                )

                await async_update_entity(hass, entity_id)
                state = hass.states.get(entity_id)
                new_target_temp = state.attributes[ATTR_TEMPERATURE]
                # Setpoints are quantized BEFORE POSTing: Celsius onto the
                # device's 0.5 °C grid (the device floors off-grid values
                # silently, live-probed, so the client floors to match) -
                # including htr_mod's comfort_temp, which is the same kind of
                # setpoint - and Fahrenheit to whole degrees (the vendor app's
                # integral F grid).
                if mock_node_status["units"] == "C":
                    assert new_target_temp == pytest.approx(sent // 0.5 * 0.5)
                else:
                    # Fahrenheit quantizes to whole degrees (the vendor app's
                    # integral F grid), so the round-trip lands within half a
                    # degree of the request. htr_mod's eco path adds the stored
                    # eco_offset before quantizing and subtracts it again for
                    # display, so an exact display value would restate that
                    # arithmetic; the grid itself is pinned in test_values.
                    device_sent = TemperatureConverter.convert(
                        sent,
                        hass.config.units.temperature_unit,
                        UnitOfTemperature.FAHRENHEIT,
                    )
                    new_device = TemperatureConverter.convert(
                        new_target_temp,
                        hass.config.units.temperature_unit,
                        UnitOfTemperature.FAHRENHEIT,
                    )
                    assert new_device == pytest.approx(device_sent, abs=0.6)


async def test_unavailable_at_startup(hass, mock_smartbox_unavailable, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox_unavailable)
    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox_unavailable.session.get_devices():
        for mock_node in await mock_smartbox_unavailable.session.get_nodes(
            mock_device["dev_id"]
        ):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)

            state = hass.states.get(entity_id)
            assert state.state == STATE_UNAVAILABLE


async def test_turn_on_and_off(hass, smartbox_stack, mock_smartbox):
    """The turn_on/turn_off services reach every heater and confirm."""
    runtime, fake = smartbox_stack
    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        dev_id = mock_device["dev_id"]
        for mock_node in await mock_smartbox.session.get_nodes(dev_id):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)

            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_HVAC_MODE: HVACMode.OFF, ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await hass.async_block_till_done()
            await confirm_write(hass, runtime, fake, dev_id, mock_node)

            # turn_on resumes the programme
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await hass.async_block_till_done()
            await confirm_write(hass, runtime, fake, dev_id, mock_node)
            state = await _refresh(hass, entity_id)
            assert state.state == HVACMode.AUTO
            status = await fake.get_node_status(dev_id, ref(mock_node))
            if mock_node["type"] == SmartboxNodeType.HTR_MOD:
                assert status["on"] is True
            assert status["mode"] == "auto"

            # turn_off reaches off
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await hass.async_block_till_done()
            await confirm_write(hass, runtime, fake, dev_id, mock_node)
            state = await _refresh(hass, entity_id)
            assert state.state == HVACMode.OFF
            status = await fake.get_node_status(dev_id, ref(mock_node))
            if mock_node["type"] == SmartboxNodeType.HTR_MOD:
                assert status["on"] is False
            else:
                assert status["mode"] == "off"


async def test_boost_preset(hass, smartbox_stack, mock_smartbox):
    """PRESET_BOOST writes the boost flag and reports boost while it runs."""
    runtime, fake = smartbox_stack
    assert DOMAIN in hass.config.components

    # Device 1 node 1 (Helki storage) starts with boost OFF, so this is a real
    # write: a node that already had boost on would make it a value-equal
    # no-op, and the preset assertion below would then pass without any write
    # taking effect.
    state, status = await _preset_write(
        hass, runtime, fake, mock_smartbox, 0, 1, PRESET_BOOST
    )
    assert ("POST", "devs/device_1/acm/1/boost") in {
        (method, path) for method, path, _body in fake.requests
    }
    state = await _refresh(hass, state.entity_id)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_BOOST
    assert status["boost"] is True


# --- property-mapping unit tests (engine store as the state source) ---


def _entity_with_world(
    status, *, away=False, node_type="other", setup=None, pid="pid", version=None
) -> SmartboxHeater:
    """Build a SmartboxHeater over a hand-built engine world (no HA harness).

    The world is installed through ``engine_with_node``; everything else uses
    the public construction paths.
    """
    engine = engine_with_node(
        dev_id=DEV,
        node_type=node_type,
        status=status,
        setup=setup or {},
        version=version,
        away={"away": away, "enabled": away, "forced": away},
    )
    return _heater_over_engine(engine, node_type, pid=pid)


def _heater_over_engine(
    engine: SmartboxEngine, node_type: str = "other", *, pid: str = "pid"
) -> SmartboxHeater:
    """Build a SmartboxHeater over an engine whose world is already installed."""
    node = MagicMock()
    node.node_type = node_type
    node.name = "node"
    node.node_id = f"{DEV}_0"
    node.addr = 0
    node.dev_id = DEV
    node.reseller = SimpleNamespace(web_url="https://web_url/", name="Test API name")
    node.home_id = "home_1"
    node.pid = pid
    node.hw_version = "hw"
    return SmartboxHeater(
        node, MagicMock(), SimpleNamespace(engine=engine), NoopFanout()
    )


class _WriteHostDouble:
    """Write host double: records posts, serves the GET half of a setup write."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    async def post(self, path: str, body: dict) -> None:
        self.posts.append((path, dict(body)))

    async def get_node_status(self, dev_id: str, node) -> dict:  # noqa: ARG002
        return {}

    async def get_node_setup(self, dev_id: str, node) -> dict:  # noqa: ARG002
        # A real node setup is never empty; an empty read is refused by the
        # write layer so a transient 204 cannot wipe the object.
        return {"units": "C"}

    async def get_node_prog(self, dev_id: str, node) -> dict:  # noqa: ARG002
        return {"prog": {}}


async def test_reads_render_without_raising_before_any_status_body_arrives() -> None:
    """A setup write confirms a node with NO status frame: reads must not raise.

    The confirming frame for a setup (or prog) write carries no status, so
    the node is available while its status slot is still empty. Reads that
    need a status key render None; the presets that come from device state
    or need no key still render.
    """
    host = _WriteHostDouble()
    engine = SmartboxEngine(host)
    ref = NodeRef("htr", "0")
    # the node has to be in the store before the write: a write merges into
    # state the wire delivered and never creates a node, so the node list
    # arrives first (with no status slot, which is the case under test)
    await engine.handle_event(
        Snapshot(
            payload=[
                {
                    "id": "home_1",
                    "devs": [
                        {
                            "dev_id": DEV,
                            "devData": {"nodes": [{"type": "htr", "addr": 0}]},
                        }
                    ],
                }
            ]
        )
    )
    await engine.set_window_mode(DEV, ref, enabled=True)
    assert host.posts == [
        (
            f"devs/{DEV}/htr/0/setup",
            {"units": "C", "window_mode_enabled": True},
        )
    ]
    await engine.handle_event(
        Update(
            dev_id=DEV,
            path="/htr/0/setup",
            body={"units": "C", "window_mode_enabled": True},
        )
    )

    node = engine.node(DEV, node_key("htr", 0))
    assert node is not None  # guard: the fixture must produce the state under test
    assert node.available is True
    assert node.status == {}

    entity = _heater_over_engine(engine, SmartboxNodeType.HTR)
    assert entity.native_current_temperature is None
    assert entity.native_target_temperature is None
    assert entity.hvac_mode is None
    assert entity.hvac_action is None
    assert entity.extra_state_attributes == {}
    assert entity.preset_mode == PRESET_HOME
    # the htr_mod preset IS the status mode, so with no body there is none
    assert _heater_over_engine(engine, SmartboxNodeType.HTR_MOD).preset_mode is None


def test_selected_temp_attribute_follows_the_status():
    """The live profile is exposed; a status without the key omits it."""
    entity = _entity_with_world(
        {"mode": "auto", "selected_temp": "ice", "units": "C"},
        node_type=SmartboxNodeType.HTR_MOD,
    )
    assert entity.extra_state_attributes["selected_temp"] == "ice"

    entity = _entity_with_world(
        {"mode": "auto", "units": "C"},
        node_type=SmartboxNodeType.HTR_MOD,
    )
    assert "selected_temp" not in entity.extra_state_attributes


def test_selected_temp_attribute_is_htr_mod_only():
    """A different node family carrying the key does not expose it."""
    entity = _entity_with_world(
        {"mode": "auto", "selected_temp": "eco", "units": "C"},
        node_type=SmartboxNodeType.HTR,
    )
    assert "selected_temp" not in entity.extra_state_attributes


@pytest.mark.parametrize("value", [4, "", None, False])
def test_selected_temp_attribute_requires_a_non_empty_string(value):
    """Ill-typed or empty server values stay out of the state attributes."""
    entity = _entity_with_world(
        {"mode": "auto", "selected_temp": value, "units": "C"},
        node_type=SmartboxNodeType.HTR_MOD,
    )
    assert "selected_temp" not in entity.extra_state_attributes


@pytest.mark.parametrize(
    ("node_attributes", "expected_preset"),
    [
        ({"away": True}, PRESET_AWAY),
        ({"boost": True}, PRESET_BOOST),
        (
            {
                "node_type": SmartboxNodeType.HTR_MOD,
                "status": {"mode": "manual", "selected_temp": "comfort"},
            },
            PRESET_COMFORT,
        ),
        (
            {
                "node_type": SmartboxNodeType.HTR_MOD,
                "status": {"mode": "manual", "selected_temp": "eco"},
            },
            PRESET_ECO,
        ),
        (
            {
                "node_type": SmartboxNodeType.HTR_MOD,
                "status": {"mode": "manual", "selected_temp": "ice"},
            },
            PRESET_FROST,
        ),
        (
            {
                "node_type": SmartboxNodeType.HTR_MOD,
                "status": {"mode": "auto"},
            },
            PRESET_SCHEDULE,
        ),
        (
            {
                "node_type": SmartboxNodeType.HTR_MOD,
                "status": {"mode": "presence"},
            },
            PRESET_ACTIVITY,
        ),
        (
            {
                "node_type": SmartboxNodeType.HTR_MOD,
                "status": {"mode": "self_learn"},
            },
            PRESET_SELF_LEARN,
        ),
        (
            {
                "node_type": "other",
                "status": {},
            },
            PRESET_HOME,
        ),
    ],
)
def test_preset_mode(node_attributes, expected_preset):
    """Test the preset_mode property."""
    status = dict(node_attributes.get("status", {}))
    if node_attributes.get("boost", False):
        status["boost"] = True
    heater = _entity_with_world(
        status,
        away=node_attributes.get("away", False),
        node_type=node_attributes.get("node_type", "other"),
    )

    assert heater.preset_mode == expected_preset


@pytest.mark.parametrize("mode", ["easy", "setback"])
def test_preset_mode_known_vendor_modes_render_none(mode):
    """Vendor-app modes without a HA preset do not raise from the entity."""
    heater = _entity_with_world(
        {"mode": mode, "selected_temp": "comfort"},
        node_type=SmartboxNodeType.HTR_MOD,
    )
    assert heater.preset_mode is None


def test_preset_mode_ill_typed_mode(caplog):
    """A non-string mode renders no preset instead of raising."""
    _warn_unknown.cache_clear()
    heater = _entity_with_world(
        {"mode": ["bogus"], "selected_temp": "comfort"},
        node_type=SmartboxNodeType.HTR_MOD,
    )
    with caplog.at_level(logging.WARNING, logger="custom_components.smartbox.mapping"):
        assert heater.preset_mode is None
    assert "Unknown smartbox node mode ['bogus']" in caplog.text


def test_preset_mode_invalid_selected_temp(caplog):
    """An unmodelled selected_temp renders no preset and warns only once."""
    _warn_unknown.cache_clear()
    heater = _entity_with_world(
        {"mode": "manual", "selected_temp": "invalid_temp"},
        node_type=SmartboxNodeType.HTR_MOD,
    )
    with caplog.at_level(logging.WARNING, logger="custom_components.smartbox.mapping"):
        assert heater.preset_mode is None
        assert heater.preset_mode is None
    assert caplog.text.count("Unknown smartbox selected_temp 'invalid_temp'") == 1


def test_preset_mode_invalid_mode(caplog):
    """An unmodelled mode renders no preset and warns only once."""
    _warn_unknown.cache_clear()
    heater = _entity_with_world(
        {"mode": "invalid_mode", "selected_temp": "invalid_temp"},
        node_type=SmartboxNodeType.HTR_MOD,
    )
    with caplog.at_level(logging.WARNING, logger="custom_components.smartbox.mapping"):
        assert heater.preset_mode is None
        assert heater.preset_mode is None
    assert caplog.text.count("Unknown smartbox node mode 'invalid_mode'") == 1


def test_preset_mode_invalid_selected_temp_bounded(caplog):
    """Server-controlled selected_temp renders bounded and single-line."""
    _warn_unknown.cache_clear()
    heater = _entity_with_world(
        {"mode": "manual", "selected_temp": "x" * 5000 + "\nCRASH"},
        node_type=SmartboxNodeType.HTR_MOD,
    )
    with caplog.at_level(logging.WARNING, logger="custom_components.smartbox.mapping"):
        assert heater.preset_mode is None
    message = caplog.records[-1].getMessage()
    assert "\n" not in message  # single line: no log forging
    assert len(message) < 400  # bounded repr (values.bounded_repr)
    assert "CRASH" not in message


def test_preset_mode_invalid_mode_bounded(caplog):
    """Server-controlled mode renders bounded and single-line."""
    _warn_unknown.cache_clear()
    heater = _entity_with_world(
        {"mode": "y" * 5000 + "\nCRASH", "selected_temp": "comfort"},
        node_type=SmartboxNodeType.HTR_MOD,
    )
    with caplog.at_level(logging.WARNING, logger="custom_components.smartbox.mapping"):
        assert heater.preset_mode is None
    message = caplog.records[-1].getMessage()
    assert "\n" not in message
    assert len(message) < 400
    assert "CRASH" not in message


@pytest.mark.parametrize(
    ("node_attributes", "expected_action"),
    [
        ({"is_heating": True}, HVACAction.HEATING),
        (
            {
                "status": {"mode": "off", "active": False},
                "node_type": SmartboxNodeType.HTR_MOD,
                "boost": False,
            },
            HVACAction.OFF,
        ),
        (
            {
                "status": {"mode": "manual", "on": False, "active": False},
                "node_type": SmartboxNodeType.HTR_MOD,
                "boost": False,
            },
            HVACAction.OFF,
        ),
        (
            {
                "status": {"mode": "manual", "on": False, "active": False},
                "node_type": SmartboxNodeType.HTR_MOD,
                "boost": True,
            },
            HVACAction.IDLE,
        ),
        (
            {
                "status": {"mode": "manual", "on": True, "active": False},
                "node_type": SmartboxNodeType.HTR_MOD,
                "boost": False,
            },
            HVACAction.IDLE,
        ),
    ],
)
def test_hvac_action(node_attributes, expected_action):
    """Test the hvac_action property."""
    status = dict(node_attributes.get("status", {}))
    if node_attributes.get("boost", False):
        status["boost"] = True
    if node_attributes.get("is_heating", False):
        status["active"] = True
    heater = _entity_with_world(
        status,
        node_type=node_attributes.get("node_type", SmartboxNodeType.HTR_MOD),
    )

    assert heater.hvac_action == expected_action


def test_water_storage_action_is_idle_at_the_water_ready_level():
    """Water storage at 50% is water-ready, so it is not actively heating."""
    heater = _entity_with_world(
        {"mode": "auto", "charging": True, "charge_level": 50, "units": "C"},
        node_type=SmartboxNodeType.ACM,
        pid="082A0000",
    )
    assert heater.hvac_action == HVACAction.IDLE

    heater = _entity_with_world(
        {"mode": "auto", "charging": True, "charge_level": 40, "units": "C"},
        node_type=SmartboxNodeType.ACM,
        pid="082A0000",
    )
    assert heater.hvac_action == HVACAction.HEATING


async def test_max_temp_clamped_by_device_limit(hass, smartbox_stack, mock_smartbox):
    """Climate max_temp clamps to max_stemp_limit where advertised."""
    runtime, fake = smartbox_stack

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_climate_entity_id(mock_node)
    state = hass.states.get(entity_id)
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    units = status["units"]
    # Climate records min/max temp attributes converted to the HA unit
    # system; the wire's max_stemp_limit is Celsius and the entity converts
    # it into the node's unit first.
    expected_max = round_temp(
        hass,
        convert_temp(
            hass, units, max_limit_value(units, float(setup["max_stemp_limit"]))
        ),
    )
    assert state.attributes["max_temp"] == expected_max

    # Changing the limit through the setup frame refreshes the climate
    # entity without a restart (the fan-out delivers setup frames too).
    changed = dict(setup)
    changed["max_stemp_limit"] = "27.0"
    await runtime.transport.deliver_update(
        mock_device["dev_id"],
        f"/{mock_node['type']}/{mock_node['addr']}/setup",
        changed,
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    # The frame carries the limit in Celsius, like the snapshot.
    assert state.attributes["max_temp"] == round_temp(
        hass, convert_temp(hass, units, max_limit_value(units, 27.0))
    )

    # An ACM without the field uses the HelkiStorage profile's 30 C maximum.
    acm_node_info = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    acm_state = hass.states.get(get_climate_entity_id(acm_node_info))
    assert acm_state.attributes["max_temp"] == pytest.approx(30.0)


# --- boost/runback/advanced-slot UI mapping (vendor-app frames) ---


def _full_boost_entity(status: dict, *, setup=None, pid="0134") -> SmartboxHeater:
    """HTR entity with a FULL boost config and a hand-built world."""
    return _entity_with_world(
        status,
        setup={
            "factory_options": {"boost_config": 3},
            **(setup or {}),
        },
        pid=pid,
    )


def _prefs_for(entity, **values) -> SmartboxHeater:
    """Attach a fixed boost-preference record to the hand-built runtime."""
    node = NodeBoostPrefs(**values)

    class _Prefs:
        def get(self, _node_id: str) -> NodeBoostPrefs:
            return node

    entity._runtime.boost_prefs = _Prefs()
    return entity


def test_runback_frame_drives_preset_and_attributes():
    """A vendor-app runback frame is presented as Runback, not Boost."""
    minute = (dt_util.utcnow().hour * 60 + dt_util.utcnow().minute + 30) % 1440
    entity = _full_boost_entity(
        {
            "boost": True,
            "boost_mode": "runback",
            "boost_end_min": minute,
            "stemp": "19.0",
            "mode": "manual",
            "units": "C",
            "locked": False,
        }
    )
    assert entity.preset_mode == PRESET_RUNBACK
    assert PRESET_RUNBACK in entity.preset_modes
    attrs = entity.extra_state_attributes
    assert attrs["boost_mode"] == "runback"
    assert attrs["boost_end_time"]
    assert 0 <= attrs["boost_remaining_minutes"] <= 60


def test_advanced_slot_frame_drives_preset():
    """The advanced-slot mode is a preset; no end time is invented."""
    entity = _full_boost_entity(
        {
            "boost": True,
            "boost_mode": "advanced_slot",
            "stemp": "20.0",
            "mode": "auto",
            "units": "C",
        }
    )
    assert entity.preset_mode == PRESET_ADVANCED_SLOT
    assert PRESET_ADVANCED_SLOT in entity.preset_modes
    assert entity.extra_state_attributes["boost_mode"] == "advanced_slot"


def test_runback_is_not_offered_for_a_set_temp_and_time_node():
    """Only FULL nodes own the /boost_mode modes."""
    entity = _entity_with_world(
        {"boost": True, "boost_mode": "runback", "mode": "manual", "units": "C"},
        setup={"factory_options": {"boost_config": 2}},
    )
    assert entity.preset_mode == PRESET_BOOST
    assert PRESET_RUNBACK not in entity.preset_modes
    assert PRESET_ADVANCED_SLOT not in entity.preset_modes


def test_status_boost_modes_available_gates_the_presets():
    entity = _full_boost_entity(
        {
            "boost": False,
            "boost_modes_available": ["temp_and_time", "runback"],
            "mode": "manual",
            "units": "C",
        }
    )
    assert PRESET_RUNBACK in entity.preset_modes
    assert PRESET_ADVANCED_SLOT not in entity.preset_modes


async def test_active_boost_temperature_edits_via_boost_mode():
    """While runback runs, the climate target re-posts /boost_mode."""
    host = _WriteHostDouble()
    engine = engine_with_node(
        dev_id=DEV,
        node_type=SmartboxNodeType.HTR,
        host=host,
        status={
            "boost": True,
            "boost_mode": "runback",
            "stemp": "19.0",
            "mode": "manual",
            "units": "C",
        },
        setup={"factory_options": {"boost_config": 3}},
        away={"away": False, "enabled": False, "forced": False},
    )
    entity = _heater_over_engine(engine, SmartboxNodeType.HTR, pid="01340000")

    await entity.async_set_temperature(**{ATTR_TEMPERATURE: 20.5})
    assert host.posts == [
        (
            f"devs/{DEV}/htr/0/boost_mode",
            {"active": True, "mode": "runback", "temp": "20.5"},
        )
    ]
    await engine.stop()


async def test_entity_boost_mode_entry_point_posts_the_app_body():
    host = _WriteHostDouble()
    engine = engine_with_node(
        dev_id=DEV,
        node_type=SmartboxNodeType.HTR,
        host=host,
        status={"mode": "manual", "units": "C"},
        setup={"factory_options": {"boost_config": 3}},
        away={"away": False, "enabled": False, "forced": False},
    )
    entity = _heater_over_engine(engine, SmartboxNodeType.HTR, pid="01340000")

    await entity.async_set_boost_mode(
        active=True, mode="runback", temperature=21.0, time_minutes=60
    )
    assert host.posts == [
        (
            f"devs/{DEV}/htr/0/boost_mode",
            {"active": True, "mode": "runback", "temp": "21.0", "time": 60},
        )
    ]
    await engine.stop()


async def test_set_boost_mode_service_contract(hass, mock_smartbox, config_entry):
    """Registration, argument validation and target checks fail loudly."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_BOOST_MODE)

    # A non-FULL node refuses the full-boost modes.
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    entity_id = get_climate_entity_id(mock_node)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BOOST_MODE,
            {ATTR_ENTITY_ID: [entity_id], "mode": "runback"},
            blocking=True,
        )
    assert err.value.translation_key == "boost_mode_unsupported"

    # Its own arguments are validated before target resolution.
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BOOST_MODE,
            {"active": True},
            blocking=True,
        )
    assert err.value.translation_key == "boost_mode_requires_mode"
    assert err.value.translation_placeholders["service"] == SERVICE_SET_BOOST_MODE

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BOOST_MODE,
            {"active": False, "temperature": 20.0},
            blocking=True,
        )
    assert err.value.translation_key == "boost_mode_inactive_args"

    # A resolved non-climate target and a missing target are errors too.
    sensor_id = "sensor.device_1_0_power"
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BOOST_MODE,
            {ATTR_ENTITY_ID: [sensor_id], "mode": "runback"},
            blocking=True,
        )
    assert err.value.translation_key == "boost_mode_wrong_target"

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BOOST_MODE,
            {"mode": "runback"},
            blocking=True,
        )
    assert err.value.translation_key == "service_no_target"


async def test_advanced_slot_temperature_is_not_editable():
    """The active advanced-slot target is server-owned, so the write is refused."""
    entity = _full_boost_entity(
        {
            "boost": True,
            "boost_mode": "advanced_slot",
            "stemp": "20.0",
            "mode": "manual",
            "units": "C",
        }
    )
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_set_temperature(temperature=21.0)
    assert err.value.translation_key == "advanced_slot_temperature_fixed"


async def test_non_full_nodes_refuse_the_full_boost_modes():
    """The private mode activators keep their translated capability guard."""
    entity = _entity_with_world(
        {"mode": "manual", "units": "C"},
        setup={"factory_options": {"boost_config": 2}},
    )
    for activate, key in (
        (entity._async_activate_runback, "runback_requires_full_boost"),
        (entity._async_activate_advanced_slot, "advanced_slot_requires_full_boost"),
    ):
        with pytest.raises(HomeAssistantError) as err:
            await activate(entity._runtime.engine)
        assert err.value.translation_key == key


async def test_full_boost_entity_rejects_an_unknown_mode():
    """The entity-level mode check is translated like the service check."""
    entity = _full_boost_entity(
        {"boost": False, "mode": "manual", "units": "C"}, pid="0134"
    )
    with pytest.raises(ServiceValidationError) as err:
        await entity.async_set_boost_mode(
            active=True, mode="nope", temperature=None, time_minutes=None
        )
    assert err.value.translation_key == "boost_mode_invalid"
    assert err.value.translation_placeholders == {"modes": ", ".join(VALID_BOOST_MODES)}


def test_runback_limits_apply_only_while_runback_is_active():
    entity = _full_boost_entity(
        {
            "boost": True,
            "boost_mode": "runback",
            "ice_temp": "7.0",
            "stemp": "19.0",
            "mode": "manual",
            "units": "C",
        },
        setup={
            "max_stemp_limit": "30.0",
            "extra_options": {"max_runback_temp": "25.0"},
        },
    )
    assert entity.min_temp == 7.0
    assert entity.max_temp == 25.0

    # Without a runback max, the global limit applies.
    entity = _full_boost_entity(
        {
            "boost": True,
            "boost_mode": "runback",
            "ice_temp": "7.0",
            "mode": "manual",
            "units": "C",
        },
        setup={"max_stemp_limit": "28.0"},
    )
    assert entity.max_temp == 28.0


def test_runback_limits_convert_celsius_extra_options_on_fahrenheit_nodes():
    """extra_options.version >= 2 means Celsius, even on an F node."""
    entity = _full_boost_entity(
        {
            "boost": True,
            "boost_mode": "runback",
            "ice_temp": "50.0",
            "mode": "manual",
            "units": "F",
        },
        setup={
            "extra_options": {"version": 2, "max_runback_temp": "24.0"},
        },
    )
    assert entity.min_temp == 50.0
    assert entity.max_temp == 75.0


def test_boost_defaults_convert_celsius_extra_options_on_fahrenheit_nodes():
    """The config numbers and activation defaults share the app's scale rule."""
    entity = _full_boost_entity(
        {
            "boost": False,
            "mode": "manual",
            "units": "F",
        },
        setup={"extra_options": {"version": 2, "boost_temp": "21.0"}},
    )
    assert entity._boost_available is True
    # 21 C -> 70 F, not a raw 21 F
    assert node_boost_params(entity._node_state())[0] == 70.0


def test_runback_min_prefers_the_setback_temperature():
    """The app uses setback_temp as the lower bound where that setting exists."""
    entity = _full_boost_entity(
        {
            "boost": True,
            "boost_mode": "runback",
            "ice_temp": "7.0",
            "mode": "manual",
            "units": "C",
        },
        setup={"extra_options": {"setback_temp": "9.0"}},
    )
    assert entity.min_temp == 9.0


async def test_set_boost_mode_service_excludes_nodes_without_boost(
    hass, mock_smartbox, config_entry
):
    """A config-3 base heater that never offers BOOST refuses the service."""
    mock_smartbox._node_info["device_1"][0]["product_id"] = "04020000"
    setup = mock_smartbox._node_setup["device_1"][0]
    setup["factory_options"] = {
        **setup.get("factory_options", {}),
        "boost_config": 3,
    }

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)
    device = (await mock_smartbox.session.get_devices())[0]
    node = (await mock_smartbox.session.get_nodes(device["dev_id"]))[0]
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BOOST_MODE,
            {ATTR_ENTITY_ID: [get_climate_entity_id(node)], "mode": "runback"},
            blocking=True,
        )
    assert err.value.translation_key == "boost_mode_unsupported"


async def test_set_boost_mode_service_excludes_storage_and_triac(
    hass, mock_smartbox, config_entry
):
    """Config 3 on storage or triac never exposes the /boost_mode service."""
    mock_smartbox._node_info["device_1"][0]["product_id"] = "011A0000"
    mock_smartbox._node_info["device_1"][1]["product_id"] = "081C0000"
    for index in (0, 1):
        setup = mock_smartbox._node_setup["device_1"][index]
        setup["factory_options"] = {
            **setup.get("factory_options", {}),
            "boost_config": 3,
        }

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)
    device = (await mock_smartbox.session.get_devices())[0]
    for index in (0, 1):
        node = (await mock_smartbox.session.get_nodes(device["dev_id"]))[index]
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                DOMAIN,
                SERVICE_SET_BOOST_MODE,
                {
                    ATTR_ENTITY_ID: [get_climate_entity_id(node)],
                    "mode": "runback",
                },
                blocking=True,
            )
        assert err.value.translation_key == "boost_mode_unsupported"


async def test_set_boost_mode_service_maps_a_write_failure(
    hass, mock_smartbox, config_entry
):
    """A failed boost-mode write is a translated error naming the entity."""
    mock_smartbox._node_info["device_1"][0]["product_id"] = "01340000"
    mock_smartbox._node_setup["device_1"][0]["factory_options"]["boost_config"] = 3
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_climate_entity_id(mock_node)
    fake.fail_next(500)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BOOST_MODE,
            {
                ATTR_ENTITY_ID: [entity_id],
                "mode": "runback",
                "temperature": 21.0,
                "duration": 60,
            },
            blocking=True,
        )
    assert err.value.translation_key == "boost_mode_write_failed"
    assert err.value.translation_placeholders["entity_id"] == entity_id


@pytest.mark.asyncio
async def test_home_preset_clears_boost_and_easy_runback():
    """HOME mirrors the app's deactivateCurrentMode before applying a mode."""
    entity = _full_boost_entity(
        {
            "boost": True,
            "boost_mode": "runback",
            "stemp": "19.0",
            "mode": "manual",
            "units": "C",
            "runback": True,
            "easy": True,
        },
        setup={
            "extra_options": {"version": 2},
            "factory_options": {"boost_config": 2},
        },
        pid="0134",
    )
    engine = entity._runtime.engine
    with (
        patch.object(engine, "set_boost", AsyncMock()) as set_boost,
        patch.object(engine, "set_runback", AsyncMock()) as set_runback,
        patch.object(engine, "set_easy", AsyncMock()) as set_easy,
    ):
        await entity.async_set_preset_mode(PRESET_HOME)
    set_boost.assert_awaited_once_with(DEV, entity._ref, on=False, legacy=False)
    set_runback.assert_awaited_once_with(DEV, entity._ref, enabled=False)
    set_easy.assert_awaited_once_with(DEV, entity._ref, enabled=False)


def test_controller_limits_fall_back_when_the_version_is_partial():
    """A partial live version keeps the discovery PID for controller limits."""
    entity = _entity_with_world(
        {"mode": "manual", "stemp": "20.0", "units": "C", "sync_status": "ok"},
        node_type="htr",
        pid="04020000",
        version={"fw_version": "1.9"},
        setup={},
    )
    limits = entity._controller_profile().limits
    assert (limits.min_c, limits.max_c) == (0.0, 30.0)


def test_late_pid_drives_the_acm_storage_mode_list():
    """A version frame that supplies the PID updates runtime storage decisions."""
    entity = _entity_with_world(
        {"mode": "off", "stemp": "20.0", "units": "C", "sync_status": "ok"},
        node_type="acm",
        pid=None,
        version={"pid": "081C0000", "fw_version": "1.8.0"},
        setup={},
    )
    assert entity._live_pid == "081C0000"
    assert entity.hvac_modes == [HVACMode.HEAT, HVACMode.OFF]


async def test_triac_full_boost_uses_the_legacy_dispatch():
    """Tevolve triac overrides FULL boost to the setup+status path."""
    entity = _full_boost_entity(
        {"boost": False, "mode": "off", "units": "C", "stemp": "20.0"},
        setup={"factory_options": {"boost_config": 3}},
        pid="011A",
    )
    engine = entity._runtime.engine
    with patch.object(engine, "set_boost", AsyncMock()) as set_boost:
        await entity.async_set_preset_mode(PRESET_BOOST)
    args, kwargs = set_boost.await_args
    assert args == (DEV, entity._ref)
    assert kwargs["on"] is True
    assert kwargs["legacy"] is True
    assert kwargs["temperature"] is not None
    assert kwargs["time_minutes"] is not None
    assert entity.supports_boost_mode is False
    assert PRESET_RUNBACK not in entity.preset_modes
    assert PRESET_ADVANCED_SLOT not in entity.preset_modes

    with patch.object(engine, "set_boost", AsyncMock()) as set_boost:
        await entity._async_deactivate_boost(engine)
    assert set_boost.await_args.kwargs["legacy"] is True


async def test_haverland_fixed_time_full_config_uses_the_boost_mode_endpoint():
    """Haverland WiFi's config 3 enters /boost_mode with the fixed-time mode."""
    entity = _full_boost_entity(
        {"boost": False, "mode": "off", "units": "C", "stemp": "20.0"},
        setup={"factory_options": {"boost_config": 3}},
        pid="011D",
    )
    engine = entity._runtime.engine
    with patch.object(engine, "set_boost_mode", AsyncMock()) as set_boost_mode:
        await entity.async_set_preset_mode(PRESET_BOOST)
    args, kwargs = set_boost_mode.await_args
    assert args == (DEV, entity._ref)
    assert kwargs == {
        "active": True,
        "mode": "max_temp_fixed_time",
        "temperature": None,
        "time_minutes": None,
    }
    assert entity.supports_boost_mode is True
    assert PRESET_RUNBACK not in entity.preset_modes


async def test_full_boost_wifi_activation_carries_the_stored_parameters():
    """A config-3 WiFi node's one-click boost still sends temp/time."""
    entity = _full_boost_entity(
        {
            "boost": False,
            "mode": "off",
            "units": "C",
            "stemp": "20.0",
            "boost_modes_available": ["temp_and_time"],
        },
        setup={
            "factory_options": {"boost_config": 3},
            "extra_options": {"boost_temp": "21.0", "boost_time": 60},
        },
        pid="0134",
    )
    engine = entity._runtime.engine
    with patch.object(engine, "set_boost_mode", AsyncMock()) as set_boost_mode:
        await entity.async_set_preset_mode(PRESET_BOOST)
    _args, kwargs = set_boost_mode.await_args
    assert kwargs["active"] is True
    assert kwargs["mode"] == "temp_and_time"
    assert kwargs["temperature"] == 21.0
    assert kwargs["time_minutes"] == 60
    assert entity.supports_boost_mode is True


async def test_full_boost_ui_parameters_override_the_device_defaults():
    """The sliders win over the device's stored boost defaults."""
    entity = _prefs_for(
        _full_boost_entity(
            {
                "boost": False,
                "mode": "off",
                "units": "C",
                "stemp": "20.0",
                "boost_modes_available": ["temp_and_time"],
            },
            setup={
                "factory_options": {"boost_config": 3},
                "extra_options": {"boost_temp": "21.0", "boost_time": 60},
            },
            pid="0134",
        ),
        boost_temp_c=23.0,
        boost_time=120,
    )
    engine = entity._runtime.engine
    with patch.object(engine, "set_boost_mode", AsyncMock()) as set_boost_mode:
        await entity.async_set_preset_mode(PRESET_BOOST)
    _args, kwargs = set_boost_mode.await_args
    assert kwargs["mode"] == "temp_and_time"
    assert kwargs["temperature"] == 23.0
    assert kwargs["time_minutes"] == 120


async def test_full_fixed_boost_posts_no_parameters():
    """A fixed-only endpoint gets the style and nothing else."""
    entity = _prefs_for(
        _full_boost_entity(
            {
                "boost": False,
                "mode": "off",
                "units": "C",
                "stemp": "20.0",
                "boost_modes_available": ["max_temp_fixed_time"],
            },
            pid="0134",
        ),
        boost_temp_c=23.0,
        boost_time=120,
    )
    engine = entity._runtime.engine
    with patch.object(engine, "set_boost_mode", AsyncMock()) as set_boost_mode:
        await entity.async_set_preset_mode(PRESET_BOOST)
    _args, kwargs = set_boost_mode.await_args
    assert kwargs["mode"] == "max_temp_fixed_time"
    assert kwargs["temperature"] is None
    assert kwargs["time_minutes"] is None


async def test_full_boost_without_an_advertised_mode_is_refused():
    """An empty capability list refuses instead of sending a guessed style."""
    entity = _prefs_for(
        _full_boost_entity(
            {
                "boost": False,
                "mode": "off",
                "units": "C",
                "stemp": "20.0",
                "boost_modes_available": [],
            },
            pid="0134",
        ),
        boost_temp_c=23.0,
        boost_time=120,
    )
    engine = entity._runtime.engine
    with (
        patch.object(engine, "set_boost_mode", AsyncMock()) as set_boost_mode,
        pytest.raises(ServiceValidationError),
    ):
        await entity.async_set_preset_mode(PRESET_BOOST)
    set_boost_mode.assert_not_awaited()


async def test_full_runback_uses_the_runback_preferences():
    """Runback reads its own temperature and duration."""
    entity = _prefs_for(
        _full_boost_entity(
            {
                "boost": False,
                "mode": "off",
                "units": "C",
                "stemp": "20.0",
                "boost_modes_available": ["temp_and_time", "runback"],
            },
            pid="0134",
        ),
        runback_temp_c=18.0,
        runback_time=60,
    )
    engine = entity._runtime.engine
    with patch.object(engine, "set_boost_mode", AsyncMock()) as set_boost_mode:
        await entity.async_set_preset_mode(PRESET_RUNBACK)
    _args, kwargs = set_boost_mode.await_args
    assert kwargs["mode"] == "runback"
    assert kwargs["temperature"] == 18.0
    assert kwargs["time_minutes"] == 60


async def test_runback_duration_must_be_on_the_30_minute_grid():
    """An off-grid runback duration is refused before any write."""
    entity = _prefs_for(
        _full_boost_entity(
            {
                "boost": False,
                "mode": "off",
                "units": "C",
                "stemp": "20.0",
                "boost_modes_available": ["temp_and_time", "runback"],
            },
            pid="0134",
        ),
        runback_temp_c=18.0,
        runback_time=45,
    )
    engine = entity._runtime.engine
    with (
        patch.object(engine, "set_boost_mode", AsyncMock()) as set_boost_mode,
        pytest.raises(ServiceValidationError),
    ):
        await entity.async_set_preset_mode(PRESET_RUNBACK)
    set_boost_mode.assert_not_awaited()


async def test_triac_full_boost_temperature_does_not_post_boost_mode():
    """A config-3 triac edits its target through the normal setpoint path."""
    entity = _full_boost_entity(
        {
            "boost": True,
            "boost_mode": "temp_and_time",
            "mode": "off",
            "units": "C",
            "stemp": "20.0",
        },
        setup={"factory_options": {"boost_config": 3}},
        pid="011A",
    )
    engine = entity._runtime.engine
    with (
        patch.object(engine, "set_boost_mode", AsyncMock()) as set_boost_mode,
        patch.object(engine, "set_setpoint", AsyncMock()) as set_setpoint,
    ):
        await entity.async_set_temperature(temperature=21.0)
    set_boost_mode.assert_not_awaited()
    set_setpoint.assert_awaited_once()


@pytest.mark.parametrize("pid", ["04020000", "01320000"])
async def test_direct_boost_preset_without_boost_is_refused(pid):
    """A direct preset call cannot activate boost on a node without BOOST."""
    entity = _full_boost_entity(
        {"boost": False, "mode": "off", "units": "C"},
        setup={"factory_options": {"boost_config": 3}},
        pid=pid,
    )
    engine = entity._runtime.engine
    with (
        patch.object(engine, "set_boost", AsyncMock()) as set_boost,
        pytest.raises(HomeAssistantError) as err,
    ):
        await entity.async_set_preset_mode(PRESET_BOOST)
    assert err.value.translation_key == "boost_unsupported"
    set_boost.assert_not_awaited()


def test_config_three_without_boost_support_has_no_boost_mode_surface():
    """A controller that never offers BOOST cannot expose /boost_mode."""
    for pid in ("04020000", "01320000"):
        entity = _full_boost_entity(
            {"boost": False, "mode": "off", "units": "C"},
            setup={"factory_options": {"boost_config": 3}},
            pid=pid,
        )
        assert entity._boost_available is False
        assert entity.supports_boost_mode is False
        assert PRESET_RUNBACK not in entity.preset_modes
        assert PRESET_ADVANCED_SLOT not in entity.preset_modes


def test_lhz_relay_config_three_keeps_the_boost_mode_surface():
    """LhzRelay 36 adds BOOST unconditionally, so config 3 is usable."""
    entity = _full_boost_entity(
        {"boost": False, "mode": "off", "units": "C"},
        setup={"factory_options": {"boost_config": 3}},
        pid="01360000",
    )
    assert entity._boost_available is True
    assert entity.supports_boost_mode is True


def test_storage_config_three_has_no_full_boost_surface():
    """Storage's config 3 still uses /boost, so /boost_mode stays hidden."""
    entity = _entity_with_world(
        {"boost": True, "boost_mode": "temp_and_time", "units": "C"},
        node_type="acm",
        pid="081C0000",
        setup={"factory_options": {"boost_config": 3}},
    )
    assert entity.supports_boost_mode is False
    assert PRESET_RUNBACK not in entity.preset_modes
    assert PRESET_ADVANCED_SLOT not in entity.preset_modes


def test_thm_28_no_probes_hides_target_temperature():
    """The Tevolve floor thermostat removes temperature control with no probes."""
    entity = _entity_with_world(
        {"mode": "manual", "stemp": "20.0", "units": "C", "sync_status": "ok"},
        node_type="thm",
        pid="0128",
        setup={"extra_options": {"sensor_mode": 3}},
    )
    assert not entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE


async def test_thm_28_no_probes_rejects_a_direct_temperature_write():
    """A direct service call cannot bypass the hidden temperature surface."""
    entity = _entity_with_world(
        {"mode": "manual", "stemp": "20.0", "units": "C", "sync_status": "ok"},
        node_type="thm",
        pid="0128",
        setup={"extra_options": {"sensor_mode": 3}},
    )
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_set_temperature(temperature=21.0)
    assert err.value.translation_key == "temperature_control_unsupported"


def test_htr_mod_manual_off_profile_reports_off_not_heating():
    """A manual htr_mod with selected_temp off is OFF, even if active is stale."""
    entity = _entity_with_world(
        {
            "mode": "manual",
            "on": True,
            "selected_temp": "off",
            "units": "C",
            "stemp": "19.0",
            "mtemp": "18.0",
            "active": True,
            "locked": False,
        },
        node_type="htr_mod",
    )
    entity._node.pid = "010A"
    entity._node.get_model_code = lambda *_args: "0A"
    assert entity.hvac_mode == HVACMode.OFF
    assert entity.hvac_action == HVACAction.OFF
    assert entity.native_target_temperature is None
    assert entity.min_temp == 10.0


async def test_classic_accumulator_turn_on_uses_heat():
    """A classic accumulator has no AUTO; the power button uses HEAT."""
    entity = _entity_with_world(
        {"mode": "off", "target_charge_per": 0, "mtemp": "18.0"},
        node_type="acm",
        setup={"factory_options": {"ui_mode": 2}},
    )
    entity._node.pid = "0814"
    entity.async_set_hvac_mode = AsyncMock()
    assert HVACMode.AUTO not in entity.hvac_modes
    await entity.async_turn_on()
    entity.async_set_hvac_mode.assert_awaited_once_with(HVACMode.HEAT)


async def test_classic_accumulator_hides_temperature_surface():
    """No target feature and no setpoint write in the classic UI mode."""
    entity = _entity_with_world(
        {"mode": "manual", "target_charge_per": 50, "mtemp": "18.0"},
        node_type="acm",
        setup={"factory_options": {"ui_mode": 2}},
    )
    entity._node.pid = "0814"
    assert not entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
    with pytest.raises(HomeAssistantError):
        await entity.async_set_temperature(**{ATTR_TEMPERATURE: 20.0})
