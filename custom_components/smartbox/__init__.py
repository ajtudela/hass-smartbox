"""The Smartbox integration.

Component lifecycle. The protocol and core layers own auth, transport and
state; this module wires them per config entry and keeps the HA-facing
behavior stable (setup/unload/reload, reauth, in-place ws_user recovery,
diagnostics).

Setup is AuthConfig -> TokenManager (via the client) -> SmartboxClient ->
SmartboxEngine -> ws_user probe -> identity discovery -> WsUserTransport ->
supervise -> monitor -> platforms. Entity identity comes from
:mod:`.identity` (plain REST reads); all state reads and writes go through
the engine. Component-level setup additionally serves the optional bundled
programme card when its JavaScript asset ships with the integration.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Final

import aiohttp
from homeassistant.components.frontend import add_extra_js_url

# HA's component root re-exports StaticPathConfig without __all__; mypy
# strict's no_implicit_reexport cannot see that re-export.
from homeassistant.components.http import StaticPathConfig  # type: ignore[attr-defined]
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from smartbox.core.engine import SUPERVISE_RUN_RESET_SECONDS, SmartboxEngine
from smartbox.protocol import (
    AuthConfig,
    AuthError,
    ManagedTokenSource,
    SmartboxClient,
    SmartboxError,
    TokenManager,
    TransportUnsupportedError,
    WsUserTransport,
    check_ws_user_support,
    timing,
)
from smartbox.protocol.retry import backoff_delay

from .adapter import EngineChangeFanOut
from .const import DOMAIN, SMARTBOX_UNAVAILABLE_DELAY, SMARTBOX_WRITE_CONFIRM_TIMEOUT
from .devices import async_ensure_box_devices
from .entity_builder import EntityBuilder
from .history import HistoryImport
from .identity import SmartboxIdentity, async_discover_identity
from .membership import MembershipWatcher, clear_node_added_issue
from .reseller import api_base_url, reseller_from_entry_data
from .services import ServiceTargets, async_register_services, async_unregister_services
from .token_store import TokenStore, token_store_for

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import Event, HomeAssistant

__version__ = "3.1.2"

_LOGGER = logging.getLogger(__name__)

# Bundled Lovelace card: served from the integration and auto-loaded by the
# frontend, so it needs no separate HACS item or Lovelace resource. The file
# check keeps a build that omits the asset from advertising a module that
# would 404 on every page.
_FRONTEND_URL = f"/{DOMAIN}_frontend"
_FRONTEND_DIR = Path(__file__).parent / "frontend"
_CARD_JS = _FRONTEND_DIR / "smartbox-program-card.js"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Device-level entity keys that predate the box device: their registry
# entries are re-keyed onto it on setup so entity ids and history survive.
_REHOMED_KEYS = ("away_status", "connected", "power_limit")

type SmartboxConfigEntry = ConfigEntry[SmartboxRuntime]


@dataclass(frozen=True)
class SmartboxRuntime:
    """Runtime data (one import surface, no globals).

    Stored on ``entry.runtime_data`` for the entity platforms. Runtime state
    does not outlive the entry: the ws_user recovery streak lives on the
    monitor task, so a reload rebuilds it. The token-store lock table in
    ``hass.data`` is the one cross-reload coordination object; it serializes
    storage access and is not runtime state.
    """

    engine: SmartboxEngine
    client: SmartboxClient
    transport: WsUserTransport
    auth: TokenManager
    token_store: TokenStore
    # "restored" when setup resumed the stored bundle, "granted" when it
    # minted a fresh session (first run, dead storage, or a rejected restore).
    token_origin: str
    identity: SmartboxIdentity
    fanout: EngineChangeFanOut
    membership: MembershipWatcher
    entity_builder: EntityBuilder
    history: HistoryImport
    # The entry data this runtime was built from. A reauth (or any data
    # change) must rebuild the client, so the update listener compares it.
    entry_data: Mapping[str, Any]
    # Where platforms publish the entities the domain services act on (the
    # services are registered once for the whole domain and fan out across
    # loaded entries).
    services: ServiceTargets = field(default_factory=ServiceTargets)


def auth_config_from_entry_data(data: Mapping[str, Any]) -> AuthConfig:
    """Build the protocol-layer AuthConfig from config-entry data.

    The reseller table supplies the endpoint identity: the Basic auth string
    is passed VERBATIM, the reseller identity travels only via ``x-serialid``
    / ``x-referer``, and the REST host is the row's ``api_host`` (normally
    ``users-api.helki.com``; Elnur uses ``api-elnur.helki.com``). The row's
    ``api_url`` is the brand key, not a host. ``RESELLER_OVERRIDES`` can
    replace the documented auth fields for manual testing.
    """
    reseller = reseller_from_entry_data(data)
    return AuthConfig(
        api_host=api_base_url(reseller),
        basic_auth_creds=reseller.basic_auth,
        serial_id=str(reseller.serial_id),
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        referer=reseller.web_url or None,
    )


def _async_rehome_device_entities(
    hass: HomeAssistant, entry: SmartboxConfigEntry, box_device_ids: dict[str, str]
) -> None:
    """Move pre-existing device-level registry entries onto the box device.

    Older versions created the away switch, connectivity sensor and power
    limit per node, with unique_ids ``{dev_id}_{addr}_{key}``. The lowest-addr
    entry per key is re-keyed to ``{dev_id}_{key}`` on the box device, keeping
    its history and settings, and the duplicates beyond it are removed.
    Idempotent: entries already on a box device no longer match the pattern.
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


# In-place ws_user recovery (see _async_monitor_supervision): a mid-run
# disappearance is re-probed by re-supervising the restartable transport. The
# long-run threshold that resets the failure streak is the engine's own, so the
# two supervision layers cannot disagree about what "a long-lived run" means.
_WS_USER_RUN_RESET_SECONDS: Final = SUPERVISE_RUN_RESET_SECONDS
_WS_USER_PROBE_BASE_SECONDS: Final = 5.0
_WS_USER_PROBE_MAX_SECONDS: Final = 300.0
# Consecutive failed re-probes before the repair issue is raised. The
# monitor keeps re-probing regardless (a backend that comes back must be
# picked up), so this only decides when the user is told.
_WS_USER_PROBE_ISSUE_AFTER: Final = 3
# Bounded shared-transport teardown for unload/HA-stop: 3x the transport's
# internal _TEARDOWN_TIMEOUT (5 s, ws_user.py) so the outer wait_for cannot
# fire mid-ws-close and leak the socket.
_WS_USER_TEARDOWN_TIMEOUT_SECONDS: Final = 15.0
# Repair issue raised while the endpoint keeps refusing re-probes.
_WS_USER_FLAPPING_ISSUE = "ws_user_flapping"


@callback
def ws_user_flapping_issue_id(entry: SmartboxConfigEntry) -> str:
    """Per-entry repair issue id for the refusing ws_user endpoint.

    Public: diagnostics reports the issue's presence.
    """
    return f"{entry.entry_id}_{_WS_USER_FLAPPING_ISSUE}"


@callback
def _create_flapping_issue(
    hass: HomeAssistant, entry: SmartboxConfigEntry, exc: TransportUnsupportedError
) -> None:
    """Raise the repair issue for a ws_user endpoint that keeps refusing."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        ws_user_flapping_issue_id(entry),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=_WS_USER_FLAPPING_ISSUE,
        translation_placeholders={"error": str(exc)},
    )


@callback
def _delete_flapping_issue(hass: HomeAssistant, entry: SmartboxConfigEntry) -> None:
    """Clear the repair issue (the endpoint answered a re-probe again)."""
    ir.async_delete_issue(hass, DOMAIN, ws_user_flapping_issue_id(entry))


async def _ws_user_probe_pause(failures: int) -> None:
    """Sleep the next in-place re-probe backoff (capped, saturating)."""
    await timing.sleep(
        backoff_delay(
            failures - 1,
            _WS_USER_PROBE_BASE_SECONDS,
            _WS_USER_PROBE_MAX_SECONDS,
        )
    )


async def _async_monitor_supervision(
    hass: HomeAssistant,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
) -> None:
    """Monitor the engine's supervision task for its TERMINAL outcomes.

    The engine restarts every transient transport exit itself; only a clean
    close (the transport was closed for teardown), a mid-run
    ``TransportUnsupportedError`` (the endpoint stopped being served, e.g. a
    backend rollback) and rejected credentials surface here.

    A mid-run disappearance is recovered IN PLACE: the transport is
    restartable and its own handshake IS the capability probe, so the monitor
    re-supervises it behind a capped backoff. Nothing in the runtime is
    invalidated by a transport-level outage, and reloading the entry from here
    has no safe owner.

    The monitor does not give up - a backend that comes back is picked up by
    the next re-probe. Reaching ``_WS_USER_PROBE_ISSUE_AFTER`` raises the
    repair issue once, and a long-lived run clears it. The streak is
    monitor-local because nothing reloads the monitor.

    Rejected credentials start the entry's reauth flow.
    """
    engine = runtime.engine
    failures = 0
    issue_raised = False
    while True:
        run_started = timing.monotonic()
        task = engine.supervision_task
        if task is None:
            return
        done, _pending = await asyncio.wait({task})
        finished = next(iter(done))
        if finished.cancelled():
            return  # teardown (engine.stop()) or entry unload
        exc = finished.exception()
        if exc is None:
            # run() returned cleanly: the transport was closed (teardown).
            return
        if isinstance(exc, AuthError):
            # An expected condition (the reauth flow handles recovery),
            # logged as a warning — a traceback here would be noise.
            _LOGGER.warning(
                "Credentials rejected on the smartbox websocket; "
                "requesting reauthentication"
            )
            entry.async_start_reauth(hass)
            return
        if not isinstance(exc, TransportUnsupportedError):
            # The engine restarts any other exit internally; nothing to do.
            return
        if timing.monotonic() - run_started > _WS_USER_RUN_RESET_SECONDS:
            # A long-lived run means the endpoint served: the streak is over
            # and the repair issue no longer applies.
            failures = 0
            if issue_raised:
                issue_raised = False
                _delete_flapping_issue(hass, entry)
        failures += 1
        if failures >= _WS_USER_PROBE_ISSUE_AFTER and not issue_raised:
            issue_raised = True
            _LOGGER.error(
                "ws_user endpoint stopped being served (%s failed re-probes); "
                "still re-probing with a capped backoff",
                failures,
            )
            _create_flapping_issue(hass, entry, exc)
        _LOGGER.warning(
            "ws_user endpoint stopped being served (%s); re-probing in place",
            exc,
        )
        await _ws_user_probe_pause(failures)
        engine.supervise(runtime.transport)


async def _async_teardown_runtime(runtime: SmartboxRuntime) -> None:
    """Bounded teardown: stop supervision, then close the transport.

    ``engine.stop()`` cancels the supervision task FIRST so it cannot restart
    the transport behind this teardown; the transport's bounded ``close()`` is
    awaited here, so unload never finishes mid-teardown. The HA-stop listener
    wraps this in a task and races shutdown, which is safe: both steps are
    idempotent and bounded.
    """
    runtime.membership.close()
    try:
        await runtime.engine.stop()
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                runtime.transport.close(), _WS_USER_TEARDOWN_TIMEOUT_SECONDS
            )
        # Retire the store last: grants that completed during teardown were
        # still worth saving, later ones must not undo a credential-change clear.
        runtime.token_store.close()


async def _async_build_runtime(
    hass: HomeAssistant, entry: SmartboxConfigEntry
) -> SmartboxRuntime:
    """Wire the stack: probe ws_user support FIRST, then discover identity.

    AuthConfig -> TokenManager (via the client) -> SmartboxClient ->
    SmartboxEngine (write host) -> WsUserTransport -> REST identity
    discovery (:mod:`.identity`). The
    capability probe runs before anything is supervised: one token (the stored
    bundle when present, otherwise a password grant), then the one-shot
    ``check_ws_user_support``. A restored token that the endpoint rejects is
    replaced by a fresh grant and probed once more, since the 401/403 verdict
    is only deterministic for a fresh grant. There is no socket_io transport,
    so a deterministic probe rejection fails setup, while transient probe
    trouble propagates raw and retries via ``ConfigEntryNotReady``. Protocol-layer
    errors propagate raw for the caller to map.
    """
    config = auth_config_from_entry_data(entry.data)
    websession = async_get_clientsession(hass)
    token_store = token_store_for(hass, entry.entry_id, entry.data)
    stored = await token_store.async_load()
    client = SmartboxClient(
        config,
        session=websession,
        on_grant=token_store.async_save,
        initial_tokens=stored,
    )
    auth = client.auth
    # Resume the previous session when a bundle survived the restart: the
    # refresh grant keeps the server-side session, so restarts stop creating
    # a new "login" row. Missing or unusable storage falls back to the
    # password grant exactly as before.
    token_bundle = await auth.get_token()
    try:
        await check_ws_user_support(
            token_bundle.access_token, config.api_host, client.session
        )
    except TransportUnsupportedError:
        if not auth.seed_accepted:
            raise
        # A 401/403 verdict is only deterministic for a fresh grant; a stored
        # access token may have been revoked in the vendor app since.
        _LOGGER.debug(
            "ws_user probe rejected the restored token; retrying with a fresh grant"
        )
        await auth.invalidate(token_bundle.access_token)
        token_bundle = await auth.get_token()
        await check_ws_user_support(
            token_bundle.access_token, config.api_host, client.session
        )
    # A password grant creates a vendor login row; a refresh continues the
    # stored session. Report what actually happened, not what was on disk.
    origin = (
        "restored"
        if auth.seed_accepted and auth.last_grant != "password"
        else "granted"
    )
    if origin == "restored":
        _LOGGER.debug(
            "Resumed stored smartbox session for entry %s (expires %s)",
            entry.entry_id,
            token_bundle.expires_at.isoformat(),
        )
    else:
        _LOGGER.debug("Minted a fresh smartbox session for entry %s", entry.entry_id)
    identity = await async_discover_identity(
        client, reseller_from_entry_data(entry.data)
    )
    engine = SmartboxEngine(
        client,
        unavailable_delay=SMARTBOX_UNAVAILABLE_DELAY,
        write_confirm_timeout=SMARTBOX_WRITE_CONFIRM_TIMEOUT,
    )
    transport = WsUserTransport(
        ManagedTokenSource(auth),
        config.api_host,
        session=websession,
    )
    services = ServiceTargets()
    entity_builder = EntityBuilder(identity, services)
    history = HistoryImport((node.node_id for node in identity.nodes), entry.options)
    # Membership first: its NodeAdded handler runs before the entity fan-out,
    # so an in-place entity add sees the addition before the slot changes.
    membership = MembershipWatcher(
        hass,
        entry,
        engine,
        identity,
        entity_builder,
        client,
        (
            identity.devices[0].reseller
            if identity.devices
            else reseller_from_entry_data(entry.data)
        ),
        history,
    )
    return SmartboxRuntime(
        engine=engine,
        client=client,
        transport=transport,
        auth=auth,
        token_store=token_store,
        token_origin=origin,
        identity=identity,
        # ONE fan-out (one engine subscription) per config entry, shared
        # by every platform's entities (see adapter.py module docstring).
        fanout=EngineChangeFanOut(engine),
        membership=membership,
        entity_builder=entity_builder,
        services=services,
        history=history,
        entry_data=dict(entry.data),
    )


async def async_setup(hass: HomeAssistant, _config: dict[str, Any]) -> bool:
    """Serve the bundled programme card when the frontend is available.

    Component-level setup runs once per process. The card is optional: a
    headless setup has no frontend, and a build that omits the asset must not
    advertise a module that would 404. With the frontend present, the static
    path and the versioned module URL are registered once; the URL's ``?v=``
    lets the module be cached aggressively and still change on every release.
    """
    if "frontend" not in hass.config.components or not _CARD_JS.is_file():
        return True
    await hass.http.async_register_static_paths(
        [StaticPathConfig(_FRONTEND_URL, str(_FRONTEND_DIR), cache_headers=True)]
    )
    add_extra_js_url(hass, f"{_FRONTEND_URL}/{_CARD_JS.name}?v={__version__}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SmartboxConfigEntry) -> bool:
    """Set up Smartbox from a config entry."""
    try:
        runtime = await _async_build_runtime(hass, entry)
    except AuthError as ex:
        raise ConfigEntryAuthFailed from ex
    except TransportUnsupportedError as ex:
        # Deterministic probe rejection: there is no socket_io
        # fallback, so the entry cannot work on this host.
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="ws_user_unsupported",
            translation_placeholders={"error": str(ex)},
        ) from ex
    except (
        SmartboxError,
        OSError,
        aiohttp.ClientError,
    ) as ex:
        # aiohttp.ClientError: defense in depth for transport-shaped
        # exceptions that are neither SmartboxError nor OSError (the
        # probe maps its own; unexpected shapes must still be a retry).
        raise ConfigEntryNotReady from ex

    # Runtime lives ONLY on entry.runtime_data (single home): the recovery
    # streak lives on the monitor task, which nothing reloads any more, so
    # there is no cross-reload state to keep in hass.data.
    entry.runtime_data = runtime
    # a successful (re)load means the endpoint is serving: clear any repair
    # issue left over from an earlier refused-probe episode
    _delete_flapping_issue(hass, entry)
    # ...and a reload is exactly what materializes nodes detected since the
    # previous setup, so the reload hint is obsolete now.
    clear_node_added_issue(hass, entry)
    runtime.engine.supervise(runtime.transport)
    entry.async_create_background_task(
        hass,
        _async_monitor_supervision(hass, entry, runtime),
        "smartbox_ws_user",
    )
    # The transport is live from here, so a setup that fails below (a platform
    # that raises, a cancelled setup) must stop it: HA runs the on-unload
    # callbacks for a failed setup too, and the monitor above is entry-tracked,
    # but the supervision task is not - without this it would keep the socket
    # open against a runtime nothing owns. A normal unload tears down twice
    # (here and in ``async_unload_entry``), which both steps absorb.
    entry.async_on_unload(lambda: _async_teardown_runtime(runtime))
    # Register the options listener BEFORE platforms: a sensor can flip the
    # first-import flag to AUTO while its initial import runs, and a listener
    # registered afterwards would miss that change and leave the mirror stale.
    entry.async_on_unload(entry.add_update_listener(update_listener))

    # The box itself is a Home Assistant device; create it (and re-home the
    # device-level registry entries from the per-node devices) before the
    # platforms attach their entities to it.
    box_device_ids = async_ensure_box_devices(
        hass, entry.entry_id, runtime.identity.devices
    )
    _async_rehome_device_entities(hass, entry, box_device_ids)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Every platform has registered its builders; membership additions can
    # now be materialized in place.
    runtime.entity_builder.mark_ready()
    # Setup discovery is a full membership list, so registry devices it no
    # longer contains start the same confirmed-removal path as live loss.
    runtime.membership.async_start()

    # Domain services are registered once, after the platforms published
    # their entities on runtime.services. Each entry adds the same cleanup
    # callback; the helper keeps the services while any entry stays loaded.
    async_register_services(hass)
    entry.async_on_unload(lambda: async_unregister_services(hass))

    @callback
    def _async_cancel_websockets_on_hass_stop(_event: Event) -> None:
        """Stop the supervised transport when Home Assistant stops."""
        runtime = entry.runtime_data
        if runtime is not None:
            hass.async_create_task(_async_teardown_runtime(runtime))

    entry.async_on_unload(
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP, _async_cancel_websockets_on_hass_stop
        )
    )
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,  # noqa: ARG001 - Home Assistant hook signature
    entry: SmartboxConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow deleting a device the account no longer holds (stale-devices).

    A live device is refused: the integration removes it itself once the
    backend confirms the absence. With no loaded runtime there is no
    membership to compare, so the user-initiated delete is allowed; the
    deleted registry entries restore the device if it is paired again.
    """
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        return True
    live = runtime.membership.live_identifiers
    return not any(
        domain == DOMAIN and identifier in live
        for domain, identifier in device_entry.identifiers
    )


async def async_unload_entry(hass: HomeAssistant, entry: SmartboxConfigEntry) -> bool:
    """Unload a config entry."""
    # Platforms FIRST: a refused platform unload keeps the entry LOADED, and
    # an entry that stays loaded must keep a live transport - tearing the
    # runtime down first would leave its entities frozen on their last value
    # with no supervision left to recover them. Pause membership before that
    # window so a late snapshot cannot add entities to a platform mid-unload.
    runtime = entry.runtime_data
    if runtime is not None:
        await runtime.membership.async_pause()
    unloaded = False
    try:
        unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if unloaded and runtime is not None:
            # Await the bounded teardown so the entry never finishes unloading
            # while its transport is still tearing down. (HA itself clears
            # entry.runtime_data once the platforms unloaded.)
            await _async_teardown_runtime(runtime)
            if dict(entry.data) != dict(runtime.entry_data):
                # Credentials changed (reauth/reconfigure): the reloaded
                # runtime must mint its own session. Clearing after teardown
                # also discards any rotation the retired runtime completed
                # during the window.
                await token_store_for(hass, entry.entry_id, entry.data).async_clear()
    finally:
        if runtime is not None and not unloaded:
            runtime.membership.resume()
    if unloaded:
        # Nothing probes the endpoint for an unloaded entry; a stale repair
        # issue would keep pointing at an endpoint the user no longer runs.
        _delete_flapping_issue(hass, entry)
        clear_node_added_issue(hass, entry)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: SmartboxConfigEntry) -> None:
    """Drop the entry's repair issues and its stored tokens."""
    ir.async_delete_issue(hass, DOMAIN, ws_user_flapping_issue_id(entry))
    clear_node_added_issue(hass, entry)
    await token_store_for(hass, entry.entry_id, entry.data).async_clear()


async def update_listener(hass: HomeAssistant, entry: SmartboxConfigEntry) -> None:
    """Reload the entry when the options the runtime was built on changed.

    The first-setup history flag is entry-global state the sensors write
    themselves, so a change to it alone must not reload: the reload would tear
    down the websocket and cancel the imports of the nodes that have not
    finished (see :mod:`.history`).
    """
    # HA deletes ``runtime_data`` when an entry unloads, so the attribute can be
    # gone by the time a late option change arrives.
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None:
        built_from = getattr(runtime, "entry_data", None)
        if built_from is not None and dict(getattr(entry, "data", {})) != built_from:
            # Credentials or reseller identity changed (reauth): only a reload
            # rebuilds the client. The reauth flow updates the entry and lets
            # this listener own the reload, as HA's helper now requires.
            await hass.config_entries.async_reload(entry.entry_id)
            return
        if not runtime.history.takes_options(entry.options):
            return
    await hass.config_entries.async_reload(entry.entry_id)
