"""The API's wire node types and the platforms' unknown-type guards."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.smartbox.climate import _build_node_climate
from custom_components.smartbox.number import _build_node_numbers
from custom_components.smartbox.select import _build_node_selects
from custom_components.smartbox.sensor import _build_node_sensors
from custom_components.smartbox.switch import _build_node_switches


def _unknown_node() -> SimpleNamespace:
    """Build a product category that is not one of the API's wire node types."""
    return SimpleNamespace(
        node_type="towel_rail_heater",
        supported_wire_type=False,
        heater_node=False,
        name="Towel rail",
        node_id="dev_1",
        addr=1,
        dev_id="dev_1",
        setup={},
        status={},
    )


def test_every_platform_skips_an_unknown_wire_type() -> None:
    node = _unknown_node()
    entry = MagicMock()
    runtime = MagicMock()
    fanout = MagicMock()
    assert _build_node_climate(node, entry, runtime, fanout) == []
    assert _build_node_numbers(node, entry, runtime, fanout) == []
    assert _build_node_selects(node, entry, runtime, fanout) == []
    assert _build_node_sensors(node, entry, runtime, fanout) == []
    assert _build_node_switches(node, entry, runtime, fanout) == []
