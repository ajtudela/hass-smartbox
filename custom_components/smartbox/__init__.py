"""The Smartbox integration."""

import asyncio
import contextlib
from dataclasses import dataclass
import logging
import re
import time
from typing import TYPE_CHECKING, Any, Final

import aiohttp
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from smartbox import AsyncSmartboxSession, WsUserSocketSession, check_ws_user_support
from smartbox.error import (
    APIUnavailableError,
    InvalidAuthError,
    SmartboxError,
    WsUserUnsupportedError,
)
from smartbox.retry import backoff_delay

from . import const
from .const import CONF_API_NAME, DOMAIN
from .models import SmartboxDevice, SmartboxNode, get_devices

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant

__version__ = "2.6.0"

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Device-level entity keys that existed on per-node devices before the box
# device was introduced; their registry entries are re-keyed on setup.
_REHOMED_KEYS = ("away_status", "connected", "power_limit")

type SmartboxConfigEntry = ConfigEntry[SmartboxData]


@dataclass
class SmartboxData:
    """Runtime data for the Smartbox class."""

    client: AsyncSmartboxSession
    devices: list[SmartboxDevice]
    nodes: list[SmartboxNode]
    # Shared per-user ws_user socket and its supervision task (None in
    # socket_io mode or when the endpoint was detected unsupported).
    ws_user_socket: WsUserSocketSession | None = None
    ws_user_task: asyncio.Task | None = None


async def create_smartbox_session_from_entry(
    hass: HomeAssistant,
    entry: SmartboxConfigEntry | dict[str, Any],
) -> AsyncSmartboxSession:
    """Create a Session class from smartbox."""
    if isinstance(entry, dict):
        data: dict[str, Any] = entry
    else:
        data = dict(entry.data)
    websession = async_get_clientsession(hass)
    session = AsyncSmartboxSession(
        api_name=data[CONF_API_NAME],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        websession=websession,
    )
    await session.health_check()
    await session.check_refresh_auth()
    return session


def _async_wire_reauth(
    hass: HomeAssistant,
    entry: SmartboxConfigEntry,
) -> None:
    """Wire per-device rejected-credentials signals to the reauth flow.

    A websocket loop dying with rejected credentials surfaces here so the
    entry can start its reauthentication flow (models.py stays entry-free).
    """
    for device in entry.runtime_data.devices:

        @callback
        def _async_reauth_required(_device: SmartboxDevice = device) -> None:
            _LOGGER.warning(
                "Smartbox credentials rejected for device %s; "
                "starting reauthentication flow",
                _device.dev_id,
            )
            entry.async_start_reauth(hass)

        entry.async_on_unload(
            async_dispatcher_connect(
                hass,
                f"{DOMAIN}_{device.dev_id}_reauth_required",
                _async_reauth_required,
            )
        )


def _async_home_box_devices(
    hass: HomeAssistant, entry: SmartboxConfigEntry
) -> dict[str, str]:
    """Create the box device for each configured Smartbox device.

    Returns dev_id -> device registry id. The device's display fields are
    written both here and by the box entities' device_info.
    """
    registry = dr.async_get(hass)
    return {
        device.dev_id: registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device.dev_id)},
            manufacturer=device.session.reseller.name,
            name=device.name,
            model_id=str(device.model_id),
            sw_version=str(device.sw_version),
            serial_number=str(device.serial_number),
            configuration_url=(
                f"{device.session.reseller.web_url}#{device.home['id']}"
            ),
        ).id
        for device in entry.runtime_data.devices
    }


def _async_rehome_device_entities(
    hass: HomeAssistant, entry: SmartboxConfigEntry, box_device_ids: dict[str, str]
) -> None:
    """Move pre-existing device-level registry entries onto the box device.

    Before the box device existed, the away switch, connectivity sensor
    and power limit were created per node with unique_ids
    ``{dev_id}_{addr}_{key}``. Re-key the lowest-addr entry per key to
    ``{dev_id}_{key}`` on the box device (keeping its history and
    settings) and remove the duplicates beyond it. Idempotent: entries
    already on a box device no longer match the per-node pattern.
    """
    entity_registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    for dev_id, box_device_id in box_device_ids.items():
        for key in _REHOMED_KEYS:
            pattern = re.compile(rf"^{re.escape(dev_id)}_(\d+)_{key}$")
            matches = []
            for registry_entry in entries:
                if registry_entry.platform != DOMAIN:
                    continue
                if match := pattern.fullmatch(registry_entry.unique_id):
                    matches.append((int(match.group(1)), registry_entry))
            if not matches:
                continue
            matches.sort(key=lambda item: item[0])
            _, first = matches[0]
            new_unique_id = f"{dev_id}_{key}"
            if (
                entity_registry.async_get_entity_id(first.domain, DOMAIN, new_unique_id)
                is None
            ):
                entity_registry.async_update_entity(
                    first.entity_id,
                    new_unique_id=new_unique_id,
                    device_id=box_device_id,
                )
            else:
                # Target already re-keyed by a partial earlier run: this
                # old per-node entry is itself a leftover duplicate.
                entity_registry.async_remove(first.entity_id)
            for _, duplicate in matches[1:]:
                entity_registry.async_remove(duplicate.entity_id)


# Backoff for restarting the shared ws_user socket after an unexpected
# exit (mirrors the per-device watchdog pattern in models.py).
_WS_USER_WATCHDOG_BASE_SECONDS: Final = 5.0
_WS_USER_WATCHDOG_MAX_SECONDS: Final = 300.0
# A run that lasted this long resets the restart ratchet: seven
# cumulative unexpected exits over weeks must not mean "every future
# socket death leaves the transport down for the full backoff".
_WS_USER_RUN_RESET_SECONDS: Final = 60.0
# Reload hysteresis for a flapping ws_user endpoint (404/200 flip-flop):
# at most _WS_USER_RELOAD_LIMIT reloads per window, then give up
# auto-reloading (manual reload required).
_WS_USER_RELOAD_WINDOW_SECONDS: Final = 600.0
_WS_USER_RELOAD_LIMIT: Final = 3
# Bounded shared-socket teardown, mirroring models.py's _TEARDOWN_TIMEOUT_SECONDS.
_WS_USER_TEARDOWN_TIMEOUT_SECONDS: Final = 10.0


@callback
def _handle_ws_user_unsupported(
    hass: HomeAssistant,
    entry: SmartboxConfigEntry,
) -> bool:
    """Reload the entry after a mid-run ws_user disappearance.

    Returns True when a reload was scheduled (the caller should stop —
    the reload tears the supervisor down), False when it refused.

    Reloads are state-guarded (only a LOADED entry reloads — a reload
    must not resurrect an unloading/disabled entry or race HA shutdown)
    and rate-limited (a flapping backend must not reload-loop forever).
    The mark history lives in ``hass.data`` keyed by entry_id so it
    SURVIVES the reload it schedules — a local counter would be
    destroyed by exactly the reload it limits.
    """
    now = time.monotonic()
    reload_marks: list[float] = hass.data.setdefault(DOMAIN, {}).setdefault(
        f"{entry.entry_id}_ws_user_reload_marks", []
    )
    reload_marks[:] = [
        mark
        for mark in reload_marks
        if now - mark < _WS_USER_RELOAD_WINDOW_SECONDS
    ]
    if entry.state is not ConfigEntryState.LOADED:
        _LOGGER.warning(
            "ws_user endpoint stopped being served, but the entry is not "
            "loaded (state=%s); not reloading", entry.state,
        )
        return False
    if len(reload_marks) >= _WS_USER_RELOAD_LIMIT:
        _LOGGER.error(
            "ws_user endpoint keeps disappearing (%s reloads in %ss); "
            "backing off until the window frees up",
            _WS_USER_RELOAD_LIMIT,
            _WS_USER_RELOAD_WINDOW_SECONDS,
        )
        return False
    reload_marks.append(now)
    _LOGGER.warning(
        "ws_user endpoint stopped being served; reloading the entry to "
        "fall back to socket_io"
    )
    entry.async_create_background_task(
        hass,
        hass.config_entries.async_reload(entry.entry_id),
        "smartbox_ws_user_reload",
    )
    return True


async def _ws_user_restart_pause(run_started: float, restarts: int) -> int:
    """Sleep the next restart backoff; return the incremented counter.

    A long-lived run resets the ratchet (see
    ``_WS_USER_RUN_RESET_SECONDS``); the exponent is saturated by the
    shared backoff helper.
    """
    if time.monotonic() - run_started > _WS_USER_RUN_RESET_SECONDS:
        restarts = 0
    await asyncio.sleep(
        backoff_delay(
            restarts,
            _WS_USER_WATCHDOG_BASE_SECONDS,
            _WS_USER_WATCHDOG_MAX_SECONDS,
        )
    )
    return restarts + 1


async def _supervise_ws_user_socket(
    hass: HomeAssistant, entry: SmartboxConfigEntry
) -> None:
    """Run and supervise the shared per-user ws_user socket.

    Mirrors the per-device watchdog pattern: unexpected exits restart the
    socket loop with capped backoff; rejected credentials surface the
    per-device reauth signals (the entry's reauth wiring picks them up).

    A mid-run :class:`WsUserUnsupportedError` (ws_user disappeared after
    the setup probe passed, e.g. a backend rollback) reloads the config
    entry: setup then re-probes the endpoint and lands every device on
    the socket_io transport with fresh managers — the same path as the
    initial fallback, without rebuilding managers here.
    """
    socket_ = entry.runtime_data.ws_user_socket
    if socket_ is None:
        return
    restarts = 0

    def _signal_reauth() -> None:
        """Rejected credentials: surface the per-device reauth signals."""
        # An expected condition (the entry's reauth wiring handles it),
        # logged as a warning — a traceback here would be noise.
        _LOGGER.warning(
            "Credentials rejected on the shared ws_user socket; "
            "requesting reauthentication"
        )
        for device in entry.runtime_data.devices:
            async_dispatcher_send(
                hass, f"{DOMAIN}_{device.dev_id}_reauth_required"
            )

    # Run/exit model: run() returns only when exit was requested
    # (teardown); exceptions propagate out and are handled here. A
    # WsUserSocketSession is re-runnable — the except Exception path
    # below restarts it in place. CancelledError (BaseException) is not
    # caught and propagates.
    while True:
        run_started = time.monotonic()
        try:
            await socket_.run()
        except WsUserUnsupportedError:
            # ws_user disappeared mid-flight (backend rollback): reload
            # the entry — setup re-probes and falls back to socket_io.
            # A refused reload (limit tripped / entry not loaded) keeps
            # THIS supervisor alive: parking on a dead transport with
            # every device manager parked would freeze the entities as
            # silently-stale forever; the reload window prunes after
            # _WS_USER_RELOAD_WINDOW_SECONDS, at which point the retry
            # below gets a fresh reload.
            if _handle_ws_user_unsupported(hass, entry):
                return
        except InvalidAuthError:
            _signal_reauth()
            return
        except Exception:
            _LOGGER.exception(
                "Shared ws_user socket exited unexpectedly; restarting"
            )
        else:
            # run() returned normally: exit was requested (teardown/cancel).
            return
        restarts = await _ws_user_restart_pause(run_started, restarts)


async def _async_cancel_ws_user_socket(entry: SmartboxConfigEntry) -> None:
    """Cancel the shared ws_user socket and its supervision task, bounded.

    The supervision task is cancelled FIRST so it cannot restart the
    socket behind this teardown; the socket's ``cancel()`` is a
    coroutine bounded by the library's own teardown timeouts. Unload
    awaits this; the HA-stop listener wraps it in a task (teardown then
    races shutdown, which is fine — cancel is idempotent and bounded).
    """
    runtime_data: SmartboxData | None = getattr(entry, "runtime_data", None)
    if runtime_data is None:
        return
    if runtime_data.ws_user_task is not None and not runtime_data.ws_user_task.done():
        runtime_data.ws_user_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime_data.ws_user_task
    if runtime_data.ws_user_socket is not None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                runtime_data.ws_user_socket.cancel(),
                _WS_USER_TEARDOWN_TIMEOUT_SECONDS,
            )


async def _async_create_ws_user_socket(
    entry: SmartboxConfigEntry,
) -> WsUserSocketSession | None:
    """Create the shared per-user socket, probing ws_user support once.

    Returns None when the backend is socket_io or the endpoint was
    detected unsupported (deterministic rejection only) — the fallback
    doctrine: transient probe trouble propagates and retries via
    ConfigEntryNotReady, exactly like every other setup step; it must
    never be mistaken for "endpoint unsupported". Replaces the
    per-device transports' failure modes with ConfigEntryAuthFailed /
    ConfigEntryNotReady as appropriate.
    """
    if const.SMARTBOX_WS_BACKEND != "ws_user":
        return None
    try:
        await check_ws_user_support(entry.runtime_data.client)
    except WsUserUnsupportedError as ex:
        _LOGGER.info("API host does not serve ws_user (%s); using socket_io", ex)
        return None
    return WsUserSocketSession(entry.runtime_data.client)


async def async_setup_entry(hass: HomeAssistant, entry: SmartboxConfigEntry) -> bool:
    """Set up Smartbox from a config entry."""
    try:
        entry.runtime_data = SmartboxData(
            client=(await create_smartbox_session_from_entry(hass, entry)),
            devices=[],
            nodes=[],
        )
    except InvalidAuthError as ex:
        raise ConfigEntryAuthFailed from ex
    except (SmartboxError, APIUnavailableError) as ex:
        raise ConfigEntryNotReady from ex

    try:
        entry.runtime_data.ws_user_socket = (
            await _async_create_ws_user_socket(entry)
        )
        devices = await get_devices(
            session=entry.runtime_data.client,
            hass=hass,
            ws_user_socket=entry.runtime_data.ws_user_socket,
        )
    except InvalidAuthError as ex:
        raise ConfigEntryAuthFailed from ex
    except (
        SmartboxError,
        APIUnavailableError,
        OSError,
        aiohttp.ClientError,
    ) as ex:
        # aiohttp.ClientError: defense in depth for transport-shaped
        # exceptions that are neither SmartboxError nor OSError (the
        # probe maps its own; unexpected shapes must still be a retry).
        raise ConfigEntryNotReady from ex
    for device in devices:
        _LOGGER.info("Setting up configured device %s", device.dev_id)
        entry.runtime_data.devices.append(device)
    for device in entry.runtime_data.devices:
        nodes = device.get_nodes()
        _LOGGER.debug("Configuring nodes for device %s %s", device.dev_id, nodes)
        entry.runtime_data.nodes.extend(nodes)

    # A websocket loop dying with rejected credentials surfaces here so the
    # entry can start its reauthentication flow (models.py stays entry-free).
    _async_wire_reauth(hass, entry)

    if entry.runtime_data.ws_user_socket is not None:
        entry.runtime_data.ws_user_task = asyncio.create_task(
            _supervise_ws_user_socket(hass, entry),
            name="smartbox_ws_user",
        )

    # The box itself is a Home Assistant device; create it (and re-home the
    # device-level registry entries from the per-node devices) before the
    # platforms attach their entities to it.
    box_device_ids = _async_home_box_devices(hass, entry)
    _async_rehome_device_entities(hass, entry, box_device_ids)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _async_cancel_devices_on_hass_stop(_event: Event) -> None:
        """Stop the device websocket sessions when Home Assistant stops."""
        for device in entry.runtime_data.devices:
            hass.async_create_task(device.cancel())
        hass.async_create_task(_async_cancel_ws_user_socket(entry))

    entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, _async_cancel_devices_on_hass_stop
        )
    )
    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmartboxConfigEntry) -> bool:
    """Unload a config entry."""
    # runtime_data only exists once setup got far enough to build the session;
    # a failed/retrying entry must still unload cleanly (the UI reload path).
    runtime_data: SmartboxData | None = getattr(entry, "runtime_data", None)
    if runtime_data is not None:
        for device in runtime_data.devices:
            await device.cancel()
        # Await the bounded shared-socket teardown so the entry never
        # finishes unloading while its transport is still tearing down.
        await _async_cancel_ws_user_socket(entry)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def update_listener(hass: HomeAssistant, entry: SmartboxConfigEntry) -> None:
    """Reload entity from config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
