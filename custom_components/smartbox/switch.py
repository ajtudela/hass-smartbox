"""Support for Smartbox switch entities."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, EntityCategory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import voluptuous as vol

from . import SmartboxConfigEntry
from .entity import SmartBoxNodeEntity
from .model import true_radiant_available, window_mode_available

_LOGGER = logging.getLogger(__name__)

ATTR_DURATION = "duration"
SERVICE_SET_BOOST_PARAMS = "set_boost_params"

BOOST_PARAMS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_TEMPERATURE): vol.Coerce(float),
        vol.Optional(ATTR_DURATION): vol.Coerce(int),
    }
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:  # pylint: disable=unused-argument
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox switch platform")

    boost_entities = []
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
            _LOGGER.debug("Creating boost switch for heater node %s", node.name)
            boost_switch = BoostSwitch(node, entry)
            switch_entities.append(boost_switch)
            boost_entities.append(boost_switch)

    async_add_entities(switch_entities, update_before_add=True)

    # Register service to configure boost parameters
    if boost_entities:

        async def handle_set_boost_params(call: ServiceCall) -> None:
            """Handle the service call."""
            entity_id = call.data.get(ATTR_ENTITY_ID)

            entity: BoostSwitch = next(
                (e for e in boost_entities if e.entity_id == entity_id),
                None,
            )

            if not entity:
                _LOGGER.error("Entity %s not found", entity_id)
                return

            update_params = {}

            if ATTR_TEMPERATURE in call.data:
                boost_temp = str(call.data[ATTR_TEMPERATURE])
                update_params["boost_temp"] = boost_temp

            if ATTR_DURATION in call.data:
                boost_time = call.data[ATTR_DURATION]
                update_params["boost_time"] = boost_time

            if update_params:
                # Update the extra_options in the node setup
                current_extra_options = entity._node.setup.get("extra_options", {}).copy()
                current_extra_options.update(update_params)

                # Send setup update to device
                await entity._node.session.set_node_setup(
                    entity._node.device.dev_id,
                    entity._node.node_info,
                    {"extra_options": current_extra_options},
                )

                # If boost is active and duration changed, we need to update it
                if entity.is_on and ATTR_DURATION in call.data:
                    # Turn boost off and back on to reset the timer
                    await entity.async_turn_off()
                    await entity.async_turn_on()

                _LOGGER.debug(
                    "Updated boost parameters for %s: %s",
                    entity_id,
                    update_params,
                )

        hass.services.async_register(
            "smartbox",
            SERVICE_SET_BOOST_PARAMS,
            handle_set_boost_params,
            schema=BOOST_PARAMS_SCHEMA,
        )

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
        # Use the device's native boost function
        await self._node.set_status(boost=True)

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn off boost mode."""
        _LOGGER.debug("Deactivating boost mode for %s", self._node.name)
        # Turn off the device's native boost function
        await self._node.set_status(boost=False)

    @property
    def is_on(self) -> bool:
        """Return if boost mode is active."""
        return self._node.boost
