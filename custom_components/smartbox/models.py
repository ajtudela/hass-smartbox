"""Models for Smartbox."""

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
import logging
import time
from typing import TYPE_CHECKING, Any, Final, cast

from homeassistant.components.climate import (
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_HOME,
    PRESET_NONE,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from smartbox import (
    AsyncSmartboxSession,
    SmartboxNodeType,
    UpdateManager,
    WsUserSocketSession,
)
from smartbox.error import APIUnavailableError, InvalidAuthError, SmartboxError
from smartbox.retry import backoff_delay

from .const import (
    DEFAULT_BOOST_TEMP,
    DEFAULT_BOOST_TIME,
    DOMAIN,
    GITHUB_ISSUES_URL,
    HEATER_NODE_TYPES,
    MAX_TEMP_LIMIT_DEFAULT,
    PRESET_FROST,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
    SMARTBOX_UNAVAILABLE_DELAY,
    SMARTBOX_WRITE_CONFIRM_TIMEOUT,
    BoostConfig,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_NODE_CREATE_CONCURRENCY = 3

# Upper bound for websocket teardown (cancel + watchdog exit). Unload/reload
# and the HA-stop hook must always complete promptly even if the websocket
# session is wedged.
_TEARDOWN_TIMEOUT_SECONDS: Final = 10.0

# Backoff for restarting the update-manager task after an unexpected exit
# (run() itself waits out transient trouble; this only bounds how fast we
# retry when it died of something unexpected).
_WATCHDOG_RESTART_BASE_SECONDS: Final = 5.0
_WATCHDOG_RESTART_MAX_SECONDS: Final = 300.0

# A programme day always covers 24 h: slot length = 1440 / len(day array).
_PROG_DAY_MINUTES: Final = 1440

FactoryOptionsDict = dict[str, bool]
SetupDict = dict[str, Any]
StatusDict = dict[str, Any]
SamplesDict = list[dict[str, Any]]
ProgDict = dict[str, list[int]]
Node = dict[str, Any]
Device = dict[str, Any]


class SmartboxDevice:
    """Smartbox device."""

    def __init__(
        self,
        device: Device,
        session: AsyncSmartboxSession,
        hass: HomeAssistant,
        ws_user_socket: WsUserSocketSession | None = None,
    ) -> None:
        """Initialise a smartbox device.

        ``ws_user_socket`` optionally carries a shared per-user
        :class:`smartbox.ws_user.WsUserSocketSession` so the device's
        UpdateManager rides on the account-wide connection instead of
        owning a per-device socket_io session.
        """
        self._device = device
        self._session = session
        self._ws_user_socket = ws_user_socket
        self._away_status: dict[str, bool] = {
            "away": False,
            "enabled": False,
            "forced": False,
        }
        self._power_limit: int = 0
        self._last_nonzero_power_limit: int | None = None
        self._rtc_time: dict[str, int] | None = None
        self._nodes: dict[tuple[str, int | str], SmartboxNode] = {}
        self._watchdog_task: asyncio.Task | None = None
        self._hass = hass
        self._connected_status: bool | None = None
        self._watchdog_restarts: int = 0
        self._stopping: bool = False
        self.update_manager: UpdateManager = UpdateManager(
            self._session,
            self.dev_id,
            unavailable_delay=SMARTBOX_UNAVAILABLE_DELAY,
            write_confirm_timeout=SMARTBOX_WRITE_CONFIRM_TIMEOUT,
            ws_user_socket=self._ws_user_socket,
        )

    @classmethod
    async def initialise_nodes(
        cls,
        device: Device,
        session: AsyncSmartboxSession,
        hass: HomeAssistant,
        ws_user_socket: WsUserSocketSession | None = None,
    ) -> SmartboxDevice:
        """Initilaise nodes."""
        self = cls(
            device=device, session=session, hass=hass, ws_user_socket=ws_user_socket
        )
        # Would do in __init__, but needs to be a coroutine
        self._connected_status = cast(
            "dict[str, bool]", (await self._session.get_device_connected(self.dev_id))
        )["connected"]
        self._away_status = {
            "away": False,
            "enabled": False,
            "forced": False,
            **cast(
                "dict[str, bool]",
                await self._session.get_device_away_status(self.dev_id),
            ),
        }

        session_nodes = cast("list[Node]", await self._session.get_nodes(self.dev_id))
        # The device-level power limit applies to every box (the app shows the
        # toggle on heater-only boxes too); 0 means "no limit".
        try:
            self._power_limit = await self._session.get_device_power_limit(self.dev_id)
        except APIUnavailableError:
            # Transport failure, not "the device has no limit": since
            # 2.6.2 this would otherwise be swallowed by the SmartboxError
            # clause below (APIUnavailableError derives from it now).
            raise
        except SmartboxError:
            _LOGGER.debug(
                "Device %s does not report a device-level power limit", self.dev_id
            )
        if self._power_limit != 0:
            self._last_nonzero_power_limit = self._power_limit

        # Bounded concurrency: a box with many nodes must not burst the
        # cloud API with one request per node at once.
        semaphore = asyncio.Semaphore(_NODE_CREATE_CONCURRENCY)

        async def create_node(node_info: Node) -> SmartboxNode:
            async with semaphore:
                return await SmartboxNode.create(
                    device=self, node_info=node_info, session=self._session
                )

        for node in await asyncio.gather(*(create_node(n) for n in session_nodes)):
            self._nodes[(node.node_type, node.addr)] = node
        _LOGGER.debug("Creating SocketSession for device %s", self.dev_id)
        self.update_manager.subscribe_to_device_connected(self._connected)
        self.update_manager.subscribe_to_device_away_status(self._away_status_update)
        self.update_manager.subscribe_to_node_setup(self._node_setup_update)
        self.update_manager.subscribe_to_device_power_limit(self._power_limit_update)
        self.update_manager.subscribe_to_node_status(self._node_status_update)
        self.update_manager.subscribe_to_node_prog(self._node_prog_update)
        self.update_manager.subscribe_to_node_availability(
            self._node_availability_update
        )

        _LOGGER.debug("Starting UpdateManager task for device %s", self.dev_id)
        self._watchdog_task = asyncio.create_task(
            self.update_manager.run(), name=f"smartbox_update_{self.dev_id}"
        )
        # run() exiting unexpectedly (e.g. credentials rejected) must never
        # leave a silently dead socket: log it, then restart or trigger reauth.
        self._watchdog_task.add_done_callback(self._watchdog_done)
        return self

    async def cancel(self) -> None:
        """Cancel the watchdog task and disconnect.

        Deliberately bounded and exception-proof: unload (UI reload) and the
        HA-stop hook must always complete promptly even when the websocket
        session is wedged. On live hardware run() parks in the websocket
        loop, so the watchdog task is still alive here; hard-cancelling it
        and then awaiting it bare used to raise CancelledError out of
        async_unload_entry, which silently killed UI reloads.
        """
        self._stopping = True
        try:
            await asyncio.wait_for(
                self.update_manager.cancel(), _TEARDOWN_TIMEOUT_SECONDS
            )
        except TimeoutError:
            _LOGGER.warning(
                "Socket teardown for device %s did not finish in %ss",
                self.dev_id,
                _TEARDOWN_TIMEOUT_SECONDS,
            )
        except Exception:
            _LOGGER.exception(
                "Error tearing down socket session for device %s", self.dev_id
            )
        finally:
            task = self._watchdog_task
            if task is not None:
                if not task.done():
                    _LOGGER.warning(
                        "Force-cancelling watchdog task for device %s", self.dev_id
                    )
                    task.cancel()
                # asyncio.wait returns on timeout instead of raising, and it
                # swallows the cancelled task's CancelledError for us; the
                # suppress only guards our own cancellation mid-wait.
                with suppress(asyncio.CancelledError):
                    await asyncio.wait({task}, timeout=_TEARDOWN_TIMEOUT_SECONDS)
                if not task.done():
                    _LOGGER.warning(
                        "Watchdog task for device %s still running after cancel",
                        self.dev_id,
                    )

    def _connected(self, connected: bool) -> None:
        _LOGGER.debug("Connected connected update: %s", connected)
        self._connected_status = connected
        if connected:
            # A (re)connection succeeded: any restart backoff can reset.
            self._watchdog_restarts = 0
        # The box connectivity entity listens on f"{DOMAIN}_{dev_id}_connected".
        async_dispatcher_send(
            self._hass, f"{DOMAIN}_{self.dev_id}_connected", self._connected_status
        )

    def _away_status_update(self, away_status: dict[str, bool]) -> None:
        _LOGGER.debug("Away status update: %s", away_status)

        merged = {**self._away_status, **away_status}
        if merged != self._away_status:
            self._away_status = merged
            # The box away-status entity listens on
            # f"{DOMAIN}_{dev_id}_away_status"; frames may carry any subset of
            # the {away, enabled, forced} keys, so dispatch the merged dict.
            async_dispatcher_send(
                self._hass, f"{DOMAIN}_{self.dev_id}_away_status", dict(merged)
            )

    def _power_limit_update(self, power_limit: int) -> None:
        _LOGGER.debug("power_limit update: %s", power_limit)
        if power_limit != 0:
            self._last_nonzero_power_limit = power_limit
        if self._power_limit != power_limit:
            self._power_limit = power_limit
            async_dispatcher_send(
                self._hass, f"{DOMAIN}_{self.dev_id}_power_limit", power_limit
            )

    def _node_status_update(
        self, node_type: str, addr: int, node_status: StatusDict
    ) -> None:
        if node_type == SmartboxNodeType.PMO:
            return
        _LOGGER.debug("Node status update: %s", node_status)
        if node_status is None or (node_type, addr) not in self._nodes:
            _LOGGER.error(
                "Received status update for unknown node %s %s", node_type, addr
            )
            return
        node: SmartboxNode = self._nodes[(node_type, addr)]
        node.update_status(node_status)
        if node_status.get("sync_status", "ok") == "ok":
            # Forward every ok frame, even one equal to the node cache:
            # after an optimistic set_status merge it is the *entity* copy
            # that is stale, and this frame is the only confirmation it
            # gets (off->heat->off desync, live finding 2026-09-28). HA's
            # state machine dedups identical state writes, so forwarding
            # is cheap.
            self.dispatch_node_status(node, node_status)

    def dispatch_node_status(self, node: SmartboxNode, status: StatusDict) -> None:
        """Forward a node status snapshot to the node's listening entities.

        Both websocket frames and the optimistic merge in
        SmartboxNode.set_status go through this single dispatcher event, so
        an entity copy cannot miss an update the node cache already has.
        """
        async_dispatcher_send(self._hass, f"{DOMAIN}_{node.node_id}_status", status)

    def _node_availability_update(
        self, node_type: str, addr: int, available: bool
    ) -> None:
        """Node availability update from the library's tracking.

        Unreachable means the box cannot reach the NODE (the gateway keeps
        reporting connected); entities of that node go Unavailable until an
        ok frame or confirmed write reports it alive again.
        """
        if node_type == SmartboxNodeType.PMO:
            return
        if (node_type, addr) not in self._nodes:
            _LOGGER.debug(
                "Received availability update for unknown node %s %s",
                node_type,
                addr,
            )
            return
        node: SmartboxNode = self._nodes[(node_type, addr)]
        node.update_availability(available)
        async_dispatcher_send(
            self._hass, f"{DOMAIN}_{node.node_id}_availability", available
        )

    def _node_setup_update(
        self, node_type: str, addr: int, node_setup: SetupDict
    ) -> None:
        _LOGGER.debug("Node setup update: %s", node_setup)
        if (node_type, addr) not in self._nodes:
            _LOGGER.error(
                "Received setup update for unknown node %s %s", node_type, addr
            )
            return
        node: SmartboxNode = self._nodes[(node_type, addr)]
        node.update_setup(node_setup)
        async_dispatcher_send(self._hass, f"{DOMAIN}_{node.node_id}_setup", node_setup)

    def _node_prog_update(self, node_type: str, addr: int, payload: Any) -> None:  # noqa: ANN401
        """Node prog (schedule) update from the websocket.

        The /prog update frame shape is recorded in ../smartbox api-notes.md
        but not yet live-confirmed; anything unexpected is ignored so the REST
        poll remains the authoritative source for the schedule.
        """
        if node_type == SmartboxNodeType.PMO:
            return
        prog = _normalize_prog(payload)
        if prog is None:
            _LOGGER.debug(
                "Ignoring unexpected prog payload for %s %s: %s",
                node_type,
                addr,
                payload,
            )
            return
        if (node_type, addr) not in self._nodes:
            _LOGGER.error(
                "Received prog update for unknown node %s %s", node_type, addr
            )
            return
        node: SmartboxNode = self._nodes[(node_type, addr)]
        node.update_prog(prog)
        async_dispatcher_send(self._hass, f"{DOMAIN}_{node.node_id}_prog", prog)

    def _watchdog_done(self, task: asyncio.Task) -> None:
        """Handle the update-manager task exiting.

        Cancellation (unload, HA stop) is the normal exit. InvalidAuthError
        means the credentials were rejected after the library's automatic
        re-auth-and-resend also failed: request a reauthentication flow.
        Anything else is unexpected -- log it and restart the task with capped
        backoff instead of leaving a dead socket and stale entities behind.
        """
        if self._stopping or task.cancelled():
            return
        exc = task.exception()
        if isinstance(exc, InvalidAuthError):
            _LOGGER.error(
                "Credentials rejected for device %s; requesting reauthentication",
                self.dev_id,
            )
            async_dispatcher_send(self._hass, f"{DOMAIN}_{self.dev_id}_reauth_required")
            return
        if exc is None:
            _LOGGER.error(
                "Update task for device %s exited unexpectedly; restarting",
                self.dev_id,
            )
        else:
            _LOGGER.error(
                "Update task for device %s exited unexpectedly: %s; restarting",
                self.dev_id,
                exc,
                exc_info=exc,
            )
        self._schedule_watchdog_restart()

    def _schedule_watchdog_restart(self) -> None:
        """Restart the update-manager task after a capped, doubling backoff."""
        delay = backoff_delay(
            self._watchdog_restarts,
            _WATCHDOG_RESTART_BASE_SECONDS,
            _WATCHDOG_RESTART_MAX_SECONDS,
        )
        self._watchdog_restarts += 1

        async def _restart() -> None:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            if self._stopping:
                return
            _LOGGER.debug("Restarting update task for device %s", self.dev_id)
            self._watchdog_task = asyncio.create_task(
                self.update_manager.run(), name=f"smartbox_update_{self.dev_id}"
            )
            self._watchdog_task.add_done_callback(self._watchdog_done)

        self._hass.async_create_task(
            _restart(), name=f"smartbox_update_restart_{self.dev_id}"
        )

    @property
    def device(self) -> Device:
        """Return the device."""
        return self._device

    @property
    def connected(self) -> bool | None:
        """Return the device."""
        return self._connected_status

    @property
    def session(self) -> AsyncSmartboxSession:
        """Return the smartbox session."""
        return self._session

    @property
    def home(self) -> dict[str, Any]:
        """Return home of the device."""
        return self._device["home"]

    @property
    def dev_id(self) -> str:
        """Return the device id."""
        return self._device["dev_id"]

    def get_nodes(self) -> list[SmartboxNode]:
        """Return all nodes."""
        return list(self._nodes.values())

    @property
    def name(self) -> str:
        """Return name of the device."""
        return self._device["name"]

    @property
    def model_id(self) -> int:
        """Return the model id."""
        return self._device["product_id"]

    @property
    def sw_version(self) -> int:
        """Return the software version of the device."""
        return self._device["fw_version"]

    @property
    def serial_number(self) -> int:
        """Return the serial number of the device."""
        return self._device["serial_id"]

    @property
    def away(self) -> bool:
        """Is the device in away mode."""
        return self._away_status["away"]

    @property
    def away_status(self) -> dict[str, bool]:
        """Full away state: {away, enabled, forced}."""
        return dict(self._away_status)

    async def set_away_status(self, away: bool) -> None:
        """Set the away status."""
        await self._session.set_device_away_status(self.dev_id, {"away": away})
        self._away_status_update(away_status={"away": away})

    @property
    def power_limit(self) -> int:
        """Get the power limit of the device (0 means no limit)."""
        return self._power_limit

    @property
    def no_power_limit(self) -> bool:
        """Whether no power limit is set."""
        return self._power_limit == 0

    @property
    def last_nonzero_power_limit(self) -> int | None:
        """Last power limit seen that was not the "no limit" value.

        Not persisted: after a restart with no limit active this is None
        (the box reports 0, which is exactly the "no limit" state).
        """
        return self._last_nonzero_power_limit

    async def set_power_limit(self, power_limit: int) -> None:
        """Set the power limit of the device."""
        await self._session.set_device_power_limit(self.dev_id, power_limit)
        self._power_limit_update(power_limit)

    async def async_refresh_rtc(self) -> dict[str, int] | None:
        """Fetch the box's RTC time, or None when unavailable.

        Errors propagate so callers can mark themselves unavailable.
        """
        rtc = await self._session.get_device_rtc_time(self.dev_id)
        if isinstance(rtc, dict):
            self._rtc_time = cast("dict[str, int]", rtc)
        return self._rtc_time


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

        async def get_samples() -> SamplesDict:
            return cast(
                "dict[str, Any]",
                await session.get_node_samples(
                    device.dev_id,
                    node_info,
                    int(time.time() - (3600 * 3)),
                    int(time.time()),
                ),
            )["samples"]

        async def get_prog() -> ProgDict | None:
            if node_info["type"] not in HEATER_NODE_TYPES:
                return None
            try:
                return _normalize_prog(
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
        status, setup, samples, version, prog = await asyncio.gather(
            get_status(),
            session.get_node_setup(device.dev_id, node_info),
            get_samples(),
            session.get_node_version(device.dev_id, node_info),
            get_prog(),
        )
        setup = cast("SetupDict", setup)
        version = cast("dict[str, str]", version)
        node = cls(device, node_info, session, status, setup, samples, version, prog)
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
        # and only flips the sync marker, which the poll path uses to mark
        # entities unavailable (entity copies skip non-ok frames, so the
        # 1-key frame never replaces a snapshot there).
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
        return _normalize_prog(response)

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

    async def async_update(self, _: Any) -> StatusDict:  # noqa: ANN401
        """Update status."""
        return self.status

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
            "ice_temp": _round_setpoint(ice_temp, self._status["units"])
            if ice_temp is not None
            else self._status.get("ice_temp"),
            "eco_temp": _round_setpoint(eco_temp, self._status["units"])
            if eco_temp is not None
            else self._status.get("eco_temp"),
            "comf_temp": _round_setpoint(comf_temp, self._status["units"])
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
        max_sample = 2
        sample = await self.get_samples(
            int(time.time() - (3600 * 3)),
            int(time.time()),
        )
        if len(sample) >= max_sample:
            self._samples = sample[-2:]
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


def get_temperature_unit(status: StatusDict) -> UnitOfTemperature | None:
    """Get the unit of temperature."""
    if "units" not in status:
        return None
    unit = status["units"]
    if unit == "C":
        return UnitOfTemperature.CELSIUS
    if unit == "F":
        return UnitOfTemperature.FAHRENHEIT
    msg = f"Unknown temp unit {unit}"
    raise ValueError(msg)


def rtc_time_to_datetime(rtc: dict[str, Any] | None) -> datetime | None:
    """Convert a device RTC payload into a local datetime, or None.

    Wire shape (live-verified 2026-09-27): ``{"d": 27, "h": 9, "m": 55,
    "n": 8, "s": 24, "w": 0, "y": 2026}`` — September reported as ``n=8``,
    so ``n`` is treated as a 0-indexed month (unconfirmed for December,
    where a 0-indexed value would be 11; a 1-indexed 12 fails to parse
    and yields None). The RTC is interpreted in the Home Assistant
    timezone: the box timezones the cloud reports have not been captured
    live yet.
    """
    if rtc is None:
        return None
    try:
        return datetime(
            year=int(rtc["y"]),
            month=int(rtc["n"]) + 1,
            day=int(rtc["d"]),
            hour=int(rtc["h"]),
            minute=int(rtc["m"]),
            second=int(rtc["s"]),
            tzinfo=dt_util.DEFAULT_TIME_ZONE,
        )
    except KeyError, TypeError, ValueError:
        return None


def get_boost_end_datetime(boost_end_min: int) -> datetime:
    """Return the local datetime at which the node boost ends.

    boost_end_min is the minute of the day (UTC) at which the boost ends, as
    reported by the API status. Returns a future datetime (the next day if the
    boost already ended earlier in the current UTC day).
    """
    now = dt_util.utcnow()
    hour, minute = divmod(int(boost_end_min), 60)
    boost_end = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if boost_end <= now:
        # Boost already ended for the current UTC day: it ends tomorrow.
        boost_end += timedelta(days=1)
    return dt_util.as_local(boost_end)


async def get_devices(
    session: AsyncSmartboxSession,
    hass: HomeAssistant,
    ws_user_socket: WsUserSocketSession | None = None,
) -> list[SmartboxDevice]:
    """Get the devices."""
    homes: list[dict[str, Any]] = cast(
        "list[dict[str, Any]]", await session.get_homes()
    )
    devices: list[SmartboxDevice] = []
    try:
        for home in homes:
            _home = home.copy()
            del _home["devs"]
            for session_device in home["devs"]:
                session_device["home"] = _home
                devices.append(
                    await SmartboxDevice.initialise_nodes(
                        session_device, session, hass, ws_user_socket=ws_user_socket
                    )
                )
    except Exception:
        # Every device created so far already runs websocket/update tasks;
        # cancel them so a failed or retried setup does not leak sessions.
        for device in devices:
            with suppress(Exception):
                await device.cancel()
        raise
    return devices


def _check_status_key(key: str, node_type: str, status: dict[str, Any]) -> None:
    if key not in status:
        msg = (
            f"'{key}' not found in {node_type} - please report to {GITHUB_ISSUES_URL}. "
            f"status: {status}"
        )
        raise KeyError(msg)


def get_target_temperature(node_type: str, status: dict[str, Any]) -> float | None:
    """Get the target temperature.

    None when the node is off: there is no set point then (matching the
    official app). The wire still carries the stale previous-mode value
    (stemp / selected_temp "off"), so the off check mirrors get_hvac_mode
    instead of trusting those keys.
    """
    if node_type == SmartboxNodeType.HTR_MOD:
        if (
            status.get("mode") == "off"
            or status.get("on", True) is False
            or status.get("selected_temp") == "off"
        ):
            return None
        _check_status_key("selected_temp", node_type, status)
        if status["selected_temp"] == "comfort":
            _check_status_key("comfort_temp", node_type, status)
            return float(status["comfort_temp"])
        if status["selected_temp"] == "eco":
            _check_status_key("comfort_temp", node_type, status)
            _check_status_key("eco_offset", node_type, status)
            return float(status["comfort_temp"]) - float(status["eco_offset"])
        if status["selected_temp"] == "ice":
            _check_status_key("ice_temp", node_type, status)
            return float(status["ice_temp"])
        msg = (
            f"Unexpected 'selected_temp' value {status['selected_temp']}"
            f" found for {node_type} - please report to"
            f" {GITHUB_ISSUES_URL}. status: {status}"
        )
        raise KeyError(msg)
    if status.get("mode") == "off":
        return None
    _check_status_key("stemp", node_type, status)
    return float(status["stemp"])


def set_temperature_args(
    node_type: str,
    status: dict[str, Any],
    temp: float,
    setup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Set targeted temperature."""
    _check_status_key("units", node_type, status)
    if node_type == SmartboxNodeType.HTR_MOD:
        if status["selected_temp"] == "comfort":
            target_temp = temp
        elif status["selected_temp"] == "eco":
            _check_status_key("eco_offset", node_type, status)
            target_temp = temp + float(status["eco_offset"])
        elif status["selected_temp"] == "ice":
            msg = "Can't set temperature for htr_mod devices when ice mode is selected"
            raise ValueError(msg)
        else:
            msg = (
                f"Unexpected 'selected_temp' value {status['selected_temp']}"
                f" found for {node_type} - please report to "
                f"{GITHUB_ISSUES_URL}. status: {status}"
            )
            raise KeyError(msg)
        return {
            "on": True,
            "mode": status["mode"],
            "selected_temp": status["selected_temp"],
            "comfort_temp": str(target_temp),
            "eco_offset": status["eco_offset"],
            "units": status["units"],
        }
    status_args = {
        "stemp": _round_setpoint(temp, status["units"]),
        "units": status["units"],
    }
    # Setpoint inside the running programme: the official app engages
    # "modified_auto" with the same body so the override survives programme
    # transitions (webapi-spec.md §4.2; live-probed 2026-09-28: the body
    # flips the mode on the wire, the heater follows, and it reverts with
    # {"mode": "auto"}). The app's INDEPENDENT_TEMP_AND_MODE_ON_UPDATE
    # capability is not wire-visible; modified_auto_span in setup is the
    # available proxy for "this unit supports the feature".
    if (
        setup is not None
        and "modified_auto_span" in setup
        and status.get("mode") == "auto"
    ):
        status_args["mode"] = "modified_auto"
    return status_args


def _round_setpoint(temp: float, units: str) -> str:
    """Round a setpoint onto the device grid and format it for the wire.

    The device silently quantizes off-grid values to the 0.5 °C grid
    (live-probed 2026-09-28, ../smartbox api-notes.md); the app only ever
    sends on-grid values. Rounding here keeps the optimistic UI value equal
    to what the device actually stores. Fahrenheit setpoints are passed
    through: their wire grid is unverified.
    """
    if units == "C":
        return f"{round(temp * 2) / 2:.1f}"
    return str(temp)


_WARNED_UNKNOWN: set[str] = set()


def warn_unknown_once(kind: str, value: object) -> None:
    """Log a single warning per unknown protocol value.

    These values are evaluated on every state write, so logging each time
    would flood the log for as long as the firmware reports them.
    """
    key = f"{kind}:{value}"
    if key in _WARNED_UNKNOWN:
        return
    _WARNED_UNKNOWN.add(key)
    _LOGGER.warning(
        "Unknown smartbox %s %r - please report to %s", kind, value, GITHUB_ISSUES_URL
    )


def get_hvac_mode(  # noqa: PLR0911
    node_type: str, status: dict[str, Any]
) -> HVACMode | None:
    """Get the mode of HVAC."""
    if status.get("boost", False):
        return HVACMode.HEAT
    _check_status_key("mode", node_type, status)
    if status["mode"] == "off" or (
        node_type == SmartboxNodeType.HTR_MOD and not status["on"]
    ):
        return HVACMode.OFF
    if status["mode"] == "manual":
        return HVACMode.HEAT
    if status["mode"] == "auto":
        return HVACMode.AUTO
    if status["mode"] == "modified_auto":
        # This occurs when the temperature is modified while in auto mode.
        # Mapping it to auto seems to make this most sense
        return HVACMode.AUTO
    if status["mode"] == "self_learn" or status["mode"] == "presence":
        return HVACMode.AUTO
    warn_unknown_once("node mode", status["mode"])
    return None


def set_hvac_mode_args(
    node_type: str, status: dict[str, Any], hvac_mode: str
) -> dict[str, Any]:
    """Set the mode of HVAC."""
    error_msg = f"Unsupported hvac mode {hvac_mode}"
    if node_type == SmartboxNodeType.HTR_MOD:
        if hvac_mode == HVACMode.OFF:
            return {"on": False}
        if hvac_mode == HVACMode.HEAT:
            # We need to pass these status keys on when setting the mode
            required_status_keys = ["selected_temp"]
            for key in required_status_keys:
                _check_status_key(key, node_type, status)
            hvac_mode_args = {k: status[k] for k in required_status_keys}
            hvac_mode_args["on"] = True
            hvac_mode_args["mode"] = "manual"
            return hvac_mode_args
        if hvac_mode == HVACMode.AUTO:
            return {"on": True, "mode": "auto"}
        raise ValueError(error_msg)
    if hvac_mode == HVACMode.OFF:
        return {"mode": "off"}
    if hvac_mode == HVACMode.HEAT:
        return {"mode": "manual"}
    if hvac_mode == HVACMode.AUTO:
        return {"mode": "auto"}
    raise ValueError(error_msg)


def set_preset_mode_status_update(
    node_type: str, status: dict[str, Any], preset_mode: str
) -> dict[str, Any]:
    """Set preset mode status update."""
    if node_type != SmartboxNodeType.HTR_MOD:
        msg = f"{node_type} nodes do not support preset {preset_mode}"
        raise ValueError(msg)
    # PRESET_HOME and PRESET_AWAY are not handled via status updates
    if preset_mode in (PRESET_HOME, PRESET_AWAY, PRESET_NONE):
        msg = f"Preset {preset_mode} is not applied through a status update"
        raise ValueError(msg)

    if preset_mode == PRESET_SCHEDULE:
        return set_hvac_mode_args(node_type, status, HVACMode.AUTO)
    if preset_mode == PRESET_SELF_LEARN:
        return {"on": True, "mode": "self_learn"}
    if preset_mode == PRESET_ACTIVITY:
        return {"on": True, "mode": "presence"}
    if preset_mode == PRESET_COMFORT:
        return {"on": True, "mode": "manual", "selected_temp": "comfort"}
    if preset_mode == PRESET_ECO:
        return {"on": True, "mode": "manual", "selected_temp": "eco"}
    if preset_mode == PRESET_FROST:
        return {"on": True, "mode": "manual", "selected_temp": "ice"}
    msg = f"Unsupported preset {preset_mode} for node type {node_type}"
    raise ValueError(msg)


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


def _normalize_prog(payload: Any) -> ProgDict | None:  # noqa: ANN401
    """Validate and normalize a schedule payload into day-keyed slot lists.

    Accepts both the REST shape ``{"prog": {...}, "sync_status": ...}`` and
    a bare day-keyed object; returns None when the shape is unexpected. Day
    keys must be strings and each value a list of ints.
    """
    if isinstance(payload, dict) and isinstance(payload.get("prog"), dict):
        payload = payload["prog"]
    if not isinstance(payload, dict):
        return None
    prog: ProgDict = {}
    for day, slots in payload.items():
        if not (
            isinstance(day, str)
            and isinstance(slots, list)
            and all(isinstance(slot, int) for slot in slots)
        ):
            return None
        prog[day] = list(slots)
    return prog


def get_current_prog_profile(prog: ProgDict, now: datetime) -> int | None:
    """Return the profile index the schedule asks for at local time ``now``.

    Day keys "0".."6" run Monday..Sunday. Slot length is 24 h divided by the
    day-array length (24 hourly slots, or 48 half-hourly where supported).
    """
    day = prog.get(str(now.weekday()))
    if not day:
        return None
    slot_minutes = _PROG_DAY_MINUTES // len(day)
    if slot_minutes < 1:
        return None
    slot = (now.hour * 60 + now.minute) // slot_minutes
    return day[min(slot, len(day) - 1)]


def get_next_prog_change(prog: ProgDict, now: datetime) -> datetime | None:
    """Return the local datetime at which the schedule next changes profile.

    Walks slot boundaries forward from ``now``, wrapping after Sunday; None
    when the programme never changes. ``now`` must be an aware datetime (its
    timezone defines "local"); DST shifts are handled best-effort.
    """
    current = get_current_prog_profile(prog, now)
    local_now = dt_util.as_local(now)
    for day_offset in range(8):
        day = prog.get(str((local_now.weekday() + day_offset) % 7))
        if not day:
            continue
        slot_minutes = _PROG_DAY_MINUTES // len(day)
        if slot_minutes < 1:
            continue
        day_start = (local_now + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        for slot, profile in enumerate(day):
            start = dt_util.as_local(day_start + timedelta(minutes=slot * slot_minutes))
            if start <= local_now:
                continue
            if profile != current:
                return start
    return None
