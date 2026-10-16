"""Tests for Smartbox select entities."""

from homeassistant.components.select import ATTR_OPTION, DOMAIN as SELECT_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME

from .mocks import (
    get_entity_id_from_unique_id,
    get_node_unique_id,
    get_object_id,
    get_priority_entity_id,
    get_priority_entity_name,
)


async def test_radiator_priority(hass, mock_smartbox, config_entry):
    """The priority select exists only where the setup advertises it."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    # Only the fw-1.9-family htr node carries priority in its setup.
    assert len(hass.states.async_entity_ids(SELECT_DOMAIN)) == 1

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_priority_entity_id(mock_node)
    state = hass.states.get(entity_id)
    # check basic properties
    assert state.object_id.startswith(
        get_object_id(get_priority_entity_name(mock_node))
    )
    assert state.name == f"{mock_node['name']} Radiator Priority"
    assert (
        state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Radiator Priority"
    )
    unique_id = get_node_unique_id(mock_device, mock_node, "priority")
    assert entity_id == get_entity_id_from_unique_id(hass, SELECT_DOMAIN, unique_id)
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    assert state.state == setup["priority"]
    assert state.attributes["options"] == ["low", "medium", "high"]

    # Selecting an option writes the setup field; the entity state picks it
    # up from the confirming websocket setup frame (same flow as the
    # window-mode switch writes).
    await hass.services.async_call(
        SELECT_DOMAIN,
        "select_option",
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "high"},
        blocking=True,
    )
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    assert setup["priority"] == "high"

    # A websocket setup frame refreshes the entity too.
    mock_smartbox.generate_socket_setup_update(
        mock_device, mock_node, {"priority": "high"}
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "high"

    mock_smartbox.generate_socket_setup_update(
        mock_device, mock_node, {"priority": "medium"}
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "medium"

    # The acm node's setup has no priority: no select entity.
    acm_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    assert hass.states.get(get_priority_entity_id(acm_node)) is None
