"""Support for Smartbox number entities.

Entities read state through the adapter's accessors over ``runtime.engine``
and write through its named ops; updates arrive via the shared
``EngineChangeFanOut``, with one registration per node - or per device for the
box-level number. The REST discovery identities are kept only for registry
identity, device-info fields and the entity creation gates.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, cast

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import (
    EntityCategory,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from smartbox.protocol.errors import SmartboxError
from smartbox.protocol.values import parse_number

from .adapter import (
    SmartboxBoxAdapterEntity,
    SmartboxNodeAdapterEntity,
    node_boost_params,
    node_max_stemp_limit,
    node_power_limit,
    node_setup_float,
    node_units,
)
from .const import DEFAULT_BOOST_TIME, MAX_TEMP_LIMIT_DEFAULT
from .entity_builder import EntityBuilder
from .exceptions import validation_error
from .identity import (
    SmartboxDeviceInfo,
    SmartboxNodeInfo,
    away_offset_available,
    max_temp_limit_available,
    node_power_limit_available,
    prog_temps_available,
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
        groups: list[EntityGroup] = []
        if node.boost_available:
            boost_entities: list[ConfigBoostDuration | ConfigBoostTemperature] = [
                ConfigBoostTemperature(
                    node, entry, runtime, fanout, "config_boost_temperature"
                ),
                ConfigBoostDuration(
                    node, entry, runtime, fanout, "config_boost_duration"
                ),
            ]
            # Publish the entities the (domain-global) service acts on.
            runtime.services.boost.extend(boost_entities)
            groups.append((cast("list[Entity]", boost_entities), True))

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
            groups.append(
                (
                    [
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
                    ],
                    True,
                )
            )
        return groups

    runtime.entity_builder.register(
        async_add_entities, build_node=build_node, build_device=build_device
    )
    for device in runtime.identity.devices:
        EntityBuilder.add_groups(build_device(device), async_add_entities)
    for node in runtime.identity.nodes:
        EntityBuilder.add_groups(build_node(node), async_add_entities)

    _LOGGER.debug("Finished setting up Smartbox number platform")


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

    The stored value follows the node's temperature scale, so the slider
    bounds are scale-coherent: 5-30 °C in 0.5 steps or 41-86 °F in whole
    degrees (the same ranges as the programme temperatures).
    """

    _attr_mode = NumberMode.SLIDER
    _attr_device_class = NumberDeviceClass.TEMPERATURE

    _C_SCALE_BOUNDS = (5.0, 30.0, 0.5)
    _F_SCALE_BOUNDS = (41.0, 86.0, 1.0)

    @property
    def native_value(self) -> float | None:
        """Return the current boost temperature."""
        state = self._node_state()
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
        await self._runtime.engine.set_extra_options(
            self._dev_id, self._ref, {"boost_temp": str(value)}
        )


class ConfigBoostDuration(SmartboxNodeNumberEntity):
    """Smartbox boost duration control."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = NumberDeviceClass.DURATION

    _attr_native_min_value: float = DEFAULT_BOOST_TIME
    _attr_native_max_value: float = 240.0
    _attr_native_step: float = 60.0

    @property
    def native_value(self) -> float | None:
        """Return the current boost duration."""
        state = self._node_state()
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
        await self._runtime.engine.set_extra_options(
            self._dev_id, self._ref, {"boost_time": int(value)}
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
        await self._runtime.engine.set_max_stemp_limit(
            self._dev_id, self._ref, value, node_units(self._node_state())
        )


class ProgTemperatureBase(_DeviceScaleBounds, SmartboxNodeNumberEntity):
    """Base for the programme profile temperature controls.

    The weekly schedule references these profiles, and the write goes out as
    the app-shaped 4-key status body (see ``engine.set_prog_temps``): profiles
    the caller does not change are sent back with their current values plus
    units. The vendor app clamps profile temps against their neighbours before
    sending (docs/webapi-spec.md §5), and the engine applies that clamp; the
    slider bounds here are scale-coherent overall bounds, not the
    context-dependent per-profile result.
    """

    _attr_mode = NumberMode.SLIDER
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    status_key: str

    _C_SCALE_BOUNDS = (5.0, 30.0, 0.5)
    _F_SCALE_BOUNDS = (41.0, 86.0, 1.0)

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
        await self._runtime.engine.set_prog_temps(
            self._dev_id,
            self._ref,
            ice_temp=current["ice_temp"],
            eco_temp=current["eco_temp"],
            comf_temp=current["comf_temp"],
            units=status.get("units"),
            changed=self.status_key,
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
