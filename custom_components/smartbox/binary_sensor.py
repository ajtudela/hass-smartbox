"""Support for Smartbox sensor entities."""

import logging
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory

from .entity import SmartboxBoxEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import SmartboxConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    _: HomeAssistant,
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox binary sensor platform")

    # Box connectivity is a device-level fact: one entity on the box device
    # itself (previously duplicated per node).
    async_add_entities(
        [Connected(device, entry) for device in entry.runtime_data.devices],
        update_before_add=True,
    )
    _LOGGER.debug("Finished setting up Smartbox binary sensor platform")


class Connected(SmartboxBoxEntity, BinarySensorEntity):
    """Smartbox box connectivity sensor."""

    _attr_key = "connected"
    _attr_websocket_event = "connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on."""
        return self._device.connected
