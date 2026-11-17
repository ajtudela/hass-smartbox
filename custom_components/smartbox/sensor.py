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
from typing import TYPE_CHECKING, Any, cast

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
    FIELD_SCHEDULE,
    PROG_PROFILE_NAMES,
    HistoryConsumptionStatus,
    SmartboxNodeType,
)
from .entity_builder import EntityBuilder
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
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from smartbox.core.state import Change

    from . import SmartboxConfigEntry, SmartboxRuntime
    from .entity_builder import EntityGroup
    from .identity import SmartboxDeviceInfo, SmartboxNodeInfo

_LOGGER = logging.getLogger(__name__)

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
        counter = parse_number_or_none(row.get("counter"))
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
        return [([ClockDriftSensor(device, entry, runtime, fanout)], False)]

    def build_node(node: SmartboxNodeInfo) -> list[EntityGroup]:
        """Build the sensors this node's type and setup expose."""
        groups: list[EntityGroup] = []
        if node.heater_node:
            groups.append(([TemperatureSensor(node, entry, runtime, fanout)], False))
        groups.append(([PowerSensor(node, entry, runtime, fanout)], False))
        # Duty Cycle: only 'htr' nodes report it.
        if node.node_type == SmartboxNodeType.HTR:
            groups.append(([DutyCycleSensor(node, entry, runtime, fanout)], False))
        groups.append(
            (
                [TotalConsumptionSensor(node, entry, runtime, fanout)],
                # The energy value is entity-local, so without this fetch it
                # stays unknown until the first 15-minute poll.
                True,
            )
        )
        if node.heater_node and node.node_type == SmartboxNodeType.ACM:
            groups.append(([ChargeLevelSensor(node, entry, runtime, fanout)], False))
        if node.boost_available:
            groups.append(([BoostEndTimeSensor(node, entry, runtime, fanout)], False))
        if node.heater_node:
            # Publish the entities the (domain-global) service acts on.
            schedule = ScheduleSensor(node, entry, runtime, fanout)
            runtime.services.schedule.append(schedule)
            groups.append(([schedule], False))
        return groups

    runtime.entity_builder.register(
        async_add_entities, build_node=build_node, build_device=build_device
    )
    for device in runtime.identity.devices:
        EntityBuilder.add_groups(build_device(device), async_add_entities)
    for node in runtime.identity.nodes:
        EntityBuilder.add_groups(build_node(node), async_add_entities)

    _LOGGER.debug("Finished setting up Smartbox sensor platform")


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
        mtemp = self._status.get("mtemp")
        if mtemp is None:
            return None
        return float(mtemp)

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
        if "power" in status and self._is_heating(status):
            return cast("float", status["power"])
        return 0

    def _is_heating(self, status: dict[str, Any]) -> bool:
        """Is the node heating right now (acm charges, others run active)."""
        if self._node.node_type == SmartboxNodeType.ACM:
            return bool(status.get("charging"))
        return bool(status.get("active"))


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
        duty = self._status.get("duty")
        if duty is None:
            return None
        return float(duty)


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
        # Charge level is 'current_charge_per' on model 1C (storage heaters),
        # 'charge_level' on the rest. Prefer the live version slot; the
        # discovery payload covers the window before the first snapshot.
        state = self._node_state()
        model_code = self._node.get_model_code(
            state.version if state is not None else None
        )
        if model_code == "1C":
            return status.get("current_charge_per")
        return status.get("charge_level")


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
        return get_boost_end_datetime(status.get("boost_end_min", 0))


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
        self._attr_options = list(PROG_PROFILE_NAMES)
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
        if profile is None or profile >= len(PROG_PROFILE_NAMES):
            # Unknown index (unverified mapping for acm/htr_mod families).
            return None
        return PROG_PROFILE_NAMES[profile]

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        await super().async_added_to_hass()
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
        self._track_next_change()
        super()._async_change(change)

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
        ref = NodeRef(self._node.node_type, str(self._node.addr))
        await self._runtime.engine.set_prog(self._dev_id, ref, prog)
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
