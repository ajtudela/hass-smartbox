"""Domain services: registration, validation and target dispatch.

The three services are domain-global. Each entry publishes its service-capable
entities on ``runtime.services``; the handlers fan out across every loaded
entry, so a call reaches the same entity set regardless of which entry loaded
first. Registration happens once, from the entry lifecycle, and removal
happens when the last loaded entry goes away.

Target resolution is deliberately the integration's own: Home Assistant's
entity-service helper filters unavailable entities out, while these services
still attempt a write when the user targets one, matching the vendor app's
behavior. Every user-visible failure goes through
``exceptions.validation_error`` (bad input) or ``exceptions.user_error``
(state restriction / failed write); the keys live under ``exceptions`` in
``strings.json``.

All three services act only on the call's target. Home Assistant does not
require a target for a manually registered service, so
:func:`require_service_target` rejects a call that resolves to nothing instead
of reporting a silent no-op.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.components.number import NumberDeviceClass
from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
)
from homeassistant.core import callback
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from smartbox.protocol.errors import AuthError, SmartboxError, TransportUnavailableError
from smartbox.protocol.writes import VALID_BOOST_MODES

from .compat import vol
from .const import (
    ATTR_DURATION,
    DAY_KEYS,
    DOMAIN,
    FIELD_ACTIVE_DAYS,
    FIELD_RESOLUTION,
    FIELD_SCHEDULE,
    FIELD_SLOT_1,
    FIELD_SLOT_2,
    SERVICE_SET_BOOST_MODE,
    SERVICE_SET_BOOST_PARAMS,
    SERVICE_SET_CHARGING_SCHEDULE,
    SERVICE_SET_SCHEDULE,
    SERVICE_SET_SCHEDULE_RESOLUTION,
    SERVICE_SET_THERMOSTAT_SCHEDULE,
)
from .exceptions import user_error, validation_error

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant, ServiceCall

    from . import SmartboxRuntime
    from .mapping import ProgDict

__all__ = [
    "BoostModeServiceTarget",
    "BoostServiceTarget",
    "ScheduleServiceTarget",
    "ServiceTargets",
    "async_register_services",
    "async_unregister_services",
    "loaded_runtimes",
    "remove_service_if_last",
    "require_service_target",
    "resolve_target_entity_ids",
]

_SERVICE_NAMES = (
    SERVICE_SET_BOOST_MODE,
    SERVICE_SET_BOOST_PARAMS,
    SERVICE_SET_SCHEDULE,
    SERVICE_SET_SCHEDULE_RESOLUTION,
    SERVICE_SET_CHARGING_SCHEDULE,
    SERVICE_SET_THERMOSTAT_SCHEDULE,
)


class ScheduleServiceTarget(Protocol):
    """Entity shape ``smartbox.set_schedule`` needs (schedule sensors)."""

    @property
    def entity_id(self) -> str | None:
        """The registry entity id the service call targets."""

    async def async_set_schedule(self, prog: ProgDict) -> None:
        """Write the schedule for this entity's node."""

    async def async_set_schedule_resolution(self, resolution: int) -> None:
        """Write the programme resolution for this entity's node."""


class ThermostatScheduleTarget(Protocol):
    """Entity shape ``smartbox.set_thermostat_schedule`` needs."""

    @property
    def entity_id(self) -> str | None:
        """The registry entity id the service call targets."""

    async def async_set_thermostat_schedule(
        self, prog: dict[str, list[dict[str, object]]]
    ) -> None:
        """Write the thermostat programme."""


class ChargingScheduleTarget(Protocol):
    """Entity shape ``smartbox.set_charging_schedule`` needs."""

    @property
    def entity_id(self) -> str | None:
        """The registry entity id the service call targets."""

    async def async_set_charging_schedule(
        self,
        *,
        active_days: list[int] | None,
        slot_1: dict[str, int] | None,
        slot_2: dict[str, int] | None,
    ) -> None:
        """Write the storage charging configuration."""


class BoostServiceTarget(Protocol):
    """Entity shape ``smartbox.set_boost_params`` needs (boost numbers)."""

    @property
    def entity_id(self) -> str | None:
        """The registry entity id the service call targets."""

    @property
    def device_class(self) -> str | None:
        """``temperature`` / ``duration``: which service field applies."""

    async def async_set_native_value(self, value: float) -> None:
        """Write one boost parameter."""


class BoostModeServiceTarget(Protocol):
    """Entity shape ``smartbox.set_boost_mode`` needs (climate entities)."""

    @property
    def entity_id(self) -> str | None:
        """The registry entity id the service call targets."""

    @property
    def supports_boost_mode(self) -> bool:
        """Whether this entity's controller has the full-boost endpoint."""

    async def async_set_boost_mode(
        self,
        *,
        active: bool,
        mode: str | None,
        temperature: float | None,
        time_minutes: int | None,
    ) -> None:
        """Activate/deactivate a full-boost mode for this entity's node."""


@dataclass
class ServiceTargets:
    """Per-entry entity lists the domain services act on.

    The services are domain-global, so their handlers iterate every LOADED
    entry's lists (see :func:`loaded_runtimes`); this object is where a
    platform publishes its entities for that fan-out.
    """

    schedule: list[ScheduleServiceTarget] = field(default_factory=list)
    thermostat_schedule: list[ThermostatScheduleTarget] = field(default_factory=list)
    charging: list[ChargingScheduleTarget] = field(default_factory=list)
    boost: list[BoostServiceTarget] = field(default_factory=list)
    climate: list[BoostModeServiceTarget] = field(default_factory=list)

    @callback
    def discard(self, target: object) -> None:
        """Forget one entity's targets; keeps the lists in step with removals.

        Identity, not ``entity_id``: a re-added entity can reuse the id while
        the outgoing entity's removal callback is still pending.
        """
        self.schedule[:] = [entity for entity in self.schedule if entity is not target]
        self.thermostat_schedule[:] = [
            entity for entity in self.thermostat_schedule if entity is not target
        ]
        self.charging[:] = [entity for entity in self.charging if entity is not target]
        self.boost[:] = [entity for entity in self.boost if entity is not target]
        self.climate[:] = [entity for entity in self.climate if entity is not target]


def resolve_target_entity_ids(hass: HomeAssistant, data: dict[str, Any]) -> set[str]:
    """Resolve a service call's targets (entity/device/area) to entity ids."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    target_entity_ids: set[str] = set(data.get(ATTR_ENTITY_ID, []))
    for device_id in data.get(ATTR_DEVICE_ID, []):
        target_entity_ids.update(
            entity.entity_id
            for entity in er.async_entries_for_device(entity_registry, device_id)
        )
    for area_id in data.get(ATTR_AREA_ID, []):
        for device in dr.async_entries_for_area(device_registry, area_id):
            target_entity_ids.update(
                entity.entity_id
                for entity in er.async_entries_for_device(entity_registry, device.id)
            )
    return target_entity_ids


def require_service_target(
    hass: HomeAssistant, data: dict[str, Any], service: str
) -> set[str]:
    """Resolve a service call's targets, or raise when there is none.

    The services act only on the call's target, and Home Assistant does not
    require one for a manually registered service. Without this a call would
    iterate nothing and report success - a silent no-op.
    """
    target_entity_ids = resolve_target_entity_ids(hass, data)
    if not target_entity_ids:
        msg = "service_no_target"
        raise validation_error(msg, service=service)
    return target_entity_ids


@callback
def remove_service(hass: HomeAssistant, service: str) -> None:
    """Drop a service, if it is still there.

    A manually registered service outlives the entities it acts on, so after
    unload it would keep accepting calls and silently skip every target.
    Guarded, so unloading one entry cannot raise over another entry's service
    of the same name.
    """
    if hass.services.has_service(DOMAIN, service):
        hass.services.async_remove(DOMAIN, service)


def loaded_runtimes(hass: HomeAssistant) -> Iterator[SmartboxRuntime]:
    """Yield the runtime of every loaded config entry of this domain.

    The domain services are registered once and act across entries, so their
    handlers resolve targets against every loaded entry's entities - not only
    the entry that happened to register the handler.
    """
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if runtime is not None:
            yield runtime


@callback
def remove_service_if_last(hass: HomeAssistant, service: str) -> None:
    """Remove a domain service once no loaded entry needs it.

    Runs from an entry's on-unload, at which point the unloading entry no
    longer counts as loaded: a surviving loaded entry keeps the service (and
    its handler) alive.
    """
    if not hass.config_entries.async_loaded_entries(DOMAIN):
        remove_service(hass, service)


async def _handle_set_boost_mode(hass: HomeAssistant, call: ServiceCall) -> None:
    """Activate/deactivate a full-boost mode on targeted climate entities."""
    active = bool(call.data.get("active", True))
    mode = call.data.get("mode")
    temperature = call.data.get(ATTR_TEMPERATURE)
    duration = call.data.get(ATTR_DURATION)
    if active and mode not in VALID_BOOST_MODES:
        msg = "boost_mode_requires_mode"
        raise validation_error(
            msg,
            service=SERVICE_SET_BOOST_MODE,
            modes=", ".join(VALID_BOOST_MODES),
        )
    if not active and (
        mode is not None or temperature is not None or duration is not None
    ):
        msg = "boost_mode_inactive_args"
        raise validation_error(msg, service=SERVICE_SET_BOOST_MODE)
    target_entity_ids = require_service_target(hass, call.data, SERVICE_SET_BOOST_MODE)
    matched = [
        climate_entity
        for other in loaded_runtimes(hass)
        for climate_entity in other.services.climate
        if climate_entity.entity_id in target_entity_ids
    ]
    if not matched:
        msg = "boost_mode_wrong_target"
        raise validation_error(msg, service=SERVICE_SET_BOOST_MODE)
    # Skip entities the endpoint cannot serve (thermostats, non-FULL heaters)
    # when a mixed target also contains a capable entity; otherwise call the
    # ineligible ones so a single non-FULL target keeps its own translated
    # refusal.
    eligible = [
        climate_entity
        for climate_entity in matched
        if climate_entity.supports_boost_mode
    ]
    for climate_entity in eligible or matched:
        try:
            await climate_entity.async_set_boost_mode(
                active=active,
                mode=mode,
                temperature=temperature,
                time_minutes=duration,
            )
        except (SmartboxError, ValueError) as ex:
            msg = "boost_mode_write_failed"
            raise user_error(
                msg,
                entity_id=climate_entity.entity_id or "",
                error=str(ex),
            ) from ex


async def _handle_set_boost_params(hass: HomeAssistant, call: ServiceCall) -> None:
    """Set the boost temperature and/or duration on targeted number entities."""
    target_entity_ids = require_service_target(
        hass, call.data, SERVICE_SET_BOOST_PARAMS
    )
    boost_temp = call.data.get(ATTR_TEMPERATURE)
    boost_time = call.data.get(ATTR_DURATION)
    matched_boost_entities = [
        boost_entity
        for other in loaded_runtimes(hass)
        for boost_entity in other.services.boost
        if boost_entity.entity_id in target_entity_ids
    ]
    # The target pickers cannot filter by capability (HA target filters only
    # know integration/domain/device_class), so a call can name heaters whose
    # setup has no boost numbers. Refuse before writing instead of dropping a
    # parameter and reporting success.
    if boost_temp is not None and not any(
        entity.device_class == NumberDeviceClass.TEMPERATURE
        for entity in matched_boost_entities
    ):
        msg = "boost_params_wrong_temperature_target"
        raise validation_error(
            msg,
            service=SERVICE_SET_BOOST_PARAMS,
        )
    if boost_time is not None and not any(
        entity.device_class == NumberDeviceClass.DURATION
        for entity in matched_boost_entities
    ):
        msg = "boost_params_wrong_duration_target"
        raise validation_error(
            msg,
            service=SERVICE_SET_BOOST_PARAMS,
        )
    for boost_entity in matched_boost_entities:
        try:
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
        except SmartboxError as ex:
            # Same surface as set_schedule: an API failure reaches the caller
            # as a translated HomeAssistantError naming the entity.
            msg = "boost_params_write_failed"
            raise user_error(
                msg,
                entity_id=boost_entity.entity_id or "",
                error=str(ex),
            ) from ex


async def _handle_set_schedule_resolution(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Write the programme resolution to targeted schedule sensors."""
    resolution = int(call.data[FIELD_RESOLUTION])
    target_entity_ids = require_service_target(
        hass, call.data, SERVICE_SET_SCHEDULE_RESOLUTION
    )
    matched = False
    for other in loaded_runtimes(hass):
        for schedule_entity in other.services.schedule:
            if schedule_entity.entity_id not in target_entity_ids:
                continue
            matched = True
            try:
                await schedule_entity.async_set_schedule_resolution(resolution)
            except ValueError as ex:
                msg = "schedule_invalid"
                raise validation_error(
                    msg,
                    entity_id=schedule_entity.entity_id or "",
                    error=str(ex),
                ) from ex
    if not matched:
        msg = "schedule_target_missing"
        raise validation_error(msg, entity_id=next(iter(target_entity_ids), ""))


async def _handle_set_schedule(hass: HomeAssistant, call: ServiceCall) -> None:
    """Write a day-keyed schedule to targeted schedule sensors."""
    schedule: ProgDict = call.data[FIELD_SCHEDULE]
    target_entity_ids = require_service_target(hass, call.data, SERVICE_SET_SCHEDULE)
    matched = False
    for other in loaded_runtimes(hass):
        for schedule_entity in other.services.schedule:
            if schedule_entity.entity_id not in target_entity_ids:
                continue
            matched = True
            try:
                await schedule_entity.async_set_schedule(schedule)
            except ValueError as ex:
                # WriteApi's pre-network validation (day keys / slot shapes)
                msg = "schedule_invalid"
                raise validation_error(
                    msg,
                    entity_id=schedule_entity.entity_id or "",
                    error=str(ex),
                ) from ex
            except SmartboxError as ex:
                if isinstance(ex, AuthError):
                    key = "schedule_auth_failed"
                elif isinstance(ex, TransportUnavailableError):
                    key = "schedule_api_unavailable"
                else:
                    key = "schedule_api_error"
                raise user_error(
                    key,
                    entity_id=schedule_entity.entity_id or "",
                    error=str(ex),
                ) from ex
    if not matched:
        msg = "schedule_wrong_target"
        raise validation_error(msg, service=SERVICE_SET_SCHEDULE)


async def _handle_set_thermostat_schedule(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Write a thermostat ``{m, t}`` programme."""
    schedule = call.data[FIELD_SCHEDULE]
    target_entity_ids = require_service_target(
        hass, call.data, SERVICE_SET_THERMOSTAT_SCHEDULE
    )
    matched = False
    for other in loaded_runtimes(hass):
        for thermostat in other.services.thermostat_schedule:
            if thermostat.entity_id not in target_entity_ids:
                continue
            matched = True
            try:
                await thermostat.async_set_thermostat_schedule(schedule)
            except ValueError as ex:
                msg = "thermostat_schedule_invalid"
                raise validation_error(
                    msg,
                    entity_id=thermostat.entity_id or "",
                    error=str(ex),
                ) from ex
            except SmartboxError as ex:
                if isinstance(ex, AuthError):
                    msg = "thermostat_schedule_auth_failed"
                elif isinstance(ex, TransportUnavailableError):
                    msg = "thermostat_schedule_api_unavailable"
                else:
                    msg = "thermostat_schedule_write_failed"
                raise user_error(
                    msg,
                    entity_id=thermostat.entity_id or "",
                    error=str(ex),
                ) from ex
    if not matched:
        msg = "thermostat_schedule_wrong_target"
        raise validation_error(msg, service=SERVICE_SET_THERMOSTAT_SCHEDULE)


async def _handle_set_charging_schedule(hass: HomeAssistant, call: ServiceCall) -> None:
    """Write the storage charging days and valley slots."""
    active_days = call.data.get(FIELD_ACTIVE_DAYS)
    slot_1 = call.data.get(FIELD_SLOT_1)
    slot_2 = call.data.get(FIELD_SLOT_2)
    target_entity_ids = require_service_target(
        hass, call.data, SERVICE_SET_CHARGING_SCHEDULE
    )
    matched = False
    for other in loaded_runtimes(hass):
        for charging in other.services.charging:
            if charging.entity_id not in target_entity_ids:
                continue
            matched = True
            try:
                await charging.async_set_charging_schedule(
                    active_days=active_days,
                    slot_1=slot_1,
                    slot_2=slot_2,
                )
            except ValueError as ex:
                msg = "charging_schedule_invalid"
                raise validation_error(
                    msg,
                    entity_id=charging.entity_id or "",
                    error=str(ex),
                ) from ex
            except SmartboxError as ex:
                if isinstance(ex, AuthError):
                    msg = "charging_schedule_auth_failed"
                elif isinstance(ex, TransportUnavailableError):
                    msg = "charging_schedule_api_unavailable"
                else:
                    msg = "charging_schedule_write_failed"
                raise user_error(
                    msg,
                    entity_id=charging.entity_id or "",
                    error=str(ex),
                ) from ex
    if not matched:
        msg = "charging_schedule_wrong_target"
        raise validation_error(msg, service=SERVICE_SET_CHARGING_SCHEDULE)


_BOOST_MODE_SCHEMA = vol.Schema(
    {
        vol.Optional("active", default=True): cv.boolean,
        vol.Optional("mode"): vol.In(VALID_BOOST_MODES),
        vol.Optional(ATTR_TEMPERATURE): vol.Coerce(float),
        vol.Optional(ATTR_DURATION): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=1440)
        ),
        **cv.ENTITY_SERVICE_FIELDS,
    }
)

_BOOST_PARAMS_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(ATTR_TEMPERATURE): vol.Coerce(float),
            vol.Optional(ATTR_DURATION): vol.All(
                vol.Coerce(int), vol.Range(min=60, max=1440)
            ),
            **cv.ENTITY_SERVICE_FIELDS,
        },
    ),
    cv.has_at_least_one_key(ATTR_TEMPERATURE, ATTR_DURATION),
)

_THERMOSTAT_SEGMENT_SCHEMA = vol.Schema(
    {
        vol.Required("m"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
        vol.Required("t"): vol.Coerce(float),
    }
)

_THERMOSTAT_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(FIELD_SCHEDULE): vol.All(
            dict,
            vol.Length(min=7, max=7),
            {
                vol.All(vol.Coerce(str), vol.In(DAY_KEYS)): [
                    _THERMOSTAT_SEGMENT_SCHEMA
                ],
            },
        ),
        **cv.ENTITY_SERVICE_FIELDS,
    }
)

_CHARGING_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required("start"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
        vol.Required("end"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1440)),
    }
)

_CHARGING_SCHEDULE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(FIELD_ACTIVE_DAYS): vol.All(
                [cv.boolean], vol.Length(min=7, max=7)
            ),
            vol.Optional(FIELD_SLOT_1): _CHARGING_SLOT_SCHEMA,
            vol.Optional(FIELD_SLOT_2): _CHARGING_SLOT_SCHEMA,
            **cv.ENTITY_SERVICE_FIELDS,
        }
    ),
    cv.has_at_least_one_key(FIELD_ACTIVE_DAYS, FIELD_SLOT_1, FIELD_SLOT_2),
)

_SCHEDULE_RESOLUTION_SCHEMA = vol.Schema(
    {
        vol.Required(FIELD_RESOLUTION): vol.All(vol.Coerce(str), vol.In(("0", "1"))),
        **cv.ENTITY_SERVICE_FIELDS,
    }
)

_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Required(FIELD_SCHEDULE): vol.All(
            dict,
            vol.Length(min=1),
            {
                vol.All(vol.Coerce(str), vol.In(DAY_KEYS)): [vol.Coerce(int)],
            },
        ),
        **cv.ENTITY_SERVICE_FIELDS,
    }
)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the domain services once for the loaded entries."""
    if not hass.services.has_service(DOMAIN, SERVICE_SET_BOOST_MODE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_BOOST_MODE,
            partial(_handle_set_boost_mode, hass),
            schema=_BOOST_MODE_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SET_BOOST_PARAMS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_BOOST_PARAMS,
            partial(_handle_set_boost_params, hass),
            schema=_BOOST_PARAMS_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_SCHEDULE,
            partial(_handle_set_schedule, hass),
            schema=_SCHEDULE_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE_RESOLUTION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_SCHEDULE_RESOLUTION,
            partial(_handle_set_schedule_resolution, hass),
            schema=_SCHEDULE_RESOLUTION_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SET_CHARGING_SCHEDULE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_CHARGING_SCHEDULE,
            partial(_handle_set_charging_schedule, hass),
            schema=_CHARGING_SCHEDULE_SCHEMA,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SET_THERMOSTAT_SCHEDULE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_THERMOSTAT_SCHEDULE,
            partial(_handle_set_thermostat_schedule, hass),
            schema=_THERMOSTAT_SCHEDULE_SCHEMA,
        )


@callback
def async_unregister_services(hass: HomeAssistant) -> None:
    """Remove the domain services once no loaded entry needs them."""
    for service in _SERVICE_NAMES:
        remove_service_if_last(hass, service)
