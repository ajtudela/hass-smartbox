"""Shared assertion/temperature helpers for the test suites."""

import asyncio
from datetime import UTC, datetime, timedelta
import json
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.const import UnitOfTemperature
from homeassistant.util.unit_conversion import TemperatureConverter
from smartbox.protocol.auth import TokenBundle

if TYPE_CHECKING:
    from collections.abc import Callable


async def until(
    pred: Callable[[], bool],
    budget: float = 10.0,
) -> None:
    """Bounded poll for a condition (the established sleep replacement).

    Raises a diagnostic ``TimeoutError`` when the condition never
    arrives - a fixed ``asyncio.sleep`` guess cannot hide a missed
    state transition. The budget is generous because a loaded CI box can
    take seconds to schedule the polled task; the poll returns as soon as
    the predicate holds, so the extra time only shows up on a real hang.
    """
    try:
        async with asyncio.timeout(budget):
            while not pred():  # noqa: ASYNC110 - intentional bounded poll
                await asyncio.sleep(0.01)
    except TimeoutError:
        msg = f"condition not met within {budget}s"
        raise TimeoutError(msg) from None


def simple_celsius_to_fahrenheit(temp: float) -> float:
    return temp * 9 / 5 + 32


def convert_temp(hass, node_units: str, temp: float) -> float:
    # Temperatures are converted to the units of the HA
    # instance, so do the same for comparison
    unit = (
        UnitOfTemperature.CELSIUS if node_units == "C" else UnitOfTemperature.FAHRENHEIT
    )
    return TemperatureConverter.convert(temp, unit, hass.config.units.temperature_unit)


def max_limit_value(node_units: str, raw_celsius: float) -> float:
    """Return the wire's Celsius max_stemp_limit in the NODE's display unit."""
    if node_units == "C":
        return raw_celsius
    return float(math.floor(raw_celsius * 9 / 5 + 32 + 0.5))


def round_temp(hass, temp: float) -> float:
    # HA uses different precisions for Fahrenheit (whole
    # integers) vs Celsius (tenths)
    if hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS:
        return round(temp, 1)
    return round(temp)


def assert_backoff_delays(delays: list[float], bases: list[float]) -> None:
    """Assert each delay is a base plus up to 20 % one-sided jitter."""
    assert len(delays) == len(bases)
    for delay, base in zip(delays, bases, strict=True):
        # The epsilon absorbs float rounding at the 20 % upper bound.
        assert base <= delay <= base * 1.2 + 1e-9


def assert_log_message(
    caplog, name: str, levelno: int, message: str, phase="call"
) -> None:
    def _find_message(r: logging.LogRecord) -> bool:
        return r.name == name and r.levelno == levelno and r.getMessage() == message

    assert any(
        # Ignoring typing due to https://github.com/python/mypy/issues/12682
        filter(_find_message, caplog.get_records(phase))
    )


def assert_no_log_errors(caplog, phase="call", *, everywhere: bool = False) -> None:
    """Assert the integration logged no ERROR records in the phase.

    Scoped to the integration's own loggers by default: HA and the recorder
    log unrelated noise in the same phase, and only our errors are this
    suite's business. ``everywhere`` widens it to every logger.
    """
    errors = [
        record
        for record in caplog.get_records(phase)
        if record.levelno >= logging.ERROR
        and (everywhere or record.name.startswith("custom_components.smartbox"))
    ]
    assert not errors, [record.getMessage() for record in errors]


def numeric_state(hass, entity_id) -> float:
    """Return an entity's numeric state, asserting it renders a real number.

    Stronger than ``state != STATE_UNAVAILABLE``, which passes for ANY other
    rendering: this also rejects unknown, empty and non-numeric values.
    """
    state = hass.states.get(entity_id).state
    try:
        return float(state)
    except ValueError:
        msg = f"{entity_id} does not render a number: {state!r}"
        raise AssertionError(msg) from None


def armed_deadlines(engine, kind: str | None = None) -> list[tuple]:
    """Return the identity of the engine's armed node deadlines.

    The confirmation/availability machinery is asserted through the engine's
    public view (``engine.deadlines``); this projects the identity part -
    ``(dev_id, node_key, kind, slot)`` - so a test can compare it against a
    literal without the timing, and can filter to one kind of window.
    """
    return [
        (deadline.dev_id, deadline.node_key, deadline.kind, deadline.slot)
        for deadline in engine.deadlines
        if kind is None or deadline.kind == kind
    ]


def test_simple_celsius_to_fahrenheit():
    assert simple_celsius_to_fahrenheit(0) == 32
    assert simple_celsius_to_fahrenheit(22.5) == 72.5
    assert simple_celsius_to_fahrenheit(19.7) == 67.46


_LIVE_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "live"


def live_fixture(name: str) -> dict:
    """Load a captured live wire payload (vendored under ``tests/fixtures``).

    The passthrough tests pin real wire shapes, so the payloads travel with
    this repo (see ``tests/fixtures/live/README.md``): reading them from
    elsewhere would skip the pins wherever that source is absent, and a
    skipped pin protects nothing.
    """
    path = _LIVE_FIXTURES / name
    if not path.exists():
        msg = f"vendored live fixture {name} is missing from {_LIVE_FIXTURES}"
        raise FileNotFoundError(msg)
    return json.loads(path.read_text())


def stored_token_bundle(
    access: str = "stored-access",
    refresh: str = "stored-refresh",
    lifetime_s: float = 4 * 3600,
) -> TokenBundle:
    """Return a bundle shaped like a stored record, for persistence tests."""
    return TokenBundle(
        access_token=access,
        refresh_token=refresh,
        expires_at=datetime.now(UTC) + timedelta(seconds=lifetime_s),
    )
