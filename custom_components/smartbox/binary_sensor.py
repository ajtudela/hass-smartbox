"""Support for Smartbox sensor entities."""

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SmartboxConfigEntry
from .entity import SmartBoxNodeEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    _: HomeAssistant,
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox binary sensor platform")

    async_add_entities(
        [Connected(node, entry) for node in entry.runtime_data.nodes],
        update_before_add=True,
    )
    async_add_entities(
        [
            LockBinarySensor(node, entry)
            for node in entry.runtime_data.nodes
            if node.heater_node
        ],
        update_before_add=True,
    )
    _LOGGER.debug("Finished setting up Smartbox binary sensor platform")


class Connected(SmartBoxNodeEntity, BinarySensorEntity):
    """Smartbox device power limit."""

    _attr_key = "connected"
    _attr_websocket_event = "connected"
    device_class = BinarySensorDeviceClass.CONNECTIVITY
    entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        return self._node.device.connected


class LockBinarySensor(SmartBoxNodeEntity, BinarySensorEntity):
    """Smartbox device power limit."""

    _attr_key = "lock"
    _attr_websocket_event = "status"
    device_class = BinarySensorDeviceClass.LOCK
    entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        if self._available is True:
            return not bool(self._node.status["locked"])
        return None
