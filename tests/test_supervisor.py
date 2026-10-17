"""Tests for the transport supervisor."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.smartbox import supervisor as supervisor_module
from custom_components.smartbox.supervisor import TransportSupervisor


def _make(run, **kwargs) -> TransportSupervisor:
    return TransportSupervisor(
        lambda coro, name: asyncio.create_task(coro, name=name),
        run,
        name="test_transport",
        base_delay=kwargs.pop("base_delay", 0.001),
        max_delay=kwargs.pop("max_delay", 0.004),
        **kwargs,
    )


async def test_restarts_after_an_exception(caplog):
    """An unexpected exception restarts run() after a backoff."""
    run = AsyncMock(side_effect=[RuntimeError("boom"), None])
    supervisor = _make(run)
    supervisor.start()
    await asyncio.wait_for(supervisor.task, 2)
    assert run.await_count == 2
    assert supervisor.restarts == 1
    assert "exited unexpectedly" in caplog.text


async def test_normal_return_ends_supervision_without_restart():
    """run() returning on its own is the end, never a crash to restart."""
    run = AsyncMock(return_value=None)
    supervisor = _make(run)
    supervisor.start()
    await asyncio.wait_for(supervisor.task, 2)
    assert run.await_count == 1


async def test_on_error_can_stop_supervision():
    """A handled error (e.g. rejected credentials) is not restarted."""
    run = AsyncMock(side_effect=ValueError("auth"))
    seen = []

    def on_error(exc):
        seen.append(exc)
        return True

    supervisor = _make(run, on_error=on_error)
    supervisor.start()
    await asyncio.wait_for(supervisor.task, 2)
    assert run.await_count == 1
    assert isinstance(seen[0], ValueError)


async def test_backoff_doubles_and_resets(monkeypatch):
    """Delays double up to the cap and reset on reset_backoff()."""
    delays = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay):
        delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(supervisor_module.asyncio, "sleep", fake_sleep)
    run = AsyncMock(side_effect=[RuntimeError()] * 4 + [None])
    supervisor = _make(run, base_delay=1, max_delay=3)
    supervisor.start()
    await asyncio.wait_for(supervisor.task, 2)
    assert delays == [1, 2, 3, 3]
    supervisor.reset_backoff()
    assert supervisor.restarts == 0


async def test_long_run_resets_backoff(monkeypatch):
    """A run that outlived reset_after restarts from the base delay."""
    delays = []
    real_sleep = asyncio.sleep
    clock = {"t": 0.0}
    calls = {"n": 0}

    async def fake_sleep(delay):
        delays.append(delay)
        await real_sleep(0)

    async def run():
        calls["n"] += 1
        # The third run is the long-lived one; the fourth ends the loop.
        clock["t"] += 100 if calls["n"] == 3 else 1
        if calls["n"] < 4:
            raise RuntimeError

    monkeypatch.setattr(supervisor_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        supervisor_module, "time", SimpleNamespace(monotonic=lambda: clock["t"])
    )
    supervisor = _make(run, base_delay=1, max_delay=8, reset_after=60)
    supervisor.start()
    await asyncio.wait_for(supervisor.task, 2)
    assert delays == [1, 2, 1]


async def test_stop_lets_run_return_and_does_not_restart(caplog):
    """cancel() makes run() return; the supervisor ends without a restart."""
    exit_requested = asyncio.Event()

    async def run():
        await exit_requested.wait()

    async def cancel():
        exit_requested.set()

    supervisor = _make(run, cancel=cancel)
    supervisor.start()
    await asyncio.sleep(0)
    with caplog.at_level(logging.WARNING):
        await supervisor.stop()
    assert supervisor.task.done()
    assert not supervisor.task.cancelled()
    assert supervisor.restarts == 0
    assert "unexpectedly" not in caplog.text
    assert "Force-cancelling" not in caplog.text


async def test_stop_is_bounded_when_teardown_hangs(monkeypatch, caplog):
    """A wedged cancel() neither hangs nor raises; the task is force-cancelled."""
    monkeypatch.setattr(supervisor_module, "_EXIT_GRACE_SECONDS", 0.01)
    never = asyncio.Event()

    async def run():
        await never.wait()

    supervisor = _make(run, cancel=never.wait, teardown_timeout=0.05)
    supervisor.start()
    await asyncio.sleep(0)
    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(supervisor.stop(), 5)
    assert supervisor.task.cancelled()
    assert "did not finish" in caplog.text
    assert "Force-cancelling" in caplog.text


async def test_stop_survives_a_failing_cancel(caplog):
    """An exception from cancel() is logged, never raised."""

    async def run():
        await asyncio.Event().wait()

    supervisor = _make(
        run, cancel=AsyncMock(side_effect=RuntimeError("bad")), teardown_timeout=0.05
    )
    supervisor.start()
    await asyncio.sleep(0)
    await supervisor.stop()
    assert "Error tearing down" in caplog.text


async def test_stop_before_start_is_a_noop():
    """Stopping a supervisor that never started is harmless."""
    supervisor = _make(AsyncMock())
    await supervisor.stop()
    assert supervisor.task is None
