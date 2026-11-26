"""Support for Smartbox sensor entities.

Entities read state through the adapter's accessors over ``runtime.engine``
and update via the shared ``EngineChangeFanOut``. Two poll instead, because
their reads are on-demand REST calls rather than pushed frames: the PMO power
sensor (``get_node_power``) and the box RTC clock-drift sensor
(``get_rtc_time``).

The consumption-history statistics machinery (a first-setup 3-year import plus
a 15-minute last-day backfill into the recorder) reads its samples through the
client's ``get_node_samples``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import logging
import time
from typing import TYPE_CHECKING, Any

# The recorder root re-exports DOMAIN and get_instance for integrations;
# mypy strict's no_implicit_reexport cannot see that re-export.
from homeassistant.components.recorder import DOMAIN as RECORDER_DOMAIN, get_instance  # type: ignore[attr-defined]
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
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util
from smartbox.device.profile import controller_limits, diagnostic_fields, has_no_probes
from smartbox.device.storage import (
    is_elnur_stepped_storage,
    is_helki_storage,
    is_water_storage,
)
from smartbox.protocol import timing
from smartbox.protocol.client import NodeRef
from smartbox.protocol.errors import SmartboxError
from smartbox.protocol.values import is_lost, normalize_prog, parse_number_or_none

from .adapter import (
    EngineChangeFanOut,
    SmartboxBoxAdapterEntity,
    SmartboxNodeAdapterEntity,
)
from .const import (
    CONF_HISTORY_CONSUMPTION,
    CONF_TIMEDELTA_POWER,
    DEFAULT_TIMEDELTA_POWER,
    ENERGY_NODE_TYPES,
    FIELD_SCHEDULE,
    HistoryConsumptionStatus,
    SmartboxNodeType,
)
from .entity_builder import EntityBuilder
from .identity import charging_schedule_available, is_htr_mod_node
from .mapping import (
    ProgDict,
    get_boost_end_datetime,
    get_current_prog_profile,
    get_next_prog_change,
    get_temperature_unit,
    rtc_time_to_datetime,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from smartbox.core.state import Change

    from . import SmartboxConfigEntry, SmartboxRuntime
    from .entity_builder import EntityGroup
    from .identity import SmartboxDeviceInfo, SmartboxNodeInfo

_LOGGER = logging.getLogger(__name__)

_MINUTES_PER_DAY = 24 * 60

# Every energy sensor shares SCAN_INTERVAL, so a multi-node account would fire
# one REST samples read per node at the same instant. Queue them: the vendor
# API and the recorder then see a short series instead of a burst.
PARALLEL_UPDATES = 1

# Platform poll interval for the entities that use HA polling - the energy
# sensor is the only one. Without it the sensor domain default (30 s) applies,
# and the slow samples read plus its recorder query run 30x more often.
SCAN_INTERVAL = timedelta(minutes=15)

# Retry spacing for a FAILING first-setup import: its three year-range reads
# are the heaviest REST calls the integration makes, so a broken endpoint is
# not re-hammered on every 15-minute tick. The gap doubles per consecutive
# failure and caps out; any success resets it.
_IMPORT_RETRY_BASE_SECONDS = 15 * 60.0
_IMPORT_RETRY_MAX_SECONDS = 6 * 60 * 60.0


def _sample_counter(row: Mapping[str, Any]) -> float | None:
    """Return a row's cumulative counter, from either wire shape.

    A heater row carries the scalar ``counter``; an accumulator row carries
    ``counters``, and the vendor app reads its first entry as the total
    consumption (``TOTAL_CONSUMPTION``) and the second as the E2/solar counter,
    ignoring the rest. The scalar field is the fallback when the list is absent
    or empty. Raw counters are Wh, the unit this sensor reports.
    """
    counters = row.get("counters")
    if isinstance(counters, list) and counters:
        value = parse_number_or_none(counters[0])
        if value is not None:
            return value
    # The app falls back to the scalar field when the list is empty; an
    # unparsable first entry falls back the same way here instead of dropping
    # a row that still carries a usable counter.
    return parse_number_or_none(row.get("counter"))


def _usable_sample_rows(rows: object, entity_id: str | None) -> list[dict[str, float]]:
    """Return the rows a statistics import can use, as ``{t, counter}`` floats.

    The wire's sample rows are server-controlled; one malformed row would
    otherwise raise out of the interval job forever (and out of the add-time
    import). Non-list payloads and unusable rows are dropped with one bounded
    warning naming the count.
    """
    usable: list[dict[str, float]] = []
    dropped = 0
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            dropped += 1
            continue
        t = parse_number_or_none(row.get("t"))
        counter = _sample_counter(row)
        if t is None or counter is None:
            dropped += 1
            continue
        usable.append({"t": t, "counter": counter})
    if dropped:
        _LOGGER.warning("Dropped %d malformed sample row(s) for %s", dropped, entity_id)
    return usable


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - HA platform signature
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox sensor platform")

    runtime = entry.runtime_data
    fanout = runtime.fanout

    # Box device clock drift (diagnostic): polls the box's own RTC.
    def build_device(device: SmartboxDeviceInfo) -> list[EntityGroup]:
        """Build the box clock-drift sensor."""
        return _build_device_sensors(device, entry, runtime, fanout)

    def build_node(node: SmartboxNodeInfo) -> list[EntityGroup]:
        """Build the sensors this node's type and setup expose."""
        return _build_node_sensors(node, entry, runtime, fanout)

    runtime.entity_builder.register(
        async_add_entities, build_node=build_node, build_device=build_device
    )
    for device in runtime.identity.devices:
        EntityBuilder.add_groups(build_device(device), async_add_entities)
    for node in runtime.identity.nodes:
        EntityBuilder.add_groups(build_node(node), async_add_entities)

    _LOGGER.debug("Finished setting up Smartbox sensor platform")


def _build_device_sensors(
    device: SmartboxDeviceInfo,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
    fanout: EngineChangeFanOut,
) -> list[EntityGroup]:
    """Build the box clock-drift sensor."""
    return [([ClockDriftSensor(device, entry, runtime, fanout)], False)]


def _diagnostic_telemetry_sensors(
    node: SmartboxNodeInfo,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
    fanout: EngineChangeFanOut,
    fields: frozenset[str],
) -> list[Entity]:
    """Build the controller health telemetry, disabled by default."""
    sensors: list[Entity] = []
    if "error_code" in fields:
        sensors.append(ErrorCodeSensor(node, entry, runtime, fanout))
    if "pcb_temp" in fields:
        sensors.append(PcbTemperatureSensor(node, entry, runtime, fanout))
    if "power_pcb_temp" in fields:
        sensors.append(PowerPcbTemperatureSensor(node, entry, runtime, fanout))
    if "act_duty" in fields:
        sensors.append(ActualDutyCycleSensor(node, entry, runtime, fanout))
    return sensors


def _diagnostic_telemetry_groups(
    node: SmartboxNodeInfo,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
    fanout: EngineChangeFanOut,
) -> list[EntityGroup]:
    """Return the diagnostic telemetry group for supported controllers."""
    fields = diagnostic_fields(node.pid, node.node_type)
    if not fields:
        return []
    sensors = _diagnostic_telemetry_sensors(node, entry, runtime, fanout, fields)
    return [(sensors, False)] if sensors else []


def _build_node_sensors(
    node: SmartboxNodeInfo,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
    fanout: EngineChangeFanOut,
) -> list[EntityGroup]:
    """Build the sensors this node's type and setup expose.

    Module-level so the per-type branches do not contribute to the platform
    setup's complexity.
    """
    if not node.supported_wire_type:
        return []
    groups: list[EntityGroup] = []
    if node.heater_surface or node.thermostat_surface:
        groups.append(([TemperatureSensor(node, entry, runtime, fanout)], False))
    if node.thermostat_surface:
        thermostat_schedule = ThermostatScheduleSensor(node, entry, runtime, fanout)
        runtime.services.thermostat_schedule.append(thermostat_schedule)
        groups.append(([thermostat_schedule], False))
    if node.node_type in ENERGY_NODE_TYPES:
        groups.append(([PowerSensor(node, entry, runtime, fanout)], False))
        groups.append(
            (
                [TotalConsumptionSensor(node, entry, runtime, fanout)],
                # The energy value is entity-local, so without this fetch it
                # stays unknown until the first 15-minute poll.
                True,
            )
        )
    # Duty Cycle: only 'htr' nodes report it.
    if node.node_type == SmartboxNodeType.HTR:
        groups.append(([DutyCycleSensor(node, entry, runtime, fanout)], False))
    groups.extend(_diagnostic_telemetry_groups(node, entry, runtime, fanout))
    if node.heater_node and node.node_type == SmartboxNodeType.ACM:
        groups.append(([ChargeLevelSensor(node, entry, runtime, fanout)], False))
        target = TargetChargeSensor(node, entry, runtime, fanout)
        if charging_schedule_available(node):
            # Water storage omits the charge days and valley slots.
            runtime.services.charging.append(target)
        groups.append(([target], False))
    if node.heater_surface and node.boost_available:
        groups.append(([BoostEndTimeSensor(node, entry, runtime, fanout)], False))
    if node.heater_surface and not (
        (node.node_type == SmartboxNodeType.ACM and is_water_storage(node.pid))
        or (node.node_type == SmartboxNodeType.THM and has_no_probes(node.setup))
    ):
        # Publish the entities the (domain-global) service acts on. A thm
        # programme is a per-day list of {m, t} temperature objects and its
        # write is {prog, units}; the heater int-slot shape does not apply.
        # Water storage uses ON/OFF_AUTO profiles and the NO_PROBES Tevolve
        # thermostat uses [OFF, ON], neither of which the heater profile
        # catalogue can render, so neither gets a generic heater schedule.
        schedule = ScheduleSensor(node, entry, runtime, fanout)
        runtime.services.schedule.append(schedule)
        groups.append(([schedule], False))
    return groups


class SmartboxNodeSensorEntity(SmartboxNodeAdapterEntity, SensorEntity):
    """Base for node-level sensor entities (identity on the node adapter base)."""

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
        attr_key: str,
    ) -> None:
        """Initialize the sensor entity."""
        super().__init__(node, entry, runtime, fanout, attr_key)
        self._entry = entry

    @property
    def _lost(self) -> bool:
        """The status slot is a bare lost marker.

        The engine never stores one (a lost-marked payload is availability
        evidence only), so this is the entity's own protection against reading
        the required keys off a body that does not carry them.
        """
        return is_lost(self._status)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra states of the sensor.

        ``locked`` is omitted when the wire never carried it, so templates see
        absence rather than a None-valued key. The wire uses bool on htr and
        0/1 on acm; the attribute is normalized to bool
        (hass-smartbox docs/api-contract.md §7 item 18).
        """
        if "locked" not in self._status:
            return {}
        return {ATTR_LOCKED: bool(self._status["locked"])}


class TemperatureSensor(SmartboxNodeSensorEntity):
    """Smartbox heater temperature sensor."""

    device_class = SensorDeviceClass.TEMPERATURE
    state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the temperature sensor."""
        super().__init__(node, entry, runtime, fanout, "temperature")

    @property
    def native_value(self) -> float | None:
        """Return the native value of the sensor."""
        return parse_number_or_none(self._status.get("mtemp"))

    @property
    def native_unit_of_measurement(self) -> UnitOfTemperature | None:
        """Return the unit of the sensor."""
        return get_temperature_unit(self._status)


class PowerSensor(SmartboxNodeSensorEntity):
    """Smartbox heater power sensor.

    The reading is the power drawn *while heating*: the heater is not active for
    the whole interval since the last update even when 'active' is true, so use
    the duty cycle sensor for run time and the energy sensor for consumption.

    PMO nodes report power only on request, so they poll ``get_node_power``.
    """

    device_class = SensorDeviceClass.POWER
    native_unit_of_measurement = UnitOfPower.WATT
    state_class = SensorStateClass.MEASUREMENT
    entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the power sensor."""
        super().__init__(node, entry, runtime, fanout, "power")
        self._pmo_ok = True
        self._pmo_power: int | None = None

    async def async_added_to_hass(self) -> None:
        """When added to hass."""
        await super().async_added_to_hass()
        if self._node.node_type == SmartboxNodeType.PMO:
            # The interval IS the poll: no pushed power read exists for PMO
            # nodes, and the entity's own state is written by the callback.
            self._entry.async_create_background_task(
                self.hass,
                self._async_update_pmo(),
                name=f"Smartbox PMO power initial read - {self.name}",
            )
            self.async_on_remove(
                async_track_time_interval(
                    self.hass,
                    self._async_update_pmo,
                    timedelta(
                        seconds=self._entry.options.get(
                            CONF_TIMEDELTA_POWER, DEFAULT_TIMEDELTA_POWER
                        )
                    ),
                    name=f"Update PMO Power - {self.name}",
                    cancel_on_shutdown=True,
                )
            )

    async def async_update(self) -> None:
        """Refresh a PMO node on demand (the update_entity service)."""
        if self._node.node_type == SmartboxNodeType.PMO:
            await self._async_update_pmo()

    async def _async_update_pmo(self, _: Any = None) -> None:  # noqa: ANN401
        """Get the latest data."""
        if self._node.node_type == SmartboxNodeType.PMO:
            try:
                self._pmo_power = await self._runtime.client.get_node_power(
                    self._dev_id,
                    NodeRef(self._node.node_type, str(self._node.addr)),
                )
            except SmartboxError:
                # Any API trouble (``SmartboxError`` covers both the transient
                # ``TransportUnavailableError`` and a rejected re-auth): surface
                # as unavailable, and never let the poll error escape the
                # interval callback.
                self._pmo_ok = False
            else:
                self._pmo_ok = True
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Node availability, AND the last PMO poll not having failed."""
        return super().available and self._pmo_ok

    @property
    def native_value(self) -> float | None:
        """Return the native value of the sensor."""
        if self._lost:
            # lost body: unknown, never a stale 0 (climate-round rule)
            return None
        status = self._status
        if self._node.node_type == SmartboxNodeType.PMO:
            # The PMO poll result (a dedicated /power read; PMO status
            # frames do not carry power).
            return self._pmo_power
        if not status:
            # No status body yet: unknown, not a real zero.
            return None
        return self._node_power(status)

    def _node_power(self, status: dict[str, Any]) -> float | None:
        """Return the controller-specific power reading."""
        state = self._node_state()
        model_code = self._node.get_model_code(
            state.version if state is not None else None
        )
        if is_htr_mod_node(self._node, model_code):
            # HaverlandHtrModController.getPower() uses the configured rated
            # power while active/heating, not status.power.
            if status.get("active") or status.get("heating"):
                setup = state.setup if state is not None else self._node.setup
                return parse_number_or_none(setup.get("power"))
            return 0
        if self._node.node_type == SmartboxNodeType.ACM:
            # StorageHeaterController.getPower() reports status.power even
            # while not charging (fan/resistor load).
            return parse_number_or_none(status.get("power")) if "power" in status else 0
        # ElectricHeaterController.getPower() requires a synced, active body.
        if status.get("sync_status") == "ok" and status.get("active") is True:
            if "power" not in status:
                # The app reads a missing field as zero while active.
                return 0
            return parse_number_or_none(status.get("power"))
        return 0


class DutyCycleSensor(SmartboxNodeSensorEntity):
    """Smartbox heater duty cycle sensor: Represents the duty cycle for the heater."""

    # Diagnostic-grade telemetry: available for troubleshooting, not part of
    # the default heater dashboard.
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    device_class = SensorDeviceClass.POWER_FACTOR
    native_unit_of_measurement = PERCENTAGE
    state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the duty cycle sensor."""
        super().__init__(node, entry, runtime, fanout, "duty_cycle")

    @property
    def native_value(self) -> float | None:
        """Return the native value of the sensor."""
        return parse_number_or_none(self._status.get("duty"))


class ErrorCodeSensor(SmartboxNodeSensorEntity):
    """The controller's raw error/fault code."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the error-code sensor."""
        super().__init__(node, entry, runtime, fanout, "error_code")

    @property
    def native_value(self) -> str | None:
        """Return the raw code; firmware sends 0, '0000' or 'none'."""
        value = self._status.get("error_code")
        if value is None or value == "":
            return None
        return str(value)


class _DiagnosticTemperatureSensor(SmartboxNodeSensorEntity):
    """A diagnostic temperature read from one status field."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _status_key: str

    @property
    def native_value(self) -> float | None:
        """Return the temperature, or None until the field arrives."""
        return parse_number_or_none(self._status.get(self._status_key))


class PcbTemperatureSensor(_DiagnosticTemperatureSensor):
    """Internal controller PCB temperature."""

    _status_key = "pcb_temp"

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the PCB temperature sensor."""
        super().__init__(node, entry, runtime, fanout, "pcb_temperature")


class PowerPcbTemperatureSensor(_DiagnosticTemperatureSensor):
    """Internal power-board PCB temperature."""

    _status_key = "power_pcb_temp"

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the power-board temperature sensor."""
        super().__init__(node, entry, runtime, fanout, "power_pcb_temperature")


class ActualDutyCycleSensor(SmartboxNodeSensorEntity):
    """The duty cycle the controller reports as actually applied."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.POWER_FACTOR
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the actual duty-cycle sensor."""
        super().__init__(node, entry, runtime, fanout, "actual_duty_cycle")

    @property
    def native_value(self) -> float | None:
        """Return the applied duty, or None until the field arrives."""
        return parse_number_or_none(self._status.get("act_duty"))


class TotalConsumptionSensor(SmartboxNodeSensorEntity):
    """Smartbox heater energy sensor: Represents the energy consumed by the heater in total."""

    device_class = SensorDeviceClass.ENERGY
    native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
    state_class = SensorStateClass.TOTAL_INCREASING
    _attr_should_poll = True

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the total consumption sensor."""
        super().__init__(node, entry, runtime, fanout, "total_consumption")
        self._total_energy: float | None = None
        self._samples_ok = True
        self._backfill_failures = 0

    @property
    def available(self) -> bool:
        """Node availability, AND the last samples read not having failed."""
        return super().available and self._samples_ok

    @property
    def native_value(self) -> float | None:
        """Return the native value of the sensor."""
        return self._total_energy

    async def _get_samples(
        self, start_time: int, end_time: int
    ) -> list[dict[str, Any]]:
        """Fetch the node's samples, keeping only usable rows.

        A row needs a numeric ``t`` and ``counter``; malformed rows are
        dropped here, once, so a single bad row cannot kill the 15-minute
        statistics job (or the entity add) for the life of the runtime.
        """
        ref = NodeRef(self._node.node_type, str(self._node.addr))
        rows = await self._runtime.client.get_node_samples(
            self._dev_id, ref, start_time, end_time
        )
        return _usable_sample_rows(rows, self.entity_id)

    async def async_update(self) -> None:
        """Get the latest data."""
        try:
            samples = await self._get_samples(
                int(time.time() - (3600 * 3)),
                int(time.time()),
            )
        except SmartboxError:
            # Transient/API trouble: surface as unavailable rather than a stale
            # value with an ERROR per poll. The cached value is kept.
            self._samples_ok = False
            return
        self._samples_ok = True
        if samples:
            latest = max(samples, key=lambda row: row["t"])
            self._total_energy = float(latest["counter"])
        await self._adjust_short_term_statistics()

    async def async_added_to_hass(self) -> None:
        """When added to hass."""
        await super().async_added_to_hass()
        # The first-setup import runs in the BACKGROUND: it is up to three
        # year-range reads plus a synchronous statistics walk, and this entity
        # is added with ``update_before_add`` - awaiting it here would hold the
        # config entry's setup open for that whole time. A failure must not
        # escape either: an exception out of ``async_added_to_hass`` leaves the
        # entity in HA's ADDING state, so it never writes a state and never
        # registers the interval. The task is entry-tracked, so unload cancels
        # it instead of letting it fetch against a torn-down runtime.
        self._entry.async_create_background_task(
            self.hass,
            self._async_initial_import(),
            name=f"Smartbox history import - {self.name}",
        )
        self.async_on_remove(
            async_track_time_interval(
                self.hass,
                self.update_statistics,
                timedelta(minutes=15),
                name=f"Update statistics - {self.name}",
                cancel_on_shutdown=True,
            )
        )

    async def _async_initial_import(self) -> None:
        """Run the first statistics pass, reporting failure like a bad poll."""
        try:
            await self.update_statistics()
            await self._adjust_short_term_statistics()
        except SmartboxError:
            self._samples_ok = False
            _LOGGER.warning(
                "Initial statistics import failed for %s; the 15-minute "
                "backfill will retry",
                self.name,
            )

    async def _adjust_short_term_statistics(self) -> None:
        """Adjust the short term statistics for the sensor."""
        # Statistics only exist when the recorder is loaded; get_instance()
        # raises KeyError on installations that disable the recorder.
        try:
            recorder = get_instance(self.hass)
        except KeyError:
            return
        if last_stat := await recorder.async_add_executor_job(
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
                recorder.async_adjust_statistics(
                    statistic_id=self.entity_id,
                    start_time=datetime.fromtimestamp(
                        last_stat[self.entity_id][0]["start"],
                        dt_util.DEFAULT_TIME_ZONE,
                    ),
                    sum_adjustment=state_value - sum_value,
                    adjustment_unit=self.native_unit_of_measurement,
                )

    async def update_statistics(self, *args: object, **kwargs: object) -> None:  # noqa: ARG002
        """Update statistics from samples."""
        history_status = HistoryConsumptionStatus(
            self._entry.options.get(
                CONF_HISTORY_CONSUMPTION, HistoryConsumptionStatus.START
            )
        )
        history = self._runtime.history
        first_import = history_status == HistoryConsumptionStatus.START and (
            history.pending(self._node.node_id)
        )
        if not self._recorder_available():
            # Recorder is optional in Home Assistant. Importing would raise
            # KeyError out of the interval job, and a first import would
            # re-read three years on every tick. Defer it like a failed read;
            # the sensor's own value still updates through async_update.
            if first_import and timing.monotonic() >= history.retry_at(
                self._node.node_id
            ):
                self._defer_first_import("recorder unavailable")
            return
        if first_import:
            try:
                samples_data = await self._first_import_samples()
            except SmartboxError:
                # A failed first-setup read is reported as unavailable, not
                # an unhandled interval job; the retry backoff is armed.
                self._samples_ok = False
                return
            if samples_data is None:
                return
        elif history_status != HistoryConsumptionStatus.OFF:
            samples_data = await self._backfill_samples()
            if samples_data is None:
                return
        else:
            samples_data = []

        statistics = self._statistics_rows(samples_data)
        if statistics:
            self._import_statistics(statistics)
        if first_import and history.complete(self._node.node_id):
            self._flip_history_option()

    def _recorder_available(self) -> bool:
        """Whether the recorder is loaded (statistics imports need it)."""
        try:
            get_instance(self.hass)
        except KeyError:
            return False
        return True

    def _defer_first_import(self, error: str) -> None:
        """Back off the next first-setup read (shared by failure paths).

        The ledger lives on :class:`HistoryImport`, so config-entry
        diagnostics can show which nodes are stuck and why. The warning is
        rate-limited: the first failure and then one per hour of consecutive
        failures, because the interval job retries every 15 minutes.
        """
        history = self._runtime.history
        node_key = self._node.node_id
        failures = history.failure_count(node_key) + 1
        delay = min(
            _IMPORT_RETRY_BASE_SECONDS * 2 ** (failures - 1),
            _IMPORT_RETRY_MAX_SECONDS,
        )
        history.record_failure(node_key, timing.monotonic() + delay, error)
        if failures == 1 or failures % 4 == 0:
            _LOGGER.warning(
                "Statistics import for %s failed (%d consecutive, retry in %gs): %s",
                self.entity_id or node_key,
                failures,
                delay,
                error,
            )

    async def _first_import_samples(self) -> list[dict[str, Any]] | None:
        """Read the 3-year window once per node; ``None`` while backing off."""
        history = self._runtime.history
        node_key = self._node.node_id
        if timing.monotonic() < history.retry_at(node_key):
            # A failing endpoint keeps its growing gap: the ranges are heavy,
            # and the pending flag means the next attempt resumes from
            # scratch, so retrying on every tick only adds load.
            return None
        samples_data: list[dict[str, Any]] = []
        try:
            for year in (3, 2, 1):
                samples_data.extend(
                    await self._get_samples(
                        int(time.time() - (year * 365 * 24 * 60 * 60)),
                        int(time.time() - ((year - 1) * 365 * 24 * 60 * 60 - 3600)),
                    )
                )
        except SmartboxError as ex:
            self._defer_first_import(type(ex).__name__)
            raise
        history.clear_failure(node_key)
        self._samples_ok = True
        return samples_data

    async def _backfill_samples(self) -> list[dict[str, Any]] | None:
        """Read the last day; ``None`` when the read failed."""
        try:
            samples_data = await self._get_samples(
                int(time.time() - (24 * 60 * 60)),
                int(time.time() + 3600),
            )
        except SmartboxError as ex:
            # A transient read failure must not escape the interval job or
            # leave a stale value rendering as available. One warning per
            # hour of consecutive failures keeps a persistent outage visible
            # without one log line per 15-minute tick.
            self._samples_ok = False
            self._backfill_failures += 1
            if self._backfill_failures == 1 or self._backfill_failures % 4 == 0:
                _LOGGER.warning(
                    "Statistics backfill for %s failed (%d consecutive): %s",
                    self.entity_id or self._node.node_id,
                    self._backfill_failures,
                    type(ex).__name__,
                )
            return None
        self._samples_ok = True
        self._backfill_failures = 0
        return samples_data

    @staticmethod
    def _statistics_rows(samples_data: list[dict[str, Any]]) -> list[StatisticData]:
        """Build top-of-the-hour statistics rows from UTC-aligned samples."""
        statistics: list[StatisticData] = []
        for entry in sorted(samples_data, key=lambda x: x["t"]):
            counter = float(entry["counter"])
            # The recorder accepts only top-of-the-hour rows. Samples are
            # UTC-aligned epoch hours; building the row in UTC keeps minute 0
            # in every HA timezone (a local +05:30 zone would otherwise turn
            # every row into :30 and drop it).
            start = dt_util.utc_from_timestamp(entry["t"]) - timedelta(hours=1)
            if start.minute == start.second == start.microsecond == 0:
                statistics.append(
                    StatisticData(start=start, sum=counter, state=counter)
                )
        return statistics

    def _import_statistics(self, statistics: list[StatisticData]) -> None:
        """Import rows under this entity's statistic id."""
        statistic_id = f"{self.entity_id}"
        metadata: StatisticMetaData = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            # The Wh statistic's conversion class: without it the recorder
            # cannot unit-convert these rows, and HA's metadata derivation
            # (device_class + unit) disagrees with the import.
            unit_class="energy",
            has_sum=True,
            source=RECORDER_DOMAIN,
            name=statistic_id,
            statistic_id=statistic_id,
            unit_of_measurement=self.native_unit_of_measurement,
        )
        # dev/node + row count only: the payload can be huge
        _LOGGER.debug("Insert statistics: %s (%d rows)", statistic_id, len(statistics))
        async_import_statistics(self.hass, metadata, statistics)

    def _flip_history_option(self) -> None:
        """Flip the entry's history flag once every node imported.

        ``complete`` marks this node imported and reports whether any is left.
        It runs only after the import above, so a failure leaves the node
        pending for the next attempt; the flag flips only once EVERY node of
        the entry has imported - flipping it earlier would leave the others
        reading a day of history on their next attempt, with the 3-year range
        never re-read.
        """
        self.hass.config_entries.async_update_entry(
            entry=self._entry,
            options={
                **self._entry.options,
                CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.AUTO,
            },
        )


class ChargeLevelSensor(SmartboxNodeSensorEntity):
    """Smartbox storage heater charge level sensor."""

    device_class = SensorDeviceClass.BATTERY
    native_unit_of_measurement = PERCENTAGE
    state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the charge level sensor."""
        super().__init__(node, entry, runtime, fanout, "charge_level")

    @property
    def native_value(self) -> int | None:
        """Return the native value of the sensor."""
        status = self._status
        if self._lost or not status:
            # lost/absent body: unknown, never a stale 0 (climate-round rule)
            return None
        # Charge level is 'current_charge_per' on the Helki storage
        # controllers (1C/2E), 'charge_level' on the rest.
        if is_helki_storage(self._live_pid):
            value = parse_number_or_none(status.get("current_charge_per"))
            return int(value) if value is not None else None
        if is_elnur_stepped_storage(self._live_pid):
            # Elnur's storage controller reports charge_level in 0..5 steps
            # and multiplies by 20 (docs/api-contract.md §3.4).
            level = parse_number_or_none(status.get("charge_level"))
            return int(level * 20) if level is not None else None
        value = parse_number_or_none(status.get("charge_level"))
        return int(value) if value is not None else None


class TargetChargeSensor(SmartboxNodeSensorEntity):
    """Storage heater target charge sensor.

    The target is a setpoint (the app writes 100/0 for ON/OFF on classic
    accumulators), so it carries no battery device class. The charging
    schedule (`setup.charging_conf`) has no entity of its own; it is exposed
    as attributes here so the target has its context without adding three
    low-value diagnostic entities.
    """

    native_unit_of_measurement = PERCENTAGE
    state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the target charge sensor."""
        super().__init__(node, entry, runtime, fanout, "target_charge")

    @property
    def native_value(self) -> int | None:
        """Return the target charge percentage."""
        if self._lost or not self._status:
            return None
        value = parse_number_or_none(self._status.get("target_charge_per"))
        return int(value) if value is not None else None

    async def async_set_charging_schedule(
        self,
        *,
        active_days: list[int] | None,
        slot_1: dict[str, int] | None,
        slot_2: dict[str, int] | None,
    ) -> None:
        """Write the storage charging configuration."""
        ref = NodeRef(self._node.node_type, str(self._node.addr))
        await self._runtime.engine.set_charging_conf(
            self._dev_id,
            ref,
            active_days=active_days,
            slot_1=slot_1,
            slot_2=slot_2,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the charging schedule slots and days."""
        state = self._node_state()
        conf = (state.setup if state is not None else {}).get("charging_conf")
        if not isinstance(conf, dict):
            return {}
        attributes: dict[str, Any] = {}
        for key, attr in (("slot_1", "charging_slot_1"), ("slot_2", "charging_slot_2")):
            slot = _format_charging_slot(conf, key)
            if slot is not None:
                attributes[attr] = slot
        days = _format_charging_days(conf)
        if days is not None:
            attributes["charging_days"] = days
        return attributes


_MINUTES_PER_DAY = 24 * 60


def _minutes_to_time(minutes: int) -> str:
    """Format a minute-of-day as HH:MM."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _format_charging_slot(conf: dict[str, Any], key: str) -> str | None:
    """Format one charging slot as ``HH:MM-HH:MM`` (or ``disabled``).

    ``end`` is a minute of the day, with 0 (and 1440) meaning midnight. A
    slot whose end precedes its start crosses midnight, as the app's
    ``checkSlotInValley`` accepts. Equal start and end is the disabled
    state, as the app's ``VALLEY_n_ENABLED`` and ``checkSlotInValley`` treat
    it; only non-integer or out-of-range values are malformed.
    """
    slot = conf.get(key)
    if not isinstance(slot, dict):
        return None
    start = slot.get("start")
    end = slot.get("end")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or not 0 <= start < _MINUTES_PER_DAY
        or not 0 <= end <= _MINUTES_PER_DAY
    ):
        return None
    if start == end:
        return "disabled"
    return f"{_minutes_to_time(start)}-{_minutes_to_time(end % _MINUTES_PER_DAY)}"


# The charging_conf.active_days array is Sunday-first: the app's DAY_0_SHORT
# is "Su" and its non-English display order rotates the same indices to
# Monday-first (docs/api-contract.md §3.4).
_CHARGING_DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def _format_charging_days(conf: dict[str, Any]) -> list[str] | None:
    """Return the active charging day names in the Sunday-first wire order."""
    days = conf.get("active_days")
    if not isinstance(days, list) or len(days) != len(_CHARGING_DAY_NAMES):
        return None
    return [
        name for name, active in zip(_CHARGING_DAY_NAMES, days, strict=True) if active
    ]


class BoostEndTimeSensor(SmartboxNodeSensorEntity):
    """Smartbox end boost time sensor."""

    device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the boost end time sensor."""
        super().__init__(node, entry, runtime, fanout, "boost_end_time")

    @property
    def native_value(self) -> datetime | None:
        """Return the native value of the sensor."""
        status = self._status
        if not status.get("boost"):
            return None
        return get_boost_end_datetime(
            status.get("boost_end_min", 0), status.get("boost_end_day")
        )


class ScheduleSensor(SmartboxNodeSensorEntity):
    """Smartbox heater schedule sensor.

    The state is the profile the programme asks for right now
    (frost/eco/comfort); the attributes hold the full day-keyed programme. The
    heater executes the schedule itself - Home Assistant reads it from the
    engine store and writes it with the ``smartbox.set_schedule`` service.
    """

    device_class = SensorDeviceClass.ENUM

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the schedule sensor."""
        super().__init__(node, entry, runtime, fanout, "schedule")
        limits = controller_limits(
            node.node_type,
            node.pid,
            node.version.get("fw_version"),
            node.setup,
        )
        self._profiles = limits.profiles
        self._attr_options = list(self._profiles)
        self._unsub_boundary: Callable[[], None] | None = None

    @property
    def _prog(self) -> ProgDict | None:
        """The node's normalized day-keyed schedule, or None when none (yet)."""
        state = self._node_state()
        return normalize_prog(state.prog) if state is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full schedule as attributes."""
        prog = self._prog
        if prog is None:
            return {}
        return {FIELD_SCHEDULE: prog}

    @property
    def native_value(self) -> str | None:
        """Return the profile the schedule asks for at the current local time."""
        prog = self._prog
        if prog is None:
            return None
        profile = get_current_prog_profile(prog, dt_util.now())
        if profile is None or profile >= len(self._profiles):
            # A node that reports more profiles than its controller catalogue
            # holds is malformed; keep the state unknown rather than guessing.
            return None
        return self._profiles[profile]

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        await super().async_added_to_hass()
        self._refresh_profiles()
        self._track_next_change()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the pending boundary update and unsubscribe."""
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None
        # The base is the only place the shared fan-out registration is
        # released: skipping it leaves a removed entity subscribed, so every
        # later node change runs its callback (re-arming the boundary timer)
        # for the life of the runtime.
        await super().async_will_remove_from_hass()

    def _async_change(self, change: Change) -> None:
        """Refresh on any node change (prog frames included)."""
        self._refresh_profiles()
        self._track_next_change()
        super()._async_change(change)

    def _refresh_profiles(self) -> None:
        """Re-resolve the catalogue when a late version frame arrives."""
        state = self._node_state()
        setup = state.setup if state is not None else self._node.setup
        limits = controller_limits(
            self._node.node_type,
            self._live_pid,
            self._live_fw_version,
            setup,
        )
        if limits.profiles != self._profiles:
            self._profiles = limits.profiles
            self._attr_options = list(self._profiles)

    @callback
    def _track_next_change(self) -> None:
        """Re-evaluate the state exactly when the schedule next changes."""
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None
        prog = self._prog
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

    async def async_set_schedule(self, prog: ProgDict) -> None:
        """Write the schedule and refresh the entity state immediately."""
        days = normalize_prog(prog)
        if days is not None:
            profile_count = len(self._profiles)
            for day, slots in days.items():
                if any(not 0 <= slot < profile_count for slot in slots):
                    msg = (
                        f"profile index out of range for day {day!r}: "
                        f"0..{profile_count - 1} allowed"
                    )
                    raise ValueError(msg)
        ref = NodeRef(self._node.node_type, str(self._node.addr))
        await self._runtime.engine.set_prog(self._dev_id, ref, prog)
        self._track_next_change()
        self.async_write_ha_state()


class ThermostatScheduleSensor(SmartboxNodeSensorEntity):
    """Thermostat programme (day-keyed ``{m, t}`` segments).

    The app stores a thermostat day as a list of segment-ending minutes and
    the temperature for that segment. The state is the temperature scheduled
    for the current minute; the raw programme is an attribute and is written
    by ``smartbox.set_thermostat_schedule``.
    """

    _attr_name = "Thermostat schedule"
    _attr_icon = "mdi:calendar-clock"
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the thermostat schedule sensor."""
        super().__init__(node, entry, runtime, fanout, "thermostat_schedule")
        self._unsub_boundary: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        """Arm the segment-boundary tracker."""
        await super().async_added_to_hass()
        self._track_next_change()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel the boundary tracker."""
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None
        await super().async_will_remove_from_hass()

    @property
    def _program(self) -> Mapping[str, Any]:
        """Return the raw day-keyed thermostat programme."""
        state = self._node_state()
        payload = state.prog if state is not None else None
        if isinstance(payload, Mapping) and isinstance(payload.get("prog"), Mapping):
            payload = payload["prog"]
        return payload if isinstance(payload, Mapping) else {}

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the node's temperature unit."""
        units = self._status.get("units")
        if isinstance(units, str):
            return units
        units = self._node.setup.get("units")
        return units if isinstance(units, str) else "C"

    @property
    def native_value(self) -> float | None:
        """Return the temperature scheduled for the current minute."""
        return self._scheduled_temperature(dt_util.now())

    def _scheduled_temperature(self, now: datetime) -> float | None:
        """Return the temperature in force at ``now``.

        An API ``{m, t}`` object covers the interval ending at ``m`` with
        temperature ``t``, so the active segment is the first whose end is
        after the current minute. After the last end, the last temperature
        remains in force until midnight.
        """
        day_key = str((now.weekday() + 1) % 7)
        day = self._program.get(day_key)
        if not isinstance(day, list) or not day:
            previous = str((int(day_key) - 1) % 7)
            day = self._program.get(previous)
        if not isinstance(day, list):
            return None
        segments = sorted(
            (seg for seg in day if isinstance(seg, Mapping)),
            key=self._segment_end,
        )
        minute = now.hour * 60 + now.minute
        for seg in segments:
            end = self._segment_minute(seg)
            if end is not None and end > minute:
                return parse_number_or_none(seg.get("t"))
        return parse_number_or_none(segments[-1].get("t")) if segments else None

    @staticmethod
    def _segment_minute(segment: Mapping[str, Any]) -> int | None:
        """Return a segment's integral end minute, or None when malformed."""
        value = segment.get("m")
        if isinstance(value, bool):
            return None
        number = parse_number_or_none(value)
        if number is None or not float(number).is_integer():
            return None
        return int(number)

    @classmethod
    def _segment_end(cls, segment: Mapping[str, Any]) -> int:
        """Return a segment's end minute for sorting."""
        return cls._segment_minute(segment) or 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the raw programme."""
        return {"schedule": dict(self._program)}

    def _next_change(self, now: datetime) -> datetime | None:
        """Return the next boundary: a segment end or the next midnight."""
        day_key = str((now.weekday() + 1) % 7)
        next_key = str((int(day_key) + 1) % 7)
        minute = now.hour * 60 + now.minute
        tomorrow = dt_util.start_of_local_day(now + timedelta(days=1))
        candidates: list[datetime] = []
        current = self._program.get(day_key)
        if isinstance(current, list):
            for seg in sorted(
                (seg for seg in current if isinstance(seg, Mapping)),
                key=self._segment_end,
            ):
                end = self._segment_minute(seg)
                if end is None or end <= minute:
                    continue
                if end == _MINUTES_PER_DAY:
                    candidates.append(tomorrow)
                else:
                    candidates.append(
                        now.replace(
                            hour=end // 60,
                            minute=end % 60,
                            second=0,
                            microsecond=0,
                        )
                    )
        following = self._program.get(next_key)
        if isinstance(following, list) and following:
            # A new day's first segment is in force from midnight, even when
            # its first explicit end is later.
            candidates.append(tomorrow)
            for seg in sorted(
                (seg for seg in following if isinstance(seg, Mapping)),
                key=self._segment_end,
            ):
                end = self._segment_minute(seg)
                if end is None:
                    continue
                if end == _MINUTES_PER_DAY:
                    candidates.append(
                        dt_util.start_of_local_day(tomorrow + timedelta(days=1))
                    )
                else:
                    candidates.append(tomorrow.replace(hour=end // 60, minute=end % 60))
        return min(candidates) if candidates else None

    @callback
    def _async_change(self, change: Change) -> None:
        """Re-arm when a pushed programme changes the next boundary."""
        self._track_next_change()
        super()._async_change(change)

    def _track_next_change(self) -> None:
        """Arm one shot at the next segment boundary."""
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None
        next_change = self._next_change(dt_util.now())
        if next_change is None or self.hass is None:
            return
        self._unsub_boundary = async_track_point_in_time(
            self.hass, self._async_boundary, next_change
        )

    @callback
    def _async_boundary(self, _: Any) -> None:  # noqa: ANN401
        """Flip the state at a boundary and re-arm."""
        self._unsub_boundary = None
        self.async_write_ha_state()
        self._track_next_change()

    async def async_set_thermostat_schedule(
        self, prog: dict[str, list[dict[str, object]]]
    ) -> None:
        """Write the thermostat programme."""
        ref = NodeRef(self._node.node_type, str(self._node.addr))
        await self._runtime.engine.set_thermostat_prog(
            self._dev_id, ref, prog, self.native_unit_of_measurement or "C"
        )
        self._track_next_change()
        self.async_write_ha_state()


class ClockDriftSensor(SmartboxBoxAdapterEntity, SensorEntity):
    """Smartbox box RTC clock drift sensor (diagnostic).

    The box keeps its own real-time clock, and a growing offset against Home
    Assistant time silently breaks the heaters' internal weekly scheduling.
    Polls ``mgr/rtc/time`` on a slow interval and reports the drift in seconds;
    the raw RTC fields are attributes. The poll is load-bearing: the drift is
    against the WALL clock, so a change-only feed would freeze the reading while
    time moves, and the cloud reads the clock from the box on demand rather than
    serve a stale one (docs/api-contract.md §6.4).
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _RTC_REFRESH_INTERVAL = timedelta(minutes=5)

    def __init__(
        self,
        device: SmartboxDeviceInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the clock drift sensor."""
        super().__init__(device, entry, runtime, fanout, "rtc_drift")
        self._entry = entry
        self._rtc: dict[str, int] | None = None
        self._rtc_ok = True

    async def async_added_to_hass(self) -> None:
        """When added to hass."""
        await super().async_added_to_hass()
        # The first fetch runs in the background: platform setup must not
        # wait on REST, and the state stays unknown until the read lands.
        # The slow interval below keeps it fresh afterwards.
        self._entry.async_create_background_task(
            self.hass,
            self._async_refresh(),
            name=f"Smartbox RTC drift - {self.name}",
        )
        # This entity polls REST on a slow interval instead of relying on
        # websocket events (RTC has no websocket feed).
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
        """Refresh on demand (the update_entity service / forced updates)."""
        await self._async_refresh()

    async def _async_refresh(self, _: Any = None) -> None:  # noqa: ANN401
        """Fetch the box RTC and update the entity."""
        try:
            self._rtc = await self._runtime.client.get_rtc_time(self._dev_id)
        except SmartboxError:
            # Transient API trouble: surface as unavailable instead of
            # leaving a stale-but-available entity.
            self._rtc_ok = False
        else:
            self._rtc_ok = True
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Box connectivity, AND the last RTC poll not having failed."""
        return super().available and self._rtc_ok

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
