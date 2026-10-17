"""Support for Smartbox switch entities."""

import logging
from typing import TYPE_CHECKING

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import SmartboxBoxEntity, SmartBoxNodeEntity
from .node import (
    max_temp_limit_available,
    true_radiant_available,
    window_mode_available,
)
from .protocol import get_boost_end_datetime

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import SmartboxConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    _: HomeAssistant,
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:  # pylint: disable=unused-argument
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox switch platform")

    switch_entities: list[SwitchEntity] = []
    # Device-level switches live on the box device itself.
    for device in entry.runtime_data.devices:
        _LOGGER.debug("Creating away switch for box %s", device.dev_id)
        switch_entities.append(AwaySwitch(device, entry))
        _LOGGER.debug("Creating no power limit switch for box %s", device.dev_id)
        switch_entities.append(NoPowerLimitSwitch(device, entry))
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

        if node.boost_available:
            _LOGGER.debug("Creating boost switch for node %s", node.name)
            boost_switch = BoostSwitch(node, entry)
            switch_entities.append(boost_switch)
        else:
            _LOGGER.info("Boost mode not available for node %s", node.name)

        if node.heater_node:
            _LOGGER.debug("Creating lock switch for node %s", node.name)
            switch_entities.append(ChildLockSwitch(node, entry))

        if max_temp_limit_available(node):
            _LOGGER.debug(
                "Creating max temperature limit switch for node %s", node.name
            )
            switch_entities.append(MaxTemperatureLimitSwitch(node, entry))

    async_add_entities(switch_entities, update_before_add=True)

    _LOGGER.debug("Finished setting up Smartbox switch platform")


class AwaySwitch(SmartboxBoxEntity, SwitchEntity):
    """Smartbox device away switch (box device level)."""

    _attr_key = "away_status"
    _attr_websocket_event = "away_status"

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn on the switch."""
        await self._device.set_away_status(away=True)

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn off the switch."""
        await self._device.set_away_status(away=False)

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._device.away

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Return the full away status as attributes."""
        return self._device.away_status


class NoPowerLimitSwitch(SmartboxBoxEntity, SwitchEntity):
    """Smartbox device "no power limit" switch (box device level).

    On the wire the power limit is a single integer where 0 means "no
    limit" (the web app renders it as this toggle plus an editfield for
    the maximum power shared by all radiators). Turning the switch off
    restores the last known non-zero limit; when none is known (typical
    after a restart with no limit active) nothing is written and a
    warning is logged — set a value via the power limit number instead.
    """

    _attr_key = "no_power_limit"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_websocket_event = "power_limit"

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn on the switch (remove the limit)."""
        await self._device.set_power_limit(0)

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn off the switch (restore the last known limit)."""
        limit = self._device.last_nonzero_power_limit
        if limit is None:
            _LOGGER.warning(
                "Cannot restore a power limit for device %s: no previous "
                "limit known since startup. Set a value on the power limit "
                "number entity instead.",
                self._device.dev_id,
            )
            return
        await self._device.set_power_limit(limit)

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._device.no_power_limit


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
            "boost_end_time": get_boost_end_datetime(self._node.boost_end_min).strftime(
                "%H:%M"
            )
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


class ChildLockSwitch(SmartBoxNodeEntity, SwitchEntity):
    """Smartbox node child lock switch.

    The heater's ``locked`` status is the keypad/panel lockout (child lock):
    while engaged, the buttons on the unit itself are ignored. On the switch
    this means on == child lock engaged, so no inversion of the status value
    is needed (the old binary sensor inverted because ``LOCK`` device class
    means on == unlocked).

    Writes go through the single-key ``set_status`` path (live finding:
    multi-key status POSTs are silently partially applied, api-notes.md).
    """

    _attr_key = "lock"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:lock"
    _attr_websocket_event = "status"

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn on the child lock."""
        await self._node.set_status(locked=True)

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Turn off the child lock."""
        await self._node.set_status(locked=False)

    @property
    def is_on(self) -> bool:
        """Return true if the child lock is engaged."""
        return self._node.locked


class MaxTemperatureLimitSwitch(SmartBoxNodeEntity, SwitchEntity, RestoreEntity):
    """Smartbox node maximum temperature limit on/off switch.

    The wire has no separate enable key: the fw-1.9-family
    max_stemp_limit setup field carries "0.0" when the limit is disabled
    (api-notes live fixtures), which is what the app's own toggle writes.
    Turning the switch back on restores the last non-zero limit seen.
    The limit's value itself is the "Maximum" number entity on the same
    node.

    Because toggle-off erases the value on the wire, the last known
    limit is persisted in this entity's restored state ("last_limit"
    attribute) and seeded back into the node's memory on HA start --
    without this, a restart would reset a re-enable to
    MAX_TEMP_LIMIT_DEFAULT.
    """

    _attr_key = "max_temperature_limit"
    _attr_websocket_event = "setup"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:thermometer-chevron-up"

    async def async_added_to_hass(self) -> None:
        """Seed the node's last-limit memory from the restored state."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is None:
            return
        limit = last.attributes.get("last_limit")
        if limit is None:
            return
        try:
            self._node.remember_max_stemp_limit(float(limit))
        except TypeError, ValueError:
            _LOGGER.warning(
                "Ignoring restored non-numeric last_limit %r for node %s",
                limit,
                self._node.name,
            )

    @property
    def extra_state_attributes(self) -> dict:
        """Return state attributes."""
        return {"last_limit": self._node.last_max_stemp_limit}

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Enable the maximum temperature limit."""
        await self._node.set_max_temp_limit_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ANN003, ARG002
        """Disable the maximum temperature limit."""
        await self._node.set_max_temp_limit_enabled(False)

    @property
    def is_on(self) -> bool:
        """Return true if the maximum temperature limit is enabled."""
        return self._node.max_temp_limit_enabled
