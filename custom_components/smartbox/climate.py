"""Support for Smartbox climate entities."""

import logging
from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.climate import (
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_HOME,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_LOCKED, ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SmartboxConfigEntry
from .const import (
    GITHUB_ISSUES_URL,
    PRESET_FROST,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
    SmartboxNodeType,
)
from .entity import SmartBoxNodeEntity
from .models import (
    SmartboxNode,
    _check_status_key,
    get_hvac_mode,
    get_target_temperature,
    get_temperature_unit,
    set_hvac_mode_args,
    set_preset_mode_status_update,
    set_temperature_args,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    _: HomeAssistant,
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up platform."""
    _LOGGER.info("Setting up Smartbox climate platform")

    async_add_entities(
        [
            SmartboxHeater(node, entry)
            for node in entry.runtime_data.nodes
            if node.heater_node
        ],
        update_before_add=True,
    )
    _LOGGER.debug("Finished setting up Smartbox climate platform")


class SmartboxHeater(SmartBoxNodeEntity, ClimateEntity):
    """Smartbox heater climate control."""

    _attr_key = "thermostat"
    _attr_name = None
    _attr_websocket_event = "status"
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.TURN_ON
    )

    def __init__(self, node: MagicMock | SmartboxNode, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        _LOGGER.debug("Setting up Smartbox climate platerqgsdform")
        super().__init__(node=node, entry=entry)
        self._status: dict[str, Any] = {}
        _LOGGER.debug("Created node unique_id=%s", self.unique_id)

    async def async_turn_off(self) -> None:
        """Turn off hvac."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        """Turn on hvac."""
        await self.async_set_hvac_mode(HVACMode.AUTO)

    @property
    def temperature_unit(self) -> str:
        """Return the unit of measurement."""
        unit = get_temperature_unit(self._status)
        if unit is not None:
            return unit
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self) -> float:
        """Return the current temperature."""
        return float(self._status["mtemp"])

    @property
    def target_temperature(self) -> float:
        """Return the target temperature."""
        return get_target_temperature(self._node.node_type, self._status)

    async def async_set_temperature(self, **kwargs: Any) -> None:  # noqa: ANN401
        """Set new target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            status_args = set_temperature_args(self._node.node_type, self._status, temp)
            await self._node.set_status(**status_args)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return current operation ie. heat or idle."""
        if self._node.is_heating(self._status):
            return HVACAction.HEATING
        if (
            self._node.status["mode"] == "off"
            or (
                self._node.node_type == SmartboxNodeType.HTR_MOD
                and not self._node.status["on"]
            )
        ) and not self._node.boost:
            return HVACAction.OFF
        return HVACAction.IDLE

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return hvac target hvac state."""
        return get_hvac_mode(self._node.node_type, self._status)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return the list of available operation modes."""
        return [HVACMode.HEAT, HVACMode.AUTO, HVACMode.OFF]

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set operation mode."""
        _LOGGER.debug("Setting HVAC mode to %s", hvac_mode)
        status_args = set_hvac_mode_args(self._node.node_type, self._status, hvac_mode)
        if self._node.boost:
            status_args["boost"] = False
        await self._node.set_status(**status_args)

    @property
    def preset_mode(self) -> str:  # noqa: PLR0911
        """Get preset mode."""
        if self._node.away:
            return PRESET_AWAY
        if self._node.boost:
            return PRESET_BOOST
        if self._node.node_type == SmartboxNodeType.HTR_MOD:
            _check_status_key("mode", self._node.node_type, self._status)
            mode = self._status["mode"]
            if mode == "auto":
                return PRESET_SCHEDULE
            if mode == "presence":
                return PRESET_ACTIVITY
            if mode == "self_learn":
                return PRESET_SELF_LEARN
            if mode == "manual":
                _check_status_key("selected_temp", self._node.node_type, self._status)
                selected_temp = self._status["selected_temp"]
                if selected_temp == "comfort":
                    return PRESET_COMFORT
                if selected_temp == "eco":
                    return PRESET_ECO
                if selected_temp == "ice":
                    return PRESET_FROST
                msg = (
                    f"'Unexpected 'selected_temp' value {'selected_temp'} found for "
                    f"{self._node.node_type} and {mode} - please report to {GITHUB_ISSUES_URL}."
                )
                raise ValueError(msg)
            msg = f"Unknown smartbox node mode {mode}"
            raise ValueError(msg)
        return PRESET_HOME

    @property
    def preset_modes(self) -> list[str]:
        """Get the preset_modes."""
        default_preset_modes = [PRESET_AWAY]
        if self._node.boost_available:
            default_preset_modes.append(PRESET_BOOST)
        if self._node.node_type == SmartboxNodeType.HTR_MOD:
            default_preset_modes.extend(
                [
                    PRESET_ACTIVITY,
                    PRESET_COMFORT,
                    PRESET_ECO,
                    PRESET_FROST,
                    PRESET_SELF_LEARN,
                    PRESET_SCHEDULE,
                ]
            )
        else:
            default_preset_modes.append(PRESET_HOME)
        return default_preset_modes

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the mode."""
        if preset_mode == PRESET_AWAY:
            await self._node.update_device_away_status(away=True)
            return
        if self._node.away:
            await self._node.update_device_away_status(away=False)
        if preset_mode == PRESET_BOOST:
            await self._node.set_status(boost=True)
            return
        if self._node.node_type == SmartboxNodeType.HTR_MOD:
            status_update = set_preset_mode_status_update(
                self._node.node_type, self._status, preset_mode
            )
            await self._node.set_status(**status_update)

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        """Return the state attributes of the device."""
        return {
            ATTR_LOCKED: self._status["locked"],
        }

    @property
    def available(self) -> bool:
        """Return True if roller and hub is available."""
        return self._available
