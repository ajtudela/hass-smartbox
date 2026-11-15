"""Tests for the integration's ws_user supervision wiring.

Exercises the capability probe at setup (via ``_async_build_runtime``
with the token/probe seams patched), and the adapter monitor over the
engine's supervision task: IN-PLACE recovery of a mid-run terminal
TransportUnsupportedError (re-probe + re-supervise, never an entry
reload), the repair issue raised once per failing streak, reauth on
rejected credentials, and the capped re-probe backoff. The real-hardware
soak (Phase 4) covers what mocks cannot.
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.helpers import issue_registry as ir
import pytest
from smartbox.core.engine import SmartboxEngine
from smartbox.protocol import (
    AuthError,
    SmartboxError,
    TokenBundle,
    TransportUnavailableError,
    TransportUnsupportedError,
    WsUserTransport,
    timing,
)
from smartbox.protocol.client import SmartboxClient

import custom_components.smartbox as smartbox_init
from custom_components.smartbox import (
    DOMAIN,
    _async_build_runtime,
    _async_monitor_supervision,
    auth_config_from_entry_data,
)
from custom_components.smartbox.history import HistoryImport
import custom_components.smartbox.reseller as reseller_mod
from custom_components.smartbox.reseller import (
    RESELLER_OVERRIDES,
    reseller_from_entry_data,
)
from tests.fake_transport import FakeTransport
from tests.test_utils import until

_PROBE_TOKEN = "hdr.eyJ1c2VySWQiOiJ1In0.sig"


def _token_bundle() -> TokenBundle:
    return TokenBundle(
        access_token=_PROBE_TOKEN,
        refresh_token="rt",
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )


def _patch_identity_reads():
    """Stub the REST identity discovery (empty world; no network)."""
    return (
        patch.object(
            SmartboxClient,
            "get_grouped_devices",
            new=AsyncMock(return_value=[]),
        ),
    )


def _patch_probe(side_effect=None):
    """Patch the two new-stack network seams: grant + probe."""
    return (
        patch(
            "smartbox.protocol.auth.TokenManager.get_token",
            new=AsyncMock(return_value=_token_bundle()),
        ),
        patch(
            "custom_components.smartbox.check_ws_user_support",
            new=side_effect
            if isinstance(side_effect, AsyncMock)
            else AsyncMock(side_effect=side_effect),
        ),
    )


class _ScriptedTransport:
    """Transport that raises scripted outcomes from run(), then parks.

    A float outcome stands for a long-lived run: it is awaited through the
    engine's sleep seam first, so a test can model "this run lasted N s"
    without real waiting.
    """

    def __init__(self, script):
        self.script = list(script)
        self.runs = 0
        self.handlers = []

    def subscribe(self, handler):
        self.handlers.append(handler)

    async def run(self):
        self.runs += 1
        if self.script:
            outcome = self.script.pop(0)
            if isinstance(outcome, (int, float)):
                await timing.sleep(outcome)
                outcome = self.script.pop(0) if self.script else None
            if isinstance(outcome, Exception):
                raise outcome
        await asyncio.Event().wait()  # park

    async def close(self):
        return None


def _runtime_for(engine, transport):
    """Build a SmartboxRuntime whose engine supervises the scripted transport."""
    runtime = smartbox_init.SmartboxRuntime(
        engine=engine,
        client=MagicMock(),
        transport=transport,
        auth=MagicMock(),
        identity=MagicMock(),
        fanout=smartbox_init.EngineChangeFanOut(engine),
        membership=MagicMock(),
        entity_builder=MagicMock(),
        history=HistoryImport([], {}),
        entry_data={},
    )
    engine.supervise(transport)
    return runtime


# --- setup-path capability probe ---


@pytest.mark.asyncio
async def test_build_runtime_probe_unsupported_raises(hass, config_entry, reseller):
    """A deterministic probe rejection propagates raw (setup maps it)."""
    with contextlib.ExitStack() as stack:
        for seam in (
            *_patch_probe(TransportUnsupportedError("404")),
            *_patch_identity_reads(),
        ):
            stack.enter_context(seam)
        with pytest.raises(TransportUnsupportedError):
            await _async_build_runtime(hass, config_entry)


@pytest.mark.parametrize(
    ("exc", "match"),
    [
        # One no-demotion path: the builder never demotes
        # non-deterministic trouble; async_setup_entry's except tuple
        # turns it into ConfigEntryNotReady.
        (OSError("network unreachable"), "network unreachable"),
        (TransportUnavailableError("boom"), "boom"),
        (SmartboxError("proxy 502"), "proxy 502"),
    ],
)
@pytest.mark.asyncio
async def test_build_runtime_probe_transient_propagates_raw(
    hass, config_entry, reseller, exc, match
):
    """Transient probe trouble propagates raw (setup maps it to NotReady)."""
    with contextlib.ExitStack() as stack:
        for seam in (*_patch_probe(exc), *_patch_identity_reads()):
            stack.enter_context(seam)
        with pytest.raises(type(exc), match=match):
            await _async_build_runtime(hass, config_entry)


@pytest.mark.asyncio
async def test_build_runtime_token_auth_error_propagates(hass, config_entry, reseller):
    """A rejected password grant propagates AuthError (reauth path)."""
    with (
        patch(
            "smartbox.protocol.auth.TokenManager.get_token",
            new=AsyncMock(side_effect=AuthError("rejected")),
        ),
        pytest.raises(AuthError),
    ):
        await _async_build_runtime(hass, config_entry)


@pytest.mark.asyncio
async def test_build_runtime_success_wires_stack(hass, config_entry, reseller):
    """The happy path: probe OK returns the fully wired runtime."""
    with contextlib.ExitStack() as stack:
        for seam in (*_patch_probe(), *_patch_identity_reads()):
            stack.enter_context(seam)
        runtime = await _async_build_runtime(hass, config_entry)
    assert isinstance(runtime.transport, WsUserTransport)
    assert isinstance(runtime.engine, SmartboxEngine)
    assert runtime.auth is runtime.client.auth
    # The transport's token source wraps the SAME manager the client uses
    # (one manager per entry: fresh-token-per-connect feeds the ws URL, and
    # an invalidation reaches both).
    assert runtime.transport.token_source.manager is runtime.auth


@pytest.mark.asyncio
async def test_auth_config_from_entry_data(config_entry, reseller):
    """AuthConfig is built from entry data + the reseller table verbatim."""
    config = auth_config_from_entry_data(config_entry.data)
    assert isinstance(config, smartbox_init.AuthConfig)
    assert config.api_host == "https://users-api.helki.com"
    assert config.basic_auth_creds == "test_credentials"
    assert config.serial_id == "10"
    assert config.username == config_entry.data["username"]
    assert config.password == config_entry.data["password"]
    assert config.referer == "https://web_url/"


def test_auth_config_uses_the_elnur_host():
    """Elnur keeps its own API host; the other rows share users-api."""
    data = {"api_name": "api-elnur", "username": "u", "password": "p"}
    config = auth_config_from_entry_data(data)
    assert config.api_host == "https://api-elnur.helki.com"


def test_reseller_overrides_apply_to_every_auth_field(monkeypatch):
    """A manual override replaces the row before AuthConfig is built."""
    data = {"api_name": "api", "username": "u", "password": "p"}
    monkeypatch.setitem(
        RESELLER_OVERRIDES,
        "api",
        {
            "api_host": "api-test.helki.com",
            "basic_auth": "override_credentials",
            "serial_id": 99,
            "web_url": "https://override.test/",
        },
    )
    reseller = reseller_from_entry_data(data)
    assert reseller.api_host == "api-test.helki.com"
    assert reseller.basic_auth == "override_credentials"
    assert reseller.serial_id == 99
    assert reseller.web_url == "https://override.test/"

    config = auth_config_from_entry_data(data)
    assert config.api_host == "https://api-test.helki.com"
    assert config.basic_auth_creds == "override_credentials"
    assert config.serial_id == "99"
    assert config.referer == "https://override.test/"


def test_reseller_overrides_without_api_host(monkeypatch):
    """Non-host overrides keep the row's default API host."""
    data = {"api_name": "api", "username": "u", "password": "p"}
    monkeypatch.setitem(RESELLER_OVERRIDES, "api", {"serial_id": 99})
    reseller = reseller_from_entry_data(data)
    assert reseller.serial_id == 99
    assert reseller.api_host == "users-api.helki.com"


def test_reseller_overrides_normalize_https_host(monkeypatch):
    """A full HTTPS URL is reduced to the hostname the row expects."""
    data = {"api_name": "api", "username": "u", "password": "p"}
    monkeypatch.setitem(
        RESELLER_OVERRIDES,
        "api",
        {"api_host": "https://api-test.helki.com/"},
    )
    reseller = reseller_from_entry_data(data)
    assert reseller.api_host == "api-test.helki.com"
    assert auth_config_from_entry_data(data).api_host == "https://api-test.helki.com"


def test_reseller_overrides_use_the_entry_brand_key(monkeypatch):
    """Shared-tenant rows are overridden by the configured brand key."""
    data = {"api_name": "api-smartcontrol", "username": "u", "password": "p"}
    base = reseller_from_entry_data(data)
    assert base.api_url == "api-lhz"
    assert base.serial_id == 17

    monkeypatch.setitem(
        RESELLER_OVERRIDES,
        "api-smartcontrol",
        {
            "api_host": "api-smartcontrol.helki.com",
            "serial_id": 99,
        },
    )
    reseller = reseller_from_entry_data(data)
    assert reseller.api_url == "api-lhz"
    assert reseller.api_host == "api-smartcontrol.helki.com"
    assert reseller.serial_id == 99
    assert (
        auth_config_from_entry_data(data).api_host
        == "https://api-smartcontrol.helki.com"
    )


def test_reseller_overrides_reject_unknown_fields(monkeypatch):
    """The manual table accepts only the documented auth fields."""
    data = {"api_name": "api", "username": "u", "password": "p"}
    monkeypatch.setitem(RESELLER_OVERRIDES, "api", {"api_url": "other"})
    with pytest.raises(ValueError, match="api_url"):
        reseller_from_entry_data(data)


@pytest.mark.parametrize(
    "value",
    [
        "http://api.test",
        "api.test/path",
        "api.test?x=1",
        "api.test#x",
        "https://user@api.test",
        "api.test:bad",
        "",
        " api.test",
    ],
)
def test_reseller_overrides_reject_invalid_hosts(monkeypatch, value):
    """api_host accepts only an HTTPS/hostname origin without extras."""
    data = {"api_name": "api", "username": "u", "password": "p"}
    monkeypatch.setitem(RESELLER_OVERRIDES, "api", {"api_host": value})
    with pytest.raises(ValueError, match="api_host"):
        reseller_from_entry_data(data)


def test_reseller_override_logs_once(monkeypatch, caplog):
    """An active override is visible once, without logging its values."""
    monkeypatch.setattr(reseller_mod, "_LOGGED_OVERRIDES", set())
    data = {"api_name": "api", "username": "u", "password": "p"}
    monkeypatch.setitem(
        RESELLER_OVERRIDES,
        "api",
        {"api_host": "api-test.helki.com", "basic_auth": "override_credentials"},
    )
    with caplog.at_level(logging.WARNING):
        reseller_from_entry_data(data)
        reseller_from_entry_data(data)
    assert caplog.text.count("Manual reseller override active") == 1
    assert "api-test.helki.com" not in caplog.text
    assert "override_credentials" not in caplog.text


# --- adapter monitor over the engine's supervision task ---


class _FakeTime:
    """Monotonic clock the test advances (patched onto the module's view)."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def instant_probe_backoff(monkeypatch):
    """Make the in-place re-probe backoff instant (no real waits)."""
    monkeypatch.setattr(
        smartbox_init, "backoff_delay", lambda _attempt, _base, _cap: 0.0
    )


async def _run_monitor(hass, entry, runtime, timeout_s=2):
    return asyncio.create_task(_async_monitor_supervision(hass, entry, runtime))


def _spy_schedule_reload(hass):
    """Patch the config-entries reload scheduling so no reload runs."""
    return patch.object(hass.config_entries, "async_schedule_reload")


async def _stop_monitor(monitor, engine):
    """Reap the monitor + engine so no task outlives the test."""
    monitor.cancel()
    await engine.stop()
    with contextlib.suppress(asyncio.CancelledError):
        await monitor


@pytest.mark.asyncio
async def test_monitor_reprobes_in_place_on_midrun_unsupported(
    hass, instant_probe_backoff
):
    """A mid-run terminal unsupported is recovered by re-supervising.

    Nothing reloads the entry: the transport is restartable and its own
    handshake IS the capability probe, so the monitor re-runs it in place.
    A reload would also be unsafe from here - an entry-tracked reload task
    is cancelled by the reload's own unload, so the entry would be torn down
    and never set up again.
    """
    transport = _ScriptedTransport([TransportUnsupportedError("endpoint gone"), None])
    engine = SmartboxEngine()
    runtime = _runtime_for(engine, transport)
    entry = SimpleNamespace(entry_id="entry_1")
    with _spy_schedule_reload(hass) as schedule:
        monitor = asyncio.create_task(_async_monitor_supervision(hass, entry, runtime))
        try:
            await until(lambda: transport.runs >= 2)
        finally:
            await _stop_monitor(monitor, engine)
    schedule.assert_not_called()  # recovery is in place, never a reload


@pytest.mark.asyncio
async def test_monitor_reprobe_warning_carries_reason(
    hass, caplog, instant_probe_backoff
):
    """The re-probe warning embeds the triggering exception's status text."""
    caplog.set_level(logging.DEBUG, logger="custom_components.smartbox")
    exc = TransportUnsupportedError(
        "ws_user endpoint rejected the handshake (status=404)"
    )
    transport = _ScriptedTransport([exc, None])
    engine = SmartboxEngine()
    runtime = _runtime_for(engine, transport)
    entry = SimpleNamespace(entry_id="entry_1")
    monitor = asyncio.create_task(_async_monitor_supervision(hass, entry, runtime))
    try:
        await until(lambda: transport.runs >= 2)
    finally:
        await _stop_monitor(monitor, engine)
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "re-probing in place" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "status=404" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_ws_user_probe_issue_raised_once_per_streak(
    hass, caplog, instant_probe_backoff
):
    """The repair issue + its ERROR fire once per failing streak."""
    caplog.set_level(logging.DEBUG, logger="custom_components.smartbox")
    exc = TransportUnsupportedError(
        "ws_user endpoint rejected the handshake (status=404)"
    )
    # four immediate failures, then a served run
    transport = _ScriptedTransport([exc, exc, exc, exc, None])
    engine = SmartboxEngine()
    runtime = _runtime_for(engine, transport)
    entry = SimpleNamespace(entry_id="entry_1")
    monitor = asyncio.create_task(_async_monitor_supervision(hass, entry, runtime))
    try:
        await until(lambda: transport.runs >= 4)
        issue = ir.async_get(hass).issues.get((DOMAIN, "entry_1_ws_user_flapping"))
        assert issue is not None
        assert issue.translation_key == "ws_user_flapping"
        assert issue.severity == ir.IssueSeverity.WARNING
        assert issue.translation_placeholders["error"] == str(exc)
        errors = [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "failed re-probes" in r.getMessage()
        ]
        assert len(errors) == 1  # once per streak, not once per failure
    finally:
        await _stop_monitor(monitor, engine)


@pytest.mark.asyncio
async def test_long_lived_run_clears_the_probe_streak_and_issue(hass, monkeypatch):
    """A run that lasted long enough clears the issue and the streak.

    The streak is monitor-local (nothing reloads the monitor any more), so
    it is cleared by evidence that the endpoint served: a run that outlasted
    the reset threshold.
    """
    fake = _FakeTime()
    monkeypatch.setattr(timing, "monotonic", fake.monotonic)
    monkeypatch.setattr(smartbox_init, "backoff_delay", lambda _a, _b, _c: 0.0)

    async def fake_engine_sleep(delay):
        fake.now += delay

    monkeypatch.setattr(timing, "sleep", fake_engine_sleep)

    exc = TransportUnsupportedError("endpoint gone")
    # three immediate failures (issue raised), a 100 s run, then another
    # failure, then a served run
    transport = _ScriptedTransport([exc, exc, exc, 100.0, exc, None])
    engine = SmartboxEngine()
    runtime = _runtime_for(engine, transport)
    entry = SimpleNamespace(entry_id="entry_1")
    monitor = asyncio.create_task(_async_monitor_supervision(hass, entry, runtime))
    try:
        await until(lambda: transport.runs >= 5)
    finally:
        await _stop_monitor(monitor, engine)
    assert (DOMAIN, "entry_1_ws_user_flapping") not in ir.async_get(hass).issues


@pytest.mark.asyncio
async def test_monitor_keeps_reprobing_forever(hass, instant_probe_backoff):
    """The monitor never gives up: a returning backend is picked up.

    Parking on a dead transport with every node frozen as silently-stale is
    worse than re-probing, so the streak only gates the repair issue.
    """
    exc = TransportUnsupportedError("endpoint gone")
    transport = _ScriptedTransport([exc] * 6 + [None])
    engine = SmartboxEngine()
    runtime = _runtime_for(engine, transport)
    entry = SimpleNamespace(entry_id="entry_1")
    monitor = asyncio.create_task(_async_monitor_supervision(hass, entry, runtime))
    try:
        await until(lambda: transport.runs >= 7)
        assert transport.runs >= 7  # well past the issue threshold
    finally:
        await _stop_monitor(monitor, engine)


@pytest.mark.asyncio
async def test_monitor_reauth_on_terminal_auth_error(hass):
    """Rejected credentials start the entry's reauth flow and stop."""
    transport = _ScriptedTransport([AuthError("rejected")])
    engine = SmartboxEngine()
    runtime = _runtime_for(engine, transport)
    entry = SimpleNamespace(
        entry_id="entry_1",
        async_start_reauth=MagicMock(),
    )
    monitor = await _run_monitor(hass, entry, runtime)
    await asyncio.wait_for(monitor, 2)
    entry.async_start_reauth.assert_called_once_with(hass)


@pytest.mark.asyncio
async def test_monitor_clean_close_returns(hass):
    """A clean run() exit (transport closed = teardown) ends the monitor."""
    transport = FakeTransport()
    engine = SmartboxEngine()
    runtime = _runtime_for(engine, transport)
    entry = SimpleNamespace(entry_id="entry_1")
    monitor = await _run_monitor(hass, entry, runtime)
    await transport.close()
    await asyncio.wait_for(monitor, 2)  # returns, no exception


@pytest.mark.asyncio
async def test_ws_user_probe_pause_uses_the_saturating_backoff(hass, monkeypatch):
    """The in-place re-probe backoff grows, caps, and never overflows."""
    slept: list[float] = []

    async def fake_sleep(delay):
        slept.append(delay)

    # Patch the timing seam the monitor waits through; patching asyncio
    # globally would break the harness.
    monkeypatch.setattr(timing, "sleep", fake_sleep)

    for failures in (1, 2, 3):
        await smartbox_init._ws_user_probe_pause(failures)
    assert slept == [5.0, 10.0, 20.0]

    # A pathological streak keeps the cap instead of overflowing.
    await smartbox_init._ws_user_probe_pause(2000)
    assert slept[-1] == smartbox_init._WS_USER_PROBE_MAX_SECONDS
