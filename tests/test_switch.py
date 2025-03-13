import logging

from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME

from custom_components.smartbox.const import DOMAIN

from .mocks import (
    get_away_status_switch_entity_id,
    get_away_status_switch_entity_name,
    get_entity_id_from_unique_id,
    get_node_unique_id,
    get_object_id,
    get_true_radiant_switch_entity_id,
    get_true_radiant_switch_entity_name,
    get_window_mode_switch_entity_id,
    get_window_mode_switch_entity_name,
)
from .test_utils import assert_log_message


async def test_away_status(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 24
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
        entity_id = get_away_status_switch_entity_id(mock_node)
        state = hass.states.get(entity_id)

        # check basic properties
        assert state.object_id.startswith(
            get_object_id(get_away_status_switch_entity_name(mock_node))
        )
        assert state.entity_id.startswith(get_away_status_switch_entity_id(mock_node))
        assert state.name == f"{mock_node['name']} Away Status"
        assert state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Away Status"
        unique_id = get_node_unique_id(mock_device, mock_node, "away_status")
        assert entity_id == get_entity_id_from_unique_id(hass, SWITCH_DOMAIN, unique_id)

    # Set device 1 to away
    mock_device_1 = (await mock_smartbox.session.get_devices())[0]
    mock_node_1 = (await mock_smartbox.session.get_nodes(mock_device_1["dev_id"]))[0]
    mock_smartbox.dev_data_update(mock_device_1, {"away_status": {"away": True}})

    entity_id = get_away_status_switch_entity_id(mock_node_1)
    await hass.helpers.entity_component.async_update_entity(entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "on"

    mock_device_2 = (await mock_smartbox.session.get_devices())[1]
    mock_node_2 = (await mock_smartbox.session.get_nodes(mock_device_2["dev_id"]))[0]
    mock_smartbox.dev_data_update(mock_device_1, {"away_status": {"away": False}})
    entity_id = get_away_status_switch_entity_id(mock_node_2)
    await hass.helpers.entity_component.async_update_entity(entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "off"

    # Turn off via HA
    entity_id = get_away_status_switch_entity_id(mock_node_1)
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.helpers.entity_component.async_update_entity(entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "off"

    # Turn device 2 to away via HA
    entity_id = get_away_status_switch_entity_id(mock_node_2)
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.helpers.entity_component.async_update_entity(entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "on"


async def test_basic_window_mode(hass, mock_smartbox, config_entry, caplog):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 24
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            entity_id = get_window_mode_switch_entity_id(mock_node)
            mock_node_setup = await mock_smartbox.session.get_node_setup(
                mock_device["dev_id"], mock_node
            )

            if "factory_options" not in mock_node_setup or not mock_node_setup[
                "factory_options"
            ].get("window_mode_available", False):
                # We shouldn't have created a switch entity for this
                assert hass.states.get(entity_id) is None
                assert_log_message(
                    caplog,
                    "custom_components.smartbox.switch",
                    logging.INFO,
                    f"Window mode not available for node {mock_node['name']}",
                )
                continue

            state = hass.states.get(entity_id)

            # check basic properties
            assert state.object_id.startswith(
                get_object_id(get_window_mode_switch_entity_name(mock_node))
            )
            assert state.name == f"{mock_node['name']} Window Mode"
            assert (
                state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Window Mode"
            )
            unique_id = get_node_unique_id(mock_device, mock_node, "window_mode")
            assert entity_id == get_entity_id_from_unique_id(
                hass, SWITCH_DOMAIN, unique_id
            )

            # Check window_mode is correct
            assert (
                state.state == "on" if mock_node_setup["window_mode_enabled"] else "off"
            )

            # Turn on window_mode via socket
            mock_smartbox.generate_socket_setup_update(
                mock_device, mock_node, {"window_mode_enabled": True}
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off window_mode via socket
            mock_smartbox.generate_socket_setup_update(
                mock_device, mock_node, {"window_mode_enabled": False}
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "off"

            # Turn on via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "off"


async def test_basic_true_radiant(hass, mock_smartbox, config_entry, caplog):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 24
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            entity_id = get_true_radiant_switch_entity_id(mock_node)
            mock_node_setup = await mock_smartbox.session.get_node_setup(
                mock_device["dev_id"], mock_node
            )

            if "factory_options" not in mock_node_setup or not mock_node_setup[
                "factory_options"
            ].get("true_radiant_available", False):
                # We shouldn't have created a switch entity for this
                assert hass.states.get(entity_id) is None
                assert_log_message(
                    caplog,
                    "custom_components.smartbox.switch",
                    logging.INFO,
                    f"True radiant not available for node {mock_node['name']}",
                )
                continue

            state = hass.states.get(entity_id)
            assert state is not None

            # check basic properties
            assert state.object_id.startswith(
                get_object_id(get_true_radiant_switch_entity_name(mock_node))
            )
            assert state.name == f"{mock_node['name']} True Radiant"
            assert (
                state.attributes[ATTR_FRIENDLY_NAME]
                == f"{mock_node['name']} True Radiant"
            )
            unique_id = get_node_unique_id(mock_device, mock_node, "true_radiant")
            assert entity_id == get_entity_id_from_unique_id(
                hass, SWITCH_DOMAIN, unique_id
            )

            # Check true_radiant is correct
            assert (
                state.state == "on" if mock_node_setup["true_radiant_enabled"] else "off"
            )

            # Turn on true_radiant via socket
            mock_smartbox.generate_socket_setup_update(
                mock_device, mock_node, {"true_radiant_enabled": True}
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off true_radiant via socket
            mock_smartbox.generate_socket_setup_update(
                mock_device, mock_node, {"true_radiant_enabled": False}
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "off"

            # Turn on via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await hass.helpers.entity_component.async_update_entity(entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "off"
