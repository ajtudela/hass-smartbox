"""Support for Smartbox switch entities."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SMARTBOX_DEVICES, SMARTBOX_NODES
from .entity import SmartBoxDeviceEntity, SmartBoxNodeEntity
from .model import true_radiant_available, window_mode_available

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:  # pylint: disable=unused-argument
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox switch platform")

    switch_entities: list[SwitchEntity] = []
    for device in hass.data[DOMAIN][SMARTBOX_DEVICES]:
        _LOGGER.debug("Creating away switch for device %s", device.name)
        switch_entities.append(AwaySwitch(device))

    for node in hass.data[DOMAIN][SMARTBOX_NODES]:
        if window_mode_available(node):
            _LOGGER.debug("Creating window_mode switch for node %s", node.name)
            switch_entities.append(WindowModeSwitch(node))
        else:
            _LOGGER.info("Window mode not available for node %s", node.name)
        if true_radiant_available(node):
            _LOGGER.debug("Creating true_radiant switch for node %s", node.name)
            switch_entities.append(TrueRadiantSwitch(node))
        else:
            _LOGGER.info("True radiant not available for node %s", node.name)

    async_add_entities(switch_entities, True)

    _LOGGER.debug("Finished setting up Smartbox switch platform")


class AwaySwitch(SmartBoxDeviceEntity, SwitchEntity):
    """Smartbox device away switch."""

    _attr_key = "away_status"

    def turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        return self._device.set_away_status(True)

    def turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        return self._device.set_away_status(False)

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self._device.away


class WindowModeSwitch(SmartBoxNodeEntity, SwitchEntity):
    """Smartbox node window mode switch."""

    _attr_key = "window_mode"

    def turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        return self._node.set_window_mode(True)

    def turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        return self._node.set_window_mode(False)

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self._node.window_mode


class TrueRadiantSwitch(SmartBoxNodeEntity, SwitchEntity):
    """Smartbox node true radiant switch."""

    _attr_key = "true_radiant"

    def turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn on the switch."""
        return self._node.set_true_radiant(True)

    def turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn off the switch."""
        return self._node.set_true_radiant(False)

    @property
    def is_on(self):
        """Return true if the switch is on."""
        return self._node.true_radiant
