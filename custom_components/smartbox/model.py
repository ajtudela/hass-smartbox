"""Models for Smartbox."""

import asyncio
import logging
from typing import Any, cast
from unittest.mock import MagicMock

from homeassistant.components.climate import (
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_HOME,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from smartbox import Session, UpdateManager

from .const import (
    GITHUB_ISSUES_URL,
    HEATER_NODE_TYPE_ACM,
    HEATER_NODE_TYPE_HTR_MOD,
    HEATER_NODE_TYPES,
    PRESET_FROST,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
)
from .types import FactoryOptionsDict, SetupDict, StatusDict

_LOGGER = logging.getLogger(__name__)


class SmartboxDevice:
    """Smartbox device."""

    def __init__(
        self,
        device,
        session: Session | MagicMock,
    ) -> None:
        """Initialise a smartbox device."""
        self._device = device
        self._session = session
        self._away = False
        self._power_limit: int = 0
        self._nodes = {}
        self._watchdog_task: asyncio.Task | None = None

    async def initialise_nodes(self, hass: HomeAssistant) -> None:
        """Initilaise nodes."""
        # Would do in __init__, but needs to be a coroutine
        session_nodes = await hass.async_add_executor_job(
            self._session.get_nodes, self.dev_id
        )

        for node_info in session_nodes:
            status = await hass.async_add_executor_job(
                self._session.get_status, self.dev_id, node_info
            )
            setup = await hass.async_add_executor_job(
                self._session.get_setup, self.dev_id, node_info
            )

            node = SmartboxNode(self, node_info, self._session, status, setup)

            self._nodes[(node.node_type, node.addr)] = node

        _LOGGER.debug("Creating SocketSession for device %s", self.dev_id)
        update_manager = UpdateManager(
            self._session,
            self.dev_id,
        )
        update_manager.subscribe_to_device_away_status(self._away_status_update)
        update_manager.subscribe_to_device_power_limit(self._power_limit_update)
        update_manager.subscribe_to_node_status(self._node_status_update)
        update_manager.subscribe_to_node_setup(self._node_setup_update)

        _LOGGER.debug("Starting UpdateManager task for device %s", self.dev_id)
        self._watchdog_task = asyncio.create_task(update_manager.run())

    def _away_status_update(self, away_status: dict[str, bool]) -> None:
        _LOGGER.debug("Away status update: %s", away_status)
        self._away = away_status["away"]

    def _power_limit_update(self, power_limit: int) -> None:
        _LOGGER.debug("power_limit update: %s", power_limit)
        self._power_limit = power_limit

    def _node_status_update(
        self, node_type: str, addr: int, node_status: StatusDict
    ) -> None:
        _LOGGER.debug("Node status update: %s", node_status)
        node = self._nodes.get((node_type, addr), None)
        if node is not None:
            node.update_status(node_status)
        else:
            _LOGGER.error(
                "Received status update for unknown node %s %s", node_type, addr
            )

    def _node_setup_update(
        self, node_type: str, addr: int, node_setup: SetupDict
    ) -> None:
        _LOGGER.debug("Node setup update: %s", node_setup)
        node = self._nodes.get((node_type, addr), None)
        if node is not None:
            node.update_setup(node_setup)
        else:
            _LOGGER.error(
                "Received setup update for unknown node %s %s", node_type, addr
            )

    @property
    def dev_id(self) -> str:
        """Return the devide id."""
        return self._device["dev_id"]

    def get_nodes(self):
        """Return all nodes."""
        for item in self._nodes:
            _LOGGER.debug("Get_nodes: %s", item)
        return self._nodes.values()

    @property
    def name(self) -> str:
        """Return name of the device."""
        return self._device["name"]

    @property
    def model_id(self) -> int:
        """Return the model id."""
        return self._device["product_id"]

    @property
    def sw_version(self) -> int:
        """Return the software version of the device."""
        return self._device["fw_version"]

    @property
    def serial_number(self) -> int:
        """Return the serial number of the device."""
        return self._device["serial_id"]

    @property
    def away(self) -> bool:
        """Is the device in away mode."""
        return self._away

    def set_away_status(self, away: bool):
        """Set the away status."""
        self._session.set_device_away_status(self.dev_id, {"away": away})
        self._away = away

    @property
    def power_limit(self) -> int:
        """Get the power limit of the device."""
        return self._power_limit

    def set_power_limit(self, power_limit: int) -> None:
        """Set the power limit of the device."""
        self._session.set_device_power_limit(self.dev_id, power_limit)
        self._power_limit = power_limit


class SmartboxNode:
    """Smartbox Node."""

    def __init__(
        self,
        device: SmartboxDevice | MagicMock,
        node_info: dict[str, Any],
        session: Session | MagicMock,
        status: dict[str, Any],
        setup: dict[str, Any],
    ) -> None:
        """Initialise a smartbox node."""
        self._device = device
        self._node_info = node_info
        self._session = session
        self._status = status
        self._setup = setup

    @property
    def node_id(self) -> str:
        """Return the id of the node."""
        return f"{self._device.dev_id}_{self._node_info['addr']}"

    @property
    def name(self) -> str:
        """Return the name of the node."""
        return self._node_info["name"]

    @property
    def node_type(self) -> str:
        """Return node type, e.g. 'htr' for heaters."""
        return self._node_info["type"]

    @property
    def addr(self) -> int:
        """Return the addr of node."""
        return self._node_info["addr"]

    @property
    def status(self) -> StatusDict:
        """Return the status of node."""
        return self._status

    def update_status(self, status: StatusDict) -> None:
        """Update status."""
        _LOGGER.debug("Updating node %s status: %s", self.name, status)
        self._status = status

    @property
    def setup(self) -> SetupDict:
        """Setup of node."""
        return self._setup

    def update_setup(self, setup: SetupDict) -> None:
        """Update setup."""
        _LOGGER.debug("Updating node %s setup: %s", self.name, setup)
        self._setup = setup

    def set_status(self, **status_args) -> StatusDict:
        """Set status."""
        self._session.set_status(self._device.dev_id, self._node_info, status_args)
        # update our status locally until we get an update
        self._status |= {**status_args}
        return self._status

    @property
    def away(self):
        """Is away mode."""
        return self._device.away

    @property
    def device(self):
        """Return the device of the node."""
        return self._device

    def update_device_away_status(self, away: bool):
        """Update device away status."""
        self._device.set_away_status(away)

    async def async_update(self, hass: HomeAssistant) -> StatusDict:
        """Update status."""
        return self.status

    @property
    def window_mode(self) -> bool:
        """Is windows mode enable."""
        if "window_mode_enabled" not in self._setup:
            raise KeyError(
                "window_mode_enabled not present in setup for node {self.name}"
            )
        return self._setup["window_mode_enabled"]

    def set_window_mode(self, window_mode: bool):
        """Set window mode."""
        self._session.set_setup(
            self._device.dev_id, self._node_info, {"window_mode_enabled": window_mode}
        )
        self._setup["window_mode_enabled"] = window_mode

    @property
    def true_radiant(self) -> bool:
        """Is a true radiant."""
        if "true_radiant_enabled" not in self._setup:
            raise KeyError(
                "true_radiant_enabled not present in setup for node {self.name}"
            )
        return self._setup["true_radiant_enabled"]

    def set_true_radiant(self, true_radiant: bool):
        """Set true radiant."""
        self._session.set_setup(
            self._device.dev_id, self._node_info, {"true_radiant_enabled": true_radiant}
        )
        self._setup["true_radiant_enabled"] = true_radiant

    def is_heating(self, status: dict[str, Any]) -> str:
        """Is heating."""
        return (
            status["charging"]
            if self.node_type == HEATER_NODE_TYPE_ACM
            else status["active"]
        )


def is_heater_node(node: SmartboxNode | MagicMock) -> bool:
    """Is this node a heater."""
    return node.node_type in HEATER_NODE_TYPES


def is_supported_node(node: SmartboxNode | MagicMock) -> bool:
    """Is this node supported."""
    return is_heater_node(node)


def get_temperature_unit(status) -> None | Any:
    """Get the unit of temperature."""
    if "units" not in status:
        return None
    unit = status["units"]
    if unit == "C":
        return UnitOfTemperature.CELSIUS
    if unit == "F":
        return UnitOfTemperature.FAHRENHEIT
    raise ValueError(f"Unknown temp unit {unit}")


async def get_devices(
    hass: HomeAssistant,
    session: Session,
) -> list[SmartboxDevice]:
    """Get the devices."""
    session_devices = await hass.async_add_executor_job(session.get_devices)
    return [
        await create_smartbox_device(
            hass,
            session_device,
            session,
        )
        for session_device in session_devices
    ]


async def create_smartbox_device(
    hass: HomeAssistant,
    device: str,
    session: Session | MagicMock,
) -> SmartboxDevice | MagicMock:
    """Create factory function for smartboxdevices."""

    device = SmartboxDevice(device, session)
    await device.initialise_nodes(hass)
    return device


def _check_status_key(key: str, node_type: str, status: dict[str, Any]):
    if key not in status:
        raise KeyError(
            f"'{key}' not found in {node_type} - please report to {GITHUB_ISSUES_URL}. "
            f"status: {status}"
        )


def get_target_temperature(node_type: str, status: dict[str, Any]) -> float:
    """Get the target temperature."""
    if node_type == HEATER_NODE_TYPE_HTR_MOD:
        _check_status_key("selected_temp", node_type, status)
        if status["selected_temp"] == "comfort":
            _check_status_key("comfort_temp", node_type, status)
            return float(status["comfort_temp"])
        if status["selected_temp"] == "eco":
            _check_status_key("comfort_temp", node_type, status)
            _check_status_key("eco_offset", node_type, status)
            return float(status["comfort_temp"]) - float(status["eco_offset"])
        if status["selected_temp"] == "ice":
            _check_status_key("ice_temp", node_type, status)
            return float(status["ice_temp"])
        raise KeyError(
            f"'Unexpected 'selected_temp' value {status['selected_temp']}"
            f" found for {node_type} - please report to"
            f" {GITHUB_ISSUES_URL}. status: {status}"
        )
    _check_status_key("stemp", node_type, status)
    return float(status["stemp"])


def set_temperature_args(
    node_type: str, status: dict[str, Any], temp: float
) -> dict[str, Any]:
    """Set targeted temperature."""
    _check_status_key("units", node_type, status)
    if node_type == HEATER_NODE_TYPE_HTR_MOD:
        if status["selected_temp"] == "comfort":
            target_temp = temp
        elif status["selected_temp"] == "eco":
            _check_status_key("eco_offset", node_type, status)
            target_temp = temp + float(status["eco_offset"])
        elif status["selected_temp"] == "ice":
            raise ValueError(
                "Can't set temperature for htr_mod devices when ice mode is selected"
            )
        else:
            raise KeyError(
                f"'Unexpected 'selected_temp' value {status['selected_temp']}"
                f" found for {node_type} - please report to "
                f"{GITHUB_ISSUES_URL}. status: {status}"
            )
        return {
            "on": True,
            "mode": status["mode"],
            "selected_temp": status["selected_temp"],
            "comfort_temp": str(target_temp),
            "eco_offset": status["eco_offset"],
            "units": status["units"],
        }
    return {
        "stemp": str(temp),
        "units": status["units"],
    }


def get_hvac_mode(node_type: str, status: dict[str, Any]) -> str:
    """Get the mode of HVAC."""
    _check_status_key("mode", node_type, status)
    if (
        status["mode"] == "off"
        or node_type == HEATER_NODE_TYPE_HTR_MOD
        and not status["on"]
    ):
        return HVACMode.OFF
    if status["mode"] == "manual":
        return HVACMode.HEAT
    if status["mode"] == "auto":
        return HVACMode.AUTO
    if status["mode"] == "modified_auto":
        # This occurs when the temperature is modified while in auto mode.
        # Mapping it to auto seems to make this most sense
        return HVACMode.AUTO
    if status["mode"] == "self_learn" or status["mode"] == "presence":
        return HVACMode.AUTO
    _LOGGER.error("Unknown smartbox node mode %s", status["mode"])
    raise ValueError(f"Unknown smartbox node mode {status['mode']}")


def set_hvac_mode_args(
    node_type: str, status: dict[str, Any], hvac_mode: str
) -> dict[str, Any]:
    """Set the mode of HVAC."""
    if node_type == HEATER_NODE_TYPE_HTR_MOD:
        if hvac_mode == HVACMode.OFF:
            return {"on": False}
        if hvac_mode == HVACMode.HEAT:
            # We need to pass these status keys on when setting the mode
            required_status_keys = ["selected_temp"]
            for key in required_status_keys:
                _check_status_key(key, node_type, status)
            hvac_mode_args = {k: status[k] for k in required_status_keys}
            hvac_mode_args["on"] = True
            hvac_mode_args["mode"] = "manual"
            return hvac_mode_args
        if hvac_mode == HVACMode.AUTO:
            return {"on": True, "mode": "auto"}
        raise ValueError(f"Unsupported hvac mode {hvac_mode}")
    if hvac_mode == HVACMode.OFF:
        return {"mode": "off"}
    if hvac_mode == HVACMode.HEAT:
        return {"mode": "manual"}
    if hvac_mode == HVACMode.AUTO:
        return {"mode": "auto"}
    raise ValueError(f"Unsupported hvac mode {hvac_mode}")


def _get_htr_mod_preset_mode(node_type: str, mode: str, selected_temp: str) -> str:
    if mode == "manual":
        if selected_temp == "comfort":
            return PRESET_COMFORT
        if selected_temp == "eco":
            return PRESET_ECO
        if selected_temp == "ice":
            return PRESET_FROST
        raise ValueError(
            f"'Unexpected 'selected_temp' value {'selected_temp'} found for "
            f"{node_type} - please report to {GITHUB_ISSUES_URL}."
        )
    if mode == "auto":
        return PRESET_SCHEDULE
    if mode == "presence":
        return PRESET_ACTIVITY
    if mode == "self_learn":
        return PRESET_SELF_LEARN
    raise ValueError(f"Unknown smartbox node mode {mode}")


def set_preset_mode_status_update(
    node_type: str, status: dict[str, Any], preset_mode: str
) -> dict[str, Any]:
    """Set preset mode status update."""
    if node_type != HEATER_NODE_TYPE_HTR_MOD:
        raise ValueError(f"{node_type} nodes do not support preset {preset_mode}")
    # PRESET_HOME and PRESET_AWAY are not handled via status updates
    assert preset_mode not in (PRESET_HOME, PRESET_AWAY)

    if preset_mode == PRESET_SCHEDULE:
        return set_hvac_mode_args(node_type, status, HVACMode.AUTO)
    if preset_mode == PRESET_SELF_LEARN:
        return {"on": True, "mode": "self_learn"}
    if preset_mode == PRESET_ACTIVITY:
        return {"on": True, "mode": "presence"}
    if preset_mode == PRESET_COMFORT:
        return {"on": True, "mode": "manual", "selected_temp": "comfort"}
    if preset_mode == PRESET_ECO:
        return {"on": True, "mode": "manual", "selected_temp": "eco"}
    if preset_mode == PRESET_FROST:
        return {"on": True, "mode": "manual", "selected_temp": "ice"}
    raise ValueError(f"Unsupported preset {preset_mode} for node type {node_type}")


def get_factory_options(node: SmartboxNode | MagicMock) -> FactoryOptionsDict:
    """Get the factory options."""
    return cast(FactoryOptionsDict, node.setup.get("factory_options", {}))


def window_mode_available(node: SmartboxNode | MagicMock) -> bool:
    """Is window mode available."""
    return get_factory_options(node).get("window_mode_available", False)


def true_radiant_available(node: SmartboxNode | MagicMock) -> bool:
    """Is true radiant available."""
    return get_factory_options(node).get("true_radiant_available", False)
