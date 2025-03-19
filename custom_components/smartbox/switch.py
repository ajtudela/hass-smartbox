"""Support for Smartbox switch entities."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SmartboxConfigEntry
from .entity import SmartBoxNodeEntity
from .models import true_radiant_available, window_mode_available

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    _: HomeAssistant,
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:  # pylint: disable=unused-argument
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox switch platform")

    switch_entities: list[SwitchEntity] = []
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
        _LOGGER.debug("Creating away switch for node %s", node.name)
        switch_entities.append(AwaySwitch(node, entry))

        if node.boost_available:
            _LOGGER.debug("Creating boost switch for node %s", node.name)
            boost_switch = BoostSwitch(node, entry)
            switch_entities.append(boost_switch)
        else:
            _LOGGER.info("Boost mode not available for node %s", node.name)

    async_add_entities(switch_entities, update_before_add=True)

    _LOGGER.debug("Finished setting up Smartbox switch platform")


class AwaySwitch(SmartBoxNodeEntity, SwitchEntity):
    """Smartbox device away switch."""

    _attr_key = "away_status"
    _attr_websocket_event = "away_status"

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn on the switch."""
        await self._node.device.set_away_status(away=True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn off the switch."""
        await self._node.device.set_away_status(away=False)
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._node.device.away


class WindowModeSwitch(SmartBoxNodeEntity, SwitchEntity):
    """Smartbox node window mode switch."""

    _attr_key = "window_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_websocket_event = "setup"

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn on the switch."""
        await self._node.set_window_mode(True)

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn off the switch."""
        await self._node.set_window_mode(False)

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._node.window_mode


class TrueRadiantSwitch(SmartBoxNodeEntity, SwitchEntity):
    """Smartbox node true radiant switch."""

    _attr_key = "true_radiant"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_websocket_event = "setup"

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn on the switch."""
        await self._node.set_true_radiant(True)

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn off the switch."""
        await self._node.set_true_radiant(False)

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._node.true_radiant


class BoostSwitch(SmartBoxNodeEntity, SwitchEntity):
    """Smartbox boost switch that activates the device's native boost mode.

    The SmartBox heaters have a built-in boost function that temporarily increases
    the temperature for a configurable amount of time. This switch provides a simple
    toggle to activate/deactivate this functionality.

    The boost temperature and duration can be configured through:
    1. The device's setup through extra_options
    2. The smartbox.set_boost_params service
    """

    _attr_key = "boost"
    _attr_websocket_event = "status"
    _attr_icon = "mdi:rocket-launch"

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state attributes."""
        return {
            "boost_temperature": self._node.boost_temp,
            "boost_duration_minutes": self._node.boost_time,
            "boost_time_remaining": self._node.remaining_boost_time,
            "boost_end_hour": f"{self._node.boost_end_min / 60:.0f}:{self._node.boost_end_min % 60:02d}"
            if self._node.remaining_boost_time
            else None,
        }

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn on boost mode."""
        _LOGGER.debug("Activating boost mode for %s", self._node.name)
        await self._node.set_status(boost=True)

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn off boost mode."""
        _LOGGER.debug("Deactivating boost mode for %s", self._node.name)
        await self._node.set_status(boost=False)

    @property
    def is_on(self) -> bool:
        """Return if boost mode is active."""
        return self._node.boost
