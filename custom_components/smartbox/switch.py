"""Support for Smartbox switch entities.

Entities read state through the adapter's typed accessors over
``runtime.engine`` and write through its named ops; updates arrive via the
shared ``EngineChangeFanOut``, with one registration per node - or per device
for the box-level switches. The REST discovery identities are kept only for
registry identity, device-info fields and the entity creation gates.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util
from smartbox.core.state import Change, DeviceChange, NodeChange
from smartbox.protocol.values import celsius_to_node_units, node_units_to_celsius

from .adapter import (
    EngineChangeFanOut,
    SmartboxBoxAdapterEntity,
    SmartboxNodeAdapterEntity,
    node_boost_params,
    node_max_stemp_limit,
    node_power_limit,
    node_setup_bool,
    node_units,
)
from .boost_activation import plan_boost
from .controller import (
    ControllerLimits,
    boost_uses_legacy_path,
    lock_kind,
    max_limit_bounds,
    supports_easy,
    supports_runback,
)
from .entity_builder import EntityBuilder
from .exceptions import user_error
from .identity import (
    SmartboxDeviceInfo,
    SmartboxNodeInfo,
    max_temp_limit_available,
    node_power_limit_available,
    true_radiant_available,
    window_mode_available,
)
from .mapping import get_boost_end_datetime

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import SmartboxConfigEntry, SmartboxRuntime
    from .entity_builder import EntityGroup

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
    _LOGGER.debug("Setting up Smartbox switch platform")

    runtime = entry.runtime_data
    fanout = runtime.fanout

    def build_device(device: SmartboxDeviceInfo) -> list[EntityGroup]:
        """Build the box-level switches the device actually supports."""
        return _build_device_switches(device, entry, runtime, fanout)

    def build_node(node: SmartboxNodeInfo) -> list[EntityGroup]:
        """Build the switches this node's setup exposes."""
        return _build_node_switches(node, entry, runtime, fanout)

    runtime.entity_builder.register(
        async_add_entities, build_node=build_node, build_device=build_device
    )
    for device in runtime.identity.devices:
        EntityBuilder.add_groups(build_device(device), async_add_entities)
    for node in runtime.identity.nodes:
        EntityBuilder.add_groups(build_node(node), async_add_entities)

    _LOGGER.debug("Finished setting up Smartbox switch platform")


def _build_device_switches(
    device: SmartboxDeviceInfo,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
    fanout: EngineChangeFanOut,
) -> list[EntityGroup]:
    """Build the box-level switches the device actually supports."""
    _LOGGER.debug("Creating away switch for box %r", device.dev_id)
    switches: list[Entity] = [AwaySwitch(device, entry, runtime, fanout)]
    if device.box_power_limit_available:
        _LOGGER.debug("Creating no power limit switch for box %r", device.dev_id)
        switches.append(NoPowerLimitSwitch(device, entry, runtime, fanout))
    return [(switches, False)]


def _build_node_switches(  # noqa: C901, PLR0912 - one branch per capability
    node: SmartboxNodeInfo,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
    fanout: EngineChangeFanOut,
) -> list[EntityGroup]:
    """Build the switches this node's setup exposes.

    Module-level so the per-capability branches do not contribute to the
    platform setup's complexity.
    """
    if not node.supported_wire_type:
        return []
    switch_entities: list[Entity] = []
    if node.thermostat_surface and "anti_grippage" in node.setup:
        switch_entities.append(
            AntiGrippageSwitch(node, entry, runtime, fanout, "anti_grippage")
        )
    if window_mode_available(node):
        _LOGGER.debug("Creating window_mode switch for node %r", node.name)
        switch_entities.append(
            WindowModeSwitch(node, entry, runtime, fanout, "window_mode")
        )
    else:
        _LOGGER.info("Window mode not available for node %r", node.name)
    if true_radiant_available(node):
        _LOGGER.debug("Creating true_radiant switch for node %r", node.name)
        switch_entities.append(
            TrueRadiantSwitch(node, entry, runtime, fanout, "true_radiant")
        )
    else:
        _LOGGER.info("True radiant not available for node %r", node.name)

    # A thm node can dispatch to an htr-derived controller whose factory
    # payload carries boost_config (Tevolve floor thermostats), but the
    # thermostat climate entity does not model boost.
    if node.heater_surface and node.boost_available:
        _LOGGER.debug("Creating boost switch for node %r", node.name)
        switch_entities.append(BoostSwitch(node, entry, runtime, fanout, "boost"))
    else:
        _LOGGER.info("Boost mode not available for node %r", node.name)
    if node.heater_surface and supports_easy(node.pid, node.setup):
        _LOGGER.debug("Creating EASY switch for node %r", node.name)
        switch_entities.append(
            EasyModeSwitch(node, entry, runtime, fanout, "easy_mode")
        )
    if node.heater_surface and supports_runback(node.pid, node.setup):
        _LOGGER.debug("Creating RUNBACK switch for node %r", node.name)
        switch_entities.append(
            RunbackModeSwitch(node, entry, runtime, fanout, "runback_mode")
        )

    if (
        node.heater_surface
        and lock_kind(
            node.node_type,
            node.pid,
            node.version.get("fw_version"),
            node.setup,
        )
        == "bool"
    ):
        _LOGGER.debug("Creating lock switch for node %r", node.name)
        switch_entities.append(ChildLockSwitch(node, entry, runtime, fanout, "lock"))

    if max_temp_limit_available(node):
        _LOGGER.debug("Creating max temperature limit switch for node %r", node.name)
        switch_entities.append(
            MaxTemperatureLimitSwitch(
                node, entry, runtime, fanout, "max_temperature_limit"
            )
        )

    if node_power_limit_available(node):
        _LOGGER.debug("Creating no node power limit switch for node %r", node.name)
        switch_entities.append(NoNodePowerLimitSwitch(node, entry, runtime, fanout))

    return [(switch_entities, False)] if switch_entities else []


class SmartboxNodeSwitchEntity(SmartboxNodeAdapterEntity, SwitchEntity):
    """Base for node-level switch entities (identity on the node adapter base)."""

    _attr_entity_category = EntityCategory.CONFIG


class AwaySwitch(SmartboxBoxAdapterEntity, SwitchEntity):
    """Smartbox device away switch (box device level)."""

    def __init__(
        self,
        device: SmartboxDeviceInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the away switch."""
        super().__init__(device, entry, runtime, fanout, "away_status")

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn on the switch."""
        await self._runtime.engine.set_away(self._dev_id, away=True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn off the switch."""
        await self._runtime.engine.set_away(self._dev_id, away=False)

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        device = self._device_state()
        return bool((device.away if device is not None else {}).get("away", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full away status as attributes."""
        device = self._device_state()
        return dict(device.away) if device is not None else {}


class NoPowerLimitSwitch(SmartboxBoxAdapterEntity, SwitchEntity):
    """Smartbox device "no power limit" switch (box device level).

    On the wire the power limit is one integer where 0 means "no limit" (the web
    app renders it as this toggle plus a field for the maximum power shared by
    all radiators). Turning the switch off restores the last known non-zero
    limit; when none is known - typical after a restart with no limit active -
    nothing is written and the call fails loudly, so the toggle cannot silently
    snap back. Set a value with the power limit number instead.

    The last-non-zero memory is session-scoped: seeded from the store on add and
    refreshed by power_limit device changes through the fan-out.
    """

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        device: SmartboxDeviceInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the no-power-limit switch."""
        super().__init__(device, entry, runtime, fanout, "no_power_limit")
        self._last_nonzero_power_limit: int | None = None

    async def async_added_to_hass(self) -> None:
        """Seed the last-non-zero memory from the store."""
        await super().async_added_to_hass()
        device = self._device_state()
        if device is not None and device.power_limit:
            self._last_nonzero_power_limit = device.power_limit

    def _async_change(self, change: Change) -> None:
        """Track non-zero power limits seen on the wire."""
        if (
            isinstance(change, DeviceChange)
            and change.kind == "power_limit"
            and change.device.power_limit
        ):
            self._last_nonzero_power_limit = change.device.power_limit
        super()._async_change(change)

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn on the switch (remove the limit)."""
        await self._runtime.engine.set_power_limit(self._dev_id, 0)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn off the switch (restore the last known limit)."""
        limit = self._last_nonzero_power_limit
        if limit is None:
            # A warning in the log was invisible to whoever flipped the
            # toggle: the switch just snapped back.
            msg = "box_power_limit_not_known"
            raise user_error(msg)
        await self._runtime.engine.set_power_limit(self._dev_id, limit)

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on; None while the limit is unknown."""
        device = self._device_state()
        if device is None or device.power_limit is None:
            return None
        return bool(device.power_limit == 0)


class NoNodePowerLimitSwitch(SmartboxNodeSwitchEntity):
    """Smartbox node "no power limit" switch (mirrors the box pair).

    On the wire the node's own limit is one integer where 0 means "no limit"
    (docs/api-ws-user.md §2.3); turning the switch off restores the last known
    non-zero limit, and fails loudly when none is known - the same UX as the
    box switch. The last-non-zero memory is session-scoped: seeded from the
    store on add and refreshed by setup changes through the fan-out.
    """

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the no-node-power-limit switch."""
        super().__init__(node, entry, runtime, fanout, "no_node_power_limit")
        self._last_nonzero_power_limit: int | None = None

    async def async_added_to_hass(self) -> None:
        """Seed the last-non-zero memory from the store."""
        await super().async_added_to_hass()
        limit = node_power_limit(self._node_state())
        if limit:
            self._last_nonzero_power_limit = limit

    def _async_change(self, change: Change) -> None:
        """Track non-zero limits seen on the wire."""
        if isinstance(change, NodeChange) and change.kind == "setup":
            limit = node_power_limit(change.node)
            if limit:
                self._last_nonzero_power_limit = limit
        super()._async_change(change)

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn on the switch (remove the limit)."""
        await self._runtime.engine.set_node_power_limit(self._dev_id, self._ref, 0)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn off the switch (restore the last known limit)."""
        limit = self._last_nonzero_power_limit
        if limit is None:
            msg = "node_power_limit_not_known"
            raise user_error(msg)
        await self._runtime.engine.set_node_power_limit(self._dev_id, self._ref, limit)

    @property
    def is_on(self) -> bool | None:
        """Return true if the switch is on; None while the limit is unknown."""
        limit = node_power_limit(self._node_state())
        return None if limit is None else limit == 0


class WindowModeSwitch(SmartboxNodeSwitchEntity):
    """Smartbox node window mode switch."""

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn on the switch."""
        await self._runtime.engine.set_window_mode(
            self._dev_id, self._ref, enabled=True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn off the switch."""
        await self._runtime.engine.set_window_mode(
            self._dev_id, self._ref, enabled=False
        )

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return bool(node_setup_bool(self._node_state(), "window_mode_enabled"))


class TrueRadiantSwitch(SmartboxNodeSwitchEntity):
    """Smartbox node true radiant switch."""

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn on the switch."""
        await self._runtime.engine.set_true_radiant(
            self._dev_id, self._ref, enabled=True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn off the switch."""
        await self._runtime.engine.set_true_radiant(
            self._dev_id, self._ref, enabled=False
        )

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return bool(node_setup_bool(self._node_state(), "true_radiant_enabled"))


class BoostSwitch(SmartboxNodeSwitchEntity):
    """Smartbox boost switch, driving the heater's own boost mode.

    Boost raises the temperature temporarily for a configurable time. The
    temperature and duration come from the node setup's ``extra_options``, and
    can be changed with the ``smartbox.set_boost_params`` service.
    """

    _attr_icon = "mdi:rocket-launch"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        state = self._node_state()
        status = self._status
        boost_temp, boost_minutes = node_boost_params(state)
        boost_end: datetime | None = None
        remaining = 0
        end_time: str | None = None
        if bool(status.get("boost", False)):
            boost_end = get_boost_end_datetime(
                status.get("boost_end_min", 0), status.get("boost_end_day")
            )
            if boost_end is not None:
                remaining = int((boost_end - dt_util.now()).total_seconds())
                if remaining:
                    end_time = boost_end.strftime("%H:%M")
        return {
            "boost_temperature": boost_temp,
            "boost_duration_minutes": boost_minutes,
            "boost_time_remaining": remaining,
            "boost_end_time": end_time,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn on boost mode."""
        _LOGGER.debug("Activating boost mode for %r", self._node.name)
        plan = plan_boost(
            self._node,
            self._node_state(),
            getattr(self._runtime, "boost_prefs", None),
        )
        if plan.mode is None:
            await self._runtime.engine.set_boost(
                self._dev_id,
                self._ref,
                on=True,
                temperature=plan.temperature,
                time_minutes=plan.minutes,
                legacy=plan.legacy,
            )
            return
        await self._runtime.engine.set_boost_mode(
            self._dev_id,
            self._ref,
            active=True,
            mode=plan.mode,
            temperature=plan.temperature,
            time_minutes=plan.minutes,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn off boost mode."""
        _LOGGER.debug("Deactivating boost mode for %r", self._node.name)
        state = self._node_state()
        setup = state.setup if state is not None else self._node.setup
        await self._runtime.engine.set_boost(
            self._dev_id,
            self._ref,
            on=False,
            legacy=boost_uses_legacy_path(self._live_pid, setup),
        )

    @property
    def is_on(self) -> bool:
        """Return if boost mode is active."""
        return bool(self._status.get("boost", False))


class ChildLockSwitch(SmartboxNodeSwitchEntity):
    """Smartbox node child lock switch.

    The heater's ``locked`` status is the keypad lockout: while engaged the
    buttons on the unit itself are ignored. On the switch, on == lock engaged,
    so the status value needs no inversion (the ``LOCK`` device class would mean
    on == unlocked, hence a switch).

    Writes go through the single-key ``set_lock`` op, because multi-key status
    POSTs are silently partially applied (docs/api-contract.md §5.1).
    """

    _attr_icon = "mdi:lock"

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn on the child lock."""
        await self._runtime.engine.set_lock(self._dev_id, self._ref, locked=True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Turn off the child lock."""
        await self._runtime.engine.set_lock(self._dev_id, self._ref, locked=False)

    @property
    def is_on(self) -> bool:
        """Return true if the child lock is engaged."""
        return bool(self._status.get("locked", False))


class MaxTemperatureLimitSwitch(SmartboxNodeSwitchEntity, RestoreEntity):
    """Smartbox node maximum temperature limit on/off switch.

    The wire has no separate enable key: the fw-1.9-family ``max_stemp_limit``
    setup field carries "0.0" when the limit is disabled
    (docs/api-contract.md §5 live fixtures), which is what the app's own toggle
    writes. Turning the switch back on restores the last non-zero limit seen.
    The limit's value itself is the "Maximum" number entity on the same node.

    Because toggle-off erases the value on the wire, the last known limit is
    persisted in this entity's restored state (the ``last_limit`` attribute) and
    re-seeded on HA start; otherwise a restart would reset a re-enable to
    MAX_TEMP_LIMIT_DEFAULT.
    """

    _attr_icon = "mdi:thermometer-chevron-up"

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
        attr_key: str,
    ) -> None:
        """Initialize the max temperature limit switch."""
        super().__init__(node, entry, runtime, fanout, attr_key)
        # The node's unit scale (the wire's max_stemp_limit is Celsius, the
        # adapter converts it); None until the store or a restored state
        # provides one, so the device default is computed on demand.
        self._last_max_stemp_limit: str | None = None

    def _controller_limits(self) -> ControllerLimits:
        """Return the decoded controller limits for this node."""
        return self._controller_profile().limits

    def _default_limit(self) -> float:
        """Return the device's default ceiling in the NODE's unit."""
        units = node_units(self._node_state()) or "C"
        return celsius_to_node_units(
            max_limit_bounds(self._controller_limits())[1], units
        )

    def _remember(self, limit: float) -> None:
        """Seed the last-known-limit memory.

        Values are kept in the node's unit; one that converts to zero or
        below, or above the device's cap (a stale-scale restore after a unit
        switch, say), is ignored so the default is used instead of writing a
        nonsense ceiling. Before the first snapshot the node's scale is
        unknown, so a positive value is kept as-is.
        """
        units = node_units(self._node_state())
        if units is None:
            if limit > 0:
                self._last_max_stemp_limit = str(limit)
            return
        celsius = node_units_to_celsius(limit, units)
        if 0 < celsius <= max_limit_bounds(self._controller_limits())[1]:
            self._last_max_stemp_limit = str(limit)

    async def async_added_to_hass(self) -> None:
        """Register with the fan-out, then seed memory from restored state."""
        await super().async_added_to_hass()
        # Restored state goes first: the live setup is the newer truth, so a
        # non-zero live limit must win over a stale restored one on restart.
        last = await self.async_get_last_state()
        if last is not None:
            limit = last.attributes.get("last_limit")
            if limit is not None:
                try:
                    self._remember(float(limit))
                except TypeError, ValueError:
                    _LOGGER.warning(
                        "Ignoring restored non-numeric last_limit %r for node %r",
                        limit,
                        self._node.name,
                    )
        limit = node_max_stemp_limit(self._node_state())
        if limit is not None:
            self._remember(limit)

    def _async_change(self, change: Change) -> None:
        """Track non-zero max_stemp_limit values seen on the wire."""
        if isinstance(change, NodeChange) and change.kind == "setup":
            limit = node_max_stemp_limit(change.node)
            if limit is not None:
                self._remember(limit)
        super()._async_change(change)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return state attributes."""
        limit = self._last_max_stemp_limit
        return {
            "last_limit": limit if limit is not None else str(self._default_limit())
        }

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Enable the maximum temperature limit."""
        limit = (
            float(self._last_max_stemp_limit)
            if self._last_max_stemp_limit is not None
            else self._default_limit()
        )
        profile = self._controller_profile(max_limit_c=limit)
        await self._runtime.engine.set_max_stemp_limit(
            self._dev_id,
            self._ref,
            limit,
            node_units(self._node_state()),
            bounds_c=profile.prog_bounds,
            offset_c=profile.limits.profile_offset_c,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Disable the maximum temperature limit."""
        profile = self._controller_profile(max_limit_c=None)
        await self._runtime.engine.set_max_stemp_limit(
            self._dev_id,
            self._ref,
            None,
            node_units(self._node_state()),
            bounds_c=profile.prog_bounds,
            offset_c=profile.limits.profile_offset_c,
        )

    @property
    def is_on(self) -> bool:
        """Return true if the maximum temperature limit is enabled."""
        return node_max_stemp_limit(self._node_state()) is not None


class EasyModeSwitch(SmartboxNodeSwitchEntity):
    """Tevolve EASY mode (status ``easy``).

    The app presents this as a persistent setting and disables it while
    RUNBACK is enabled. Turning EASY on therefore clears RUNBACK first.
    """

    _attr_name = "Easy mode"
    _attr_icon = "mdi:emoticon-happy-outline"

    @property
    def is_on(self) -> bool:
        """Return whether EASY mode is active."""
        return bool(self._status.get("easy", False))

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Enable EASY mode; RUNBACK and EASY are mutually exclusive."""
        if bool(self._status.get("runback", False)):
            await self._runtime.engine.set_runback(
                self._dev_id, self._ref, enabled=False
            )
        await self._runtime.engine.set_easy(self._dev_id, self._ref, enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Disable EASY mode."""
        await self._runtime.engine.set_easy(self._dev_id, self._ref, enabled=False)


class RunbackModeSwitch(SmartboxNodeSwitchEntity):
    """Tevolve RUNBACK mode (status ``runback``).

    RUNBACK and EASY are mutually exclusive in the app; enabling RUNBACK
    clears EASY first.
    """

    _attr_name = "Runback"
    _attr_icon = "mdi:run-fast"

    @property
    def is_on(self) -> bool:
        """Return whether RUNBACK mode is active."""
        return bool(self._status.get("runback", False))

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Enable RUNBACK; EASY and RUNBACK are mutually exclusive."""
        if bool(self._status.get("easy", False)):
            await self._runtime.engine.set_easy(self._dev_id, self._ref, enabled=False)
        await self._runtime.engine.set_runback(self._dev_id, self._ref, enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Disable RUNBACK."""
        await self._runtime.engine.set_runback(self._dev_id, self._ref, enabled=False)


class AntiGrippageSwitch(SmartboxNodeSwitchEntity):
    """Thermostat anti-grippage protection (setup string on/off)."""

    _attr_name = "Anti-grippage"
    _attr_icon = "mdi:snowflake-alert"

    def _setup(self) -> Mapping[str, Any]:
        state = self._node_state()
        return state.setup if state is not None else self._node.setup

    @property
    def is_on(self) -> bool:
        """Return whether anti-grippage is enabled."""
        return self._setup().get("anti_grippage") in ("on", True)

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Enable anti-grippage."""
        await self._runtime.engine.set_setup_value(
            self._dev_id, self._ref, "anti_grippage", "on"
        )

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401, ARG002
        """Disable anti-grippage."""
        await self._runtime.engine.set_setup_value(
            self._dev_id, self._ref, "anti_grippage", "off"
        )
