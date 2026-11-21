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


def test_thermostat_has_no_power_or_consumption_sensors() -> None:
    node = MagicMock()
    node.node_type = "thm"
    node.supported_wire_type = True
    node.heater_node = False
    node.boost_available = False
    node.name = "Thermostat"
    node.node_id = "dev_1_11"
    node.addr = 11
    node.dev_id = "dev_1"
    node.home_id = "home_1"
    node.pid = "0109"
    node.hw_version = "1.1"
    node.setup = {}
    node.status = {}
    node.reseller = SimpleNamespace(web_url="https://web_url/", name="Test")

    runtime = SimpleNamespace(services=SimpleNamespace(schedule=[]))
    groups = _build_node_sensors(node, MagicMock(), runtime, MagicMock())
    classes = {type(entity).__name__ for entities, _ in groups for entity in entities}

    assert "PowerSensor" not in classes
    assert "TotalConsumptionSensor" not in classes
