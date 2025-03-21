import logging
from unittest.mock import MagicMock

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.components.climate.const import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
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
)
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
    active_or_charging_update,
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

            await hass.helpers.entity_component.async_update_entity(entity_id)
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
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            assert state.state == STATE_UNAVAILABLE

            mock_node_status = mock_smartbox.generate_new_socket_status(
                mock_device, mock_node
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            _check_state(hass, mock_node, mock_node_status, state)


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
        await hass.helpers.entity_component.async_update_entity(entity_id)
        state = hass.states.get(entity_id)
        assert state.attributes[ATTR_PRESET_MODE] == PRESET_AWAY
    # but all device_2's should still be home
    mock_device_2 = (await mock_smartbox.session.get_devices())[1]
    for mock_node in await mock_smartbox.session.get_nodes(mock_device_2["dev_id"]):
        if not is_heater_node(mock_node):
            continue
        entity_id = get_climate_entity_id(mock_node)
        await hass.helpers.entity_component.async_update_entity(entity_id)
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
        await hass.helpers.entity_component.async_update_entity(entity_id)
        state = hass.states.get(entity_id)
        assert state.attributes[ATTR_PRESET_MODE] == PRESET_AWAY
    # but all device_2's should still be home
    mock_device_2 = (await mock_smartbox.session.get_devices())[1]
    for mock_node in await mock_smartbox.session.get_nodes(mock_device_2["dev_id"]):
        if not is_heater_node(mock_node):
            continue
        entity_id = get_climate_entity_id(mock_node)
        await hass.helpers.entity_component.async_update_entity(entity_id)
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
            await hass.helpers.entity_component.async_update_entity(entity_id)
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

    await hass.helpers.entity_component.async_update_entity(entity_id_device_2_node_1)
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

    await hass.helpers.entity_component.async_update_entity(entity_id_device_2_node_1)
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

    await hass.helpers.entity_component.async_update_entity(entity_id_device_2_node_1)
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

    await hass.helpers.entity_component.async_update_entity(entity_id_device_2_node_2)
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

    await hass.helpers.entity_component.async_update_entity(entity_id_device_2_node_1)
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

    await hass.helpers.entity_component.async_update_entity(entity_id_device_2_node_2)
    state = hass.states.get(entity_id_device_2_node_2)
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_FROST
    mock_node_status = await mock_smartbox.session.get_status(
        mock_device_2["dev_id"], mock_device_2_node_2
    )
    assert mock_node_status["mode"] == "manual"
    assert mock_node_status["selected_temp"] == "ice"


async def test_set_hvac_mode(hass, mock_smartbox, config_entry):
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
            await hass.helpers.entity_component.async_update_entity(entity_id)
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
            await hass.helpers.entity_component.async_update_entity(entity_id)
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
                await hass.services.async_call(
                    CLIMATE_DOMAIN,
                    SERVICE_SET_TEMPERATURE,
                    {
                        ATTR_TEMPERATURE: old_target_temp + 1,
                        ATTR_ENTITY_ID: get_climate_entity_id(mock_node),
                    },
                    blocking=True,
                )

                await hass.helpers.entity_component.async_update_entity(entity_id)
                state = hass.states.get(entity_id)
                new_target_temp = state.attributes[ATTR_TEMPERATURE]
                assert new_target_temp == pytest.approx(old_target_temp + 1)


async def test_hvac_action(hass, mock_smartbox, config_entry):
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

            mock_smartbox.generate_socket_status_update(
                mock_device,
                mock_node,
                active_or_charging_update(node_type=mock_node["type"], active=False),
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE

            mock_smartbox.generate_socket_status_update(
                mock_device,
                mock_node,
                active_or_charging_update(node_type=mock_node["type"], active=True),
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING

            mock_smartbox.generate_socket_status_update(
                mock_device,
                mock_node,
                active_or_charging_update(node_type=mock_node["type"], active=False),
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            assert state.attributes[ATTR_HVAC_ACTION] in [
                HVACAction.OFF,
                HVACAction.IDLE,
            ]


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

            await hass.helpers.entity_component.async_update_entity(entity_id)
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
            await hass.helpers.entity_component.async_update_entity(entity_id)
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

            await hass.helpers.entity_component.async_update_entity(entity_id)
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

            await hass.helpers.entity_component.async_update_entity(entity_id)
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

    await hass.helpers.entity_component.async_update_entity(entity_id_device_2_node_2)
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
    heater._status = mock_node.status

    assert heater.preset_mode == expected_preset


def test_preset_mode_invalid_selected_temp():
    """Test preset_mode raises ValueError for invalid selected_temp."""
    mock_node = MagicMock()
    mock_node.node_type = SmartboxNodeType.HTR_MOD
    mock_node.status = {"mode": "manual", "selected_temp": "invalid_temp"}
    mock_node.away = False
    mock_node.boost = False

    heater = SmartboxHeater(mock_node, MagicMock())
    heater._status = mock_node.status
    with pytest.raises(ValueError, match="Unexpected 'selected_temp' value"):
        _ = heater.preset_mode


def test_preset_mode_invalid_mode():
    """Test preset_mode raises ValueError for invalid mode."""
    mock_node = MagicMock()
    mock_node.node_type = SmartboxNodeType.HTR_MOD
    mock_node.status = {"mode": "invalid_mode", "selected_temp": "invalid_temp"}
    mock_node.away = False
    mock_node.boost = False

    heater = SmartboxHeater(mock_node, MagicMock())
    heater._status = mock_node.status

    with pytest.raises(ValueError, match="Unknown smartbox node mode"):
        _ = heater.preset_mode
