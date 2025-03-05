"""Support for Smartbox sensor entities."""

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfPower, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SmartboxConfigEntry
from .const import DEFAULT_BOOST_TIME
from .entity import SmartBoxDeviceEntity, SmartBoxNodeEntity
from .model import get_temperature_unit

_LOGGER = logging.getLogger(__name__)
_MAX_POWER_LIMIT = 9999


async def async_setup_entry(
    _: HomeAssistant,
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox number platform")

    # Add power limit entities
    async_add_entities(
        [
            PowerLimit(device, entry)
            for device in entry.runtime_data.devices
            if device.power_limit != 0
        ],
        update_before_add=True,
    )
    # Add boost temperature and duration entities for each heater
    async_add_entities(
        [
            ConfigBoostTemperature(node, entry)
            for node in entry.runtime_data.nodes
            if node.boost_available
        ],
        update_before_add=True,
    )
    async_add_entities(
        [
            ConfigBoostDuration(node, entry)
            for node in entry.runtime_data.nodes
            if node.boost_available
        ],
        update_before_add=True,
    )
    _LOGGER.debug("Finished setting up Smartbox number platform")


class PowerLimit(SmartBoxDeviceEntity, NumberEntity):
    """Smartbox device power limit."""

    _attr_key = "power_limit"
    _attr_websocket_event = "power_limit"
    _websocket_event = "power_limit"
    native_max_value: float = _MAX_POWER_LIMIT
    _attr_entity_category = EntityCategory.CONFIG
    native_unit_of_measurement = UnitOfPower.WATT

    @property
    def native_value(self) -> float:
        """Return the native value of the number."""
        return self._device.power_limit

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        await self._device.set_power_limit(int(value))
        self.async_write_ha_state()


class ConfigBoostTemperature(SmartBoxNodeEntity, NumberEntity):
    """Smartbox boost temperature control."""

    _attr_key = "config_boost_temperature"
    _attr_websocket_event = "setup"
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE

    _attr_native_min_value: float = 5.0
    _attr_native_max_value: float = 30.0
    _attr_native_step: float = 0.5

    @property
    def native_value(self) -> float:
        """Return the current boost temperature."""
        return self._node.boost_temp

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        if unit := get_temperature_unit(self._status) is not None:
            return unit
        return UnitOfTemperature.CELSIUS

    async def async_set_native_value(self, value: float) -> None:
        """Set the boost temperature."""
        await self._node.set_extra_options({"boost_temp": str(value)})


class ConfigBoostDuration(SmartBoxNodeEntity, NumberEntity):
    """Smartbox boost duration control."""

    _attr_key = "config_boost_duration"
    _attr_websocket_event = "setup"
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = NumberDeviceClass.DURATION

    _attr_native_min_value: float = DEFAULT_BOOST_TIME
    _attr_native_max_value: float = 240.0
    _attr_native_step: float = 60.0

    @property
    def native_value(self) -> float:
        """Return the current boost duration."""
        return self._node.boost_time

    async def async_set_native_value(self, value: float) -> None:
        """Set the boost duration."""
        await self._node.set_extra_options({"boost_time": int(value)})
