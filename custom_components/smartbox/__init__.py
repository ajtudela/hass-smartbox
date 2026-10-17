"""The Smartbox integration."""

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

from . import const
from .const import CONF_API_NAME, DOMAIN
from .device import SmartboxDevice, get_devices
from .supervisor import TransportSupervisor

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant

    from .node import SmartboxNode

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
    # Shared per-user ws_user socket and its supervisor (None in socket_io
    # mode or when the endpoint was detected unsupported).
    ws_user_socket: WsUserSocketSession | None = None
    ws_user_supervisor: TransportSupervisor | None = None


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
    entry can start its reauthentication flow (device.py stays entry-free).
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
# exit (mirrors the per-device supervisor in device.py).
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
# Bounded shared-socket teardown, mirroring the supervisor teardown timeout.
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
        mark for mark in reload_marks if now - mark < _WS_USER_RELOAD_WINDOW_SECONDS
    ]
    if entry.state is not ConfigEntryState.LOADED:
        _LOGGER.warning(
            "ws_user endpoint stopped being served, but the entry is not "
            "loaded (state=%s); not reloading",
            entry.state,
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


def _create_ws_user_supervisor(
    hass: HomeAssistant, entry: SmartboxConfigEntry
) -> TransportSupervisor:
    """Build the supervisor of the shared per-user ws_user socket.

    Unexpected exits restart the socket loop with capped backoff; rejected
    credentials surface the per-device reauth signals (the entry's reauth
    wiring picks them up).

    A mid-run :class:`WsUserUnsupportedError` (ws_user disappeared after
    the setup probe passed, e.g. a backend rollback) reloads the config
    entry: setup then re-probes the endpoint and lands every device on
    the socket_io transport with fresh managers. A refused reload (limit
    tripped / entry not loaded) keeps THIS supervisor alive: parking on a
    dead transport with every device manager parked would freeze the
    entities as silently-stale forever; the reload window prunes after
    ``_WS_USER_RELOAD_WINDOW_SECONDS``, at which point the retry gets a
    fresh reload.
    """
    socket_ = entry.runtime_data.ws_user_socket
    if socket_ is None:
        msg = "ws_user supervisor requested without a ws_user socket"
        raise ValueError(msg)

    def _on_error(exc: Exception) -> bool:
        if isinstance(exc, WsUserUnsupportedError):
            return _handle_ws_user_unsupported(hass, entry)
        if isinstance(exc, InvalidAuthError):
            # An expected condition (the entry's reauth wiring handles it),
            # logged as a warning: a traceback here would be noise.
            _LOGGER.warning(
                "Credentials rejected on the shared ws_user socket; "
                "requesting reauthentication"
            )
            for device in entry.runtime_data.devices:
                async_dispatcher_send(hass, f"{DOMAIN}_{device.dev_id}_reauth_required")
            return True
        return False

    return TransportSupervisor(
        lambda coro, name: entry.async_create_background_task(hass, coro, name),
        socket_.run,
        name="smartbox_ws_user",
        base_delay=_WS_USER_WATCHDOG_BASE_SECONDS,
        max_delay=_WS_USER_WATCHDOG_MAX_SECONDS,
        cancel=socket_.cancel,
        on_error=_on_error,
        reset_after=_WS_USER_RUN_RESET_SECONDS,
        teardown_timeout=_WS_USER_TEARDOWN_TIMEOUT_SECONDS,
    )


async def _async_cancel_ws_user_socket(entry: SmartboxConfigEntry) -> None:
    """Stop the shared ws_user socket and its supervision, bounded.

    The supervisor flags the stop first so it cannot restart the socket
    behind this teardown, then cancels the socket (bounded by the library's
    own teardown timeouts). Unload awaits this; the HA-stop listener wraps it
    in a task (teardown then races shutdown, which is fine: stop is
    idempotent and bounded).
    """
    runtime_data: SmartboxData | None = getattr(entry, "runtime_data", None)
    if runtime_data is None or runtime_data.ws_user_supervisor is None:
        return
    await runtime_data.ws_user_supervisor.stop()


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
        entry.runtime_data.ws_user_socket = await _async_create_ws_user_socket(entry)
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
    # entry can start its reauthentication flow (device.py stays entry-free).
    _async_wire_reauth(hass, entry)

    if entry.runtime_data.ws_user_socket is not None:
        supervisor = _create_ws_user_supervisor(hass, entry)
        entry.runtime_data.ws_user_supervisor = supervisor
        supervisor.start()

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
    # Platforms first: when they refuse to unload the entry stays loaded
    # (FAILED_UNLOAD) with live entities, which must keep their transport.
    if not (
        unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    ):
        return False
    # runtime_data only exists once setup got far enough to build the session;
    # a failed/retrying entry must still unload cleanly (the UI reload path).
    runtime_data: SmartboxData | None = getattr(entry, "runtime_data", None)
    if runtime_data is not None:
        for device in runtime_data.devices:
            await device.cancel()
        # Await the bounded shared-socket teardown so the entry never
        # finishes unloading while its transport is still tearing down.
        await _async_cancel_ws_user_socket(entry)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: SmartboxConfigEntry) -> None:
    """Drop the per-entry state kept in ``hass.data`` across reloads."""
    domain_data = hass.data.get(DOMAIN)
    if domain_data is not None:
        domain_data.pop(f"{entry.entry_id}_ws_user_reload_marks", None)


async def update_listener(hass: HomeAssistant, entry: SmartboxConfigEntry) -> None:
    """Reload entity from config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
