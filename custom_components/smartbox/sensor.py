"""Support for Smartbox sensor entities."""

import logging
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_LOCKED,
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    HEATER_NODE_TYPE_ACM,
    HEATER_NODE_TYPE_HTR,
    HEATER_NODE_TYPE_HTR_MOD,
    SMARTBOX_NODES,
)
from .entity import SmartBoxNodeEntity
from .model import SmartboxNode, get_temperature_unit, is_heater_node

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox sensor platform")

    # Temperature
    async_add_entities(
        [
            TemperatureSensor(node)
            for node in hass.data[DOMAIN][SMARTBOX_NODES]
            if is_heater_node(node)
        ],
        True,
    )
    # Power
    async_add_entities(
        [
            PowerSensor(node)
            for node in hass.data[DOMAIN][SMARTBOX_NODES]
            if is_heater_node(node) and node.node_type != HEATER_NODE_TYPE_HTR_MOD
        ],
        True,
    )
    # Duty Cycle and Energy
    # Only nodes of type 'htr' seem to report the duty cycle, which is needed
    # to compute energy consumption
    async_add_entities(
        [
            DutyCycleSensor(node)
            for node in hass.data[DOMAIN][SMARTBOX_NODES]
            if node.node_type == HEATER_NODE_TYPE_HTR
        ],
        True,
    )
    async_add_entities(
        [
            EnergySensor(node)
            for node in hass.data[DOMAIN][SMARTBOX_NODES]
            if node.node_type == HEATER_NODE_TYPE_HTR
        ],
        True,
    )
    # Charge Level
    async_add_entities(
        [
            ChargeLevelSensor(node)
            for node in hass.data[DOMAIN][SMARTBOX_NODES]
            if is_heater_node(node) and node.node_type == HEATER_NODE_TYPE_ACM
        ],
        True,
    )

    _LOGGER.debug("Finished setting up Smartbox sensor platform")


class SmartboxSensorBase(SmartBoxNodeEntity, SensorEntity):
    """Base class for Smartbox sensor."""

    def __init__(self, node: SmartboxNode | MagicMock) -> None:
        """Initialize the Climate Entity."""
        super().__init__(node=node)
        self._status: dict[str, Any] = {}
        self._available = False  # unavailable until we get an update
        self._samples: dict[str, Any] = {}
        self._last_update: datetime | None = None
        self._time_since_last_update: timedelta | None = None
        _LOGGER.debug("Created node unique_id=%s", self.unique_id)

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Return extra states of the sensor."""
        return {
            ATTR_LOCKED: self._status["locked"],
        }

    @property
    def available(self) -> bool:
        """Return the availability of the sensor."""
        return self._available

    async def async_update(self) -> None:
        """Get the latest data."""
        new_status = await self._node.async_update(self.hass)
        if new_status["sync_status"] == "ok":
            # update our status
            self._status = new_status
            self._available = True
            update_time = datetime.now()
            if self._last_update is not None:
                self._time_since_last_update = update_time - self._last_update
            self._last_update = update_time
        else:
            self._available = False
            self._last_update = None
            self._time_since_last_update = None

    @property
    def time_since_last_update(self) -> timedelta | None:
        """Return the time since the data have been updated."""
        return self._time_since_last_update


class TemperatureSensor(SmartboxSensorBase):
    """Smartbox heater temperature sensor."""

    _attr_key = "temperature"
    device_class = SensorDeviceClass.TEMPERATURE
    state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float:
        """Return the native value of the sensor."""
        return self._status["mtemp"]

    @property
    def native_unit_of_measurement(self) -> Any:
        """Return the unit of the sensor."""
        return get_temperature_unit(self._status)


class PowerSensor(SmartboxSensorBase):
    """Smartbox heater power sensor.

    Note: this represents the power the heater is drawing *when heating*; the
    heater is not always active over the entire period since the last update,
    even when 'active' is true. The duty cycle sensor indicates how much it
    was active. To measure energy consumption, use the corresponding energy
    sensor.
    """

    _attr_key = "power"
    device_class = SensorDeviceClass.POWER
    native_unit_of_measurement = UnitOfPower.WATT
    state_class = SensorStateClass.MEASUREMENT
    entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> float:
        """Return the native value of the sensor."""
        return self._status["power"] if self._node.is_heating(self._status) else 0


class DutyCycleSensor(SmartboxSensorBase):
    """Smartbox heater duty cycle sensor: Represents the duty cycle for the heater."""

    _attr_key = "duty_cycle"
    device_class = SensorDeviceClass.POWER_FACTOR
    native_unit_of_measurement = PERCENTAGE
    state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float:
        """Return the native value of the sensor."""
        return self._status["duty"]


class EnergySensor(SmartboxSensorBase):
    """Smartbox heater energy sensor: Represents the energy consumed by the heater."""

    _attr_key = "energy"
    device_class = SensorDeviceClass.ENERGY
    native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> float | None:
        """Return the native value of the sensor."""
        time_since_last_update = self.time_since_last_update
        if time_since_last_update is not None:
            return (
                float(self._status["power"])
                * float(self._status["duty"])
                / 100
                * time_since_last_update.seconds
                / 60
                / 60
            )
        return None


class ChargeLevelSensor(SmartboxSensorBase):
    """Smartbox storage heater charge level sensor."""

    _attr_key = "charge_level"
    device_class = SensorDeviceClass.BATTERY
    native_unit_of_measurement = PERCENTAGE
    state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the native value of the sensor."""
        return self._status["charge_level"]
