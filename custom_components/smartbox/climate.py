"""Support for Smartbox climate entities."""

import logging
from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.climate import (
    PRESET_ACTIVITY,
    PRESET_AWAY,
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

from .const import (
    DOMAIN,
    HEATER_NODE_TYPE_HTR_MOD,
    PRESET_FROST,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
    SMARTBOX_NODES,
)
from .entity import SmartBoxNodeEntity
from .model import (
    SmartboxNode,
    _check_status_key,
    _get_htr_mod_preset_mode,
    get_hvac_mode,
    get_target_temperature,
    get_temperature_unit,
    is_heater_node,
    set_hvac_mode_args,
    set_preset_mode_status_update,
    set_temperature_args,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up platform."""
    _LOGGER.info("Setting up Smartbox climate platform")

    async_add_entities(
        [
            SmartboxHeater(node)
            for node in hass.data[DOMAIN][SMARTBOX_NODES]
            if is_heater_node(node)
        ],
        True,
    )
    _LOGGER.debug("Finished setting up Smartbox climate platform")


class SmartboxHeater(SmartBoxNodeEntity, ClimateEntity):
    """Smartbox heater climate control."""

    _attr_key = "thermostat"
    _attr_name = None

    def __init__(self, node: MagicMock | SmartboxNode) -> None:
        """Initialize the sensor."""
        _LOGGER.debug("Setting up Smartbox climate platerqgsdform")
        super().__init__(node=node)
        self._status: dict[str, Any] = {}
        self._available = False  # unavailable until we get an update
        self._enable_turn_on_off_backwards_compatibility = False
        self._supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        _LOGGER.debug("Created node unique_id=%s", self.unique_id)

    async def async_turn_off(self) -> None:
        """Turn off hvac."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        """Turn on hvac."""
        await self.async_set_hvac_mode(HVACMode.AUTO)

    @property
    def supported_features(self) -> int:
        """Return the list of supported features."""
        return self._supported_features

    @property
    def should_poll(self) -> bool:
        """Return the polling state."""
        return True

    @property
    def temperature_unit(self) -> str:
        """Return the unit of measurement."""
        unit = get_temperature_unit(self._status)
        if unit is not None:
            return unit
        return (
            UnitOfTemperature.CELSIUS  # climate sensors need a temperature unit on construction
        )

    @property
    def current_temperature(self) -> float:
        """Return the current temperature."""
        return float(self._status["mtemp"])

    @property
    def target_temperature(self) -> float:
        """Return the target temperature."""
        return get_target_temperature(self._node.node_type, self._status)

    def set_temperature(self, **kwargs):
        """Set new target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            status_args = set_temperature_args(self._node.node_type, self._status, temp)
            self._node.set_status(**status_args)

    @property
    def hvac_action(self) -> str:
        """Return current operation ie. heat or idle."""
        return (
            HVACAction.HEATING
            if self._node.is_heating(self._status)
            else HVACAction.IDLE
        )

    @property
    def hvac_mode(self) -> str:
        """Return hvac target hvac state."""
        return get_hvac_mode(self._node.node_type, self._status)

    @property
    def hvac_modes(self) -> list[str]:
        """Return the list of available operation modes."""
        return [HVACMode.HEAT, HVACMode.AUTO, HVACMode.OFF]

    def set_hvac_mode(self, hvac_mode):
        """Set operation mode."""
        _LOGGER.debug("Setting HVAC mode to %s", hvac_mode)
        status_args = set_hvac_mode_args(self._node.node_type, self._status, hvac_mode)
        self._node.set_status(**status_args)

    @property
    def preset_mode(self) -> str:
        """Get preset mode."""
        if self._node.away:
            return PRESET_AWAY
        if self._node.node_type == HEATER_NODE_TYPE_HTR_MOD:
            _check_status_key("mode", self._node.node_type, self._status)
            _check_status_key("selected_temp", self._node.node_type, self._status)
            return _get_htr_mod_preset_mode(
                self._node.node_type,
                self._status["mode"],
                self._status["selected_temp"],
            )
        return PRESET_HOME

    @property
    def preset_modes(self) -> list[str]:
        """Get the preset_modes."""
        if self._node.node_type == HEATER_NODE_TYPE_HTR_MOD:
            return [
                PRESET_ACTIVITY,
                PRESET_AWAY,
                PRESET_COMFORT,
                PRESET_ECO,
                PRESET_FROST,
                PRESET_SCHEDULE,
                PRESET_SELF_LEARN,
            ]

        return [PRESET_AWAY, PRESET_HOME]

    def set_preset_mode(self, preset_mode: str) -> None:
        """Set the mode."""
        if preset_mode == PRESET_AWAY:
            self._node.update_device_away_status(True)
            return
        if self._node.away:
            self._node.update_device_away_status(False)
        if self._node.node_type == HEATER_NODE_TYPE_HTR_MOD:
            status_update = set_preset_mode_status_update(
                self._node.node_type, self._status, preset_mode
            )
            self._node.set_status(**status_update)
        elif preset_mode != PRESET_HOME:
            raise ValueError(
                f"Unsupported preset_mode {preset_mode} for {self._node.node_type} node"
            )

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

    async def async_update(self) -> None:
        """Get the latest data."""
        new_status = await self._node.async_update(self.hass)
        if new_status["sync_status"] == "ok":
            # update our status
            self._status = new_status
            self._available = True
        else:
            self._available = False
