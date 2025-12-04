"""Models for Smartbox."""

import asyncio
from datetime import datetime, timedelta
import logging
import math
import time
from typing import Any, cast
from unittest.mock import MagicMock

from dateutil import tz
from homeassistant.components.climate import (
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_HOME,
    PRESET_NONE,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from smartbox import AsyncSmartboxSession, SmartboxNodeType, UpdateManager

from .const import (
    DEFAULT_BOOST_TEMP,
    DEFAULT_BOOST_TIME,
    DOMAIN,
    GITHUB_ISSUES_URL,
    HEATER_NODE_TYPES,
    PRESET_FROST,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
    BoostConfig,
)

_LOGGER = logging.getLogger(__name__)

FactoryOptionsDict = dict[str, bool]
SetupDict = dict[str, Any]
StatusDict = dict[str, Any]
SamplesDict = dict[str, Any]
Node = dict[str, Any]
Device = dict[str, Any]


class SmartboxDevice:
    """Smartbox device."""

    def __init__(
        self,
        device: Device,
        session: AsyncSmartboxSession | MagicMock,
        hass: HomeAssistant,
    ) -> None:
        """Initialise a smartbox device."""
        self._device = device
        self._session = session
        self._away: bool = False
        self._power_limit: int = 0
        self._nodes = {}
        self._watchdog_task: asyncio.Task | None = None
        self._hass = hass
        self._connected_status: bool | None = None
        self.update_manager: UpdateManager = UpdateManager(
            self._session,
            self.dev_id,
        )

    @classmethod
    async def initialise_nodes(
        cls,
        device: Device,
        session: AsyncSmartboxSession | MagicMock,
        hass: HomeAssistant,
    ) -> None:
        """Initilaise nodes."""
        self = cls(device=device, session=session, hass=hass)
        # Would do in __init__, but needs to be a coroutine
        self._connected_status = (
            await self._session.get_device_connected(self.dev_id)
        )["connected"]
        session_nodes: list[Node] = await self._session.get_nodes(self.dev_id)

        for node_info in session_nodes:
            if node_info["type"] == SmartboxNodeType.PMO:
                self._power_limit = await self._session.get_device_power_limit(
                    self.dev_id
                )
            self._away = (await self._session.get_device_away_status(self.dev_id))[
                "away"
            ]
            node: SmartboxNode = await SmartboxNode.create(
                device=self, node_info=node_info, session=self._session
            )

            self._nodes[(node.node_type, node.addr)] = node
        _LOGGER.debug("Creating SocketSession for device %s", self.dev_id)
        self.update_manager.subscribe_to_device_connected(self._connected)
        self.update_manager.subscribe_to_device_away_status(self._away_status_update)
        self.update_manager.subscribe_to_node_setup(self._node_setup_update)
        self.update_manager.subscribe_to_device_power_limit(self._power_limit_update)
        self.update_manager.subscribe_to_node_status(self._node_status_update)

        _LOGGER.debug("Starting UpdateManager task for device %s", self.dev_id)
        self._watchdog_task = asyncio.create_task(self.update_manager.run())
        return self

    def _connected(self, connected: bool) -> None:
        _LOGGER.debug("Connected connected update: %s", connected)
        self._connected_status = connected
        async_dispatcher_send(
            self._hass,
            f"{DOMAIN}_{self.dev_id}_connected",
            self._connected_status,
        )

    def _away_status_update(self, away_status: dict[str, bool]) -> None:
        _LOGGER.debug("Away status update: %s", away_status)

        if self._away != away_status["away"]:
            self._away = away_status["away"]
            for node in self._nodes.values():
                async_dispatcher_send(
                    self._hass, f"{DOMAIN}_{node.node_id}_away_status", self._away
                )

    def _power_limit_update(self, power_limit: int) -> None:
        _LOGGER.debug("power_limit update: %s", power_limit)
        if self._power_limit != power_limit:
            self._power_limit = power_limit
            async_dispatcher_send(
                self._hass, f"{DOMAIN}_{self.dev_id}_power_limit", power_limit
            )

    def _node_status_update(
        self, node_type: str, addr: int, node_status: StatusDict
    ) -> None:
        if node_type == SmartboxNodeType.PMO:
            return
        _LOGGER.debug("Node status update: %s", node_status)
        if node_status is not None and (node_type, addr) in self._nodes:
            node: SmartboxNode | None = self._nodes.get((node_type, addr), None)
            if node is not None and node.status != node_status:
                node.update_status(node_status)
                async_dispatcher_send(
                    self._hass, f"{DOMAIN}_{node.node_id}_status", node_status
                )
        else:
            _LOGGER.error(
                "Received status update for unknown node %s %s", node_type, addr
            )

    def _node_setup_update(
        self, node_type: str, addr: int, node_setup: SetupDict
    ) -> None:
        _LOGGER.debug("Node setup update: %s", node_setup)
        if (node_type, addr) in self._nodes:
            node: SmartboxNode | None = self._nodes.get((node_type, addr), None)
            if node is not None and node.setup != node_setup:
                node.update_setup(node_setup)
                async_dispatcher_send(
                    self._hass, f"{DOMAIN}_{node.node_id}_setup", node_setup
                )
        else:
            _LOGGER.error(
                "Received setup update for unknown node %s %s", node_type, addr
            )

    @property
    def device(self) -> Device:
        """Return the device."""
        return self._device

    @property
    def connected(self) -> bool | None:
        """Return the device."""
        return self._connected_status

    @property
    def home(self) -> dict[str, Any]:
        """Return home of the device."""
        return self._device["home"]

    @property
    def dev_id(self) -> str:
        """Return the device id."""
        return self._device["dev_id"]

    def get_nodes(self) -> list["SmartboxNode"]:
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

    async def set_away_status(self, away: bool) -> None:
        """Set the away status."""
        await self._session.set_device_away_status(self.dev_id, {"away": away})
        self._away_status_update(away_status={"away": away})

    @property
    def power_limit(self) -> int:
        """Get the power limit of the device."""
        return self._power_limit

    async def set_power_limit(self, power_limit: int) -> None:
        """Set the power limit of the device."""
        await self._session.set_device_power_limit(self.dev_id, power_limit)
        self._power_limit = power_limit


class SmartboxNode:
    """Smartbox Node."""

    def __init__(
        self,
        device: SmartboxDevice | MagicMock,
        node_info: Node,
        session: AsyncSmartboxSession | MagicMock,
        status: StatusDict,
        setup: SetupDict,
        samples: SamplesDict,
    ) -> None:
        """Initialise a smartbox node."""
        self._device = device
        self._node_info = node_info
        self._session = session
        self._status = status
        self._setup = setup
        self._samples = samples

    @classmethod
    async def create(
        cls,
        device: SmartboxDevice | MagicMock,
        session: AsyncSmartboxSession | MagicMock,
        node_info: Node,
    ) -> None:
        """Create a smartbox node."""
        if node_info["type"] != SmartboxNodeType.PMO:
            status: StatusDict = await session.get_node_status(device.dev_id, node_info)
        else:
            status: StatusDict = {
                "sync_status": "ok",
                "locked": False,
                "power": await session.get_device_power_limit(device.dev_id, node_info),
            }
        setup: SetupDict = await session.get_node_setup(device.dev_id, node_info)
        samples: SamplesDict = (
            await session.get_node_samples(
                device.dev_id,
                node_info,
                int(time.time() - (3600 * 3)),
                int(time.time()),
            )
        )["samples"]
        return cls(device, node_info, session, status, setup, samples)

    @property
    def node_info(self) -> Node:
        """Return the node info."""
        return self._node_info

    @property
    def node_id(self) -> str:
        """Return the id of the node."""
        return f"{self._device.dev_id}_{self._node_info['addr']}"

    @property
    def name(self) -> str:
        """Return the name of the node."""
        return self._node_info["name"] if self._node_info["name"] else self.device.name

    @property
    def node_type(self) -> str:
        """Return node type, e.g. 'htr' for heaters."""
        return self._node_info["type"]

    @property
    def addr(self) -> int:
        """Return the addr of node."""
        return self._node_info["addr"]

    @property
    def product_id(self) -> str:
        """Return the product id of the node."""
        return self._node_info.get("product_id", "")

    def get_model_code(self) -> str:
        """Extract the model code from version.pid or product_id.

        Returns characters 3-4 (index 2-3) from version.pid if available,
        otherwise from product_id. For example: "XX1CXX" -> "1C" or "081C" -> "1C"
        This mirrors the behaviour used in the official web app.

        This is used to identify specific storage heater models that have
        different data structures or behaviors.
        """
        min_length = 4
        model_code_start = 2
        model_code_end = 4

        version_info = self._node_info.get("version", {})
        if isinstance(version_info, dict):
            pid = version_info.get("pid", "")
            if pid:
                pid_upper = pid.upper() if pid else ""
                if len(pid_upper) >= min_length:
                    return pid_upper[model_code_start:model_code_end]

        product_id = self.product_id.upper() if self.product_id else ""
        if len(product_id) >= min_length:
            return product_id[model_code_start:model_code_end]

        return ""

    @property
    def status(self) -> StatusDict:
        """Return the status of node."""
        return self._status

    def update_status(self, status: StatusDict) -> None:
        """Update status."""
        _LOGGER.debug("Updating node %s status: %s", self.name, status)
        self._status |= {**status}

    @property
    def setup(self) -> SetupDict:
        """Setup of node."""
        return self._setup

    def update_setup(self, setup: SetupDict) -> None:
        """Update setup."""
        _LOGGER.debug("Updating node %s setup: %s", self.name, setup)
        self._setup = setup

    async def set_status(self, **status_args: StatusDict) -> StatusDict:
        """Set status."""
        await self._session.set_node_status(
            self._device.dev_id, self._node_info, status_args
        )
        # update our status locally until we get an update
        self._status |= {**status_args}
        return self._status

    @property
    def away(self) -> bool:
        """Is away mode."""
        return self._device.away

    @property
    def device(self) -> SmartboxDevice:
        """Return the device of the node."""
        return self._device

    @property
    def session(self) -> AsyncSmartboxSession:
        """Return the smartbox session."""
        return self._session

    async def update_device_away_status(self, away: bool) -> None:
        """Update device away status."""
        await self._device.set_away_status(away)

    async def async_update(self, _: Any) -> StatusDict:  # noqa: ANN401
        """Update status."""
        return self.status

    @property
    def window_mode(self) -> bool:
        """Is windows mode enable."""
        if "window_mode_enabled" not in self._setup:
            msg = "window_mode_enabled not present in setup for node {self.name}"
            raise KeyError(msg)
        return self._setup["window_mode_enabled"]

    async def set_window_mode(self, window_mode: bool) -> bool:
        """Set window mode."""
        await self._session.set_node_setup(
            self._device.dev_id,
            self._node_info,
            {"window_mode_enabled": window_mode},
        )
        self._setup["window_mode_enabled"] = window_mode
        return window_mode

    @property
    def true_radiant(self) -> bool:
        """Is a true radiant."""
        if "true_radiant_enabled" not in self._setup:
            msg = "true_radiant_enabled not present in setup for node {self.name}"
            raise KeyError(msg)
        return self._setup["true_radiant_enabled"]

    async def set_true_radiant(self, true_radiant: bool) -> None:
        """Set true radiant."""
        await self._session.set_node_setup(
            self._device.dev_id,
            self._node_info,
            {"true_radiant_enabled": true_radiant},
        )
        self._setup["true_radiant_enabled"] = true_radiant

    async def set_extra_options(self, options: dict[str, Any]) -> None:
        """Set window mode."""
        await self._session.set_node_setup(
            self._device.dev_id,
            self._node_info,
            {"extra_options": options},
        )

    def is_heating(self, status: dict[str, Any]) -> str:
        """Is heating."""
        return (
            status["charging"]
            if self.node_type == SmartboxNodeType.ACM
            else status["active"]
        )

    async def update_power(self) -> None:
        """Update power."""
        self._status["power"] = await self._session.get_device_power_limit(
            self.device.dev_id,
            self._node_info,
        )

    async def update_samples(self) -> None:
        """Update the samples."""
        max_sample = 2
        sample = await self.get_samples(
            int(time.time() - (3600 * 3)),
            int(time.time()),
        )
        if len(sample) >= max_sample:
            self._samples = sample[-2:]
            _LOGGER.debug("Updating node %s samples: %s", self.name, self._samples)

    async def get_samples(self, start_time: int, end_time: int) -> SamplesDict:
        """Update the samples."""
        return (
            await self._session.get_node_samples(
                self.device.dev_id,
                self._node_info,
                start_time,
                end_time,
            )
        )["samples"]

    @property
    def total_energy(self) -> float | None:
        """Get the energy used."""
        if not self._samples:
            return None
        return self._samples[-1]["counter"]

    @property
    def boost_config(self) -> BoostConfig:
        """Get the boost config."""
        _boost_config = self._setup.get("factory_options", {}).get("boost_config", 0)
        return BoostConfig(_boost_config)

    @property
    def boost(self) -> bool:
        """Boost status."""
        return self.status.get("boost", False)

    @property
    def boost_available(self) -> bool:
        """Is boost available."""
        return bool(self.boost_config.value)

    @property
    def heater_node(self) -> bool:
        """Is this node a heater."""
        return self.node_type in HEATER_NODE_TYPES

    @property
    def boost_time(self) -> float:
        """Get the boost time."""
        return float(
            self.setup.get("extra_options", {}).get("boost_time", DEFAULT_BOOST_TIME)
        )

    @property
    def boost_temp(self) -> float:
        """Get the boost time."""
        return float(
            self.setup.get("extra_options", {}).get("boost_temp", DEFAULT_BOOST_TEMP)
        )

    @property
    def boost_end_min(self) -> int:
        """Get the boost end time."""
        return self.status.get("boost_end_min", 0)

    @property
    def remaining_boost_time(self) -> int:
        """Return the remaining boost time."""
        if not self.boost:
            return 0
        boost_end = self.boost_end_min
        today = datetime.now(tz.tzutc()) + timedelta(hours=1)
        boost_end_min = boost_end % 60
        boost_end_hour = math.trunc(boost_end / 60)
        boost_end_datetime = today.replace(
            hour=boost_end_hour, minute=boost_end_min
        ).astimezone(tz.tzlocal())
        return (boost_end_datetime - today).total_seconds()


def get_temperature_unit(status: StatusDict) -> None | UnitOfTemperature:
    """Get the unit of temperature."""
    if "units" not in status:
        return None
    unit = status["units"]
    if unit == "C":
        return UnitOfTemperature.CELSIUS
    if unit == "F":
        return UnitOfTemperature.FAHRENHEIT
    msg = f"Unknown temp unit {unit}"
    raise ValueError(msg)


async def get_devices(
    session: AsyncSmartboxSession | MagicMock, hass: HomeAssistant
) -> list[SmartboxDevice]:
    """Get the devices."""
    homes: list[dict[str, Any]] = await session.get_homes()
    devices: list[SmartboxDevice] = []
    for home in homes:
        _home = home.copy()
        del _home["devs"]
        for session_device in home["devs"]:
            session_device["home"] = _home
            devices.append(
                await SmartboxDevice.initialise_nodes(session_device, session, hass)
            )
    return devices


def _check_status_key(key: str, node_type: str, status: dict[str, Any]) -> None:
    if key not in status:
        msg = (
            f"'{key}' not found in {node_type} - please report to {GITHUB_ISSUES_URL}. "
            f"status: {status}"
        )
        raise KeyError(msg)


def get_target_temperature(node_type: str, status: dict[str, Any]) -> float:
    """Get the target temperature."""
    if node_type == SmartboxNodeType.HTR_MOD:
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
        if status["selected_temp"] == "off":
            return float(0)
        msg = (
            f"'Unexpected 'selected_temp' value {status['selected_temp']}"
            f" found for {node_type} - please report to"
            f" {GITHUB_ISSUES_URL}. status: {status}"
        )
        raise KeyError(msg)
    _check_status_key("stemp", node_type, status)
    return float(status["stemp"])


def set_temperature_args(
    node_type: str, status: dict[str, Any], temp: float
) -> dict[str, Any]:
    """Set targeted temperature."""
    _check_status_key("units", node_type, status)
    if node_type == SmartboxNodeType.HTR_MOD:
        if status["selected_temp"] == "comfort":
            target_temp = temp
        elif status["selected_temp"] == "eco":
            _check_status_key("eco_offset", node_type, status)
            target_temp = temp + float(status["eco_offset"])
        elif status["selected_temp"] == "ice":
            msg = "Can't set temperature for htr_mod devices when ice mode is selected"
            raise ValueError(msg)
        else:
            msg = (
                f"'Unexpected 'selected_temp' value {status['selected_temp']}"
                f" found for {node_type} - please report to "
                f"{GITHUB_ISSUES_URL}. status: {status}"
            )
            raise KeyError(msg)
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


def get_hvac_mode(node_type: str, status: dict[str, Any]) -> HVACMode | None:
    """Get the mode of HVAC."""
    if status.get("boost", False):
        return HVACMode.HEAT
    _check_status_key("mode", node_type, status)
    if status["mode"] == "off" or (
        node_type == SmartboxNodeType.HTR_MOD and not status["on"]
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
    msg = "Unknown smartbox node mode %s", status["mode"]
    _LOGGER.error(msg)
    raise ValueError(msg)


def set_hvac_mode_args(
    node_type: str, status: dict[str, Any], hvac_mode: str
) -> dict[str, Any]:
    """Set the mode of HVAC."""
    error_msg = f"Unsupported hvac mode {hvac_mode}"
    if node_type == SmartboxNodeType.HTR_MOD:
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
        raise ValueError(error_msg)
    if hvac_mode == HVACMode.OFF:
        return {"mode": "off"}
    if hvac_mode == HVACMode.HEAT:
        return {"mode": "manual"}
    if hvac_mode == HVACMode.AUTO:
        return {"mode": "auto"}
    raise ValueError(error_msg)


def set_preset_mode_status_update(
    node_type: str, status: dict[str, Any], preset_mode: str
) -> dict[str, Any]:
    """Set preset mode status update."""
    if node_type != SmartboxNodeType.HTR_MOD:
        msg = f"{node_type} nodes do not support preset {preset_mode}"
        raise ValueError(msg)
    # PRESET_HOME and PRESET_AWAY are not handled via status updates
    assert preset_mode not in (PRESET_HOME, PRESET_AWAY, PRESET_NONE)  # noqa: S101

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
    msg = f"Unsupported preset {preset_mode} for node type {node_type}"
    raise ValueError(msg)


def get_factory_options(node: SmartboxNode | MagicMock) -> FactoryOptionsDict:
    """Get the factory options."""
    return cast("FactoryOptionsDict", node.setup.get("factory_options", {}))


def window_mode_available(node: SmartboxNode | MagicMock) -> bool:
    """Is window mode available."""
    return get_factory_options(node).get("window_mode_available", False)


def true_radiant_available(node: SmartboxNode | MagicMock) -> bool:
    """Is true radiant available."""
    return get_factory_options(node).get("true_radiant_available", False)
