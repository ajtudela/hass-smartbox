import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.climate import HVACAction, HVACMode
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
from homeassistant.helpers.entity_component import async_update_entity
from homeassistant.util.unit_conversion import TemperatureConverter
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartbox.climate import SmartboxHeater, get_hvac_mode
from custom_components.smartbox.const import (
    DOMAIN,
    PRESET_FROST,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
    SmartboxNodeType,
)

from .mocks import (
    get_climate_entity_id,
    get_climate_entity_name,
    get_entity_id_from_unique_id,
    get_node_unique_id,
    get_object_id,
    is_heater_node,
)
from .test_utils import assert_no_log_errors, convert_temp, round_temp

_LOGGER = logging.getLogger(__name__)


def _check_state(hass, mock_node, mock_node_status, state):
    assert state.state == get_hvac_mode(mock_node["type"], mock_node_status)
    assert state.attributes[ATTR_LOCKED] == mock_node_status["locked"]

    assert round_temp(hass, state.attributes[ATTR_CURRENT_TEMPERATURE]) == round_temp(
        hass,
        convert_temp(hass, mock_node_status["units"], float(mock_node_status["mtemp"])),
    )
    # ATTR_TEMPERATURE actually stores the target temperature
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


async def test_basic_climate(hass, mock_smartbox, config_entry, caplog, recorder_mock):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
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

            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            _check_state(hass, mock_node, mock_node_status, state)

            # check we opened a socket and the run function was awaited
            socket = mock_smartbox.get_socket(mock_device["dev_id"])
            socket.run.assert_awaited()

            mock_node_status = mock_smartbox.generate_socket_status_update(
                mock_device,
                mock_node,
                {"mtemp": str(float(mock_node_status["mtemp"]) + 1)},
            )

            await async_update_entity(hass, entity_id)
            new_state = hass.states.get(entity_id)
            assert (
                new_state.attributes[ATTR_CURRENT_TEMPERATURE]
                != state.attributes[ATTR_CURRENT_TEMPERATURE]
            )
            _check_state(hass, mock_node, mock_node_status, new_state)

    # Make sure we don't log any errors during setup
    assert_no_log_errors(caplog)


async def test_unavailable(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)
            state = hass.states.get(entity_id)

            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            _check_state(hass, mock_node, mock_node_status, state)

            mock_node_status = mock_smartbox.generate_socket_node_unavailable(
                mock_device, mock_node
            )
            # Availability flips via the library's grace window (patched
            # to 0.05 s in tests), not synchronously with the frame.
            await asyncio.sleep(0.1)
            await hass.async_block_till_done()
            state = hass.states.get(entity_id)
            assert state.state == STATE_UNAVAILABLE

            mock_node_status = mock_smartbox.generate_new_socket_status(
                mock_device, mock_node
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            _check_state(hass, mock_node, mock_node_status, state)


async def test_persistent_lost_marks_node_unavailable(
    hass, mock_smartbox, config_entry
):
    """A lost frame never followed by ok -> node entities Unavailable.

    Node-availability feature (smartbox 2.6.1): the device-level connected
    flag stays true, but the library reports the node unreachable after a
    bare {"sync_status": "lost"} frame with no ok follow-up (live probes,
    ../smartbox api-notes.md 2026-09-30). The confirming ok frame (or any
    later ok frame) marks it available again.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    # Bare lost frame; no ok confirmation follows within the (test-patched)
    # unavailability delay -> Unavailable.
    mock_smartbox.generate_socket_node_unavailable(mock_device, mock_node)
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    # Recovery: an ok frame marks the node available again.
    mock_smartbox.generate_new_socket_status(mock_device, mock_node)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE


async def test_write_to_unreachable_node_marks_unavailable(
    hass, mock_smartbox, config_entry
):
    """A write whose confirmation never arrives -> Unavailable.

    The server ACKs writes to unreachable nodes without applying them
    (../smartbox api-notes.md, 2026-09-30): the confirm window expires with
    no ok frame, the one-shot discriminating GET sees the stale lost state
    and the node is marked Unavailable.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    # Node goes dead (lost frame); then a user command is sent anyway.
    mock_smartbox.generate_socket_node_unavailable(mock_device, mock_node)
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    climate_entity = hass.data["entity_components"]["climate"].get_entity(entity_id)
    status_ref = climate_entity._status
    # HA's service targeting silently DROPS unavailable entities
    # (helpers/service.py: "if not entity.available: continue"), so a user
    # command never reaches the handler while the node is Unavailable.
    # Invoke the entity method directly to exercise the real write path.
    await climate_entity.async_set_hvac_mode(HVACMode.OFF)
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()
    # The optimistic-merge dispatch after the write carries the cache's
    # lost marker; the entity skip guard must keep the last full ok
    # snapshot. Pinned by object identity (production frame bodies are
    # fresh dicts per message; the mock aliases its socket dict).
    assert climate_entity._status is status_ref
    # The confirm window expired with no ok frame; the GET shows the write
    # never landed -> still (and affirmatively) Unavailable.
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    # Power comes back: ok frame -> Available again.
    mock_smartbox.generate_new_socket_status(mock_device, mock_node)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE


async def test_write_on_live_node_stays_available(hass, mock_smartbox, config_entry):
    """Writes never false-flip a live node, even with no confirming frame.

    MockSmartbox does not push confirming frames after writes, so the
    confirm window expires here exactly like for a live node that produced
    a silent no-op write: the discriminating GET sees the written values
    merged in the mock and the node stays available.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_HVAC_MODE: HVACMode.OFF, ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == HVACMode.OFF  # write applied, not flipped dead
    assert state.state != STATE_UNAVAILABLE


async def test_hvac_write_confirming_frame_reaches_entity(
    hass, mock_smartbox, config_entry, caplog, recorder_mock
):
    """Off -> heat -> off desync regression (live finding, 2026-09-28).

    Writes optimistically merge into the node cache; a confirming websocket
    frame that equals that cache used to be deduped away, so the entity
    never saw the write until some unrelated state change. A write must
    update the entity immediately and every ok frame must be forwarded,
    even when it equals the node cache.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)

    # Known starting point: off
    mock_smartbox.generate_socket_status_update(mock_device, mock_node, {"mode": "off"})
    await hass.async_block_till_done()
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
    # node cache; it must still reach the entity
    confirmed = mock_smartbox.generate_socket_status_update(mock_device, mock_node, {})
    await hass.async_block_till_done()
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
    hass, mock_smartbox, config_entry, caplog, recorder_mock
):
    """Mode changes mirror the backend frames (E7 decision, 2026-09-28).

    HA does not assume setpoints: a mode write shows the backend's own
    intermediate view (the stale pre-switch stemp, delivered via the
    optimistic merge) and resolves to the device's value when the server
    feeds the device state back (settle round-trip, ~2-3 s live). Nothing
    is held or suppressed: every ok frame is applied and dispatched (E3).
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)
    units = (await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node))[
        "units"
    ]
    stale_stemp = (
        await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node)
    )["stemp"]

    # Start off: no set point attribute (display semantics matching the app)
    mock_smartbox.generate_socket_status_update(mock_device, mock_node, {"mode": "off"})
    await hass.async_block_till_done()
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
    mock_smartbox.generate_socket_status_update(
        mock_device, mock_node, {"stemp": "21.5"}
    )
    await hass.async_block_till_done()
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
    hass, mock_smartbox, config_entry, caplog, recorder_mock
):
    """Setpoint inside the running programme engages modified_auto (E5).

    HA must send the app's body {"stemp", "units", "mode": "modified_auto"}
    when the node runs its programme (and its setup advertises
    modified_auto_span): the plain setpoint write is stored and applied but
    the override is dropped at the next programme transition (mode stays
    auto); the app's body keeps the override and reverts with
    {"mode": "auto"} (live-probed 2026-09-28, see ../smartbox api-notes.md).
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_climate_entity_id(mock_node)
    units = (await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node))[
        "units"
    ]

    # Advertise the modified_auto feature the way the live units do
    mock_smartbox.generate_socket_setup_update(
        mock_device, mock_node, {"modified_auto_span": 6}
    )
    await hass.async_block_till_done()

    # Setpoint grid step matches the app: 0.5 for Celsius, 1.0 for
    # Fahrenheit (user-verified against the app, 2026-09-28)
    assert hass.states.get(entity_id).attributes["target_temp_step"] == (
        0.5 if units == "C" else 1.0
    )

    set_status_mock = AsyncMock(side_effect=mock_smartbox.session.set_node_status)
    mock_smartbox.session.set_node_status = set_status_mock

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: 21.5},
        blocking=True,
    )
    await hass.async_block_till_done()

    set_status_mock.assert_awaited_once()
    # HA passes the service temperature in display units; the integration
    # sends device units ([F] family: display °C, devices °F)
    device_temp = TemperatureConverter.convert(
        21.5,
        hass.config.units.temperature_unit,
        UnitOfTemperature.CELSIUS if units == "C" else UnitOfTemperature.FAHRENHEIT,
    )
    payload = set_status_mock.call_args.args[2]
    assert payload == {
        "stemp": str(device_temp),
        "units": units,
        "mode": "modified_auto",
    }

    # The device reports the override back: HA shows AUTO with the override
    mock_smartbox.generate_socket_status_update(
        mock_device, mock_node, {"mode": "modified_auto", "stemp": str(device_temp)}
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == HVACMode.AUTO
    assert round_temp(hass, state.attributes[ATTR_TEMPERATURE]) == round_temp(
        hass, convert_temp(hass, units, device_temp)
    )
    # The explicit revert renders AUTO too
    mock_smartbox.generate_socket_status_update(
        mock_device, mock_node, {"mode": "auto"}
    )
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


async def test_away(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    mock_device_1 = (await mock_smartbox.session.get_devices())[0]
    mock_smartbox.dev_data_update(mock_device_1, {"away_status": {"away": True}})
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
        mock_node_status = await mock_smartbox.session.get_status(
            mock_device_2["dev_id"], mock_node
        )
        _check_not_away_preset(
            mock_node["type"],
            mock_node_status,
            state.attributes[ATTR_PRESET_MODE],
        )


async def test_away_preset(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
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
        mock_node_status = await mock_smartbox.session.get_status(
            mock_device_2["dev_id"], mock_node
        )
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

    # test nothing is now away
    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            _check_not_away_preset(
                mock_node["type"],
                mock_node_status,
                state.attributes[ATTR_PRESET_MODE],
            )


async def test_schedule_preset(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    # Device 2 node 1 starts in manual mode, selected_temp comfort
    mock_device_2 = (await mock_smartbox.session.get_devices())[1]
    mock_device_2_node_1 = (
        await mock_smartbox.session.get_nodes(mock_device_2["dev_id"])
    )[1]
    entity_id_device_2_node_1 = get_climate_entity_id(mock_device_2_node_1)

    state = hass.states.get(entity_id_device_2_node_1)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_COMFORT

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {
            ATTR_PRESET_MODE: PRESET_SCHEDULE,
            ATTR_ENTITY_ID: entity_id_device_2_node_1,
        },
        blocking=True,
    )

    await async_update_entity(hass, entity_id_device_2_node_1)
    state = hass.states.get(entity_id_device_2_node_1)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_SCHEDULE
    mock_node_status = await mock_smartbox.session.get_status(
        mock_device_2["dev_id"], mock_device_2_node_1
    )
    assert mock_node_status["mode"] == "auto"


async def test_self_learn_preset(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    # Device 2 node 1 starts in manual mode, selected_temp comfort
    mock_device_2 = (await mock_smartbox.session.get_devices())[1]
    mock_device_2_node_1 = (
        await mock_smartbox.session.get_nodes(mock_device_2["dev_id"])
    )[1]
    entity_id_device_2_node_1 = get_climate_entity_id(mock_device_2_node_1)

    state = hass.states.get(entity_id_device_2_node_1)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_COMFORT

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {
            ATTR_PRESET_MODE: PRESET_SELF_LEARN,
            ATTR_ENTITY_ID: entity_id_device_2_node_1,
        },
        blocking=True,
    )

    await async_update_entity(hass, entity_id_device_2_node_1)
    state = hass.states.get(entity_id_device_2_node_1)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_SELF_LEARN
    mock_node_status = await mock_smartbox.session.get_status(
        mock_device_2["dev_id"], mock_device_2_node_1
    )
    assert mock_node_status["mode"] == "self_learn"


async def test_activity_preset(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    # Device 2 node 1 starts in manual mode, selected_temp comfort
    mock_device_2 = (await mock_smartbox.session.get_devices())[1]
    mock_device_2_node_1 = (
        await mock_smartbox.session.get_nodes(mock_device_2["dev_id"])
    )[1]
    entity_id_device_2_node_1 = get_climate_entity_id(mock_device_2_node_1)

    state = hass.states.get(entity_id_device_2_node_1)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_COMFORT

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {
            ATTR_PRESET_MODE: PRESET_ACTIVITY,
            ATTR_ENTITY_ID: entity_id_device_2_node_1,
        },
        blocking=True,
    )

    await async_update_entity(hass, entity_id_device_2_node_1)
    state = hass.states.get(entity_id_device_2_node_1)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_ACTIVITY
    mock_node_status = await mock_smartbox.session.get_status(
        mock_device_2["dev_id"], mock_device_2_node_1
    )
    assert mock_node_status["mode"] == "presence"


async def test_comfort_preset(hass, mock_smartbox):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="test_username_1",
        data=mock_smartbox.config[DOMAIN],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    # Device 2 node 2 starts in manual mode, selected_temp eco
    mock_device_2 = (await mock_smartbox.session.get_devices())[1]
    mock_device_2_node_2 = (
        await mock_smartbox.session.get_nodes(mock_device_2["dev_id"])
    )[5]
    entity_id_device_2_node_2 = get_climate_entity_id(mock_device_2_node_2)

    state = hass.states.get(entity_id_device_2_node_2)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_ECO

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {
            ATTR_PRESET_MODE: PRESET_COMFORT,
            ATTR_ENTITY_ID: entity_id_device_2_node_2,
        },
        blocking=True,
    )

    await async_update_entity(hass, entity_id_device_2_node_2)
    state = hass.states.get(entity_id_device_2_node_2)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_COMFORT
    mock_node_status = await mock_smartbox.session.get_status(
        mock_device_2["dev_id"], mock_device_2_node_2
    )
    assert mock_node_status["mode"] == "manual"
    assert mock_node_status["selected_temp"] == "comfort"


async def test_eco_preset(hass, mock_smartbox):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="test_username_1",
        data=mock_smartbox.config[DOMAIN],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    # Device 2 node 1 starts in manual mode, selected_temp comfort
    mock_device_2 = (await mock_smartbox.session.get_devices())[1]
    mock_device_2_node_1 = (
        await mock_smartbox.session.get_nodes(mock_device_2["dev_id"])
    )[1]
    entity_id_device_2_node_1 = get_climate_entity_id(mock_device_2_node_1)

    state = hass.states.get(entity_id_device_2_node_1)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_COMFORT

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {
            ATTR_PRESET_MODE: PRESET_ECO,
            ATTR_ENTITY_ID: entity_id_device_2_node_1,
        },
        blocking=True,
    )

    await async_update_entity(hass, entity_id_device_2_node_1)
    state = hass.states.get(entity_id_device_2_node_1)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_ECO
    mock_node_status = await mock_smartbox.session.get_status(
        mock_device_2["dev_id"], mock_device_2_node_1
    )
    assert mock_node_status["mode"] == "manual"
    assert mock_node_status["selected_temp"] == "eco"


async def test_frost_preset(hass, mock_smartbox):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="test_username_1",
        data=mock_smartbox.config[DOMAIN],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    # Device 2 node 2 starts in manual mode, selected_temp eco
    mock_device_2 = (await mock_smartbox.session.get_devices())[1]
    mock_device_2_node_2 = (
        await mock_smartbox.session.get_nodes(mock_device_2["dev_id"])
    )[5]
    entity_id_device_2_node_2 = get_climate_entity_id(mock_device_2_node_2)

    state = hass.states.get(entity_id_device_2_node_2)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_ECO

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {
            ATTR_PRESET_MODE: PRESET_FROST,
            ATTR_ENTITY_ID: entity_id_device_2_node_2,
        },
        blocking=True,
    )

    await async_update_entity(hass, entity_id_device_2_node_2)
    state = hass.states.get(entity_id_device_2_node_2)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_FROST
    mock_node_status = await mock_smartbox.session.get_status(
        mock_device_2["dev_id"], mock_device_2_node_2
    )
    assert mock_node_status["mode"] == "manual"
    assert mock_node_status["selected_temp"] == "ice"


async def test_set_hvac_mode(hass, mock_smartbox, config_entry):  # noqa: C901
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_HVAC_MODE: HVACMode.AUTO, ATTR_ENTITY_ID: ENTITY_MATCH_ALL},
        blocking=True,
    )

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)
            state = hass.states.get(entity_id)
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            if mock_node["type"] == SmartboxNodeType.HTR_MOD:
                assert mock_node_status["on"]
            assert mock_node_status["mode"] == "auto"

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_HVAC_MODE: HVACMode.HEAT, ATTR_ENTITY_ID: ENTITY_MATCH_ALL},
        blocking=True,
    )

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == HVACMode.HEAT
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            if mock_node["type"] == SmartboxNodeType.HTR_MOD:
                assert mock_node_status["on"]
            assert mock_node_status["mode"] == "manual"

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_HVAC_MODE: HVACMode.OFF, ATTR_ENTITY_ID: ENTITY_MATCH_ALL},
        blocking=True,
    )

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            # Ici si il est en boost ça reste en ON?
            assert state.state == HVACMode.OFF
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            if mock_node["type"] == SmartboxNodeType.HTR_MOD:
                assert not mock_node_status["on"]
            else:
                assert mock_node_status["mode"] == "off"


async def test_set_target_temp(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)

            state = hass.states.get(entity_id)
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            old_target_temp = state.attributes[f"current_{ATTR_TEMPERATURE}"]
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
                            ATTR_ENTITY_ID: get_climate_entity_id(mock_node),
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
                        ATTR_ENTITY_ID: get_climate_entity_id(mock_node),
                    },
                    blocking=True,
                )

                await async_update_entity(hass, entity_id)
                state = hass.states.get(entity_id)
                new_target_temp = state.attributes[ATTR_TEMPERATURE]
                # Plain stemp setpoints are rounded onto the device's
                # 0.5 °C grid before POSTing (the device quantizes
                # off-grid values silently); htr_mod (comfort_temp) and
                # Fahrenheit setpoints pass through unrounded
                if (
                    mock_node["type"] != SmartboxNodeType.HTR_MOD
                    and mock_node_status["units"] == "C"
                ):
                    assert new_target_temp == pytest.approx(round(sent * 2) / 2)
                else:
                    assert new_target_temp == pytest.approx(sent)


async def test_unavailable_at_startup(hass, mock_smartbox_unavailable, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

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


async def test_turn_on(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)

            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_HVAC_MODE: HVACMode.OFF, ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == HVACMode.OFF
            # await async_turn_off
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_HVAC_MODE: HVACMode.AUTO, ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == HVACMode.AUTO
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            if mock_node["type"] == SmartboxNodeType.HTR_MOD:
                assert mock_node_status["on"]
            assert mock_node_status["mode"] == "auto"


async def test_turn_off(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_climate_entity_id(mock_node)

            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_HVAC_MODE: HVACMode.AUTO, ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == HVACMode.AUTO
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_SET_HVAC_MODE,
                {ATTR_HVAC_MODE: HVACMode.OFF, ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )

            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == HVACMode.OFF
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            if mock_node["type"] == SmartboxNodeType.HTR_MOD:
                assert not mock_node_status["on"]
            else:
                assert mock_node_status["mode"] == "off"


async def test_boost_preset(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(CLIMATE_DOMAIN)) == 7
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    # Device 2 node 1 starts in manual mode, selected_temp comfort
    mock_device_2 = (await mock_smartbox.session.get_devices())[1]
    mock_device_2_node_2 = (
        await mock_smartbox.session.get_nodes(mock_device_2["dev_id"])
    )[2]
    entity_id_device_2_node_2 = get_climate_entity_id(mock_device_2_node_2)

    state = hass.states.get(entity_id_device_2_node_2)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_BOOST

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {
            ATTR_PRESET_MODE: PRESET_BOOST,
            ATTR_ENTITY_ID: entity_id_device_2_node_2,
        },
        blocking=True,
    )

    await async_update_entity(hass, entity_id_device_2_node_2)
    state = hass.states.get(entity_id_device_2_node_2)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_BOOST
    mock_node_status = await mock_smartbox.session.get_status(
        mock_device_2["dev_id"], mock_device_2_node_2
    )
    assert mock_node_status["mode"] == "auto"


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
            },
            PRESET_HOME,
        ),
    ],
)
def test_preset_mode(node_attributes, expected_preset):
    """Test the preset_mode property."""
    mock_node = MagicMock()
    mock_node.away = node_attributes.get("away", False)
    mock_node.boost = node_attributes.get("boost", False)
    mock_node.node_type = node_attributes.get("node_type", "other")
    mock_node.status = node_attributes.get("status", {})

    heater = SmartboxHeater(mock_node, MagicMock())

    assert heater.preset_mode == expected_preset


def test_preset_mode_invalid_selected_temp():
    """Test preset_mode is None for an unknown selected_temp."""
    mock_node = MagicMock()
    mock_node.node_type = SmartboxNodeType.HTR_MOD
    mock_node.status = {"mode": "manual", "selected_temp": "invalid_temp"}
    mock_node.away = False
    mock_node.boost = False

    heater = SmartboxHeater(mock_node, MagicMock())
    assert heater.preset_mode is None


def test_preset_mode_invalid_mode():
    """Test preset_mode is None for an unknown mode."""
    mock_node = MagicMock()
    mock_node.node_type = SmartboxNodeType.HTR_MOD
    mock_node.status = {"mode": "invalid_mode", "selected_temp": "invalid_temp"}
    mock_node.away = False
    mock_node.boost = False

    heater = SmartboxHeater(mock_node, MagicMock())

    assert heater.preset_mode is None


@pytest.mark.parametrize(
    ("node_attributes", "expected_action"),
    [
        ({"is_heating": True}, HVACAction.HEATING),
        (
            {
                "status": {"mode": "off"},
                "node_type": SmartboxNodeType.HTR_MOD,
                "boost": False,
            },
            HVACAction.OFF,
        ),
        (
            {
                "status": {"mode": "manual", "on": False},
                "node_type": SmartboxNodeType.HTR_MOD,
                "boost": False,
            },
            HVACAction.OFF,
        ),
        (
            {
                "status": {"mode": "manual", "on": False},
                "node_type": SmartboxNodeType.HTR_MOD,
                "boost": True,
            },
            HVACAction.IDLE,
        ),
        (
            {
                "status": {"mode": "manual", "on": True},
                "node_type": SmartboxNodeType.HTR_MOD,
                "boost": False,
            },
            HVACAction.IDLE,
        ),
    ],
)
def test_hvac_action(node_attributes, expected_action):
    """Test the hvac_action property."""
    mock_node = MagicMock()
    mock_node.is_heating = MagicMock(
        return_value=node_attributes.get("is_heating", False)
    )
    mock_node.status = node_attributes.get("status", {})
    mock_node.node_type = node_attributes.get("node_type", SmartboxNodeType.HTR_MOD)
    mock_node.boost = node_attributes.get("boost", False)

    heater = SmartboxHeater(mock_node, MagicMock())

    assert heater.hvac_action == expected_action


async def test_max_temp_clamped_by_device_limit(hass, mock_smartbox, config_entry):
    """Climate max_temp clamps to max_stemp_limit where advertised."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_climate_entity_id(mock_node)
    state = hass.states.get(entity_id)
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    status = await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node)
    units = status["units"]
    # Climate records min/max temp attributes converted to the HA unit
    # system; the wire limit is in the device's scale.
    expected_max = round_temp(
        hass, convert_temp(hass, units, float(setup["max_stemp_limit"]))
    )
    assert state.attributes["max_temp"] == expected_max

    # Changing the limit through the setup frame refreshes the climate
    # entity without a restart.
    mock_smartbox.generate_socket_setup_update(
        mock_device, mock_node, {"max_stemp_limit": "27.0"}
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.attributes["max_temp"] == round_temp(
        hass, convert_temp(hass, units, 27.0)
    )

    # Nodes without the field keep HA's default maximum (35 C), regardless
    # of the device's scale.
    acm_node_info = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    acm_state = hass.states.get(get_climate_entity_id(acm_node_info))
    assert acm_state.attributes["max_temp"] == pytest.approx(35.0)
