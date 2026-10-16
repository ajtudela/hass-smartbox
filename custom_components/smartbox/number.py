"""Support for Smartbox sensor entities."""

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import (
    ATTR_TEMPERATURE,
    EntityCategory,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import (
    ATTR_DURATION,
    DEFAULT_BOOST_TIME,
    DOMAIN,
    MAX_TEMP_LIMIT_DEFAULT,
    SERVICE_SET_BOOST_PARAMS,
)
from .entity import SmartboxBoxEntity, SmartBoxNodeEntity
from .models import (
    away_offset_available,
    get_temperature_unit,
    max_temp_limit_available,
    prog_temps_available,
    resolve_target_entity_ids,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import SmartboxConfigEntry

_LOGGER = logging.getLogger(__name__)
# Upper bound enforced by the web app UI (cannot save a larger number).
_MAX_POWER_LIMIT = 60000


class _DeviceScaleBounds:
    """Mixin: number bounds expressed per device temperature scale.

    The official app presents these sliders in the device's scale
    (app-verified 2026-09-29), so each scale gets its own
    (min, max, step) via the overrides below. Subclasses must not set
    _attr_native_min_value/_attr_native_max_value/_attr_native_step.
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox number platform")

    # Power limit lives on the box device itself; always created (a limit
    # of 0 means "no limit" and is managed by the no_power_limit switch).
    async_add_entities(
        [PowerLimit(device, entry) for device in entry.runtime_data.devices],
        update_before_add=True,
    )
    # Add boost temperature and duration entities for each heater
    boost_entities: list[ConfigBoostDuration | ConfigBoostTemperature] = []
    boost_entities.extend(
        [
            ConfigBoostTemperature(node, entry)
            for node in entry.runtime_data.nodes
            if node.boost_available
        ],
    )
    boost_entities.extend(
        [
            ConfigBoostDuration(node, entry)
            for node in entry.runtime_data.nodes
            if node.boost_available
        ]
    )
    async_add_entities(boost_entities, update_before_add=True)

    # Away offset: present in the setup of every non-PMO node.
    async_add_entities(
        [
            AwayOffset(node, entry)
            for node in entry.runtime_data.nodes
            if away_offset_available(node)
        ],
        update_before_add=True,
    )
    # Maximum target temperature: fw-1.9-family setup field only.
    async_add_entities(
        [
            MaxTemperature(node, entry)
            for node in entry.runtime_data.nodes
            if max_temp_limit_available(node)
        ],
        update_before_add=True,
    )
    # Programme profile temperatures (Frost/Eco/Comfort): plain htr/acm.
    async_add_entities(
        [
            prog_temp_cls(node, entry)
            for node in entry.runtime_data.nodes
            if prog_temps_available(node)
            for prog_temp_cls in (
                ProgFrostTemperature,
                ProgEcoTemperature,
                ProgComfortTemperature,
            )
        ],
        update_before_add=True,
    )

    async def handle_set_boost_params(call: ServiceCall) -> None:
        """Handle the service call."""
        target_entity_ids = resolve_target_entity_ids(hass, call.data)
        boost_temp = call.data.get(ATTR_TEMPERATURE)
        boost_time = call.data.get(ATTR_DURATION)
        for boost_entity in boost_entities:
            if boost_entity.entity_id not in target_entity_ids:
                continue
            if (
                boost_temp is not None
                and boost_entity.device_class == NumberDeviceClass.TEMPERATURE
            ):
                await boost_entity.async_set_native_value(boost_temp)
            if (
                boost_time is not None
                and boost_entity.device_class == NumberDeviceClass.DURATION
            ):
                await boost_entity.async_set_native_value(boost_time)

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_BOOST_PARAMS,
        handle_set_boost_params,
        schema=vol.All(
            vol.Schema(
                {
                    vol.Optional(ATTR_TEMPERATURE): vol.Coerce(float),
                    vol.Optional(ATTR_DURATION): vol.Coerce(int),
                    **(cv.ENTITY_SERVICE_FIELDS),
                },
            ),
            cv.has_at_least_one_key(ATTR_TEMPERATURE, ATTR_DURATION),
        ),
    )
    _LOGGER.debug("Finished setting up Smartbox number platform")


class PowerLimit(SmartboxBoxEntity, NumberEntity):
    """Smartbox device power limit (box device level).

    A value of 0 means "no limit" — the box entity renders as such and
    the companion no_power_limit switch manages that state.
    """

    _attr_key = "power_limit"
    _attr_websocket_event = "power_limit"
    _attr_native_min_value = 0.0
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


class ConfigBoostTemperature(_DeviceScaleBounds, SmartBoxNodeEntity, NumberEntity):
    """Smartbox boost temperature control."""

    _attr_key = "config_boost_temperature"
    _attr_websocket_event = "setup"
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE

    _C_SCALE_BOUNDS = (5.0, 30.0, 0.5)
    _F_SCALE_BOUNDS = (41.0, 86.0, 1.0)

    @property
    def native_value(self) -> float:
        """Return the current boost temperature."""
        return self._node.boost_temp

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        if (unit := get_temperature_unit(self._status)) is not None:
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


class AwayOffset(_DeviceScaleBounds, SmartBoxNodeEntity, NumberEntity):
    """Smartbox away temperature offset control.

    While the box's away switch is on, the heater adjusts its target
    temperature by this amount. This is a DELTA in the device's
    temperature scale (C/F), not an absolute temperature, so it carries
    no TEMPERATURE device class: HA must not unit-convert it as one
    (2.0 °F is a 2 °F adjustment, not -16.7 °C). Wire values are strings.
    App-verified slider bounds (2026-09-29): 0.0-5.0 in 0.5 °C steps, or
    0-9 in whole °F. The app slider is 0-based (no negative offsets, so
    heaters can only "lower" their away target); if cooling units ever
    expose negative away offsets, the lower bound would need revisiting.
    """

    _attr_key = "away_offset"
    _attr_websocket_event = "setup"
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    _C_SCALE_BOUNDS = (0.0, 5.0, 0.5)
    _F_SCALE_BOUNDS = (0.0, 9.0, 1.0)

    @property
    def native_value(self) -> float | None:
        """Return the current away offset."""
        return self._node.away_offset

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        if (unit := get_temperature_unit(self._status)) is not None:
            return unit
        return UnitOfTemperature.CELSIUS

    async def async_set_native_value(self, value: float) -> None:
        """Set the away offset."""
        await self._node.set_away_offset(value)


class MaxTemperature(_DeviceScaleBounds, SmartBoxNodeEntity, NumberEntity):
    """Smartbox maximum target temperature control.

    Upper limit the heater accepts as a target temperature. The climate
    entity clamps its own max_temp to the same value. fw-1.9-family
    setup field only. App-verified slider bounds (2026-09-29): 16-30 in
    1 °C steps, or 60-86 in 2 °F steps. The limit can be turned off via
    the node's "Maximum limit" switch (wire "0.0"); while disabled this
    number's state is unknown until a value is written, which re-enables
    the limit.
    """

    _attr_key = "max_temperature"
    _attr_websocket_event = "setup"
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE

    _C_SCALE_BOUNDS = (16.0, MAX_TEMP_LIMIT_DEFAULT, 1.0)
    _F_SCALE_BOUNDS = (60.0, 86.0, 2.0)

    @property
    def native_value(self) -> float | None:
        """Return the current maximum target temperature."""
        return self._node.max_stemp_limit

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        if (unit := get_temperature_unit(self._status)) is not None:
            return unit
        return UnitOfTemperature.CELSIUS

    async def async_set_native_value(self, value: float) -> None:
        """Set the maximum target temperature."""
        await self._node.set_max_stemp_limit(value)


class ProgTemperatureBase(_DeviceScaleBounds, SmartBoxNodeEntity, NumberEntity):
    """Base for the programme profile temperature controls.

    The node's weekly schedule references these profiles; the write goes
    out as the app-shaped 4-key status body (see
    SmartboxNode.set_prog_temps). The device clamps profile temps against
    each other (webapi-spec.md §4.4); no client-side clamping here.
    Slider bounds are scale-coherent but NOT app-verified: the app
    additionally clamps profile temps against their schedule neighbours,
    so its effective range is context-dependent; the C grid below matches
    the app's coarse span and Fahrenheit is scaled to the same span.
    """

    _attr_websocket_event = "status"
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    status_key: str

    _C_SCALE_BOUNDS = (5.0, 30.0, 0.5)
    _F_SCALE_BOUNDS = (41.0, 86.0, 1.0)

    @property
    def native_value(self) -> float | None:
        """Return the current profile temperature."""
        if self.status_key not in self._status:
            return None
        return float(self._status[self.status_key])

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        if (unit := get_temperature_unit(self._status)) is not None:
            return unit
        return UnitOfTemperature.CELSIUS

    async def async_set_native_value(self, value: float) -> None:
        """Set the profile temperature."""
        await self._node.set_prog_temps(**{self.status_key: value})


class ProgFrostTemperature(ProgTemperatureBase):
    """Smartbox frost (ice) programme temperature control."""

    _attr_key = "prog_frost_temperature"
    status_key = "ice_temp"


class ProgEcoTemperature(ProgTemperatureBase):
    """Smartbox eco programme temperature control."""

    _attr_key = "prog_eco_temperature"
    status_key = "eco_temp"


class ProgComfortTemperature(ProgTemperatureBase):
    """Smartbox comfort programme temperature control."""

    _attr_key = "prog_comfort_temperature"
    status_key = "comf_temp"
