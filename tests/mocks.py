"""Shared test helpers for the test suites.

Entity-name/id formulas over the mock world, plus the mock world itself.

The mock world is served ONLY through the protocol-layer seams: the
conftest patches :class:`SmartboxClient`'s identity discovery reads with
it, and platform tests read it to build expected values.
"""

from collections.abc import Mapping
from copy import deepcopy
import logging
from typing import Any

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.climate.const import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.helpers import entity_registry
from smartbox.protocol.client import NodeRef
from smartbox.protocol.errors import SmartboxError

from custom_components.smartbox.const import DOMAIN, HEATER_NODE_TYPES, SmartboxNodeType
from tests.const import CONF_DEVICE_IDS

_LOGGER = logging.getLogger(__name__)

# Live-verified RTC payload shape (api-contract.md, 2026-09-27): n is treated
# as a 0-indexed month by the integration.
MOCK_DEVICE_RTC = {"d": 27, "h": 9, "m": 55, "n": 8, "s": 24, "w": 0, "y": 2026}


MockNode = Mapping[str, Any] | NodeRef


def _addr(node: MockNode) -> int:
    """Node address from a mock node-info dict OR a protocol NodeRef."""
    if isinstance(node, Mapping):
        return node["addr"]
    return int(node.addr)


def get_climate_entity_name(mock_node: Mapping[str, Any]) -> str:
    return mock_node["name"]


def get_sensor_entity_name(mock_node: Mapping[str, Any], sensor_type: str) -> str:
    return f"{mock_node['name']} {sensor_type.capitalize()}"


def get_away_status_switch_entity_name(mock_device: Mapping[str, Any]) -> str:
    return f"{mock_device['name']} Away Status"


def get_no_power_limit_switch_entity_name(mock_device: Mapping[str, Any]) -> str:
    return f"{mock_device['name']} No Power Limit"


def get_connected_binary_sensor_entity_name(mock_device: Mapping[str, Any]) -> str:
    return f"{mock_device['name']} Connectivity"


def get_clock_drift_sensor_entity_name(mock_device: Mapping[str, Any]) -> str:
    return f"{mock_device['name']} Clock Drift"


def get_boost_switch_entity_name(mock_device: Mapping[str, Any]) -> str:
    return f"{mock_device['name']} Boost"


def get_boost_temperature_entity_name(mock_device: Mapping[str, Any]) -> str:
    return f"{mock_device['name']} Boost Temperature"


def get_boost_duration_entity_name(mock_device: Mapping[str, Any]) -> str:
    return f"{mock_device['name']} Boost Duration"


def get_away_offset_entity_name(mock_node: Mapping[str, Any]) -> str:
    return f"{mock_node['name']} Away Offset"


def get_max_temperature_entity_name(mock_node: Mapping[str, Any]) -> str:
    return f"{mock_node['name']} Maximum"


def get_prog_temp_entity_name(mock_node: Mapping[str, Any], profile: str) -> str:
    return f"{mock_node['name']} {profile.capitalize()}"


def get_max_limit_switch_entity_name(mock_node: Mapping[str, Any]) -> str:
    return f"{mock_node['name']} Maximum Limit"


def get_max_limit_switch_entity_id(mock_node: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_max_limit_switch_entity_name(mock_node))
    return get_entity_id_from_object_id(object_id, SWITCH_DOMAIN)


def get_node_power_limit_number_entity_name(mock_node: Mapping[str, Any]) -> str:
    return f"{mock_node['name']} Power Limit"


def get_node_power_limit_entity_id(mock_node: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_node_power_limit_number_entity_name(mock_node))
    return get_entity_id_from_object_id(object_id, NUMBER_DOMAIN)


def get_no_node_power_limit_switch_entity_name(mock_node: Mapping[str, Any]) -> str:
    return f"{mock_node['name']} No Power Limit"


def get_no_node_power_limit_switch_entity_id(mock_node: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_no_node_power_limit_switch_entity_name(mock_node))
    return get_entity_id_from_object_id(object_id, SWITCH_DOMAIN)


def get_priority_entity_name(mock_node: Mapping[str, Any]) -> str:
    return f"{mock_node['name']} Radiator Priority"


def get_duty_limit_entity_name(mock_node: Mapping[str, Any]) -> str:
    return f"{mock_node['name']} Power Limit Factor"


def get_window_mode_switch_entity_name(mock_node: Mapping[str, Any]) -> str:
    return f"{mock_node['name']} Window Mode"


def get_true_radiant_switch_entity_name(mock_node: Mapping[str, Any]) -> str:
    return f"{mock_node['name']} True Radiant"


def get_power_limit_number_entity_name(mock_device: Mapping[str, Any]) -> str:
    return f"{mock_device['name']} Power Limit"


def get_object_id(entity_name: str) -> str:
    return entity_name.lower().replace(" ", "_")


def get_entity_id_from_object_id(object_id: str, domain: str) -> str:
    return f"{domain}.{object_id}"


def get_climate_entity_id(mock_node: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_climate_entity_name(mock_node))
    return get_entity_id_from_object_id(object_id, CLIMATE_DOMAIN)


def get_sensor_entity_id(mock_node: Mapping[str, Any], sensor_type: str) -> str:
    object_id = get_object_id(get_sensor_entity_name(mock_node, sensor_type))
    return get_entity_id_from_object_id(object_id, SENSOR_DOMAIN)


def get_away_status_switch_entity_id(mock_device: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_away_status_switch_entity_name(mock_device))
    return get_entity_id_from_object_id(object_id, SWITCH_DOMAIN)


def get_no_power_limit_switch_entity_id(mock_device: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_no_power_limit_switch_entity_name(mock_device))
    return get_entity_id_from_object_id(object_id, SWITCH_DOMAIN)


def get_connected_binary_sensor_entity_id(mock_device: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_connected_binary_sensor_entity_name(mock_device))
    return get_entity_id_from_object_id(object_id, BINARY_SENSOR_DOMAIN)


def get_clock_drift_sensor_entity_id(mock_device: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_clock_drift_sensor_entity_name(mock_device))
    return get_entity_id_from_object_id(object_id, SENSOR_DOMAIN)


def get_boost_switch_entity_id(mock_device: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_boost_switch_entity_name(mock_device))
    return get_entity_id_from_object_id(object_id, SWITCH_DOMAIN)


def get_boost_duration_entity_id(mock_device: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_boost_duration_entity_name(mock_device))
    return get_entity_id_from_object_id(object_id, NUMBER_DOMAIN)


def get_boost_temperature_entity_id(mock_device: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_boost_temperature_entity_name(mock_device))
    return get_entity_id_from_object_id(object_id, NUMBER_DOMAIN)


def get_window_mode_switch_entity_id(mock_node: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_window_mode_switch_entity_name(mock_node))
    return get_entity_id_from_object_id(object_id, SWITCH_DOMAIN)


def get_true_radiant_switch_entity_id(mock_node: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_true_radiant_switch_entity_name(mock_node))
    return get_entity_id_from_object_id(object_id, SWITCH_DOMAIN)


def get_power_limit_number_entity_id(mock_device: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_power_limit_number_entity_name(mock_device))
    return get_entity_id_from_object_id(object_id, NUMBER_DOMAIN)


def get_away_offset_entity_id(mock_node: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_away_offset_entity_name(mock_node))
    return get_entity_id_from_object_id(object_id, NUMBER_DOMAIN)


def get_max_temperature_entity_id(mock_node: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_max_temperature_entity_name(mock_node))
    return get_entity_id_from_object_id(object_id, NUMBER_DOMAIN)


def get_prog_temp_entity_id(mock_node: Mapping[str, Any], profile: str) -> str:
    object_id = get_object_id(get_prog_temp_entity_name(mock_node, profile))
    return get_entity_id_from_object_id(object_id, NUMBER_DOMAIN)


def get_priority_entity_id(mock_node: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_priority_entity_name(mock_node))
    return get_entity_id_from_object_id(object_id, SELECT_DOMAIN)


def get_duty_limit_entity_id(mock_node: Mapping[str, Any]) -> str:
    object_id = get_object_id(get_duty_limit_entity_name(mock_node))
    return get_entity_id_from_object_id(object_id, SELECT_DOMAIN)


def get_device_unique_id(mock_device: Mapping[str, Any], entity_type: str) -> str:
    return f"{mock_device['dev_id']}_{entity_type}"


def get_node_unique_id(
    mock_device: Mapping[str, Any], mock_node: Mapping[str, Any], entity_type: str
) -> str:
    return f"{mock_device['dev_id']}_{mock_node['addr']}_{entity_type}"


def get_entity_id_from_unique_id(hass, platform, unique_id):
    er = entity_registry.async_get(hass)
    entity_id = er.async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None
    return entity_id


def get_device_by_identifier(device_registry, identifier):
    """Return the device for ``identifier``, on either line the dev range spans.

    HA 2026.10 deprecated ``async_get_device`` and the HA test harness fails on
    the deprecation; the older line only exposes the old lookup.
    """
    if hasattr(device_registry, "async_get_devices"):
        devices = device_registry.async_get_devices(identifiers={identifier})
        return devices[0] if devices else None
    return device_registry.async_get_device(identifiers={identifier})


class MockSmartbox:
    """The mock world: fixture data + an async read view for tests.

    ``session`` is a plain read view over the world (no mock session
    object behind it). The conftest patches SmartboxClient's
    identity-discovery reads onto the same world, so a real setup builds
    its identity from the mock data.
    """

    def __init__(
        self,
        mock_config,
        mock_home_info,
        mock_device_info,
        mock_node_info,
        mock_node_setup: dict[str, dict[int, Mapping[str, Any]]],
        mock_node_prog: dict[str, dict[int, dict[str, list[int]]]],
        mock_node_away: dict[str, Mapping[str, Any]],
        mock_device_power: dict[str, dict[int, Mapping[str, Any]]],
        mock_node_status: dict[str, dict[int, Mapping[str, Any]]],
        start_available=True,
    ):
        self.config = mock_config
        self._home_info = mock_home_info
        self._device_info = mock_device_info
        self._devices = [
            self._device_info[dev_id] for dev_id in mock_config[DOMAIN][CONF_DEVICE_IDS]
        ]
        self._node_info = mock_node_info
        self._node_setup = mock_node_setup
        self._node_prog = mock_node_prog
        self._node_away = mock_node_away
        self._device_power = mock_device_power
        self._node_status = mock_node_status
        if not start_available:
            for dev in self._devices:
                for node_info in self._node_info[dev["dev_id"]]:
                    self._node_status[dev["dev_id"]][node_info["addr"]][
                        "sync_status"
                    ] = "lost"

    @property
    def session(self):
        """The world read view the platform suites share (``session.*``)."""
        return self

    def add_node(
        self,
        dev_id: str,
        node_info: Mapping[str, Any],
        *,
        setup: Mapping[str, Any],
        status: Mapping[str, Any],
        prog: dict[str, list[int]],
    ) -> None:
        """Add a node to this test world without touching the module fixtures."""
        self._node_info = {
            device_id: list(nodes) for device_id, nodes in self._node_info.items()
        }
        self._node_info[dev_id].append(dict(node_info))
        addr = int(node_info["addr"])
        for slot, value in (
            (self._node_setup[dev_id], dict(setup)),
            (self._node_status[dev_id], dict(status)),
        ):
            if isinstance(slot, list):
                while len(slot) <= addr:
                    slot.append({})
                slot[addr] = value
            else:
                slot[addr] = value
        self._node_prog[dev_id][addr] = deepcopy(prog)

    def add_device(
        self,
        device_info: Mapping[str, Any],
        node_info: Mapping[str, Any],
        *,
        setup: Mapping[str, Any],
        status: Mapping[str, Any],
        prog: dict[str, list[int]],
    ) -> None:
        """Add a box with one node to this test world."""
        dev_id = str(device_info["dev_id"])
        self._home_info = deepcopy(self._home_info)
        self._device_info = {**self._device_info, dev_id: dict(device_info)}
        self._devices.append(dict(device_info))
        self._home_info[0]["devs"].append(dict(device_info))
        self._node_info = {
            device_id: list(nodes) for device_id, nodes in self._node_info.items()
        }
        self._node_info[dev_id] = [dict(node_info)]
        self._node_setup[dev_id] = [dict(setup)]
        self._node_status[dev_id] = [dict(status)]
        self._node_prog[dev_id] = {int(node_info["addr"]): deepcopy(prog)}
        self._node_away = {
            **self._node_away,
            dev_id: {"away": False, "enabled": True, "forced": False},
        }
        self._device_power = {**self._device_power, dev_id: 0}

    async def get_grouped_devices(self) -> list[dict[str, Any]]:
        return deepcopy(self._home_info)

    async def get_devices(self) -> list[dict[str, Any]]:
        return [dict(device) for device in self._devices]

    async def get_power_limit(self, dev_id: str) -> dict[str, str]:
        """Probe the box power-limit endpoint; None means no htr_system."""
        limit = self._device_power.get(dev_id)
        if limit is None:
            msg = "box power limit not available"
            raise SmartboxError(msg)
        return {"power_limit": str(limit)}

    async def get_dev_data(self, dev_id: str) -> dict[str, Any]:
        """Return the REST device payload the capability probe confirms against.

        The REST read never carries `connected` (that key arrives with the ws
        snapshot), and a device without a box serves the empty power-limit
        placeholder object (docs/api-contract.md §3.1 E46, §7 item 17).
        """
        data: dict[str, Any] = {
            "nodes": deepcopy(self._node_info[dev_id]),
            "away_status": dict(self._node_away[dev_id]),
        }
        limit = self._device_power.get(dev_id)
        if limit is None:
            data["htr_system"] = {"power_limit": {}}
        else:
            data["htr_system"] = {
                "power_limit": {"power_limit": str(limit)},
                "setup": {"power_limit": int(limit)},
            }
        return data

    async def get_nodes(self, dev_id: str) -> list[dict[str, Any]]:
        return deepcopy(self._node_info[dev_id])

    async def get_status(self, dev_id: str, node: MockNode) -> Mapping[str, Any]:
        """Return the node's status; a lost node reports the bare lost marker."""
        status = self._node_status[dev_id][_addr(node)]
        if status["sync_status"] == "lost":
            return {"sync_status": "lost"}
        return dict(status)

    async def get_node_setup(self, dev_id: str, node: MockNode) -> Mapping[str, Any]:
        return dict(self._node_setup[dev_id][_addr(node)])

    async def get_node_prog(self, dev_id: str, node: MockNode) -> dict[str, Any]:
        return {
            "prog": deepcopy(self._node_prog[dev_id][_addr(node)]),
            "sync_status": "ok",
        }

    async def get_device_away_status(self, dev_id: str) -> Mapping[str, Any]:
        return dict(self._node_away[dev_id])

    async def get_device_power_limit(self, dev_id: str) -> int:
        return self._device_power[dev_id]

    async def get_node_version(self, dev_id: str, node: MockNode) -> dict[str, str]:
        node_info = self._node_info[dev_id][_addr(node)]
        return {
            "hw_version": node_info.get("fw_version"),
            "pid": node_info.get("product_id"),
        }

    async def get_node_power(self, dev_id: str, node: MockNode) -> int:
        return int(self._node_status[dev_id][_addr(node)]["power"])


def active_or_charging_update(node_type: str, active: bool) -> Mapping[str, Any]:
    return (
        {"charging": active}
        if node_type == SmartboxNodeType.ACM
        else {"active": active}
    )


def is_heater_node(node: Mapping[str, Any]) -> bool:
    return node["type"] in HEATER_NODE_TYPES
