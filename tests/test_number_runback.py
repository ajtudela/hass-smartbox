"""FULL-boost attribute numbers backed by the HA preference store."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from homeassistant.exceptions import ServiceValidationError
import pytest
from smartbox.core.engine import SmartboxEngine
from smartbox.core.state import DeviceState, NodeState, WorldState, node_key

from custom_components.smartbox.boost_prefs import NodeBoostPrefs
from custom_components.smartbox.number import (
    ConfigBoostDuration,
    ConfigBoostTemperature,
    ConfigRunbackDuration,
    ConfigRunbackTemperature,
)
from tests.test_entity_surface_matrix import _node

FULL = {
    "units": "C",
    "factory_options": {"boost_config": 3},
    "extra_options": {"boost_temp": "21.0", "boost_time": 60},
}
STATUS = {
    "units": "C",
    "stemp": "20.0",
    "boost_modes_available": ["temp_and_time", "runback"],
}


class _Prefs:
    """Mutable in-memory stand-in for the entry store."""

    def __init__(self) -> None:
        self.record = NodeBoostPrefs()

    def get(self, node_id: str) -> NodeBoostPrefs:  # noqa: ARG002
        return self.record

    async def async_update(self, node_id: str, **fields) -> None:  # noqa: ARG002
        values = {**self.record.__dict__, **fields}
        self.record = NodeBoostPrefs(**values)


def _entity(cls, node=None, prefs=None):
    node = node or _node("htr", "01340000", "1.9", FULL, STATUS)
    prefs = prefs or _Prefs()
    engine = SmartboxEngine()
    key = node_key(node.node_type, node.addr)
    device = DeviceState(
        dev_id=node.dev_id,
        away={},
        node_states={
            key: NodeState(
                status=dict(node.status),
                setup=dict(node.setup),
                version=dict(node.version),
            )
        },
    )
    object.__setattr__(engine, "_world", WorldState(devices={node.dev_id: device}))
    runtime = SimpleNamespace(
        engine=engine,
        boost_prefs=prefs,
        services=SimpleNamespace(),
        client=MagicMock(),
    )
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.options = {}
    fanout = SimpleNamespace(register_node=lambda *_args: lambda: None)
    entity = cls(node, entry, runtime, fanout, f"config_{cls.__name__.lower()}")
    return entity, prefs


async def test_boost_numbers_write_the_ha_store() -> None:
    temp, prefs = _entity(ConfigBoostTemperature)
    assert temp.native_value == 21.0
    await temp.async_set_native_value(23.0)
    assert prefs.record.boost_temp_c == 23.0

    minutes, prefs = _entity(ConfigBoostDuration)
    assert minutes.native_value == 60.0
    await minutes.async_set_native_value(120)
    assert prefs.record.boost_time == 120


async def test_runback_numbers_write_the_ha_store() -> None:
    temp, prefs = _entity(ConfigRunbackTemperature)
    assert temp.native_value == 20.0
    await temp.async_set_native_value(18.0)
    assert prefs.record.runback_temp_c == 18.0

    minutes, prefs = _entity(ConfigRunbackDuration)
    assert minutes.native_value == 30.0
    await minutes.async_set_native_value(60)
    assert prefs.record.runback_time == 60


async def test_attribute_numbers_refuse_out_of_range_values() -> None:
    temp, _prefs = _entity(ConfigRunbackTemperature)
    with pytest.raises(ServiceValidationError):
        await temp.async_set_native_value(100.0)
    minutes, _prefs = _entity(ConfigRunbackDuration)
    with pytest.raises(ServiceValidationError):
        await minutes.async_set_native_value(5000)
