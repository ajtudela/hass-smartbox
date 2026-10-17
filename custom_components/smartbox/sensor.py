"""Support for Smartbox sensor entities."""

from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

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
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util
from smartbox.error import APIUnavailableError, InvalidAuthError, SmartboxError
import voluptuous as vol

from .const import (
    CONF_HISTORY_CONSUMPTION,
    CONF_TIMEDELTA_POWER,
    DAY_KEYS,
    DEFAULT_TIMEDELTA_POWER,
    DOMAIN,
    FIELD_SCHEDULE,
    PROG_PROFILE_NAMES,
    SERVICE_SET_SCHEDULE,
    HistoryConsumptionStatus,
    SmartboxNodeType,
)
from .core.coordinator import ConsumptionCoordinator
from .core.protocol import (
    ProgDict,
    get_boost_end_datetime,
    get_current_prog_profile,
    get_next_prog_change,
    get_temperature_unit,
    rtc_time_to_datetime,
)
from .entity import SmartboxBoxEntity, SmartBoxNodeEntity

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import SmartboxConfigEntry
    from .core.coordinator import ConsumptionData
    from .core.device import SmartboxDevice
    from .core.node import SmartboxNode

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=15)


async def async_setup_entry(
    hass: HomeAssistant,
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
        [PowerSensor(node, entry) for node in entry.runtime_data.nodes],
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
        [
            TotalConsumptionSensor(
                node, entry, ConsumptionCoordinator(hass, entry, node)
            )
            for node in entry.runtime_data.nodes
        ],
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
    # Schedule: one sensor per heater node. REST is the authoritative
    # source; websocket prog frames only trigger an immediate refresh (the
    # /prog frame shape is not yet live-confirmed, see ../smartbox
    # api-notes.md).
    schedule_entities: list[ScheduleSensor] = [
        ScheduleSensor(node, entry)
        for node in entry.runtime_data.nodes
        if node.heater_node
    ]
    async_add_entities(schedule_entities, update_before_add=True)

    # Box device clock drift (diagnostic): polls the box's own RTC.
    async_add_entities(
        [ClockDriftSensor(device, entry) for device in entry.runtime_data.devices],
        update_before_add=True,
    )

    # Registered once per domain by the platform; HA dispatches to the
    # entities of every config entry.
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_SCHEDULE,
        {
            vol.Required(FIELD_SCHEDULE): vol.All(
                dict,
                vol.Length(min=1),
                {
                    vol.All(vol.Coerce(str), vol.In(DAY_KEYS)): [vol.Coerce(int)],
                },
            ),
        },
        _async_handle_set_schedule,
    )
    _LOGGER.debug("Finished setting up Smartbox sensor platform")


async def _async_handle_set_schedule(entity: Entity, call: ServiceCall) -> None:
    """Handle the set_schedule service for one targeted entity."""
    if not isinstance(entity, ScheduleSensor):
        return
    schedule: ProgDict = call.data[FIELD_SCHEDULE]
    try:
        await entity.async_set_schedule(schedule)
    except InvalidAuthError as ex:
        msg = f"Authentication failed setting the schedule on {entity.entity_id}: {ex}"
        raise HomeAssistantError(msg) from ex
    except APIUnavailableError as ex:
        msg = f"API unavailable setting the schedule on {entity.entity_id}: {ex}"
        raise HomeAssistantError(msg) from ex
    except SmartboxError as ex:
        msg = f"API error setting the schedule on {entity.entity_id}: {ex}"
        raise HomeAssistantError(msg) from ex


class SmartboxSensorBase(SmartBoxNodeEntity, SensorEntity):
    """Base class for Smartbox sensor."""

    def __init__(
        self,
        node: SmartboxNode,
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
    def native_unit_of_measurement(self) -> UnitOfTemperature | None:
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
            try:
                await self._node.update_power()
            except APIUnavailableError:
                # Transient API trouble: surface as unavailable instead of
                # leaving a stale-but-available entity.
                self._api_available = False
            else:
                self._api_available = True
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
    """Smartbox heater energy sensor: Represents the energy consumed by the heater in total.

    Driven by a :class:`ConsumptionCoordinator` (one query per cycle shared by
    the counter and the recorder statistics import).
    """

    _attr_key = "total_consumption"
    device_class = SensorDeviceClass.ENERGY
    native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self,
        node: SmartboxNode,
        entry: SmartboxConfigEntry,
        coordinator: ConsumptionCoordinator,
    ) -> None:
        """Initialize the consumption sensor."""
        super().__init__(node=node, entry=entry)
        self._coordinator = coordinator

    @property
    def native_value(self) -> float | None:
        """Return the native value of the sensor."""
        return self._node.total_energy

    async def async_added_to_hass(self) -> None:
        """When added to hass."""
        await super().async_added_to_hass()
        # The first fetch also performs the initial statistics import,
        # otherwise it would take a day for the first cycle to do it. A
        # failed refresh is not fatal: the coordinator retries every cycle.
        await self._coordinator.async_refresh()
        await self._async_process(self._coordinator.data)
        self.async_on_remove(
            self._coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Process the samples of a finished refresh cycle."""
        self.hass.async_create_background_task(
            self._async_process(self._coordinator.data),
            name=f"smartbox_consumption_{self._node.node_id}",
        )

    async def _async_process(self, data: ConsumptionData | None) -> None:
        """Import the statistics of a refresh and publish the new state."""
        self._api_available = self._coordinator.last_update_success
        if data is not None and self._api_available:
            self.import_statistics(data)
            await self._adjust_short_term_statistics()
        self.async_write_ha_state()

    async def _adjust_short_term_statistics(self) -> None:
        """Adjust the short term statistics for the sensor."""
        if last_stat := await get_instance(self.hass).async_add_executor_job(
            get_last_short_term_statistics,
            self.hass,
            1,
            self.entity_id,
            True,  # noqa: FBT003
            {"sum", "state"},
        ):
            state_value = last_stat[self.entity_id][0]["state"]
            sum_value = last_stat[self.entity_id][0]["sum"]
            if (
                state_value is not None
                and sum_value is not None
                and (sum_value != state_value)
            ):
                get_instance(self.hass).async_adjust_statistics(
                    statistic_id=self.entity_id,
                    start_time=datetime.fromtimestamp(
                        last_stat[self.entity_id][0]["start"],
                        dt_util.DEFAULT_TIME_ZONE,
                    ),
                    sum_adjustment=state_value - sum_value,
                    adjustment_unit=self.native_unit_of_measurement,
                )

    def import_statistics(self, data: ConsumptionData) -> None:
        """Import the fetched samples as recorder statistics."""
        history_status = data.history
        if history_status == HistoryConsumptionStatus.START:
            # The full history is imported once; later cycles only add the
            # last day. Flipping the option reloads the entry.
            self.hass.config_entries.async_update_entry(
                entry=self.config_entry,
                options={
                    **self.config_entry.options,
                    CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.AUTO,
                },
            )
        statistics: list[StatisticData] = []
        for entry in data.samples:
            counter = float(entry["counter"])
            start = datetime.fromtimestamp(
                entry["t"], dt_util.DEFAULT_TIME_ZONE
            ) - timedelta(hours=1)
            if start.minute == 0:
                statistics.append(
                    StatisticData(start=start, sum=counter, state=counter)
                )
        if statistics and history_status != HistoryConsumptionStatus.OFF:
            statistic_id = f"{self.entity_id}"
            metadata: StatisticMetaData = StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                unit_class=None,
                has_sum=True,
                source=RECORDER_DOMAIN,
                name=statistic_id,
                statistic_id=statistic_id,
                unit_of_measurement=self.native_unit_of_measurement,
            )
            _LOGGER.debug("Insert statistics: %s %s", metadata, statistics)
            async_import_statistics(self.hass, metadata, statistics)


class ChargeLevelSensor(SmartboxSensorBase):
    """Smartbox storage heater charge level sensor."""

    _attr_key = "charge_level"
    device_class = SensorDeviceClass.BATTERY
    native_unit_of_measurement = PERCENTAGE
    state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the native value of the sensor."""
        # Different heater models use different field names for charge level
        # Returns 'current_charge_per' if model == 1C (storage heaters)
        # Other heater models use 'charge_level' directly
        model_code = self._node.get_model_code()
        if model_code == "1C":
            return self._status.get("current_charge_per", 0)
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
        return get_boost_end_datetime(self._node.boost_end_min)


class ScheduleSensor(SmartboxSensorBase):
    """Smartbox heater schedule sensor.

    Exposes the heater's own weekly programme: the state is the profile the
    programme asks for right now (frost/eco/comfort), and the attributes hold
    the full day-keyed programme. The heater executes the schedule itself;
    Home Assistant only reads it, refreshes on websocket notifications and
    writes it with the smartbox.set_schedule service.

    REST is the authoritative source (polled each platform cycle); websocket
    /prog frames only trigger an immediate refresh because their shape is not
    yet live-confirmed (../smartbox api-notes.md).
    """

    _attr_key = "schedule"
    _attr_should_poll = True
    device_class = SensorDeviceClass.ENUM

    def __init__(self, node: SmartboxNode, entry: SmartboxConfigEntry) -> None:
        """Initialize the schedule sensor."""
        super().__init__(node=node, entry=entry)
        self._attr_websocket_event = "prog"
        self._unsub_boundary: Callable[[], None] | None = None
        self._attr_options = list(PROG_PROFILE_NAMES)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full schedule as attributes."""
        prog = self._node.prog
        if prog is None:
            return {}
        return {FIELD_SCHEDULE: prog}

    @property
    def native_value(self) -> str | None:
        """Return the profile the schedule asks for at the current local time."""
        prog = self._node.prog
        if prog is None:
            return None
        profile = get_current_prog_profile(prog, dt_util.now())
        if profile is None or profile >= len(PROG_PROFILE_NAMES):
            # Unknown index (unverified mapping for acm/htr_mod families).
            return None
        return PROG_PROFILE_NAMES[profile]

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        await super().async_added_to_hass()
        # The base registers websocket dispatchers only for non-polling
        # entities; this one polls REST and additionally refreshes
        # immediately when a websocket prog frame arrives.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_{self._node.node_id}_prog",
                self._async_update,
            )
        )
        self._track_next_change()

    async def async_update(self) -> None:
        """Refresh the schedule from the API."""
        try:
            prog = await self._node.async_refresh_prog()
        except APIUnavailableError:
            # Transient API trouble: surface as unavailable instead of
            # leaving a stale-but-available entity.
            self._api_available = False
            return
        except SmartboxError:
            _LOGGER.exception(
                "Error refreshing the schedule for %s; keeping last known",
                self._node.name,
            )
            return
        self._api_available = True
        if prog is not None:
            self._node.update_prog(prog)
        self._track_next_change()

    @callback
    def _async_update(self, _: Any) -> None:  # noqa: ANN401
        """React to a websocket prog frame; the device updated the cache."""
        self._track_next_change()
        self.async_write_ha_state()

    @callback
    def _track_next_change(self) -> None:
        """Re-evaluate the state exactly when the schedule next changes."""
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None
        prog = self._node.prog
        if not prog:
            return
        next_change = get_next_prog_change(prog, dt_util.now())
        if next_change is None:
            return
        self._unsub_boundary = async_track_point_in_time(
            self.hass, self._async_boundary, next_change
        )

    @callback
    def _async_boundary(self, _: Any) -> None:  # noqa: ANN401
        """Handle a slot boundary: flip the state and re-arm the tracker."""
        self._unsub_boundary = None
        self.async_write_ha_state()
        self._track_next_change()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the pending boundary update."""
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None

    async def async_set_schedule(self, prog: ProgDict) -> None:
        """Write the schedule and refresh the entity state immediately."""
        await self._node.set_prog(prog)
        self._track_next_change()
        self.async_write_ha_state()


class ClockDriftSensor(SmartboxBoxEntity, SensorEntity):
    """Smartbox box RTC clock drift sensor (diagnostic).

    The box keeps its own real-time clock; a growing offset against Home
    Assistant time can silently break the heaters' internal weekly
    scheduling. Polls ``mgr/rtc/time`` (live-verified read) and reports
    the drift in seconds; the raw RTC fields are exposed as attributes.
    """

    _attr_key = "rtc_drift"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _RTC_REFRESH_INTERVAL = timedelta(minutes=5)

    def __init__(self, device: SmartboxDevice, entry: SmartboxConfigEntry) -> None:
        """Initialize the clock drift sensor."""
        super().__init__(device=device, entry=entry)
        self._rtc: dict[str, int] | None = None

    async def async_added_to_hass(self) -> None:
        """When added to hass."""
        await super().async_added_to_hass()
        # This entity polls REST on a slow interval instead of relying on
        # websocket events (RTC has no websocket feed); the base skips
        # dispatcher wiring for entities without a websocket event.
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self._async_refresh,
                self._RTC_REFRESH_INTERVAL,
                name=f"Update RTC drift - {self.name}",
                cancel_on_shutdown=True,
            )
        )

    async def async_update(self) -> None:
        """Get the latest RTC data."""
        await self._async_refresh(None)

    async def _async_refresh(self, _) -> None:  # noqa: ANN001
        """Fetch the box RTC and update the entity."""
        try:
            self._rtc = await self._device.async_refresh_rtc()
        except SmartboxError, APIUnavailableError:
            # Transient API trouble: surface as unavailable instead of
            # leaving a stale-but-available entity.
            self._attr_available = False
        else:
            self._attr_available = True
        # update_before_add runs async_update before the entity id exists;
        # writing the state is only valid once added.
        if self.entity_id is not None:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the raw RTC fields as attributes."""
        if self._rtc is None:
            return {}
        return {"rtc": self._rtc}

    @property
    def native_value(self) -> float | None:
        """Return the drift of the box clock in seconds."""
        if self._rtc is None or (box_time := rtc_time_to_datetime(self._rtc)) is None:
            return None
        return round((box_time - dt_util.now()).total_seconds())
