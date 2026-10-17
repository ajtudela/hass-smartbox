"""Supervision of long-running transport tasks (websocket sessions)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import time
from typing import TYPE_CHECKING, Final

from smartbox.retry import backoff_delay

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

_LOGGER = logging.getLogger(__name__)

DEFAULT_TEARDOWN_TIMEOUT_SECONDS: Final = 10.0
_EXIT_GRACE_SECONDS: Final = 1.5


class TransportSupervisor:
    """Run a transport ``run()`` coroutine and restart it when it dies.

    The library guarantees that ``run()`` only returns once an exit was
    requested (``cancel()``) or after raising, so a normal return ends the
    supervision. Any other exception is logged and the coroutine is restarted
    after a capped, doubling backoff, unless ``on_error`` handles it and asks
    to stop (rejected credentials, a vanished endpoint...).

    ``stop()`` is deliberately bounded and exception-proof: unload and the
    HA-stop hook must complete promptly even when the session is wedged.
    """

    def __init__(
        self,
        create_task: Callable[[Coroutine[None, None, None], str], asyncio.Task[None]],
        run: Callable[[], Awaitable[None]],
        *,
        name: str,
        base_delay: float,
        max_delay: float,
        cancel: Callable[[], Awaitable[None]] | None = None,
        on_error: Callable[[Exception], bool] | None = None,
        reset_after: float | None = None,
        teardown_timeout: float = DEFAULT_TEARDOWN_TIMEOUT_SECONDS,
    ) -> None:
        """Initialise the supervisor.

        Parameters
        ----------
        create_task
            Creates the supervising task; lets HA track it (``(coro, name)``).
        run
            The transport coroutine function to keep alive.
        name
            Task and log name.
        base_delay, max_delay
            Bounds of the restart backoff, in seconds.
        cancel
            Coroutine function asking the transport to exit; awaited by
            ``stop()`` within ``teardown_timeout``.
        on_error
            Called with the exception that ended ``run()``; return True to stop
            supervising (the error was handled), False to log and restart.
        reset_after
            A run that lasted longer than this many seconds resets the backoff.
        teardown_timeout
            Upper bound, in seconds, of each step of ``stop()``.

        """
        self._create_task = create_task
        self._run = run
        self._name = name
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._cancel = cancel
        self._on_error = on_error
        self._reset_after = reset_after
        self._teardown_timeout = teardown_timeout
        self._restarts = 0
        self._stopping = False
        self.task: asyncio.Task[None] | None = None

    @property
    def restarts(self) -> int:
        """Return the number of consecutive restarts since the last reset."""
        return self._restarts

    def start(self) -> None:
        """Start supervising the transport."""
        self.task = self._create_task(self._loop(), self._name)

    def reset_backoff(self) -> None:
        """Forget previous failures (a connection just succeeded)."""
        self._restarts = 0

    async def _loop(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await self._run()
            except Exception as exc:
                # The supervisor exists to survive anything the transport raises.
                if self._on_error is not None and self._on_error(exc):
                    return
                if self._stopping:
                    return
                _LOGGER.exception(
                    "%s exited unexpectedly; restarting", self._name, exc_info=exc
                )
            else:
                if not self._stopping:
                    _LOGGER.warning(
                        "%s returned without being asked to stop", self._name
                    )
                return
            if (
                self._reset_after is not None
                and time.monotonic() - started > self._reset_after
            ):
                self._restarts = 0
            delay = backoff_delay(self._restarts, self._base_delay, self._max_delay)
            self._restarts += 1
            await asyncio.sleep(delay)
            if self._stopping:
                return

    async def stop(self) -> None:
        """Stop supervising and tear the transport down, bounded."""
        self._stopping = True
        if self._cancel is not None:
            try:
                await asyncio.wait_for(self._cancel(), self._teardown_timeout)
            except TimeoutError:
                _LOGGER.warning(
                    "Teardown of %s did not finish in %ss",
                    self._name,
                    self._teardown_timeout,
                )
            except Exception:
                _LOGGER.exception("Error tearing down %s", self._name)
        task = self.task
        if task is None:
            return
        # run() returns by itself shortly after cancel() (python-socketio's
        # wait() sleeps ~1 s): give it that grace before forcing it.
        with suppress(asyncio.CancelledError):
            await asyncio.wait({task}, timeout=_EXIT_GRACE_SECONDS)
        if not task.done():
            _LOGGER.warning("Force-cancelling %s", self._name)
            task.cancel()
        # asyncio.wait returns on timeout instead of raising and swallows the
        # cancelled task's CancelledError; the suppress only guards our own
        # cancellation mid-wait.
        with suppress(asyncio.CancelledError):
            await asyncio.wait({task}, timeout=self._teardown_timeout)
        if not task.done():
            _LOGGER.warning("%s still running after cancel", self._name)
