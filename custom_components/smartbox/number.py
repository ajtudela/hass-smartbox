"""Support for Smartbox number entities.

Entities read state through the adapter's accessors over ``runtime.engine``
and write through its named ops; updates arrive via the shared
``EngineChangeFanOut``, with one registration per node - or per device for the
box-level number. The REST discovery identities are kept only for registry
identity, device-info fields and the entity creation gates.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import math
from typing import TYPE_CHECKING, Any, ClassVar, cast

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import (
    EntityCategory,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.util.unit_conversion import TemperatureConverter
from smartbox.device.codes import accepts_extra_options
from smartbox.device.modes import boost_params_editable
from smartbox.device.profile import (
    ControllerLimits,
    boost_temperature_bounds,
    controller_limits,
    max_limit_bounds,
    profile_bounds,
)
from smartbox.protocol.errors import SmartboxError
from smartbox.protocol.values import (
    celsius_to_node_units,
    node_units_to_celsius,
    parse_number,
    parse_number_or_none,
)

from .adapter import (
    SmartboxBoxAdapterEntity,
    SmartboxNodeAdapterEntity,
    extra_temp_from_node_units,
    node_boost_params,
    node_max_stemp_limit,
    node_max_stemp_limit_c,
    node_power_limit,
    node_runback_limits,
    node_runback_params,
    node_setup_float,
    node_units,
)
from .const import (
    BOOST_TIME_MIN,
    BOOST_TIME_STEP,
    MAX_TEMP_LIMIT_DEFAULT,
    RUNBACK_TIME_DEFAULT,
    RUNBACK_TIME_MAX,
    RUNBACK_TIME_STEP,
)
from .entity_builder import EntityBuilder
from .exceptions import validation_error
from .identity import (
    SmartboxDeviceInfo,
    SmartboxNodeInfo,
    away_offset_available,
    boost_params_available,
    max_temp_limit_available,
    node_power_limit_available,
    prog_temps_available,
    runback_params_available,
)
from .mapping import get_temperature_unit

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import SmartboxConfigEntry, SmartboxRuntime
    from .adapter import EngineChangeFanOut
    from .entity_builder import EntityGroup

_LOGGER = logging.getLogger(__name__)

# Updates arrive by push, and actions ride the single ws_user connection,
# whose frames are already ordered; the engine serializes writes per node.
# A client-side queue would only add latency to scenes and scripts.
PARALLEL_UPDATES = 0

# Upper bound enforced by the web app UI (cannot save a larger number).
_MAX_POWER_LIMIT = 60000


class _DeviceScaleBounds:
    """Mixin: number bounds expressed per device temperature scale.

    The official app presents these sliders in the device's scale, so each
    scale gets its own (min, max, step) via the overrides below. Subclasses
    must not set the ``_attr_native_*`` bounds themselves.
    """

    _C_SCALE_BOUNDS: ClassVar[tuple[float, float, float]]
    _F_SCALE_BOUNDS: ClassVar[tuple[float, float, float]]

    # Provided by the entity base classes; redeclared for the mixin.
    _status: dict[str, Any]

    @property
    def _device_scale_bounds(self) -> tuple[float, float, float]:
        """Return (min, max, step) for the device's temperature scale."""
        if get_temperature_unit(self._status) == UnitOfTemperature.FAHRENHEIT:
            return self._F_SCALE_BOUNDS
        return self._C_SCALE_BOUNDS

    def _bounds_from_celsius(
        self,
        min_c: float,
        max_c: float,
        celsius_step: float,
        fahrenheit_step: float,
    ) -> tuple[float, float, float]:
        """Convert a Celsius (min, max) pair to the node's native scale."""
        if get_temperature_unit(self._status) == UnitOfTemperature.FAHRENHEIT:
            convert = TemperatureConverter.convert
            return (
                float(
                    math.floor(
                        convert(
                            min_c,
                            UnitOfTemperature.CELSIUS,
                            UnitOfTemperature.FAHRENHEIT,
                        )
                    )
                ),
                float(
                    math.ceil(
                        convert(
                            max_c,
                            UnitOfTemperature.CELSIUS,
                            UnitOfTemperature.FAHRENHEIT,
                        )
                    )
                ),
                fahrenheit_step,
            )
        return (min_c, max_c, celsius_step)

    @property
    def native_min_value(self) -> float:
        """Return the native minimum value."""
        return self._device_scale_bounds[0]

    @property
    def native_max_value(self) -> float:
        """Return the native maximum value."""
        return self._device_scale_bounds[1]

    @property
    def native_step(self) -> float:
        """Return the native step."""
        return self._device_scale_bounds[2]


class SmartboxNodeNumberEntity(SmartboxNodeAdapterEntity, NumberEntity):
    """Base for node-level number entities (identity on the node adapter base)."""

    _attr_entity_category = EntityCategory.CONFIG

    def _controller_limits(self) -> ControllerLimits:
        """Return the decoded controller limits for this node."""
        return self._controller_profile().limits


def _build_device_entities(
    device: SmartboxDeviceInfo,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
    fanout: EngineChangeFanOut,
) -> list[EntityGroup]:
    """Build the box power-limit number when the device exposes it."""
    if not device.box_power_limit_available:
        return []
    return [([PowerLimit(device, entry, runtime, fanout, "power_limit")], True)]


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - HA platform signature
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox number platform")

    runtime = entry.runtime_data
    fanout = runtime.fanout

    # Power limit lives on the box device itself; a limit of 0 means "no
    # limit" and is managed by the no_power_limit switch. Boxless devices
    # reject the endpoint, so they get neither entity.
    def build_device(device: SmartboxDeviceInfo) -> list[EntityGroup]:
        """Build the box power-limit number when the device exposes it."""
        return _build_device_entities(device, entry, runtime, fanout)

    def build_node(node: SmartboxNodeInfo) -> list[EntityGroup]:
        """Build the number entities this node's setup exposes."""
        return _build_node_numbers(node, entry, runtime, fanout)

    runtime.entity_builder.register(
        async_add_entities, build_node=build_node, build_device=build_device
    )
    for device in runtime.identity.devices:
        EntityBuilder.add_groups(build_device(device), async_add_entities)
    for node in runtime.identity.nodes:
        EntityBuilder.add_groups(build_node(node), async_add_entities)

    _LOGGER.debug("Finished setting up Smartbox number platform")


def _build_node_numbers(
    node: SmartboxNodeInfo,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
    fanout: EngineChangeFanOut,
) -> list[EntityGroup]:
    """Build the number entities this node's setup exposes.

    Module-level so the per-capability branches do not contribute to the
    platform setup's complexity.
    """
    if not node.supported_wire_type:
        return []
    groups: list[EntityGroup] = []
    # A thm node can dispatch to an htr-derived controller whose factory
    # payload carries boost_config (Tevolve floor thermostats), but the
    # thermostat climate entity does not model boost. Legacy product codes
    # reject the extra_options object the two boost numbers write, so the
    # entities (and the service targets they publish) exist only where that
    # setup write can land.
    if (
        node.heater_surface
        and node.boost_available
        and boost_params_available(node)
        and (
            not boost_params_editable(node.node_type, node.pid, node.setup)
            or accepts_extra_options(node.pid)
        )
    ):
        boost_entities: list[ConfigBoostDuration | ConfigBoostTemperature] = [
            ConfigBoostTemperature(
                node, entry, runtime, fanout, "config_boost_temperature"
            ),
            ConfigBoostDuration(node, entry, runtime, fanout, "config_boost_duration"),
        ]
        # Publish the entities the (domain-global) service acts on.
        runtime.services.boost.extend(boost_entities)
        groups.append((cast("list[Entity]", boost_entities), True))

    if node.heater_surface and node.boost_available and runback_params_available(node):
        groups.append(
            (
                [
                    ConfigRunbackTemperature(
                        node, entry, runtime, fanout, "config_runback_temperature"
                    ),
                    ConfigRunbackDuration(
                        node, entry, runtime, fanout, "config_runback_duration"
                    ),
                ],
                True,
            )
        )

    if node_power_limit_available(node):
        groups.append(
            (
                [NodePowerLimit(node, entry, runtime, fanout, "node_power_limit")],
                True,
            )
        )
    if away_offset_available(node):
        groups.append(
            (
                [AwayOffset(node, entry, runtime, fanout, "away_offset")],
                True,
            )
        )
    if max_temp_limit_available(node):
        groups.append(
            (
                [MaxTemperature(node, entry, runtime, fanout, "max_temperature")],
                True,
            )
        )
    if prog_temps_available(node):
        limits = controller_limits(
            node.node_type,
            node.pid,
            node.version.get("fw_version"),
            node.setup,
        )
        prog_entities = [
            prog_temp_cls(
                node,
                entry,
                runtime,
                fanout,
                f"prog_{profile}_temperature",
            )
            for profile, prog_temp_cls in (
                ("frost", ProgFrostTemperature),
                ("eco", ProgEcoTemperature),
                ("comfort", ProgComfortTemperature),
            )
            if profile_bounds(limits, profile) is not None
        ]
        if prog_entities:
            groups.append((cast("list[Entity]", prog_entities), True))
    return groups


class PowerLimit(SmartboxBoxAdapterEntity, NumberEntity):
    """Smartbox device power limit (box device level).

    A value of 0 means "no limit"; the companion ``no_power_limit`` switch
    manages that state. The attribute below carries the hint into the
    more-info dialog, because Home Assistant has no entity-level help-text key
    (``data_description`` is a flow/service key).
    """

    _attr_native_min_value = 0.0
    _attr_native_max_value = _MAX_POWER_LIMIT
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Explain the "0 means no limit" wire semantic."""
        return {"unlimited_value": 0}

    @property
    def native_value(self) -> float | None:
        """Return the native value of the number."""
        device = self._device_state()
        return device.power_limit if device is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        await self._runtime.engine.set_power_limit(self._dev_id, int(value))


class NodePowerLimit(SmartboxNodeNumberEntity):
    """Smartbox node power limit (node device level).

    The node's OWN limit: ``0`` means "no limit" and the companion
    ``no_node_power_limit`` switch manages that state, mirroring the box pair
    (docs/api-ws-user.md §2.3; node-level "0" acceptance is unverified - no
    pmo hardware probed).
    """

    _attr_native_min_value = 0.0
    _attr_native_max_value = _MAX_POWER_LIMIT
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    @property
    def extra_state_attributes(self) -> dict[str, int]:
        """Explain the "0 means no limit" wire semantic."""
        return {"unlimited_value": 0}

    @property
    def native_value(self) -> float | None:
        """Return the node's limit from the store."""
        return node_power_limit(self._node_state())

    async def async_set_native_value(self, value: float) -> None:
        """Update the node's limit."""
        await self._runtime.engine.set_node_power_limit(
            self._dev_id, self._ref, int(value)
        )


class ConfigBoostTemperature(_DeviceScaleBounds, SmartboxNodeNumberEntity):
    """Smartbox boost temperature control.

    Legacy controllers store the value on the device (``extra_options``), so
    the slider reads and writes the same field the vendor app uses. FULL
    WiFi/ATC controllers take it per activation; those values live in the
    entry's :class:`BoostPrefs` store so the slider survives a restart.
    """

    _attr_mode = NumberMode.SLIDER
    _attr_device_class = NumberDeviceClass.TEMPERATURE

    _C_SCALE_BOUNDS = (5.0, 30.0, 0.5)
    _F_SCALE_BOUNDS = (41.0, 86.0, 1.0)

    @property
    def _stores_locally(self) -> bool:
        """Whether this controller takes the value per activation."""
        state = self._node_state()
        setup = state.setup if state is not None else self._node.setup
        return not boost_params_editable(self._node.node_type, self._live_pid, setup)

    @property
    def _device_scale_bounds(self) -> tuple[float, float, float]:
        """Return the controller's boost-temperature range."""
        min_c, max_c = boost_temperature_bounds(self._controller_limits())
        return self._bounds_from_celsius(min_c, max_c, 0.5, 1.0)

    @property
    def native_value(self) -> float | None:
        """Return the current boost temperature."""
        state = self._node_state()
        if self._stores_locally:
            pref = self._runtime.boost_prefs.get(self._node.node_id).boost_temp_c
            if pref is not None:
                return celsius_to_node_units(pref, node_units(state) or "C")
        boost_temp, _boost_minutes = node_boost_params(state)
        return boost_temp

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        if (unit := get_temperature_unit(self._status)) is not None:
            return unit
        return UnitOfTemperature.CELSIUS

    async def async_set_native_value(self, value: float) -> None:
        """Set the boost temperature.

        The service field selector is scale-agnostic, so validate the value
        against THIS node's bounds before writing a nonsense temperature.
        """
        if not self.native_min_value <= value <= self.native_max_value:
            msg = "boost_temperature_out_of_range"
            raise validation_error(
                msg,
                min=f"{self.native_min_value:g}",
                max=f"{self.native_max_value:g}",
                unit=self.native_unit_of_measurement or "",
            )
        state = self._node_state()
        if self._stores_locally:
            units = node_units(state) or "C"
            value_c = node_units_to_celsius(value, units) if units == "F" else value
            await self._runtime.boost_prefs.async_update(
                self._node.node_id, boost_temp_c=value_c
            )
            return
        await self._runtime.engine.set_extra_options(
            self._dev_id,
            self._ref,
            {"boost_temp": f"{extra_temp_from_node_units(state, value):.1f}"},
        )


class ConfigBoostDuration(SmartboxNodeNumberEntity):
    """Smartbox boost duration control."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = NumberDeviceClass.DURATION

    _attr_native_min_value: float = BOOST_TIME_MIN
    _attr_native_max_value: float = 1440.0
    _attr_native_step: float = BOOST_TIME_STEP

    @property
    def _stores_locally(self) -> bool:
        """Whether this controller takes the value per activation."""
        state = self._node_state()
        setup = state.setup if state is not None else self._node.setup
        return not boost_params_editable(self._node.node_type, self._live_pid, setup)

    @property
    def native_max_value(self) -> float:
        """Return the controller's boost-duration ceiling."""
        return float(self._controller_limits().boost_max_minutes)

    @property
    def native_value(self) -> float | None:
        """Return the current boost duration."""
        state = self._node_state()
        if self._stores_locally:
            pref = self._runtime.boost_prefs.get(self._node.node_id).boost_time
            if pref is not None:
                return float(pref)
        _boost_temp, boost_minutes = node_boost_params(state)
        return boost_minutes

    async def async_set_native_value(self, value: float) -> None:
        """Set the boost duration.

        The custom service calls this directly, where HA's own number
        validation does not run; enforce the entity's bounds here.
        """
        if not self.native_min_value <= value <= self.native_max_value:
            msg = "boost_duration_out_of_range"
            raise validation_error(
                msg,
                min=f"{self.native_min_value:g}",
                max=f"{self.native_max_value:g}",
                unit=self.native_unit_of_measurement or "",
            )
        if self._stores_locally:
            await self._runtime.boost_prefs.async_update(
                self._node.node_id, boost_time=int(value)
            )
            return
        await self._runtime.engine.set_extra_options(
            self._dev_id, self._ref, {"boost_time": int(value)}
        )


class ConfigRunbackTemperature(_DeviceScaleBounds, SmartboxNodeNumberEntity):
    """Runback target temperature for a FULL-boost node.

    The vendor app pre-fills from ``default_runback_temp``; that device field
    stays the initial value. A user edit is stored in the entry's
    :class:`BoostPrefs` so it never collides with the shared legacy fields.
    """

    _attr_mode = NumberMode.SLIDER
    _attr_device_class = NumberDeviceClass.TEMPERATURE

    _C_SCALE_BOUNDS = (5.0, 30.0, 0.5)
    _F_SCALE_BOUNDS = (41.0, 86.0, 1.0)

    @property
    def _device_scale_bounds(self) -> tuple[float, float, float]:
        """Return the runback temperature range in the node's scale."""
        state = self._node_state()
        limits = self._controller_limits()
        min_temp, max_temp = node_runback_limits(state)
        units = node_units(state)
        if min_temp is None:
            min_c = limits.min_c
        else:
            min_c = node_units_to_celsius(min_temp, "F") if units == "F" else min_temp
        if max_temp is None:
            max_c = limits.max_c
        else:
            max_c = node_units_to_celsius(max_temp, "F") if units == "F" else max_temp
        return self._bounds_from_celsius(min_c, max_c, 0.5, 1.0)

    @property
    def native_value(self) -> float | None:
        """Return the runback temperature."""
        state = self._node_state()
        pref = self._runtime.boost_prefs.get(self._node.node_id).runback_temp_c
        if pref is not None:
            return celsius_to_node_units(pref, node_units(state) or "C")
        temperature, _minutes = node_runback_params(state)
        return temperature

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        if (unit := get_temperature_unit(self._status)) is not None:
            return unit
        return UnitOfTemperature.CELSIUS

    async def async_set_native_value(self, value: float) -> None:
        """Store the runback temperature as a Home Assistant preference."""
        if not self.native_min_value <= value <= self.native_max_value:
            msg = "runback_temperature_out_of_range"
            raise validation_error(
                msg,
                min=f"{self.native_min_value:g}",
                max=f"{self.native_max_value:g}",
                unit=self.native_unit_of_measurement or "",
            )
        state = self._node_state()
        units = node_units(state) or "C"
        value_c = node_units_to_celsius(value, units) if units == "F" else value
        await self._runtime.boost_prefs.async_update(
            self._node.node_id, runback_temp_c=value_c
        )


class ConfigRunbackDuration(SmartboxNodeNumberEntity):
    """Runback duration for a FULL-boost node (30-minute app grid).

    The app shares ``extra_options.boost_time`` as a runback default, but the
    modes are separate screens with separate limits. This entity keeps a
    runback-specific Home Assistant preference instead of overloading the
    boost duration field.
    """

    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = NumberDeviceClass.DURATION

    _attr_native_min_value: float = RUNBACK_TIME_DEFAULT
    _attr_native_max_value: float = float(RUNBACK_TIME_MAX)
    _attr_native_step: float = float(RUNBACK_TIME_STEP)

    @property
    def native_max_value(self) -> float:
        """Return the node's ``max_runback_time`` ceiling when present."""
        state = self._node_state()
        setup = state.setup if state is not None else self._node.setup
        extra = setup.get("extra_options")
        raw = extra.get("max_runback_time") if isinstance(extra, Mapping) else None
        value = parse_number_or_none(raw)
        if value is None:
            return float(RUNBACK_TIME_MAX)
        return float(max(value, RUNBACK_TIME_DEFAULT))

    @property
    def native_value(self) -> float | None:
        """Return the runback duration preference, or the app default."""
        pref = self._runtime.boost_prefs.get(self._node.node_id).runback_time
        if pref is None:
            return float(RUNBACK_TIME_DEFAULT)
        return float(pref)

    async def async_set_native_value(self, value: float) -> None:
        """Store the runback duration as a Home Assistant preference."""
        if not self.native_min_value <= value <= self.native_max_value:
            msg = "runback_duration_out_of_range"
            raise validation_error(
                msg,
                min=f"{self.native_min_value:g}",
                max=f"{self.native_max_value:g}",
                unit=self.native_unit_of_measurement or "",
            )
        await self._runtime.boost_prefs.async_update(
            self._node.node_id, runback_time=int(value)
        )


class AwayOffset(_DeviceScaleBounds, SmartboxNodeNumberEntity):
    """Smartbox away temperature offset control.

    While the box's away switch is on, the heater shifts its target
    temperature by this amount. It is a DELTA in the device's scale, not an
    absolute temperature, so it carries no TEMPERATURE device class and HA must
    not unit-convert it. Wire values are strings.

    Slider bounds are 0.0-5.0 in 0.5 °C steps, or 0-9 in whole °F. The app
    slider is 0-based, so heaters can only lower their away target.
    """

    _attr_mode = NumberMode.SLIDER

    _C_SCALE_BOUNDS = (0.0, 5.0, 0.5)
    _F_SCALE_BOUNDS = (0.0, 9.0, 1.0)

    @property
    def native_value(self) -> float | None:
        """Return the current away offset."""
        return node_setup_float(self._node_state(), "away_offset")

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        if (unit := get_temperature_unit(self._status)) is not None:
            return unit
        return UnitOfTemperature.CELSIUS

    async def async_set_native_value(self, value: float) -> None:
        """Set the away offset."""
        await self._runtime.engine.set_away_offset(self._dev_id, self._ref, value)


class MaxTemperature(_DeviceScaleBounds, SmartboxNodeNumberEntity):
    """Smartbox maximum target temperature control.

    The ceiling the heater accepts as a target; the climate entity clamps its
    own max_temp to it. fw-1.9-family setup field only, with slider bounds of
    16-30 in 1 °C steps or 60-86 in 2 °F steps. The companion switch turns the
    limit off (wire "0.0"); while disabled this number's state is unknown until
    a write re-enables it.
    """

    _attr_mode = NumberMode.SLIDER
    _attr_device_class = NumberDeviceClass.TEMPERATURE

    _C_SCALE_BOUNDS = (16.0, MAX_TEMP_LIMIT_DEFAULT, 1.0)
    _F_SCALE_BOUNDS = (60.0, 86.0, 2.0)

    @property
    def _device_scale_bounds(self) -> tuple[float, float, float]:
        """Return the controller's max-limit range."""
        min_c, max_c = max_limit_bounds(self._controller_limits())
        return self._bounds_from_celsius(min_c, max_c, 1.0, 2.0)

    @property
    def native_value(self) -> float | None:
        """Return the current maximum target temperature."""
        return node_max_stemp_limit(self._node_state())

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        if (unit := get_temperature_unit(self._status)) is not None:
            return unit
        return UnitOfTemperature.CELSIUS

    async def async_set_native_value(self, value: float) -> None:
        """Set the maximum target temperature."""
        profile = self._controller_profile(max_limit_c=value)
        await self._runtime.engine.set_max_stemp_limit(
            self._dev_id,
            self._ref,
            value,
            node_units(self._node_state()),
            bounds_c=profile.prog_bounds,
            offset_c=profile.limits.profile_offset_c,
        )


class ProgTemperatureBase(_DeviceScaleBounds, SmartboxNodeNumberEntity):
    """Base for the programme profile temperature controls.

    The weekly schedule references these profiles, and the write goes out as
    the app-shaped 4-key status body (see ``engine.set_prog_temps``): profiles
    the caller does not change are sent back with their current values plus
    units. The vendor app clamps profile temps against their neighbours before
    sending (docs/api-ws-user.md §2.8), and the engine applies that clamp; the
    slider bounds here are scale-coherent overall bounds, not the
    context-dependent per-profile result.
    """

    _attr_mode = NumberMode.SLIDER
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    status_key: str

    _C_SCALE_BOUNDS = (5.0, 30.0, 0.5)
    _F_SCALE_BOUNDS = (41.0, 86.0, 1.0)

    @property
    def _device_scale_bounds(self) -> tuple[float, float, float]:
        """Return the controller's bounds for this programme profile."""
        state = self._node_state()
        profile = self._controller_profile(max_limit_c=node_max_stemp_limit_c(state))
        bounds = (profile.prog_bounds or {}).get(self.status_key)
        if bounds is None:
            return super()._device_scale_bounds
        return self._bounds_from_celsius(bounds[0], bounds[1], 0.5, 1.0)

    @property
    def native_value(self) -> float | None:
        """Return the current profile temperature."""
        value = self._status.get(self.status_key)
        if value is None:
            return None
        try:
            return parse_number(value)
        except SmartboxError as ex:
            _LOGGER.debug("Ignoring malformed %r: %s", self.status_key, ex)
            return None

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        if (unit := get_temperature_unit(self._status)) is not None:
            return unit
        return UnitOfTemperature.CELSIUS

    async def async_set_native_value(self, value: float) -> None:
        """Set the profile temperature."""
        status = self._status
        current = {
            key: parse_number(status[key]) if key in status else None
            for key in ("ice_temp", "eco_temp", "comf_temp")
        }
        current[self.status_key] = value
        state = self._node_state()
        setup_units = state.setup.get("units") if state is not None else None
        units = status.get("units") or setup_units or "C"
        profile = self._controller_profile(max_limit_c=node_max_stemp_limit_c(state))
        await self._runtime.engine.set_prog_temps(
            self._dev_id,
            self._ref,
            ice_temp=current["ice_temp"],
            eco_temp=current["eco_temp"],
            comf_temp=current["comf_temp"],
            units=units,
            changed=self.status_key,
            bounds_c=profile.prog_bounds,
            offset_c=profile.limits.profile_offset_c,
        )


class ProgFrostTemperature(ProgTemperatureBase):
    """Smartbox frost (ice) programme temperature control."""

    status_key = "ice_temp"


class ProgEcoTemperature(ProgTemperatureBase):
    """Smartbox eco programme temperature control."""

    status_key = "eco_temp"


class ProgComfortTemperature(ProgTemperatureBase):
    """Smartbox comfort programme temperature control."""

    status_key = "comf_temp"
