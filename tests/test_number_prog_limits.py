"""Programme-temperature sliders respect the node's max-stemp ceiling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.smartbox.number import (
    MaxTemperature,
    ProgComfortTemperature,
    ProgEcoTemperature,
    ProgFrostTemperature,
)
from tests.stack import NoopFanout, engine_with_node
from tests.test_entity_surface_matrix import _node

SETUP = {"units": "C", "max_stemp_limit": "23.0"}
STATUS = {
    "units": "C",
    "stemp": "20.0",
    "ice_temp": "2.0",
    "eco_temp": "3.0",
    "comf_temp": "4.0",
}


def _entity(cls):
    node = _node("htr", "04020000", "1.9", SETUP, STATUS)
    engine = engine_with_node(
        dev_id=node.dev_id,
        node_type=node.node_type,
        addr=node.addr,
        status=node.status,
        setup=node.setup,
        version=node.version,
    )
    runtime = SimpleNamespace(
        engine=engine, boost_prefs=None, services=None, client=None
    )
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.options = {}
    fanout = NoopFanout()
    return cls(node, entry, runtime, fanout, f"prog_{cls.__name__.lower()}")


def test_programme_sliders_follow_the_stored_limit() -> None:
    """Comfort/eco/frost ceilings are 23/22.5/22 with a 23 °C limit."""
    assert _entity(ProgComfortTemperature).native_max_value == 23.0
    assert _entity(ProgEcoTemperature).native_max_value == 22.5
    assert _entity(ProgFrostTemperature).native_max_value == 22.0
    assert _entity(ProgComfortTemperature).native_min_value == 1.0
    assert _entity(ProgEcoTemperature).native_min_value == 0.5
    assert _entity(ProgFrostTemperature).native_min_value == 0.0


async def test_programme_write_uses_the_clamped_bounds() -> None:
    """The engine receives the node-limited ranges, so writes stay in range."""
    entity = _entity(ProgComfortTemperature)
    entity._runtime.engine.set_prog_temps = AsyncMock()
    await entity.async_set_native_value(23.0)
    _args, kwargs = entity._runtime.engine.set_prog_temps.await_args
    assert kwargs["bounds_c"] == {
        "ice_temp": (0.0, 22.0),
        "eco_temp": (0.5, 22.5),
        "comf_temp": (1.0, 23.0),
    }


async def test_setting_the_limit_uses_the_new_ceiling_for_correction() -> None:
    """The follow-up profile correction is sanitized against the new limit."""
    entity = _entity(MaxTemperature)
    entity._runtime.engine.set_max_stemp_limit = AsyncMock()
    await entity.async_set_native_value(23.0)
    _args, kwargs = entity._runtime.engine.set_max_stemp_limit.await_args
    assert kwargs["bounds_c"] == {
        "ice_temp": (0.0, 22.0),
        "eco_temp": (0.5, 22.5),
        "comf_temp": (1.0, 23.0),
    }
