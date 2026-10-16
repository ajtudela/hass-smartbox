"""Tests for the Smartbox binary sensor platform."""

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.smartbox.const import DOMAIN

from .mocks import get_connected_binary_sensor_entity_id, get_entity_id_from_unique_id


async def test_connected_sensor_updates_without_polling(
    hass, mock_smartbox, config_entry
):
    """The box connectivity entity must react to websocket events, not polls.

    Regression test: the device-level connected event used to be dispatched on
    a device-id signal while the entity listened on a per-node signal, so it
    stayed frozen at its initial value forever. Connectivity now lives on the
    box device itself (one entity, not one per node).
    """
    mock_smartbox.session.get_device_connected.return_value = {"connected": True}
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    device_dict = (await mock_smartbox.session.get_devices())[0]
    runtime_device = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.devices[
        0
    ]
    assert runtime_device.dev_id == device_dict["dev_id"]

    entity_id = get_connected_binary_sensor_entity_id(device_dict)
    assert hass.states.get(entity_id).state == "on"

    # Simulate connectivity loss arriving over the websocket: no poll happens.
    runtime_device._connected(connected=False)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"

    runtime_device._connected(connected=True)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"


async def test_connected_sensor_is_on_box_device(hass, mock_smartbox, config_entry):
    """One connectivity entity per box, attached to the box device."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    for device_dict in await mock_smartbox.session.get_devices():
        entity_id = get_connected_binary_sensor_entity_id(device_dict)
        unique_id = f"{device_dict['dev_id']}_connected"
        assert (
            get_entity_id_from_unique_id(hass, BINARY_SENSOR_DOMAIN, unique_id)
            == entity_id
        )
        box_device = device_registry.async_get_device(
            identifiers={(DOMAIN, device_dict["dev_id"])}
        )
        assert box_device is not None
        entity_entry = entity_registry.async_get(entity_id)
        assert entity_entry.device_id == box_device.id
