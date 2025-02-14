"""Support for Smartbox switch entities."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SmartboxConfigEntry
from .entity import SmartBoxDeviceEntity, SmartBoxNodeEntity
from .model import true_radiant_available, window_mode_available

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:  # pylint: disable=unused-argument
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox switch platform")

    switch_entities: list[SwitchEntity] = []
    for device in entry.runtime_data.devices:
        _LOGGER.debug("Creating away switch for device %s", device.name)
        switch_entities.append(AwaySwitch(device, entry))

    for node in entry.runtime_data.nodes:
        if window_mode_available(node):
            _LOGGER.debug("Creating window_mode switch for node %s", node.name)
            switch_entities.append(WindowModeSwitch(node, entry))
        else:
            _LOGGER.info("Window mode not available for node %s", node.name)
        if true_radiant_available(node):
            _LOGGER.debug("Creating true_radiant switch for node %s", node.name)
            switch_entities.append(TrueRadiantSwitch(node, entry))
        else:
            _LOGGER.info("True radiant not available for node %s", node.name)

    async_add_entities(switch_entities, True)

    _LOGGER.debug("Finished setting up Smartbox switch platform")


class AwaySwitch(SmartBoxDeviceEntity, SwitchEntity):
    """Smartbox device away switch."""

    _attr_key = "away_status"
    _attr_websocket_event = "away_status"

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        return await self._device.set_away_status(True)

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        return await self._device.set_away_status(False)

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self._device.away


class WindowModeSwitch(SmartBoxNodeEntity, SwitchEntity):
    """Smartbox node window mode switch."""

    _attr_key = "window_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_websocket_event = "setup"

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        return await self._node.set_window_mode(True)

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        return await self._node.set_window_mode(False)

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self._node.window_mode


class TrueRadiantSwitch(SmartBoxNodeEntity, SwitchEntity):
    """Smartbox node true radiant switch."""

    _attr_key = "true_radiant"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_websocket_event = "setup"

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        return await self._node.set_true_radiant(True)

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        return await self._node.set_true_radiant(False)

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self._node.true_radiant
