"""Tests for the integration's ws_user wiring (detection, fallback, supervisor).

Exercises the capability probe at setup, the fallback to socket_io, the
shared-socket creation path, and the supervisor's exit handling
(restart backoff, mid-run reload on WsUserUnsupportedError, reauth on
rejected credentials) with mocked sessions — the real-hardware soak
(Phase 4) covers what mocks cannot.
"""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
import pytest
from smartbox.session import AsyncSmartboxSession
from smartbox.ws_user import WsUserSocketSession

import custom_components.smartbox as smartbox_init
from custom_components.smartbox import (
    InvalidAuthError,
    SmartboxError,
    WsUserUnsupportedError,
    _async_create_ws_user_socket,
    _create_ws_user_supervisor,
)
from custom_components.smartbox.const import CONF_TRANSPORT, TRANSPORT_SOCKET_IO


async def _supervise_ws_user_socket(hass, entry) -> None:
    """Run the ws_user supervision loop inline (no task wrapper)."""
    await _create_ws_user_supervisor(hass, entry)._loop()


@pytest.mark.asyncio
async def test_setup_probe_unsupported_falls_back(config_entry):
    """A deterministic probe rejection falls back to socket_io (None)."""
    config_entry.runtime_data = SimpleNamespace(client=MagicMock())
    with patch(
        "custom_components.smartbox.check_ws_user_support",
        side_effect=WsUserUnsupportedError("404"),
    ):
        assert await _async_create_ws_user_socket(config_entry) is None


@pytest.mark.parametrize(
    ("exc", "match"),
    [
        # The helper never demotes non-deterministic trouble: the except
        # tuple in async_setup_entry turns these into ConfigEntryNotReady.
        (OSError("network unreachable"), "network unreachable"),
        (SmartboxError("boom"), "boom"),
        (aiohttp.ClientError("proxy 502"), "proxy 502"),
    ],
)
@pytest.mark.asyncio
async def test_setup_probe_transient_propagates_raw(hass, config_entry, exc, match):
    """Transient probe trouble propagates raw (setup maps it to NotReady)."""
    config_entry.runtime_data = SimpleNamespace(client=MagicMock())
    with (
        patch(
            "custom_components.smartbox.check_ws_user_support",
            side_effect=exc,
        ),
        pytest.raises(type(exc), match=match),
    ):
        await _async_create_ws_user_socket(config_entry)


@pytest.mark.asyncio
async def test_setup_probe_auth_failure_raises_auth_failed(hass, config_entry):
    """A rejected refresh during the probe must start the reauth flow."""
    config_entry.runtime_data = SimpleNamespace(client=MagicMock())
    with (
        patch(
            "custom_components.smartbox.check_ws_user_support",
            side_effect=InvalidAuthError("bad credentials"),
        ),
        pytest.raises(InvalidAuthError),
    ):
        await _async_create_ws_user_socket(config_entry)


@pytest.mark.asyncio
async def test_setup_probe_success_builds_shared_socket(hass, config_entry):
    """The happy path: probe OK returns a session on the entry's client."""
    config_entry.runtime_data = SimpleNamespace(client=MagicMock())
    client = config_entry.runtime_data.client
    with patch("custom_components.smartbox.check_ws_user_support", AsyncMock()):
        sock = await _async_create_ws_user_socket(config_entry)
    assert isinstance(sock, WsUserSocketSession)
    assert sock._session is client


@pytest.mark.asyncio
async def test_setup_probe_backend_socket_io_skips_probe(hass, config_entry):
    """The "socket_io" transport option never probes."""
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_TRANSPORT: TRANSPORT_SOCKET_IO}
    )
    config_entry.runtime_data = SimpleNamespace(client=MagicMock())
    probe = AsyncMock()
    with patch("custom_components.smartbox.check_ws_user_support", probe):
        assert await _async_create_ws_user_socket(config_entry) is None
    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_supervisor_reload_on_midrun_unsupported(hass, monkeypatch):
    """A mid-run WsUserUnsupportedError reloads the entry and stops.

    The reload is state-guarded (only a LOADED entry reloads) and goes
    through the entry's background-task helper, targeting THIS entry.
    """
    sock = MagicMock()
    sock.run = AsyncMock(side_effect=WsUserUnsupportedError("endpoint gone"))
    entry = SimpleNamespace(
        entry_id="entry_1",
        state=ConfigEntryState.LOADED,
        # Close the scheduled reload coroutine instead of awaiting it:
        # no "coroutine was never awaited" noise in the suite.
        async_create_background_task=MagicMock(
            side_effect=lambda _hass, coro, _name: coro.close()
        ),
        runtime_data=SimpleNamespace(ws_user_socket=sock, devices=[]),
    )
    monkeypatch.setattr("custom_components.smartbox.async_dispatcher_send", MagicMock())

    task = asyncio.create_task(_supervise_ws_user_socket(hass, entry))
    await asyncio.wait_for(task, 2)
    assert entry.async_create_background_task.call_count == 1
    call = entry.async_create_background_task.call_args
    assert call.args[1].__name__ == "async_reload"
    assert call.args[0] is hass
    assert call.args[2] == "smartbox_ws_user_reload"


@pytest.mark.asyncio
async def test_ws_user_unsupported_reload_state_guard(hass):
    """Only a LOADED entry reloads: no resurrection of an unloading one."""
    entry = SimpleNamespace(
        entry_id="entry_1",
        state=ConfigEntryState.NOT_LOADED,
        async_create_background_task=MagicMock(),
        runtime_data=SimpleNamespace(ws_user_socket=MagicMock(), devices=[]),
    )
    assert smartbox_init._handle_ws_user_unsupported(hass, entry) is False
    entry.async_create_background_task.assert_not_called()


@pytest.mark.asyncio
async def test_ws_user_unsupported_reload_rate_limited(hass, monkeypatch):
    """A flapping endpoint cannot reload-loop forever.

    After _WS_USER_RELOAD_LIMIT reloads inside the window, auto-reload
    refuses (the supervisor then pauses with the watchdog backoff and
    retries once the window prunes). The marks live in hass.data so the
    limit SURVIVES the reload it schedules — a supervisor-local list
    would be destroyed by exactly the reload it limits.
    """
    entry = SimpleNamespace(
        entry_id="entry_1",
        state=ConfigEntryState.LOADED,
        # Close the scheduled reload coroutine instead of awaiting it:
        # no "coroutine was never awaited" noise in the suite.
        async_create_background_task=MagicMock(
            side_effect=lambda _hass, coro, _name: coro.close()
        ),
        runtime_data=SimpleNamespace(ws_user_socket=MagicMock(), devices=[]),
    )
    marks_key = "entry_1_ws_user_reload_marks"
    for _ in range(smartbox_init._WS_USER_RELOAD_LIMIT):
        assert smartbox_init._handle_ws_user_unsupported(hass, entry) is True
    assert entry.async_create_background_task.call_count == (
        smartbox_init._WS_USER_RELOAD_LIMIT
    )
    # One more flap inside the window: refused.
    assert smartbox_init._handle_ws_user_unsupported(hass, entry) is False

    # The window prunes: once the marks are older than the window they
    # do not count, and a reload is allowed again.
    marks = hass.data[smartbox_init.DOMAIN][marks_key]
    monkeypatch.setattr(
        smartbox_init.time,
        "monotonic",
        lambda: marks[0] + smartbox_init._WS_USER_RELOAD_WINDOW_SECONDS + 1,
    )
    assert smartbox_init._handle_ws_user_unsupported(hass, entry) is True
    assert len(marks) == 1


@pytest.mark.asyncio
async def test_supervisor_pauses_once_after_refused_reload(monkeypatch):
    """A refused reload backs off once per failed run, not twice."""
    sock = MagicMock()
    sock.run = AsyncMock(side_effect=[WsUserUnsupportedError("gone"), None])
    entry = SimpleNamespace(
        entry_id="entry_1", runtime_data=SimpleNamespace(ws_user_socket=sock)
    )
    monkeypatch.setattr(
        smartbox_init, "_handle_ws_user_unsupported", lambda _hass, _entry: False
    )
    delays = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(
        "custom_components.smartbox.supervisor.asyncio.sleep", fake_sleep
    )
    await asyncio.wait_for(_supervise_ws_user_socket(SimpleNamespace(), entry), 2)
    assert delays == [smartbox_init._WS_USER_WATCHDOG_BASE_SECONDS]


@pytest.mark.asyncio
async def test_supervisor_reauth_on_invalid_auth(monkeypatch):
    """Rejected credentials dispatch per-device reauth and stop."""
    sock = MagicMock()
    sock.run = AsyncMock(side_effect=InvalidAuthError("rejected"))
    devices = [SimpleNamespace(dev_id=f"dev_{i}") for i in range(2)]
    entry = SimpleNamespace(
        entry_id="entry_1",
        runtime_data=SimpleNamespace(ws_user_socket=sock, devices=devices),
    )
    hass = SimpleNamespace()
    sent = []
    monkeypatch.setattr(
        "custom_components.smartbox.async_dispatcher_send",
        lambda _hass, signal: sent.append(signal),
    )
    await asyncio.wait_for(_supervise_ws_user_socket(hass, entry), 2)
    assert sent == [
        "smartbox_dev_0_reauth_required",
        "smartbox_dev_1_reauth_required",
    ]


@pytest.mark.asyncio
async def test_supervisor_restarts_real_session_after_unexpected_exit(
    mocker, monkeypatch, session_stub
):
    """Regression: a REAL WsUserSocketSession must be re-runnable.

    The first run() exits with an unexpected error; the supervisor must
    restart it in place — run() resets its per-run flags, connects again
    and parks. A one-shot run() would make the restart a no-op and leave
    the transport permanently dead.
    """

    class HangingWsCtx:
        async def __aenter__(self):
            await asyncio.sleep(30)

        async def __aexit__(self, *exc):
            return False

    attempts = {"n": 0}

    internal_error = RuntimeError("unexpected internal error")

    def fake_ws_connect(_self, _url, **_kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise internal_error
        return HangingWsCtx()

    mocker.patch.object(
        aiohttp.ClientSession, "ws_connect", fake_ws_connect, create=True
    )
    # Real but tiny backoff sleeps (module-level constants, watchdog
    # pattern) — a global asyncio.sleep patch would break the hang
    # simulation itself.
    monkeypatch.setattr(smartbox_init, "_WS_USER_WATCHDOG_BASE_SECONDS", 0.005)
    monkeypatch.setattr(smartbox_init, "_WS_USER_WATCHDOG_MAX_SECONDS", 0.01)

    sock = WsUserSocketSession(session_stub)
    entry = SimpleNamespace(
        entry_id="entry_1",
        runtime_data=SimpleNamespace(ws_user_socket=sock, devices=[]),
    )
    hass = SimpleNamespace()
    task = asyncio.create_task(_supervise_ws_user_socket(hass, entry))
    await asyncio.sleep(0.2)
    assert attempts["n"] >= 2, "run() was not restarted after the exit"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)


@pytest.fixture
async def session_stub(mocker, reseller):
    """Return a real session with the auth refresh stubbed for ws_user."""
    payload = json.dumps({"userId": "user_under_test"}).encode()
    b64payload = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    session = AsyncSmartboxSession(
        api_name="test_api_name_1",
        username="test_user",
        password="test_password",
    )
    session._access_token = f"hdr.{b64payload}.sig"
    mocker.patch.object(session, "check_refresh_auth", AsyncMock())
    yield session
    await session.aclose_owned_session()


@pytest.mark.asyncio
async def test_setup_entry_ws_user_active_full_wiring(
    hass, mock_smartbox, config_entry
):
    """Full setup with ws_user active: shared socket + supervision task.

    Everything else in the suite exercises the fallback path (the mocked
    session's token demotes the probe); this drives the ws_user path with
    the probe stubbed out and verifies the supervisor task lifecycle.
    """
    entry = config_entry
    # The shared-socket factory is patched so the real class never
    # touches the network. run() parks (as a live supervised socket
    # does) until cancel() releases it, so the teardown assertions
    # below actually evidence the stop/unload paths.
    created = MagicMock()
    run_release = asyncio.Event()

    async def park_run():
        await run_release.wait()

    async def cancel_socket():
        run_release.set()

    created.run = park_run
    created.cancel = AsyncMock(side_effect=cancel_socket)

    with (
        patch(
            "custom_components.smartbox.check_ws_user_support",
            AsyncMock(),
        ),
        patch(
            "custom_components.smartbox.WsUserSocketSession",
            return_value=created,
        ),
        patch(
            "custom_components.smartbox.get_devices",
            AsyncMock(return_value=[]),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)

    # Supervision task created and the shared socket stored, and the
    # parked run proves the supervision task is alive.
    assert entry.runtime_data.ws_user_socket is created
    supervision_task = entry.runtime_data.ws_user_supervisor.task
    assert supervision_task is not None
    assert not supervision_task.done()

    # The HA-stop listener tears the transport down (fire-and-forget
    # there is fine — the task is tracked by the loop and bounded).
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    created.cancel.assert_awaited()
    assert supervision_task.done(), "supervision task survived HA stop"

    # Unload tears the socket down and stops the supervision task —
    # AWAITED, so the entry never finishes unloading mid-teardown
    # (cancel is idempotent, so the second cancel is a no-op).
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert created.cancel.await_count >= 1
    assert supervision_task.done()
