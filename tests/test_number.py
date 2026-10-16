from homeassistant.components.number import ATTR_VALUE, DOMAIN as NUMBER_DOMAIN
from homeassistant.components.number.const import SERVICE_SET_VALUE
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME, UnitOfTemperature
from homeassistant.helpers.entity_component import async_update_entity
from homeassistant.util.unit_conversion import TemperatureConverter
import pytest

from custom_components.smartbox.const import DOMAIN, SERVICE_SET_BOOST_PARAMS

from .const import MOCK_SMARTBOX_DEVICE_POWER
from .mocks import (
    get_away_offset_entity_id,
    get_away_offset_entity_name,
    get_boost_duration_entity_id,
    get_boost_duration_entity_name,
    get_boost_temperature_entity_id,
    get_boost_temperature_entity_name,
    get_device_unique_id,
    get_entity_id_from_unique_id,
    get_max_temperature_entity_id,
    get_max_temperature_entity_name,
    get_node_unique_id,
    get_object_id,
    get_power_limit_number_entity_id,
    get_power_limit_number_entity_name,
    get_prog_temp_entity_id,
)
from .test_utils import convert_temp, round_temp


async def test_power_limit(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(NUMBER_DOMAIN)) == 26
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components
    # One power limit entity per box device, always created (0 = no limit).
    for mock_device in await mock_smartbox.session.get_devices():
        entity_id = get_entity_id_from_unique_id(
            hass, NUMBER_DOMAIN, get_device_unique_id(mock_device, "power_limit")
        )
        state = hass.states.get(entity_id)
        # check basic properties
        assert state.object_id.startswith(
            get_object_id(get_power_limit_number_entity_name(mock_device))
        )
        assert state.name == f"{mock_device['name']} Power Limit"
        assert (
            state.attributes[ATTR_FRIENDLY_NAME]
            == f"{mock_device['name']} Power Limit"
        )
        assert entity_id == get_power_limit_number_entity_id(mock_device)
        assert state.attributes["max"] == 60000
        assert state.attributes["min"] == 0
        assert state.state == str(MOCK_SMARTBOX_DEVICE_POWER[mock_device["dev_id"]])

    mock_device_1 = (await mock_smartbox.session.get_devices())[0]
    runtime_device = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.devices[
        0
    ]
    assert runtime_device.dev_id == mock_device_1["dev_id"]

    entity_id = get_power_limit_number_entity_id(mock_device_1)
    await async_update_entity(hass, entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "1500"

    entity_id = get_power_limit_number_entity_id(mock_device_1)
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 500},
        blocking=True,
    )
    state = hass.states.get(entity_id)
    assert state.state == "500"
    assert runtime_device.power_limit == 500


async def test_boost_temperature(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(NUMBER_DOMAIN)) == 26
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components
    mock_device = (await mock_smartbox.session.get_devices())[1]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[3]
    entity_id = get_boost_temperature_entity_id(mock_node)
    state = hass.states.get(entity_id)
    # check basic properties
    assert state.object_id.startswith(
        get_object_id(get_boost_temperature_entity_name(mock_node))
    )
    assert state.entity_id.startswith(get_boost_temperature_entity_id(mock_node))
    assert state.name == f"{mock_node['name']} Boost Temperature"
    assert (
        state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Boost Temperature"
    )
    unique_id = get_node_unique_id(mock_device, mock_node, "config_boost_temperature")
    assert entity_id == get_entity_id_from_unique_id(hass, NUMBER_DOMAIN, unique_id)
    min_val = state.attributes.get("min")
    max_val = state.attributes.get("max")
    # Bounds follow the device scale (5-30 °C / 41-86 °F), which is the same
    # span once HA converts to its own unit system.
    assert min_val == pytest.approx(5.0, abs=0.5)
    assert max_val == pytest.approx(30.0, abs=0.5)
    test_value = round((min_val + max_val) / 2, 1)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: test_value},
        blocking=True,
    )
    await async_update_entity(hass, entity_id)
    state = hass.states.get(entity_id)
    assert state.state == str(test_value)


async def test_boost_duration(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(NUMBER_DOMAIN)) == 26
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components
    mock_device = (await mock_smartbox.session.get_devices())[1]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[3]
    entity_id = get_boost_duration_entity_id(mock_node)
    state = hass.states.get(entity_id)
    # check basic properties
    assert state.object_id.startswith(
        get_object_id(get_boost_duration_entity_name(mock_node))
    )
    assert state.entity_id.startswith(get_boost_duration_entity_id(mock_node))
    assert state.name == f"{mock_node['name']} Boost Duration"
    assert state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Boost Duration"
    unique_id = get_node_unique_id(mock_device, mock_node, "config_boost_duration")
    assert entity_id == get_entity_id_from_unique_id(hass, NUMBER_DOMAIN, unique_id)
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 120.0},
        blocking=True,
    )
    # Faut simuler le retour de la websocket
    await async_update_entity(hass, entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "120.0"


async def test_set_boost_params_service_targets_entities(
    hass, mock_smartbox, config_entry
):
    """Test the smartbox.set_boost_params service with entity_id targets."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[1]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[3]
    temp_entity_id = get_boost_temperature_entity_id(mock_node)
    duration_entity_id = get_boost_duration_entity_id(mock_node)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_BOOST_PARAMS,
        {
            ATTR_ENTITY_ID: [temp_entity_id, duration_entity_id],
            "temperature": 21.5,
            "duration": 120,
        },
        blocking=True,
    )

    # Simulate the websocket state refresh
    await async_update_entity(hass, temp_entity_id)
    await async_update_entity(hass, duration_entity_id)
    mock_node_status = await mock_smartbox.session.get_status(
        mock_device["dev_id"], mock_node
    )
    state = hass.states.get(temp_entity_id)
    expected_temp = round_temp(
        hass, convert_temp(hass, mock_node_status["units"], 21.5)
    )
    assert state.state == str(expected_temp)
    state = hass.states.get(duration_entity_id)
    assert state.state == "120.0"

    # The service must send merged extra_options, preserving unrelated keys
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    assert setup["extra_options"]["boost_temp"] == "21.5"
    assert setup["extra_options"]["boost_time"] == 120


async def test_away_offset(hass, mock_smartbox, config_entry):
    """The away offset number tracks the setup field and writes it back."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_away_offset_entity_id(mock_node)
    state = hass.states.get(entity_id)
    # check basic properties
    assert state.object_id.startswith(
        get_object_id(get_away_offset_entity_name(mock_node))
    )
    assert state.name == f"{mock_node['name']} Away Offset"
    assert state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Away Offset"
    unique_id = get_node_unique_id(mock_device, mock_node, "away_offset")
    assert entity_id == get_entity_id_from_unique_id(hass, NUMBER_DOMAIN, unique_id)
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    assert float(state.state) == float(setup["away_offset"])
    # No device class: bounds are native and not unit-converted; they
    # differ per device scale (app-verified: 0-5 °C in 0.5 steps, or
    # 0-9 °F in whole steps).
    status = await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node)
    if status["units"] == "C":
        assert state.attributes["min"] == 0
        assert state.attributes["max"] == 5
        assert state.attributes["step"] == 0.5
    else:
        assert state.attributes["min"] == 0
        assert state.attributes["max"] == 9
        assert state.attributes["step"] == 1

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 4.5},
        blocking=True,
    )
    await async_update_entity(hass, entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "4.5"
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    assert setup["away_offset"] == "4.5"


async def test_max_temperature(hass, mock_smartbox, config_entry):
    """The max temperature number exists only where the setup advertises it."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_max_temperature_entity_id(mock_node)
    state = hass.states.get(entity_id)
    # check basic properties
    assert state.object_id.startswith(
        get_object_id(get_max_temperature_entity_name(mock_node))
    )
    assert state.name == f"{mock_node['name']} Maximum"
    assert (
        state.attributes[ATTR_FRIENDLY_NAME]
        == f"{mock_node['name']} Maximum"
    )
    unique_id = get_node_unique_id(mock_device, mock_node, "max_temperature")
    assert entity_id == get_entity_id_from_unique_id(hass, NUMBER_DOMAIN, unique_id)

    status = await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node)
    units = status["units"]
    device_unit = (
        UnitOfTemperature.CELSIUS if units == "C" else UnitOfTemperature.FAHRENHEIT
    )
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    # TEMPERATURE-class numbers display in the HA instance's unit system.
    expected_initial = round_temp(
        hass, convert_temp(hass, units, float(setup["max_stemp_limit"]))
    )
    assert float(state.state) == expected_initial
    # TEMPERATURE-class numbers display bounds in the HA instance's unit
    # system (metric °C in tests); integer native bounds render with
    # floor/ceil at 0 decimals. Native: 16-30 °C in 1-degree steps, or
    # 60-86 °F in 2-degree steps (app-verified).
    if units == "C":
        assert state.attributes["min"] == 16
        assert state.attributes["max"] == 30
        assert state.attributes["step"] == 1
    else:
        assert state.attributes["min"] == 15.5  # floor(15.6 °C from 60 °F)
        assert state.attributes["max"] == 30.0  # ceil(30.0 °C from 86 °F)
        assert state.attributes["step"] == 2

    # Write via HA's service: values are in HA units, stored in device units.
    min_val = state.attributes["min"]
    max_val = state.attributes["max"]
    test_value = round((min_val + max_val) / 2, 1)
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: test_value},
        blocking=True,
    )
    await async_update_entity(hass, entity_id)
    state = hass.states.get(entity_id)
    assert float(state.state) == test_value
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    expected_native = TemperatureConverter.convert(
        test_value, hass.config.units.temperature_unit, device_unit
    )
    assert float(setup["max_stemp_limit"]) == pytest.approx(expected_native)

    # The acm node's setup has no max_stemp_limit: no entity.
    acm_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    assert hass.states.get(get_max_temperature_entity_id(acm_node)) is None


async def test_prog_temps(hass, mock_smartbox, config_entry):
    """Frost/Eco/Comfort profile temps read status and write the 4-key body."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    status = await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node)
    units = status["units"]
    status_keys = {"frost": "ice_temp", "eco": "eco_temp", "comfort": "comf_temp"}
    entity_ids = {
        profile: get_prog_temp_entity_id(mock_node, profile)
        for profile in status_keys
    }
    for profile, entity_id in entity_ids.items():
        state = hass.states.get(entity_id)
        assert state.name == f"{mock_node['name']} {profile.capitalize()}"
        assert (
            state.attributes[ATTR_FRIENDLY_NAME]
            == f"{mock_node['name']} {profile.capitalize()}"
        )
        expected_display = round_temp(
            hass, convert_temp(hass, units, float(status[status_keys[profile]]))
        )
        assert float(state.state) == expected_display

    # Setting one profile temp sends the app-shaped 4-key body: the
    # untouched profiles go out with their current values plus units.
    # Write via HA's service (HA units), stored in the device's scale.
    eco_entity_id = entity_ids["eco"]
    eco_state = hass.states.get(eco_entity_id)
    test_value = round(
        (eco_state.attributes["min"] + eco_state.attributes["max"]) / 2, 1
    )
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: eco_entity_id, ATTR_VALUE: test_value},
        blocking=True,
    )
    for entity_id in entity_ids.values():
        await async_update_entity(hass, entity_id)
    new_status = await mock_smartbox.session.get_status(
        mock_device["dev_id"], mock_node
    )
    device_unit = (
        UnitOfTemperature.CELSIUS if units == "C" else UnitOfTemperature.FAHRENHEIT
    )
    expected_native = TemperatureConverter.convert(
        test_value, hass.config.units.temperature_unit, device_unit
    )
    assert float(new_status["eco_temp"]) == pytest.approx(expected_native)
    # Untouched values were sent back unchanged.
    assert new_status["ice_temp"] == status["ice_temp"]
    assert new_status["comf_temp"] == status["comf_temp"]
    assert new_status["units"] == units
    assert float(hass.states.get(eco_entity_id).state) == test_value
    for profile in ("frost", "comfort"):
        expected_display = round_temp(
            hass,
            convert_temp(hass, units, float(status[status_keys[profile]])),
        )
        assert float(hass.states.get(entity_ids[profile]).state) == expected_display

    # htr_mod nodes manage profiles via comfort_temp/eco_offset: no entities.
    other_device = (await mock_smartbox.session.get_devices())[1]
    other_node = (await mock_smartbox.session.get_nodes(other_device["dev_id"]))[1]
    assert hass.states.get(get_prog_temp_entity_id(other_node, "eco")) is None
