"""Support for Smartbox sensor entities."""

import logging

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SMARTBOX_DEVICES
from .entity import SmartBoxDeviceEntity

_LOGGER = logging.getLogger(__name__)
_MAX_POWER_LIMIT = 9999


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox number platform")

    async_add_entities(
        [PowerLimit(device) for device in hass.data[DOMAIN][SMARTBOX_DEVICES]],
        True,
    )
    _LOGGER.debug("Finished setting up Smartbox number platform")


class PowerLimit(SmartBoxDeviceEntity, NumberEntity):
    """Smartbox device power limit."""

    _attr_key = "power_limit"
    native_max_value: float = _MAX_POWER_LIMIT

    @property
    def native_value(self) -> float:
        """Return the native value of the number."""
        return self._device.power_limit

    def set_native_value(self, value: float) -> None:
        """Update the current value."""
        self._device.set_power_limit(int(value))
