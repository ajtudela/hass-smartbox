from homeassistant.components.number import ATTR_VALUE
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.number import SERVICE_SET_VALUE
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME
from mocks import (
    get_entity_id_from_unique_id,
    get_node_unique_id,
    get_object_id,
    get_power_limit_number_entity_id,
    get_power_limit_number_entity_name,
)

from custom_components.smartbox.const import DOMAIN


async def test_power_limit(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(NUMBER_DOMAIN)) == 2
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components
    for mock_device in mock_smartbox.session.get_devices():
        mock_node = mock_smartbox.session.get_nodes(mock_device["dev_id"])[0]
        entity_id = get_power_limit_number_entity_id(mock_node)
        state = hass.states.get(entity_id)
        # check basic properties
        assert state.object_id.startswith(
            get_object_id(get_power_limit_number_entity_name(mock_node))
        )
        assert state.entity_id.startswith(get_power_limit_number_entity_id(mock_node))
        assert state.name == f"{mock_node['name']} Power Limit"
        assert (
            state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Power Limit"
        )
        unique_id = get_node_unique_id(mock_device, mock_node, "power_limit")
        assert entity_id == get_entity_id_from_unique_id(hass, NUMBER_DOMAIN, unique_id)

        # Starts not away
        assert state.state == "0"

    # Set device 1 power limit
    mock_device_1 = mock_smartbox.session.get_devices()[0]
    mock_node_1 = mock_smartbox.session.get_nodes(mock_device_1["dev_id"])[0]
    mock_smartbox.dev_data_update(
        mock_device_1, {"htr_system": {"setup": {"power_limit": "1000"}}}
    )

    entity_id = get_power_limit_number_entity_id(mock_node_1)
    await hass.helpers.entity_component.async_update_entity(entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "1000"

    mock_device_2 = mock_smartbox.session.get_devices()[1]
    mock_node_2 = mock_smartbox.session.get_nodes(mock_device_2["dev_id"])[0]
    entity_id = get_power_limit_number_entity_id(mock_node_2)
    await hass.helpers.entity_component.async_update_entity(entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "0"

    # Set back to 0 via HA
    entity_id = get_power_limit_number_entity_id(mock_node_1)
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 0},
        blocking=True,
    )
    await hass.helpers.entity_component.async_update_entity(entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "0"

    # Set device 2 to 500 via HA
    entity_id = get_power_limit_number_entity_id(mock_node_2)
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 500},
        blocking=True,
    )
    await hass.helpers.entity_component.async_update_entity(entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "500"
