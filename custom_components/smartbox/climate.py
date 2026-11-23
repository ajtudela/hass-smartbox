"""Support for Smartbox climate entities.

Entities read state through the adapter's accessors over ``runtime.engine`` and
write through its named ops; updates arrive via the shared
``EngineChangeFanOut``, one registration per node. Setup frames refresh the
entity too, so the max_temp clamp tracks ``max_stemp_limit`` without a restart.
The mapping helpers live in ``mapping.py``; the REST discovery identity is kept
only for registry identity and device-info fields.
"""

import logging
from typing import TYPE_CHECKING, Any, cast

# HA's component root re-exports these public names without __all__;
# mypy strict's no_implicit_reexport cannot see that re-export.
from homeassistant.components.climate import (  # type: ignore[attr-defined]
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_HOME,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_LOCKED,
    ATTR_TEMPERATURE,
    MAJOR_VERSION,
    MINOR_VERSION,
    UnitOfTemperature,
)
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter
from smartbox.protocol.values import (
    BoostConfig,
    is_lost,
    normalize_prog,
    parse_number_or_none,
)
from smartbox.protocol.writes import VALID_BOOST_MODES

from .adapter import (
    EngineChangeFanOut,
    SmartboxNodeAdapterEntity,
    node_max_stemp_limit,
    node_runback_limits,
)
from .boost_activation import ActivationPlan, plan_boost, plan_runback
from .const import (
    PRESET_ADVANCED_SLOT,
    PRESET_FROST,
    PRESET_RUNBACK,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
    SmartboxNodeType,
)
from .controller import (
    ControllerLimits,
    boost_uses_legacy_path,
    climate_bounds,
    controller_limits,
    has_no_probes,
    supports_easy,
    supports_runback,
)
from .entity_builder import EntityBuilder
from .exceptions import user_error, validation_error
from .identity import boost_available, boost_config
from .mapping import (
    AutoOverrideWrite,
    HtrModPolicy,
    HtrModWrite,
    acm_active,
    check_status_key,
    get_boost_end_datetime,
    get_hvac_mode,
    get_next_prog_change,
    get_target_temperature,
    get_temperature_unit,
    htr_mod_policy,
    htr_mod_setpoint_limits,
    hvac_mode_write,
    preset_mode_write,
    temperature_write,
    warn_unknown_once,
)
from .storage import (
    available_modes as storage_available_modes,
    classic_accumulator,
    current_mode as storage_current_mode,
    write_for as storage_write_for,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from smartbox.core.engine import SmartboxEngine

    from . import SmartboxConfigEntry, SmartboxRuntime
    from .entity_builder import EntityGroup
    from .identity import SmartboxNodeInfo

# The native temperature API arrived in 2026.11. Keep the old property names
# for older supported HA versions, without overriding them on 2026.11+ where
# doing so triggers the deprecation report.
_HA_HAS_NATIVE_TEMPERATURE = (MAJOR_VERSION, MINOR_VERSION) >= (2026, 11)

_LOGGER = logging.getLogger(__name__)

# Updates arrive by push, and actions ride the single ws_user connection,
# whose frames are already ordered; the engine serializes writes per node.
# A client-side queue would only add latency to scenes and scripts.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - HA platform signature
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox climate platform")

    runtime = entry.runtime_data
    fanout = runtime.fanout

    def build_node(node: SmartboxNodeInfo) -> list[EntityGroup]:
        """Build the entities for one heater or thermostat node."""
        return _build_node_climate(node, entry, runtime, fanout)

    runtime.entity_builder.register(async_add_entities, build_node=build_node)
    for node in runtime.identity.nodes:
        EntityBuilder.add_groups(build_node(node), async_add_entities)

    _LOGGER.debug("Finished setting up Smartbox climate platform")


def _build_node_climate(
    node: SmartboxNodeInfo,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
    fanout: EngineChangeFanOut,
) -> list[EntityGroup]:
    """Build the climate entity for a heater or thermostat node."""
    if not node.supported_wire_type:
        return []
    if node.thermostat_surface:
        entity: SmartboxHeater = SmartboxThermostat(node, entry, runtime, fanout)
    elif node.heater_surface:
        entity = SmartboxHeater(node, entry, runtime, fanout)
    else:
        return []
    runtime.services.climate.append(entity)
    return [([entity], False)]


class SmartboxHeater(SmartboxNodeAdapterEntity, ClimateEntity):
    """Smartbox heater climate control."""

    _attr_name = None
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Hide the temperature surface on a classic accumulator or NO_PROBES."""
        features = self._attr_supported_features
        state = self._node_state()
        setup = state.setup if state is not None else self._node.setup
        if self._node.node_type == SmartboxNodeType.ACM and classic_accumulator(
            self._live_pid, setup
        ):
            features &= ~ClimateEntityFeature.TARGET_TEMPERATURE
        if (
            self._node.node_type == SmartboxNodeType.THM
            and self._node.heater_surface
            and has_no_probes(setup)
        ):
            features &= ~ClimateEntityFeature.TARGET_TEMPERATURE
        return ClimateEntityFeature(features)

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(node, entry, runtime, fanout, "thermostat")

    def _controller_limits(self) -> ControllerLimits:
        """Return the decoded controller limits for this node."""
        state = self._node_state()
        setup = state.setup if state is not None else self._node.setup
        return controller_limits(
            self._node.node_type,
            self._live_pid,
            self._live_fw_version,
            setup,
        )

    @property
    def _lost(self) -> bool:
        """The status slot is a bare lost marker.

        The engine never stores one (a lost-marked payload is availability
        evidence only), so this is the entity's own protection: a marker in the
        status carries none of the required keys, and property evaluation must
        render None rather than raise.
        """
        return is_lost(self._status)

    @property
    def _status_blank(self) -> bool:
        """True while no status body has arrived yet.

        Availability does not imply a body: a setup or prog write can confirm
        its own write, marking the node available, before any status frame is
        seen. Reads needing a status key must then render None.
        """
        return not self._status

    @property
    def _away(self) -> bool:
        """Device away flag; PRESET_HOME until the wire delivered away."""
        device = self._device_state()
        return bool((device.away if device is not None else {}).get("away", False))

    @property
    def _boost(self) -> bool:
        """Boost flag from the node status (True while boost is running)."""
        return bool(self._status.get("boost", False))

    @property
    def _boost_available(self) -> bool:
        """Is boost available on this node (see ``identity.boost_available``)."""
        state = self._node_state()
        if state is None:
            return False
        return boost_available(
            state.setup,
            node_type=self._node.node_type,
            pid=self._live_pid,
            fw_version=self._live_fw_version,
            brand_name=(
                self._node.reseller.name if self._node.reseller is not None else None
            ),
        )

    @property
    def _boost_config_value(self) -> int:
        """The node's vendor-app boost configuration (0 when absent)."""
        state = self._node_state()
        return boost_config(state.setup if state is not None else {})

    @property
    def supports_boost_mode(self) -> bool:
        """Whether ``smartbox.set_boost_mode`` applies to this entity.

        Only the full-boost controller supports the ``/boost_mode`` endpoint;
        a thermostat never does, even when its setup carries a boost config
        from an htr-derived controller.
        """
        state = self._node_state()
        setup = state.setup if state is not None else self._node.setup
        return (
            self._node.node_type not in (SmartboxNodeType.THM, SmartboxNodeType.ACM)
            and self._boost_available
            and not boost_uses_legacy_path(self._live_pid, setup)
            and self._boost_config_value == BoostConfig.FULL
        )

    @property
    def _boost_mode_value(self) -> str | None:
        """The active wire boost mode (``boost_mode``), when the status says."""
        value = self._status.get("boost_mode")
        return value if isinstance(value, str) and value else None

    @property
    def _boost_modes_available(self) -> list[str]:
        """The modes status advertises (``boost_modes_available``)."""
        value = self._status.get("boost_modes_available")
        if not isinstance(value, (list, tuple, set)):
            return []
        return [mode for mode in value if isinstance(mode, str)]

    def _active_boost_end(self) -> datetime | None:
        """Return when the running boost/runback/advanced-slot ends."""
        if not self._boost:
            return None
        if "boost_end_min" in self._status:
            end = get_boost_end_datetime(
                self._status["boost_end_min"], self._status.get("boost_end_day")
            )
            if end is not None:
                return end
        if self._boost_mode_value != "advanced_slot":
            return None
        state = self._node_state()
        prog = normalize_prog(state.prog) if state is not None else None
        if not prog:
            return None
        return get_next_prog_change(prog, dt_util.now())

    async def async_turn_off(self) -> None:
        """Turn off hvac."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        """Turn on using AUTO when offered, otherwise the app's HEAT/ON mode."""
        mode = HVACMode.AUTO if HVACMode.AUTO in self.hvac_modes else HVACMode.HEAT
        await self.async_set_hvac_mode(mode)

    @property
    def native_temperature_unit(self) -> str:
        """Return the unit of measurement."""
        unit = get_temperature_unit(self._status)
        if unit is not None:
            return unit
        return UnitOfTemperature.CELSIUS

    @property
    def native_current_temperature(self) -> float | None:
        """Return the current temperature."""
        if self._lost or self._status_blank:
            return None
        value = self._status.get("mtemp")
        return parse_number_or_none(value)

    @property
    def native_target_temperature(self) -> float | None:
        """Return the target temperature."""
        if self._lost or self._status_blank:
            return None
        state = self._node_state()
        prog = normalize_prog(state.prog) if state is not None else None
        target = get_target_temperature(self._node.node_type, self._status, prog)
        # In AUTO the target comes from the programme and may be ICE (7 C),
        # below the manual comfort/eco union; do not clamp it to that profile.
        if (
            self._node.node_type == SmartboxNodeType.HTR_MOD
            and self._status.get("mode") == "auto"
        ):
            return target
        limits = self._htr_mod_status_limits()
        if target is None or limits is None:
            return target
        # The advertised range is the comfort/eco union; clamp a stale device
        # value into it so HA's slider and the state agree.
        return min(max(target, limits[0]), limits[1])

    if not _HA_HAS_NATIVE_TEMPERATURE:

        @property
        def temperature_unit(self) -> str:
            """Return the unit of measurement (pre-2026.11 API)."""
            return self.native_temperature_unit

        @property
        def current_temperature(self) -> float | None:
            """Return the current temperature (pre-2026.11 API)."""
            return self.native_current_temperature

        @property
        def target_temperature(self) -> float | None:
            """Return the target temperature (pre-2026.11 API)."""
            return self.native_target_temperature

    def _model_code(self) -> str | None:
        """Return the node's product code (live version slot, then discovery)."""
        state = self._node_state()
        return self._node.get_model_code(state.version if state is not None else None)

    def _htr_mod_policy(self) -> HtrModPolicy | None:
        """Return the node's htr_mod setpoint policy, when it has one."""
        if self._node.node_type != SmartboxNodeType.HTR_MOD:
            return None
        return htr_mod_policy(self._model_code())

    def _htr_mod_status_limits(self) -> tuple[float, float] | None:
        """Return the editable htr_mod setpoint range in the node's unit."""
        policy = self._htr_mod_policy()
        if policy is None or self._lost or self._status_blank:
            return None
        return htr_mod_setpoint_limits(self._status, policy)

    @property
    def target_temperature_step(self) -> float:
        """Setpoint grid step, in the entity's unit.

        Matches the official app on Celsius nodes: 0.5 for the htr family and
        the htr_mod policy's 1 C grid. Fahrenheit nodes get 1 F, the grid the
        wire stores (values.format_setpoint); the app's htr_mod Fahrenheit
        step uses the absolute-value conversion and is not a usable reference.
        """
        if self.native_temperature_unit != UnitOfTemperature.CELSIUS:
            return 1.0
        policy = self._htr_mod_policy()
        return policy.step_c if policy is not None else 0.5

    @property
    def min_temp(self) -> float:
        """HA's default minimum in the device's scale.

        A plain property on purpose: ``ClimateEntity.min_temp`` is a
        cached_property, and this entity is added before the store has any
        state, so the inherited cache would freeze the Celsius default for
        Fahrenheit devices. htr_mod follows the app's per-profile range.
        """
        htr_mod_limits = self._htr_mod_status_limits()
        if htr_mod_limits is not None:
            return htr_mod_limits[0]
        if self.preset_mode == PRESET_RUNBACK:
            min_runback, _max_runback = node_runback_limits(self._node_state())
            if min_runback is not None:
                return min_runback
        min_c, _max_c = climate_bounds(self._controller_limits(), None)
        return TemperatureConverter.convert(
            min_c,
            UnitOfTemperature.CELSIUS,
            self.native_temperature_unit,
        )

    @property
    def max_temp(self) -> float:
        """Maximum settable temperature, clamped by the device's limit.

        The fw-1.9 family reports ``max_stemp_limit`` in setup, in the device's
        scale; "0.0" means no user limit, so HA's default maximum applies.
        Refreshed on setup frames via the fan-out. Plain property: see min_temp.
        """
        htr_mod_limits = self._htr_mod_status_limits()
        if htr_mod_limits is not None:
            return htr_mod_limits[1]
        state = self._node_state()
        limit = node_max_stemp_limit(state)
        _min_c, max_c = climate_bounds(self._controller_limits(), None)
        max_temp = TemperatureConverter.convert(
            max_c,
            UnitOfTemperature.CELSIUS,
            self.native_temperature_unit,
        )
        if limit is not None:
            max_temp = min(max_temp, limit)
        if self.preset_mode == PRESET_RUNBACK:
            _min_runback, max_runback = node_runback_limits(state)
            if max_runback is not None:
                max_temp = min(max_temp, max_runback)
        return max_temp

    async def async_set_temperature(self, **kwargs: Any) -> None:  # noqa: ANN401
        """Set new target temperature."""
        if not self.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE:
            msg = "temperature_control_unsupported"
            raise user_error(msg)
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        engine = self._runtime.engine
        if self._boost and self.supports_boost_mode:
            mode = self._boost_mode_value or "temp_and_time"
            if mode in ("temp_and_time", "runback"):
                # The app's active-mode editor re-posts /boost_mode with the
                # new temperature; the defaults stay untouched.
                await engine.set_boost_mode(
                    self._dev_id,
                    self._ref,
                    active=True,
                    mode=mode,
                    temperature=temp,
                )
                return
            if mode == "advanced_slot":
                msg = "advanced_slot_temperature_fixed"
                raise user_error(msg)
        state = self._node_state()
        write = temperature_write(
            self._node.node_type,
            self._status,
            temp,
            state.setup if state is not None else {},
            model_code=self._model_code(),
        )
        if isinstance(write, HtrModWrite):
            await engine.set_htr_mod_status(
                self._dev_id,
                self._ref,
                on=write.on,
                mode=write.mode,
                selected_temp=write.selected_temp,
                comfort_temp=write.comfort_temp,
                eco_offset=write.eco_offset,
                units=write.units,
            )
        elif isinstance(write, AutoOverrideWrite):
            # App-shaped override for setpoints inside the running programme
            # (temperature_write's gated condition).
            await engine.engage_modified_auto(
                self._dev_id, self._ref, temp, write.units
            )
        else:
            await engine.set_setpoint(self._dev_id, self._ref, temp, write.units)

    @property
    def hvac_action(self) -> HVACAction | None:  # noqa: PLR0911 - one return per action
        """Return current operation ie. heat or idle."""
        node_type = self._node.node_type
        status = self._status
        if self._lost or self._status_blank:
            return None
        if node_type == SmartboxNodeType.ACM:
            if acm_active(status, self._model_code()):
                return HVACAction.HEATING
        elif (
            node_type == SmartboxNodeType.HTR_MOD
            and status.get("mode") == "manual"
            and status.get("selected_temp") == "off"
            and not self._boost
        ):
            return HVACAction.OFF
        elif status.get("active"):
            return HVACAction.HEATING
        mode = status.get("mode")
        if mode is None and not self._boost:
            return None
        if (
            mode == "off"
            or (node_type == SmartboxNodeType.HTR_MOD and not status.get("on", True))
        ) and not self._boost:
            return HVACAction.OFF
        return HVACAction.IDLE

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return hvac target hvac state."""
        if self._lost or self._status_blank:
            return None
        if "mode" not in self._status and not self._boost:
            return None
        if self._node.node_type == SmartboxNodeType.ACM:
            state = self._node_state()
            setup = state.setup if state is not None else self._node.setup
            return storage_current_mode(
                self._node.node_type, self._live_pid, setup, self._status
            )
        return get_hvac_mode(self._node.node_type, self._status)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available operation modes."""
        if self._node.node_type == SmartboxNodeType.ACM:
            state = self._node_state()
            setup = state.setup if state is not None else self._node.setup
            return storage_available_modes(self._node.node_type, self._live_pid, setup)
        return [HVACMode.HEAT, HVACMode.AUTO, HVACMode.OFF]

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set operation mode."""
        _LOGGER.debug("Setting HVAC mode to %s", hvac_mode)
        engine = self._runtime.engine
        if self._node.node_type == SmartboxNodeType.ACM:
            state = self._node_state()
            setup = state.setup if state is not None else self._node.setup
            storage_write = storage_write_for(
                self._node.node_type, self._live_pid, setup, hvac_mode
            )
            if storage_write is None:
                msg = "hvac_mode_unsupported"
                raise user_error(msg)
            if self._boost:
                await self._async_deactivate_boost(engine)
            if (
                storage_write.kind == "charge"
                and storage_write.target_charge is not None
            ):
                await engine.set_target_charge(
                    self._dev_id, self._ref, storage_write.target_charge
                )
            elif storage_write.mode is not None:
                await engine.set_mode(self._dev_id, self._ref, storage_write.mode)
            else:
                msg = "hvac_mode_unsupported"
                raise user_error(msg)
            return
        write = hvac_mode_write(self._node.node_type, self._status, hvac_mode)
        if isinstance(write, HtrModWrite):
            await engine.set_htr_mod_status(
                self._dev_id,
                self._ref,
                on=write.on,
                mode=write.mode,
                selected_temp=write.selected_temp,
            )
        else:
            await engine.set_mode(self._dev_id, self._ref, write.mode)
        # Leaving boost is its own accepted write: the engine's named ops are
        # single-key.
        if self._boost:
            await self._async_deactivate_boost(engine)

    async def _async_apply_activation(
        self, engine: SmartboxEngine, plan: ActivationPlan
    ) -> None:
        """Send one resolved activation on the path the controller uses."""
        if plan.mode is None:
            await engine.set_boost(
                self._dev_id,
                self._ref,
                on=True,
                temperature=plan.temperature,
                time_minutes=plan.minutes,
                legacy=plan.legacy,
            )
            return
        await engine.set_boost_mode(
            self._dev_id,
            self._ref,
            active=True,
            mode=plan.mode,
            temperature=plan.temperature,
            time_minutes=plan.minutes,
        )

    async def _async_deactivate_boost(self, engine: SmartboxEngine) -> None:
        """Deactivate boost on the path the controller family uses."""
        state = self._node_state()
        setup = state.setup if state is not None else self._node.setup
        await engine.set_boost(
            self._dev_id,
            self._ref,
            on=False,
            legacy=boost_uses_legacy_path(self._live_pid, setup),
        )

    def _preset_mode_for_manual(self) -> str | None:
        check_status_key("selected_temp", self._node.node_type, self._status)
        selected_temp = self._status["selected_temp"]
        if selected_temp == "comfort":
            return PRESET_COMFORT
        if selected_temp == "eco":
            return PRESET_ECO
        if selected_temp == "ice":
            return PRESET_FROST
        if selected_temp == "off":
            return None
        warn_unknown_once("selected_temp", selected_temp)
        return None

    def _active_boost_preset(self) -> str:
        """Map the running boost/runback/advanced-slot mode to its preset."""
        mode = self._boost_mode_value
        if mode == "runback" and PRESET_RUNBACK in self.preset_modes:
            return PRESET_RUNBACK
        if mode == "advanced_slot" and PRESET_ADVANCED_SLOT in self.preset_modes:
            return PRESET_ADVANCED_SLOT
        return PRESET_BOOST

    @property
    def preset_mode(self) -> str | None:
        """Get preset mode."""
        if self._lost:
            return None
        if self._away:
            return PRESET_AWAY
        if self._boost:
            return self._active_boost_preset()
        if self._node.node_type == SmartboxNodeType.HTR_MOD:
            return self._preset_mode_for_htr_mod()
        return PRESET_HOME

    def _preset_mode_for_htr_mod(self) -> str | None:
        """Map an htr_mod status mode to its preset (None when unmodelled)."""
        # This node type's preset IS the status mode, so a node whose
        # status has not arrived yet has no preset to report.
        if self._status_blank:
            return None
        check_status_key("mode", self._node.node_type, self._status)
        mode = self._status["mode"]
        if mode in ("easy", "setback"):
            # Vendor-app modes without a HA preset; the HVAC mode still
            # reports a usable state and the entity stays available.
            return None
        mapped = (
            {
                "auto": PRESET_SCHEDULE,
                "presence": PRESET_ACTIVITY,
                "self_learn": PRESET_SELF_LEARN,
            }.get(mode)
            if isinstance(mode, str)
            else None
        )
        if mapped is not None:
            return mapped
        if mode == "manual":
            return self._preset_mode_for_manual()
        warn_unknown_once("node mode", mode)
        return None

    @property
    def preset_modes(self) -> list[str]:
        """Get the preset_modes."""
        default_preset_modes = [PRESET_AWAY]
        if self._boost or self._boost_available:
            default_preset_modes.append(PRESET_BOOST)
        if self.supports_boost_mode:
            active_mode = self._boost_mode_value
            available = self._boost_modes_available
            if "runback" in available or active_mode == "runback":
                default_preset_modes.append(PRESET_RUNBACK)
            if "advanced_slot" in available or active_mode == "advanced_slot":
                default_preset_modes.append(PRESET_ADVANCED_SLOT)
        if self._node.node_type == SmartboxNodeType.HTR_MOD:
            default_preset_modes.extend(
                [
                    PRESET_ACTIVITY,
                    PRESET_COMFORT,
                    PRESET_ECO,
                    PRESET_FROST,
                    PRESET_SELF_LEARN,
                    PRESET_SCHEDULE,
                ]
            )
        else:
            default_preset_modes.append(PRESET_HOME)
        return default_preset_modes

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return read-only active-mode facts alongside the entity state.

        These are display/template data, not controls: the climate target is
        the editable active temperature, and the config numbers hold the
        defaults. On ``htr_mod`` nodes, ``selected_temp`` is exposed when the
        status carries it. It is the live profile label (comfort/eco/ice/off),
        not a temperature; unmodelled labels are passed through for
        diagnostics. A lost body carries neither key: both are OMITTED (no
        stale last-known value is kept - the entity is unavailable while lost,
        and the next ok frame restores them).
        """
        if self._lost or self._status_blank:
            return {}
        attributes: dict[str, Any] = {}
        if "locked" in self._status:
            # htr reports a bool, acm reports 0/1; expose one type
            # (hass-smartbox docs/api-contract.md §7 item 18).
            attributes[ATTR_LOCKED] = bool(self._status["locked"])
        if self._node.node_type == SmartboxNodeType.HTR_MOD:
            value = self._status.get("selected_temp")
            if isinstance(value, str) and value:
                attributes["selected_temp"] = value
        if self._boost:
            mode = self._boost_mode_value
            if mode is not None:
                attributes["boost_mode"] = mode
            end = self._active_boost_end()
            if end is not None:
                attributes["boost_end_time"] = end.isoformat()
                attributes["boost_remaining_minutes"] = max(
                    0, int((end - dt_util.now()).total_seconds() // 60)
                )
        return attributes

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the mode."""
        engine = self._runtime.engine
        if preset_mode == PRESET_AWAY:
            await engine.set_away(self._dev_id, away=True)
            return
        if self._away:
            await engine.set_away(self._dev_id, away=False)
        if preset_mode == PRESET_HOME:
            await self._async_clear_special_modes(engine)
            return
        if preset_mode == PRESET_RUNBACK:
            await self._async_activate_runback(engine)
            return
        if preset_mode == PRESET_ADVANCED_SLOT:
            await self._async_activate_advanced_slot(engine)
            return
        if preset_mode == PRESET_BOOST:
            if not self._boost_available:
                msg = "boost_unsupported"
                raise user_error(msg)
            plan = plan_boost(
                self._node,
                self._node_state(),
                getattr(self._runtime, "boost_prefs", None),
            )
            await self._async_apply_activation(engine, plan)
            return
        if self._node.node_type == SmartboxNodeType.HTR_MOD:
            write = preset_mode_write(self._node.node_type, self._status, preset_mode)
            if isinstance(write, HtrModWrite):
                await engine.set_htr_mod_status(
                    self._dev_id,
                    self._ref,
                    on=write.on,
                    mode=write.mode,
                    selected_temp=write.selected_temp,
                )
            else:
                await engine.set_mode(self._dev_id, self._ref, write.mode)

    async def _async_clear_special_modes(self, engine: SmartboxEngine) -> None:
        """Clear boost and Tevolve EASY/RUNBACK, like the app's mode switch."""
        if self._boost:
            await self._async_deactivate_boost(engine)
        status = self._status
        state = self._node_state()
        setup = state.setup if state is not None else self._node.setup
        if supports_easy(self._live_pid, setup) and status.get("easy"):
            await engine.set_easy(self._dev_id, self._ref, enabled=False)
        if supports_runback(self._live_pid, setup) and status.get("runback"):
            await engine.set_runback(self._dev_id, self._ref, enabled=False)

    async def _async_activate_runback(self, engine: SmartboxEngine) -> None:
        """Activate the vendor app's runback mode with its configured values."""
        plan = plan_runback(
            self._node,
            self._node_state(),
            getattr(self._runtime, "boost_prefs", None),
        )
        await engine.set_boost_mode(
            self._dev_id,
            self._ref,
            active=True,
            mode="runback",
            temperature=plan.temperature,
            time_minutes=plan.minutes,
        )

    async def _async_activate_advanced_slot(self, engine: SmartboxEngine) -> None:
        """Activate the boost-until-next-schedule mode."""
        if not self.supports_boost_mode:
            msg = "advanced_slot_requires_full_boost"
            raise user_error(msg)
        await engine.set_boost_mode(
            self._dev_id,
            self._ref,
            active=True,
            mode="advanced_slot",
        )

    async def async_set_boost_mode(
        self,
        *,
        active: bool,
        mode: str | None,
        temperature: float | None,
        time_minutes: int | None,
    ) -> None:
        """Service entry point for the full-boost endpoint."""
        if not self.supports_boost_mode:
            msg = "boost_mode_unsupported"
            raise user_error(msg)
        if active and mode not in VALID_BOOST_MODES:
            msg = "boost_mode_invalid"
            raise validation_error(msg, modes=", ".join(VALID_BOOST_MODES))
        await self._runtime.engine.set_boost_mode(
            self._dev_id,
            self._ref,
            active=active,
            mode=mode,
            temperature=temperature,
            time_minutes=time_minutes,
        )


class SmartboxThermostat(SmartboxHeater):
    """Thermostat (`thm`) climate entity.

    A thermostat is not a heater: it has a heat/cold load type, no boost and
    no charge. The shape comes from the vendor 2.54.2 ``ThermostatController``
    (docs/api-contract.md §3.5): status ``mode``/``stemp``/``mtemp``/
    ``active``/``units``, plus the generic ``low_batt`` shell flag; setup
    ``type`` (``heat``/``cold``), ``control_mode``, ``anti_grippage`` and
    ``away_offset``. The app's 7 C floor (its temperature service is built
    with ``minTempCelsius: 7``) and the controller's 35 C maximum set the
    range. The app offers AUTO/OFF (its ON resolves to AUTO); MANUAL is not
    in its mode list, so HEAT/COOL here are an unverified manual override.
    Setpoint writes are the plain ``{stemp, units}`` body: the controller
    deletes ``mode: modified_auto`` before POSTing.
    """

    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )

    @property
    def _setup(self) -> Mapping[str, Any]:
        """Return the live setup, with the discovery copy as the fallback."""
        state = self._node_state()
        if state is not None and state.setup:
            return state.setup
        return self._node.setup

    @property
    def _load_type(self) -> str:
        """Return the thermostat's load type (``heat``/``cold``)."""
        value = self._setup.get("type")
        return "cold" if isinstance(value, str) and value.lower() == "cold" else "heat"

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the operation modes this load type supports."""
        active = HVACMode.COOL if self._load_type == "cold" else HVACMode.HEAT
        return [active, HVACMode.AUTO, HVACMode.OFF]

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current mode, with a cold load reported as COOL."""
        mode = super().hvac_mode
        if mode == HVACMode.HEAT and self._load_type == "cold":
            return HVACMode.COOL
        return mode

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set operation mode; COOL is the cold-load form of the wire's manual."""
        target = HVACMode.HEAT if hvac_mode == HVACMode.COOL else hvac_mode
        await super().async_set_hvac_mode(target)

    async def async_turn_on(self) -> None:
        """Turn on using AUTO when offered, otherwise the app's HEAT/ON mode."""
        mode = HVACMode.AUTO if HVACMode.AUTO in self.hvac_modes else HVACMode.HEAT
        await self.async_set_hvac_mode(mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:  # noqa: ANN401
        """Set a target temperature with the thermostat's plain setpoint body.

        ``ThermostatController.setStatus`` deletes ``mode: modified_auto``
        before POSTing, so the wire body is ``{stemp, units}``. The inherited
        heater path would engage a modified_auto override when
        ``setup.modified_auto_span`` is present.
        """
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        # setup=None keeps the helper off the modified_auto branch.
        write = temperature_write(self._node.node_type, self._status, temp)
        await self._runtime.engine.set_setpoint(
            self._dev_id, self._ref, temp, cast("str", write.units)
        )

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current action (heating/cooling/idle/off)."""
        if self._lost or self._status_blank:
            return None
        if self._status.get("mode") == "off":
            return HVACAction.OFF
        if (
            self._node.node_type == SmartboxNodeType.HTR_MOD
            and self._status.get("mode") == "manual"
            and self._status.get("selected_temp") == "off"
        ):
            return HVACAction.OFF
        if self._status.get("active") is True:
            if self._load_type == "cold":
                return HVACAction.COOLING
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def preset_modes(self) -> list[str]:
        """Thermostats have no heater presets."""
        return []

    @property
    def preset_mode(self) -> str | None:
        """Thermostats have no heater presets."""
        return None

    async def async_set_preset_mode(self, _preset_mode: str) -> None:
        """Reject preset writes the entity does not advertise."""
        msg = "preset_mode_unsupported"
        raise user_error(msg)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the thermostat facts that have no entity of their own."""
        if self._lost or self._status_blank:
            return {}
        attributes: dict[str, Any] = {"load_type": self._load_type}
        setup = self._setup
        if "control_mode" in setup:
            attributes["control_mode"] = setup["control_mode"]
        return attributes
