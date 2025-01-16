import logging

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_LOCKED, STATE_UNAVAILABLE
from mocks import (
    active_or_charging_update,
    get_entity_id_from_unique_id,
    get_node_unique_id,
    get_object_id,
    get_sensor_entity_id,
    get_sensor_entity_name,
)
from pytest import approx
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_utils import convert_temp, round_temp

from custom_components.smartbox.const import (
    DOMAIN,
    HEATER_NODE_TYPE_ACM,
    HEATER_NODE_TYPE_HTR_MOD,
)

_LOGGER = logging.getLogger(__name__)


def _check_temp_state(hass, mock_node_status, state):
    assert round_temp(hass, float(state.state)) == round_temp(
        hass,
        convert_temp(hass, mock_node_status["units"], float(mock_node_status["mtemp"])),
    )


async def test_basic_temp(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 13
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in mock_smartbox.session.get_devices():
        for mock_node in mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            entity_id = get_sensor_entity_id(mock_node, "temperature")
            state = hass.states.get(entity_id)

            # check basic properties
            assert state.object_id.startswith(
                get_object_id(get_sensor_entity_name(mock_node, "temperature"))
            )
            assert state.entity_id.startswith(
                get_sensor_entity_id(mock_node, "temperature")
            )
            assert state.name == f"{mock_node['name']} Temperature"
            assert (
                state.attributes[ATTR_FRIENDLY_NAME]
                == f"{mock_node['name']} Temperature"
            )
            unique_id = get_node_unique_id(mock_device, mock_node, "temperature")
            assert entity_id == get_entity_id_from_unique_id(
                hass, SENSOR_DOMAIN, unique_id
            )

            mock_node_status = mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            assert state.attributes[ATTR_LOCKED] == mock_node_status["locked"]
            _check_temp_state(hass, mock_node_status, state)

            mock_node_status = mock_smartbox.generate_socket_status_update(
                mock_device,
                mock_node,
                {"mtemp": str(float(mock_node_status["mtemp"]) + 1)},
            )

            await hass.helpers.entity_component.async_update_entity(entity_id)
            new_state = hass.states.get(entity_id)
            assert new_state.state != state.state
            _check_temp_state(hass, mock_node_status, new_state)

            # test unavailable
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
            assert state.state != STATE_UNAVAILABLE


async def test_basic_power(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 13
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in mock_smartbox.session.get_devices():
        for mock_node in mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if mock_node["type"] == HEATER_NODE_TYPE_HTR_MOD:
                continue
            entity_id = get_sensor_entity_id(mock_node, "power")
            state = hass.states.get(entity_id)

            # check basic properties
            assert state.object_id.startswith(
                get_object_id(get_sensor_entity_name(mock_node, "power"))
            )
            assert state.entity_id.startswith(get_sensor_entity_id(mock_node, "power"))
            assert state.name == f"{mock_node['name']} Power"
            assert state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Power"
            unique_id = get_node_unique_id(mock_device, mock_node, "power")
            assert entity_id == get_entity_id_from_unique_id(
                hass, SENSOR_DOMAIN, unique_id
            )

            # make sure it's active/charging
            mock_smartbox.generate_socket_status_update(
                mock_device,
                mock_node,
                active_or_charging_update(mock_node["type"], True),
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            mock_node_status = mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            assert state.attributes[ATTR_LOCKED] == mock_node_status["locked"]
            assert float(state.state) == approx(float(mock_node_status["power"]))

            # make sure it's inactive/not charging
            mock_smartbox.generate_socket_status_update(
                mock_device,
                mock_node,
                active_or_charging_update(mock_node["type"], False),
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            mock_node_status = mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            assert float(state.state) == 0

            # test unavailable
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
            assert state.state != STATE_UNAVAILABLE


async def test_unavailable(hass, mock_smartbox_unavailable):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="test_username_1",
        data=mock_smartbox_unavailable.config[DOMAIN],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 13
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in mock_smartbox_unavailable.session.get_devices():
        for mock_node in mock_smartbox_unavailable.session.get_nodes(
            mock_device["dev_id"]
        ):
            sensor_types = (
                ["temperature"]
                if mock_node["type"] == HEATER_NODE_TYPE_HTR_MOD
                else ["temperature", "power"]
            )
            for sensor_type in sensor_types:
                entity_id = get_sensor_entity_id(mock_node, "temperature")

                state = hass.states.get(entity_id)
                assert state.state == STATE_UNAVAILABLE


async def test_basic_charge_level(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 13
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in mock_smartbox.session.get_devices():
        for mock_node in mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            # Only supported on acm nodes
            if mock_node["type"] != HEATER_NODE_TYPE_ACM:
                continue

            entity_id = get_sensor_entity_id(mock_node, "charge_level")
            state = hass.states.get(entity_id)

            # check basic properties
            assert state.object_id.startswith(
                get_object_id(get_sensor_entity_name(mock_node, "charge_level"))
            )
            assert state.entity_id.startswith(
                get_sensor_entity_id(mock_node, "charge_level")
            )
            assert state.name == f"{mock_node['name']} Charge Level"
            assert (
                state.attributes[ATTR_FRIENDLY_NAME]
                == f"{mock_node['name']} Charge Level"
            )
            unique_id = get_node_unique_id(mock_device, mock_node, "charge_level")
            assert entity_id == get_entity_id_from_unique_id(
                hass, SENSOR_DOMAIN, unique_id
            )

            # Check charge level is correct
            mock_smartbox.generate_socket_status_update(
                mock_device,
                mock_node,
                active_or_charging_update(mock_node["type"], True),
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            mock_node_status = mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            assert state.attributes[ATTR_LOCKED] == mock_node_status["locked"]
            assert int(state.state) == approx(int(mock_node_status["charge_level"]))

            # Update charge level via socket
            mock_smartbox.generate_socket_status_update(
                mock_device, mock_node, {"charge_level": 5}
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            mock_node_status = mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            assert int(state.state) == 5

            # test unavailable
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
            assert state.state != STATE_UNAVAILABLE
