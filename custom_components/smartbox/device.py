"""The Smartbox device (box): websocket supervision and its nodes."""

import asyncio
from contextlib import suppress
import logging
from typing import TYPE_CHECKING, Any, Final, cast

from homeassistant.helpers.dispatcher import async_dispatcher_send
from smartbox import (
    AsyncSmartboxSession,
    SmartboxNodeType,
    UpdateManager,
    WsUserSocketSession,
)
from smartbox.error import APIUnavailableError, InvalidAuthError, SmartboxError

from .const import DOMAIN, SMARTBOX_UNAVAILABLE_DELAY, SMARTBOX_WRITE_CONFIRM_TIMEOUT
from .node import SmartboxNode
from .protocol import Device, Node, SetupDict, StatusDict, normalize_prog
from .supervisor import TransportSupervisor

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_NODE_CREATE_CONCURRENCY = 3

# Backoff for restarting the update-manager task after an unexpected exit
# (run() itself waits out transient trouble; this only bounds how fast we
# retry when it died of something unexpected).
_WATCHDOG_RESTART_BASE_SECONDS: Final = 5.0
_WATCHDOG_RESTART_MAX_SECONDS: Final = 300.0


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
        self._hass = hass
        self._connected_status: bool | None = None
        self.update_manager: UpdateManager = UpdateManager(
            self._session,
            self.dev_id,
            unavailable_delay=SMARTBOX_UNAVAILABLE_DELAY,
            write_confirm_timeout=SMARTBOX_WRITE_CONFIRM_TIMEOUT,
            ws_user_socket=self._ws_user_socket,
        )
        self._supervisor = TransportSupervisor(
            lambda coro, name: self._hass.async_create_background_task(coro, name),  # noqa: PLW0108
            lambda: self.update_manager.run(),  # noqa: PLW0108
            name=f"smartbox_update_{self.dev_id}",
            base_delay=_WATCHDOG_RESTART_BASE_SECONDS,
            max_delay=_WATCHDOG_RESTART_MAX_SECONDS,
            cancel=lambda: self.update_manager.cancel(),  # noqa: PLW0108
            on_error=self._on_update_error,
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
        self._supervisor.start()
        return self

    async def cancel(self) -> None:
        """Stop the update task and disconnect, bounded.

        Deliberately exception-proof: unload (UI reload) and the HA-stop hook
        must always complete promptly even when the websocket session is
        wedged (see :class:`TransportSupervisor`).
        """
        await self._supervisor.stop()

    def _connected(self, connected: bool) -> None:
        _LOGGER.debug("Connected connected update: %s", connected)
        self._connected_status = connected
        if connected:
            # A (re)connection succeeded: any restart backoff can reset.
            self._supervisor.reset_backoff()
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
        SmartboxNode.set_status go through this single dispatcher event.
        Entities read the node's own status, so the payload only signals
        that something changed.
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
        prog = normalize_prog(payload)
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

    def _on_update_error(self, exc: Exception) -> bool:
        """Handle the update task dying; return True to stop supervising.

        InvalidAuthError means the credentials were rejected after the
        library's automatic re-auth-and-resend also failed: request a
        reauthentication flow. Anything else is unexpected and gets restarted
        by the supervisor with capped backoff.
        """
        if not isinstance(exc, InvalidAuthError):
            return False
        _LOGGER.error(
            "Credentials rejected for device %s; requesting reauthentication",
            self.dev_id,
        )
        async_dispatcher_send(self._hass, f"{DOMAIN}_{self.dev_id}_reauth_required")
        return True

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
