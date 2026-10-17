"""The Smartbox node: cached state and writes of one heater."""

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, cast

from homeassistant.util import dt as dt_util
from smartbox import AsyncSmartboxSession, SmartboxNodeType
from smartbox.error import APIUnavailableError, SmartboxError

from custom_components.smartbox.const import (
    DEFAULT_BOOST_TEMP,
    DEFAULT_BOOST_TIME,
    HEATER_NODE_TYPES,
    MAX_TEMP_LIMIT_DEFAULT,
    BoostConfig,
)

from .protocol import (
    FactoryOptionsDict,
    Node,
    ProgDict,
    SamplesDict,
    SetupDict,
    StatusDict,
    get_boost_end_datetime,
    normalize_prog,
    round_setpoint,
)

if TYPE_CHECKING:
    from .device import SmartboxDevice

_LOGGER = logging.getLogger(__name__)


class SmartboxNode:
    """Smartbox Node."""

    def __init__(
        self,
        device: SmartboxDevice,
        node_info: Node,
        session: AsyncSmartboxSession,
        status: StatusDict,
        setup: SetupDict,
        samples: SamplesDict,
        version: dict[str, str],
        prog: ProgDict | None = None,
    ) -> None:
        """Initialise a smartbox node."""
        self._device = device
        self._node_info = node_info
        self._session = session
        self._status = status
        self._setup = setup
        self._samples = samples
        self._version = version
        self._prog = prog
        # Last non-zero max_stemp_limit seen on the wire or written; used to
        # restore a sensible value when the limit is re-enabled (the app's
        # toggle-off writes "0.0" to max_stemp_limit, erasing the old value).
        # Session-scoped by itself; the limit switch seeds it from
        # RestoreEntity state on HA start (switch.py).
        self._last_max_stemp_limit = str(MAX_TEMP_LIMIT_DEFAULT)
        # Whether the box can currently reach this node (None = unknown
        # until the first frame or REST read reports sync state).
        self._available: bool | None = None

    @classmethod
    async def create(
        cls,
        device: SmartboxDevice,
        session: AsyncSmartboxSession,
        node_info: Node,
    ) -> SmartboxNode:
        """Create a smartbox node."""

        async def get_status() -> StatusDict:
            if node_info["type"] != SmartboxNodeType.PMO:
                return cast(
                    "StatusDict",
                    await session.get_node_status(device.dev_id, node_info),
                )
            return {
                "sync_status": "ok",
                "locked": False,
                "power": await session.get_device_power_limit(device.dev_id, node_info),
            }

        async def get_prog() -> ProgDict | None:
            if node_info["type"] not in HEATER_NODE_TYPES:
                return None
            try:
                return normalize_prog(
                    await session.get_node_prog(device.dev_id, node_info)
                )
            except APIUnavailableError:
                # Transient: setup retries via ConfigEntryNotReady.
                raise
            except SmartboxError:
                # No schedule support (unverified on non-htr families).
                _LOGGER.debug(
                    "Node %s does not support a schedule", node_info.get("name")
                )
                return None

        # The five reads are independent: issue them together instead of
        # paying five sequential round trips per node.
        status, setup, version, prog = await asyncio.gather(
            get_status(),
            session.get_node_setup(device.dev_id, node_info),
            session.get_node_version(device.dev_id, node_info),
            get_prog(),
        )
        setup = cast("SetupDict", setup)
        version = cast("dict[str, str]", version)
        # The consumption samples are not fetched here: the sensor's
        # ConsumptionCoordinator loads them on its first refresh.
        node = cls(device, node_info, session, status, setup, [], version, prog)
        node.update_availability(status.get("sync_status", "ok") == "ok")
        return node

    @property
    def node_info(self) -> Node:
        """Return the node info."""
        return self._node_info

    def update_availability(self, available: bool) -> None:
        """Record whether the box can currently reach this node."""
        self._available = available

    @property
    def available(self) -> bool | None:
        """Whether the box can reach this node (None while unknown)."""
        return self._available

    @property
    def node_id(self) -> str:
        """Return the id of the node."""
        return f"{self._device.dev_id}_{self._node_info['addr']}"

    @property
    def name(self) -> str:
        """Return the name of the node."""
        return self._node_info["name"] or self.device.name

    @property
    def node_type(self) -> str:
        """Return node type, e.g. 'htr' for heaters."""
        return self._node_info["type"]

    @property
    def addr(self) -> int:
        """Return the addr of node."""
        return self._node_info["addr"]

    @property
    def status(self) -> StatusDict:
        """Return the status of node."""
        return self._status

    @property
    def version(self) -> dict[str, str]:
        """Version info of node (includes PID)."""
        return self._version

    @property
    def hw_version(self) -> str | None:
        """Product ID of the node."""
        return self._version.get("hw_version")

    @property
    def pid(self) -> str | None:
        """Product ID of the node."""
        return self._version.get("pid")

    def get_model_code(self) -> str | None:
        """Get the model code from the PID.

        Uses the last two characters of the PID, uppercased, matching the
        official app behaviour for 4-character PIDs
        (JS: pid.toUpperCase().slice(2, 4)).
        For PID '081c', this returns '1C'.
        """
        pid_min_length = 2
        pid = self.pid
        if pid and len(pid) >= pid_min_length:
            return pid[-pid_min_length:].upper()
        return None

    def update_status(self, status: StatusDict) -> None:
        """Update status."""
        _LOGGER.debug("Updating node %s status: %s", self.name, status)
        # The transient post-write frames carry just {"sync_status": "lost"}
        # (../smartbox api-notes.md): merging keeps the last full snapshot
        # and only flips the sync marker.
        self._status |= {**status}

    @property
    def prog(self) -> ProgDict | None:
        """The node's weekly schedule, or None when it has none (yet)."""
        return self._prog

    def update_prog(self, prog: ProgDict) -> None:
        """Update the local schedule cache."""
        _LOGGER.debug("Updating node %s prog: %s", self.name, prog)
        self._prog = prog

    async def async_refresh_prog(self) -> ProgDict | None:
        """Fetch the schedule from the API.

        Raw mode returns the wire payload ``{"prog": {...}, "sync_status":
        ...}``; returns None (keeping any cached schedule) when the payload is
        unexpected or out of sync. Transient API failures propagate so entity
        polls can mark themselves unavailable.
        """
        response = await self._session.get_node_prog(
            self._device.dev_id, self._node_info
        )
        if not isinstance(response, dict) or response.get("sync_status", "ok") != "ok":
            return None
        return normalize_prog(response)

    async def set_prog(self, prog: ProgDict) -> None:
        """Set (part of) the weekly schedule.

        The library GETs the current schedule, merges the given days over it
        and POSTs the complete object, so partial day updates are safe. The
        local cache is merged optimistically: writes are not immediately
        visible to GET (several seconds of device read-through lag).
        """
        await self._session.set_node_prog(
            self._device.dev_id, self._node_info, {"prog": prog}
        )
        if self._prog is not None:
            self._prog = {**self._prog, **prog}

    @property
    def setup(self) -> SetupDict:
        """Setup of node."""
        return self._setup

    def update_setup(self, setup: SetupDict) -> None:
        """Update setup."""
        _LOGGER.debug("Updating node %s setup: %s", self.name, setup)
        limit = setup.get("max_stemp_limit")
        try:
            if limit is not None and float(limit) > 0:
                self._last_max_stemp_limit = str(limit)
        except TypeError, ValueError:
            # Malformed wire value; skip the memory update (the property
            # reads of max_stemp_limit have the same constraint).
            _LOGGER.warning(
                "Ignoring non-numeric max_stemp_limit %r for node %s",
                limit,
                self.name,
            )
        self._setup = setup

    async def set_status(self, **status_args: Any) -> StatusDict:  # noqa: ANN401
        """Set status."""
        await self._session.set_node_status(
            self._device.dev_id, self._node_info, status_args
        )
        # update our status locally until we get an update
        self._status |= {**status_args}
        # The server acknowledges writes to unreachable nodes without
        # applying them (api-notes.md, 2026-09-30): arm the confirmation
        # window so the availability tracking can catch a write that never
        # lands. The confirming ok frame (fast on a live node) cancels it.
        self._device.update_manager.expect_write_confirmation(
            self._node_info, status_args, kind="status"
        )
        # Entities never poll (_attr_should_poll=False): notify them of the
        # optimistic merge on the same dispatcher event the websocket frames
        # use. Otherwise a write whose confirming frame equals this merged
        # status is the only signal the entity needs -- and for an accepted
        # no-op write the server may not push any change frame at all --
        # which is what locked entities out of sync (live finding,
        # 2026-09-28). Snapshot the cache: later merges mutate it in place.
        self._device.dispatch_node_status(self, {**self._status})
        return self._status

    def _expect_setup_confirmation(self, body: dict[str, Any]) -> None:
        """Arm the write-confirmation window for a setup write."""
        self._device.update_manager.expect_write_confirmation(
            self._node_info, body, kind="setup"
        )

    @property
    def away(self) -> bool:
        """Is away mode."""
        return self._device.away

    @property
    def device(self) -> SmartboxDevice:
        """Return the device of the node."""
        return self._device

    @property
    def session(self) -> AsyncSmartboxSession:
        """Return the smartbox session."""
        return self._session

    async def update_device_away_status(self, away: bool) -> None:
        """Update device away status."""
        await self._device.set_away_status(away)

    @property
    def window_mode(self) -> bool:
        """Is windows mode enable."""
        if "window_mode_enabled" not in self._setup:
            msg = f"window_mode_enabled not present in setup for node {self.name}"
            raise KeyError(msg)
        return self._setup["window_mode_enabled"]

    async def set_window_mode(self, window_mode: bool) -> bool:
        """Set window mode."""
        await self._session.set_node_setup(
            self._device.dev_id,
            self._node_info,
            {"window_mode_enabled": window_mode},
        )
        self._expect_setup_confirmation({"window_mode_enabled": window_mode})
        self._setup["window_mode_enabled"] = window_mode
        return window_mode

    @property
    def true_radiant(self) -> bool:
        """Is a true radiant."""
        if "true_radiant_enabled" not in self._setup:
            msg = f"true_radiant_enabled not present in setup for node {self.name}"
            raise KeyError(msg)
        return self._setup["true_radiant_enabled"]

    async def set_true_radiant(self, true_radiant: bool) -> None:
        """Set true radiant."""
        await self._session.set_node_setup(
            self._device.dev_id,
            self._node_info,
            {"true_radiant_enabled": true_radiant},
        )
        self._expect_setup_confirmation({"true_radiant_enabled": true_radiant})
        self._setup["true_radiant_enabled"] = true_radiant

    async def set_extra_options(self, options: dict[str, Any]) -> None:
        """Set extra options, preserving the other existing options.

        The node setup holds a single extra_options object; always send the
        merged object so unrelated keys are not wiped by the API.
        """
        extra_options: dict[str, Any] = {
            **self._setup.get("extra_options", {}),
            **options,
        }
        await self._session.set_node_setup(
            self._device.dev_id,
            self._node_info,
            {"extra_options": extra_options},
        )
        self._expect_setup_confirmation({"extra_options": extra_options})
        self._setup["extra_options"] = extra_options

    @property
    def away_offset(self) -> float | None:
        """Away offset in the node's temperature scale, None when absent.

        While the box's away switch is on, the heater lowers its target
        temperature by this amount (the app's slider is 0-based, so
        offsets cannot be negative). Wire values are strings.
        """
        if "away_offset" not in self._setup:
            return None
        return float(self._setup["away_offset"])

    async def set_away_offset(self, offset: float) -> None:
        """Set the away offset."""
        await self._session.set_node_setup(
            self._device.dev_id,
            self._node_info,
            {"away_offset": str(offset)},
        )
        self._expect_setup_confirmation({"away_offset": str(offset)})
        self._setup["away_offset"] = str(offset)

    @property
    def max_stemp_limit(self) -> float | None:
        """Maximum target temperature, None when absent or disabled.

        fw-1.9-family setup field only; the wire reports "0.0" on units
        with the limit disabled, which reads as None here. Toggling the
        limit on/off is the max_temperature_limit switch's job (see
        set_max_temp_limit_enabled).
        """
        if "max_stemp_limit" not in self._setup:
            return None
        limit = float(self._setup["max_stemp_limit"])
        return limit if limit > 0 else None

    @property
    def max_temp_limit_enabled(self) -> bool:
        """Is the maximum target temperature limit enabled.

        The wire has no separate enable key: max_stemp_limit "0.0" IS the
        disabled state (api-notes live fixtures), so enabled == a non-zero
        value is present.
        """
        return self.max_stemp_limit is not None

    async def set_max_stemp_limit(self, limit: float) -> None:
        """Set the maximum target temperature."""
        await self._session.set_node_setup(
            self._device.dev_id,
            self._node_info,
            {"max_stemp_limit": str(limit)},
        )
        self._expect_setup_confirmation({"max_stemp_limit": str(limit)})
        self._setup["max_stemp_limit"] = str(limit)
        if limit > 0:
            self._last_max_stemp_limit = str(limit)

    async def set_max_temp_limit_enabled(self, enabled: bool) -> None:
        """Enable/disable the maximum target temperature limit.

        Disabling writes the wire's "0.0" sentinel; re-enabling restores
        the last non-zero limit seen (or MAX_TEMP_LIMIT_DEFAULT, the
        number's slider cap, when none was ever observed). The memory
        survives HA restarts via the limit switch's RestoreEntity state
        (switch.py seeds it back with remember_max_stemp_limit).
        """
        value = self._last_max_stemp_limit if enabled else "0.0"
        await self._session.set_node_setup(
            self._device.dev_id,
            self._node_info,
            {"max_stemp_limit": value},
        )
        self._expect_setup_confirmation({"max_stemp_limit": value})
        self._setup["max_stemp_limit"] = value

    @property
    def last_max_stemp_limit(self) -> str:
        """Last non-zero max_stemp_limit memory, wire format.

        Session memory seeded by update_setup/set_max_stemp_limit and,
        across HA restarts, by the limit switch's RestoreEntity state.
        """
        return self._last_max_stemp_limit

    def remember_max_stemp_limit(self, limit: float) -> None:
        """Seed the last-known-limit memory (limit switch restore)."""
        if limit > 0:
            self._last_max_stemp_limit = str(limit)

    @property
    def priority(self) -> str | None:
        """Radiator priority (low/medium/high), None when absent.

        fw-1.9-family setup field only. The device itself takes it into
        account when enforcing the box power limit; this is only the setting.
        """
        if "priority" not in self._setup:
            return None
        return str(self._setup["priority"])

    async def set_priority(self, priority: str) -> None:
        """Set the radiator priority."""
        await self._session.set_node_setup(
            self._device.dev_id,
            self._node_info,
            {"priority": priority},
        )
        self._expect_setup_confirmation({"priority": priority})
        self._setup["priority"] = priority

    async def set_prog_temps(
        self,
        *,
        ice_temp: float | None = None,
        eco_temp: float | None = None,
        comf_temp: float | None = None,
    ) -> None:
        """Set programme profile temperatures (Frost/Eco/Comfort).

        Uses the app-shaped 4-key status body {ice_temp, eco_temp,
        comf_temp, units} (webapi-spec.md §4.4): unmodified profile temps
        are sent back with their current values. The dedicated
        /prog_temps endpoint exists in the library but has no known app
        caller, so the status POST path is preferred (same reasoning as
        the child-lock switch). set_status() merges optimistically and
        dispatches to entities.
        """
        status_args = {
            "ice_temp": round_setpoint(ice_temp, self._status["units"])
            if ice_temp is not None
            else self._status.get("ice_temp"),
            "eco_temp": round_setpoint(eco_temp, self._status["units"])
            if eco_temp is not None
            else self._status.get("eco_temp"),
            "comf_temp": round_setpoint(comf_temp, self._status["units"])
            if comf_temp is not None
            else self._status.get("comf_temp"),
            "units": self._status.get("units"),
        }
        await self.set_status(**status_args)

    def is_heating(self, status: dict[str, Any]) -> bool:
        """Is heating."""
        return (
            status["charging"]
            if self.node_type == SmartboxNodeType.ACM
            else status["active"]
        )

    async def update_power(self) -> None:
        """Update power."""
        self._status["power"] = await self._session.get_device_power_limit(
            self.device.dev_id,
            self._node_info,
        )

    async def update_samples(self) -> None:
        """Update the samples."""
        sample = await self.get_samples(
            int(time.time() - (3600 * 3)),
            int(time.time()),
        )
        self.apply_samples(sample)

    def apply_samples(self, samples: SamplesDict) -> None:
        """Keep the two newest samples (oldest first) as the node's counter."""
        max_sample = 2
        if len(samples) >= max_sample:
            self._samples = samples[-2:]
            _LOGGER.debug("Updating node %s samples: %s", self.name, self._samples)

    async def get_samples(self, start_time: int, end_time: int) -> SamplesDict:
        """Update the samples."""
        return (
            cast(
                "dict[str,Any]",
                await self._session.get_node_samples(
                    self.device.dev_id,
                    self._node_info,
                    start_time,
                    end_time,
                ),
            )
        )["samples"]

    @property
    def total_energy(self) -> float | None:
        """Get the energy used."""
        if not self._samples:
            return None
        return self._samples[-1]["counter"]

    @property
    def boost_config(self) -> BoostConfig:
        """Get the boost config."""
        _boost_config = self._setup.get("factory_options", {}).get("boost_config", 0)
        return BoostConfig(_boost_config)

    @property
    def boost(self) -> bool:
        """Boost status."""
        return self.status.get("boost", False)

    @property
    def boost_available(self) -> bool:
        """Is boost available."""
        return bool(self.boost_config.value)

    @property
    def heater_node(self) -> bool:
        """Is this node a heater."""
        return self.node_type in HEATER_NODE_TYPES

    @property
    def locked(self) -> bool:
        """Is the node's child lock engaged."""
        return bool(self.status.get("locked", False))

    @property
    def boost_time(self) -> float:
        """Get the boost time."""
        return float(
            self.setup.get("extra_options", {}).get("boost_time", DEFAULT_BOOST_TIME)
        )

    @property
    def boost_temp(self) -> float:
        """Get the boost time."""
        return float(
            self.setup.get("extra_options", {}).get("boost_temp", DEFAULT_BOOST_TEMP)
        )

    @property
    def boost_end_min(self) -> int:
        """Get the boost end time."""
        return self.status.get("boost_end_min", 0)

    @property
    def remaining_boost_time(self) -> int:
        """Return the remaining boost time."""
        if not self.boost:
            return 0
        boost_end = get_boost_end_datetime(self.boost_end_min)
        return int((boost_end - dt_util.now()).total_seconds())


def get_factory_options(node: SmartboxNode) -> FactoryOptionsDict:
    """Get the factory options."""
    return cast("FactoryOptionsDict", node.setup.get("factory_options", {}))


def window_mode_available(node: SmartboxNode) -> bool:
    """Is window mode available."""
    return get_factory_options(node).get("window_mode_available", False)


def true_radiant_available(node: SmartboxNode) -> bool:
    """Is true radiant available."""
    return get_factory_options(node).get("true_radiant_available", False)


def away_offset_available(node: SmartboxNode) -> bool:
    """Is the away offset configurable on this node."""
    return "away_offset" in node.setup


def max_temp_limit_available(node: SmartboxNode) -> bool:
    """Is the maximum target temperature limit configurable on this node.

    True whenever the setup carries the fw-1.9-family max_stemp_limit key,
    including "0.0" (limit disabled) -- the number and its on/off switch
    both exist so the limit can be re-enabled from HA.
    """
    return "max_stemp_limit" in node.setup


def priority_available(node: SmartboxNode) -> bool:
    """Is the radiator priority configurable on this node.

    fw-1.9-family setup field only.
    """
    return "priority" in node.setup


def prog_temps_available(node: SmartboxNode) -> bool:
    """Are the programme profile temps (Frost/Eco/Comfort) settable.

    Plain htr/acm nodes only: the schedule profiles resolve to their own
    temperatures. htr_mod nodes manage the same feature via
    comfort_temp/eco_offset through the climate setpoint instead.
    """
    return node.node_type in (
        SmartboxNodeType.HTR,
        SmartboxNodeType.ACM,
    ) and all(key in node.status for key in ("ice_temp", "eco_temp", "comf_temp"))
