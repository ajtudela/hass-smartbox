"""Support for Smartbox sensor entities."""

from datetime import datetime, timedelta
import logging
import math
import time
from unittest.mock import MagicMock

from dateutil import tz
from homeassistant.components.recorder import DOMAIN as RECORDER_DOMAIN, get_instance
from homeassistant.components.recorder.models.statistics import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_short_term_statistics,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_LOCKED,
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt

from . import SmartboxConfigEntry
from .const import (
    CONF_HISTORY_CONSUMPTION,
    CONF_TIMEDELTA_POWER,
    DEFAULT_TIMEDELTA_POWER,
    HistoryConsumptionStatus,
    SmartboxNodeType,
)
from .entity import SmartBoxNodeEntity
from .models import SmartboxNode, get_temperature_unit

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=15)


async def async_setup_entry(
    _: HomeAssistant,
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox sensor platform")
    # Temperature
    async_add_entities(
        [
            TemperatureSensor(node, entry)
            for node in entry.runtime_data.nodes
            if node.heater_node
        ],
        update_before_add=True,
    )
    # Power
    async_add_entities(
        [
            PowerSensor(node, entry)
            for node in entry.runtime_data.nodes
            # if is_heater_node(node) and node.node_type != SmartboxNodeType.HTR_MOD
        ],
        update_before_add=True,
    )
    # Duty Cycle and Energy
    # Only nodes of type 'htr' seem to report the duty cycle, which is needed
    # to compute energy consumption
    async_add_entities(
        [
            DutyCycleSensor(node, entry)
            for node in entry.runtime_data.nodes
            if node.node_type == SmartboxNodeType.HTR
        ],
        update_before_add=True,
    )
    async_add_entities(
        [TotalConsumptionSensor(node, entry) for node in entry.runtime_data.nodes],
        update_before_add=True,
    )

    # Charge Level
    async_add_entities(
        [
            ChargeLevelSensor(node, entry)
            for node in entry.runtime_data.nodes
            if node.heater_node and node.node_type == SmartboxNodeType.ACM
        ],
        update_before_add=True,
    )
    async_add_entities(
        [
            BoostEndTimeSensor(node, entry)
            for node in entry.runtime_data.nodes
            if node.boost_available
        ],
        update_before_add=True,
    )
    _LOGGER.debug("Finished setting up Smartbox sensor platform")


class SmartboxSensorBase(SmartBoxNodeEntity, SensorEntity):
    """Base class for Smartbox sensor."""

    def __init__(
        self,
        node: SmartboxNode | MagicMock,
        entry: SmartboxConfigEntry,
    ) -> None:
        """Initialize the Climate Entity."""
        super().__init__(node=node, entry=entry)
        self.config_entry = entry
        self._attr_websocket_event = "status"
        _LOGGER.debug("Created node unique_id=%s", self.unique_id)

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Return extra states of the sensor."""
        return {
            ATTR_LOCKED: self._node.status["locked"],
        }

    @property
    def available(self) -> bool:
        """Return the availability of the sensor."""
        return self._available


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
    def native_unit_of_measurement(self) -> None | UnitOfTemperature:
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

    async def async_added_to_hass(self) -> None:
        """When added to hass."""
        await super().async_added_to_hass()
        if self._node.node_type == SmartboxNodeType.PMO:
            self._attr_should_poll = True
            self.async_on_remove(
                async_track_time_interval(
                    self.hass,
                    self._async_update_pmo,
                    timedelta(
                        seconds=self.config_entry.options.get(
                            CONF_TIMEDELTA_POWER, DEFAULT_TIMEDELTA_POWER
                        )
                    ),
                    name=f"Update PMO Power - {self.name}",
                    cancel_on_shutdown=True,
                )
            )

    async def _async_update_pmo(self, _) -> None:  # noqa: ANN001
        """Get the latest data."""
        if self._node.node_type == SmartboxNodeType.PMO:
            await self._node.update_power()
            self.async_write_ha_state()

    @property
    def native_value(self) -> float:
        """Return the native value of the sensor."""
        return (
            self._status["power"]
            if (
                self._node.node_type == SmartboxNodeType.PMO
                or ("power" in self._status and self._node.is_heating(self._status))
            )
            else 0
        )


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


class TotalConsumptionSensor(SmartboxSensorBase):
    """Smartbox heater energy sensor: Represents the energy consumed by the heater in total."""

    _attr_key = "total_consumption"
    device_class = SensorDeviceClass.ENERGY
    native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    state_class = SensorStateClass.TOTAL_INCREASING
    _attr_should_poll = True

    @property
    def native_value(self) -> float | None:
        """Return the native value of the sensor."""
        return self._node.total_energy

    async def async_update(self) -> None:
        """Get the latest data."""
        await self._node.update_samples()
        self._attr_state = self._node.total_energy
        await self._adjust_short_term_statistics()

    async def async_added_to_hass(self) -> None:
        """When added to hass."""
        # perform initial statistics import when sensor is added, otherwise it would take
        # 1 day when _handle_coordinator_update is triggered for the first time.
        self._available = True
        await self.update_statistics()
        await self._adjust_short_term_statistics()
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self.update_statistics,
                timedelta(minutes=15),
                name=f"Update statistics - {self.name}",
                cancel_on_shutdown=True,
            )
        )

    async def _adjust_short_term_statistics(self) -> None:
        """Adjust the short term statistics for the sensor."""
        if (
            last_stat := await get_instance(self.hass).async_add_executor_job(
                get_last_short_term_statistics,
                self.hass,
                1,
                self.entity_id,
                True,  # noqa: FBT003
                {"sum", "state"},
            )
        ) and (
            last_stat[self.entity_id][0]["sum"] != last_stat[self.entity_id][0]["state"]
        ):
            get_instance(self.hass).async_adjust_statistics(
                statistic_id=self.entity_id,
                start_time=datetime.fromtimestamp(
                    last_stat[self.entity_id][0]["start"], tz.tzlocal()
                ),
                sum_adjustment=last_stat[self.entity_id][0]["state"]
                - last_stat[self.entity_id][0]["sum"],
                adjustment_unit=self.native_unit_of_measurement,
            )

    async def update_statistics(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003, ARG002
        """Update statistics from samples."""
        history_status = HistoryConsumptionStatus(
            self.config_entry.options.get(
                CONF_HISTORY_CONSUMPTION, HistoryConsumptionStatus.START
            )
        )
        statistic_id = f"{self.entity_id}"
        samples_data = []
        if history_status == HistoryConsumptionStatus.START:
            # last 3 years
            for year in (3, 2, 1):
                year_sample = await self._node.get_samples(
                    int(time.time() - (year * 365 * 24 * 60 * 60)),
                    int(time.time() - ((year - 1) * 365 * 24 * 60 * 60 - 3600)),
                )
                samples_data.extend(year_sample)
            self.hass.config_entries.async_update_entry(
                entry=self.config_entry,
                options={
                    **self.config_entry.options,
                    CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.AUTO,
                },
            )
        elif history_status == HistoryConsumptionStatus.AUTO:
            # last day
            samples_data = await self._node.get_samples(
                int(time.time() - (24 * 60 * 60)),
                int(time.time() + 3600),
            )

        samples_data = sorted(samples_data, key=lambda x: x["t"])
        statistics: list[StatisticData] = []
        for entry in samples_data:
            counter = float(entry["counter"])
            start = datetime.fromtimestamp(entry["t"], tz.tzlocal()) - timedelta(
                hours=1
            )
            if start.minute == 0:
                statistics.append(
                    StatisticData(start=start, sum=counter, state=counter)
                )
        if statistics and history_status != HistoryConsumptionStatus.OFF:
            metadata: StatisticMetaData = StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                unit_class = None,
                has_sum=True,
                source=RECORDER_DOMAIN,
                name=statistic_id,
                statistic_id=statistic_id,
                unit_of_measurement=self.native_unit_of_measurement,
            )
            _LOGGER.debug("Insert statistics: %s %s", metadata, statistics)
            async_import_statistics(self.hass, metadata, statistics )


class ChargeLevelSensor(SmartboxSensorBase):
    """Smartbox storage heater charge level sensor."""

    _attr_key = "charge_level"
    device_class = SensorDeviceClass.BATTERY
    native_unit_of_measurement = PERCENTAGE
    state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the native value of the sensor."""
        # Different storage heater models use different field names for charge level
        # Model 1C storage heaters use 'current_charge_per'
        # Other storage heater models use 'charge_level' directly
        model_code = self._node.get_model_code()

        if model_code == "1C":
            # Model 1C storage heaters: use current_charge_per field
            return self._status.get("current_charge_per", 0)

        # Default for other storage heater models: use charge_level field
        return self._status.get("charge_level", 0)


class BoostEndTimeSensor(SmartboxSensorBase):
    """Smartbox end boost time sensor."""

    _attr_key = "boost_end_time"
    device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | None:
        """Return the native value of the sensor."""
        if not self._node.boost:
            return None
        boost_end = self._node.boost_end_min
        boost_end_time = dt.now().replace(
            hour=math.trunc(boost_end / 60), minute=boost_end % 60
        )
        if boost_end_time < dt.now():
            boost_end_time = boost_end_time + timedelta(days=1)
        return boost_end_time
