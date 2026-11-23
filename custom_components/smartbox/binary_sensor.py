"""Support for Smartbox binary sensor entities.

Entities read state through the adapter's accessors over ``runtime.engine``;
updates arrive via the shared ``EngineChangeFanOut``. The REST discovery
identity is kept only for registry identity and device-info fields.
"""

from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING, Any

# The component root re-exports this public name without __all__; mypy
# strict's no_implicit_reexport cannot see that re-export.
from homeassistant.components.binary_sensor import (  # type: ignore[attr-defined]
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from smartbox.protocol.values import parse_number_or_none

from .adapter import (
    EngineChangeFanOut,
    SmartboxBoxAdapterEntity,
    SmartboxNodeAdapterEntity,
)
from .const import (
    PRODUCT_CODE_WATER_STORAGE,
    WATER_FULLY_READY_LEVEL,
    WATER_READY_LEVEL,
    SmartboxNodeType,
)
from .entity_builder import EntityBuilder

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import SmartboxConfigEntry, SmartboxRuntime
    from .entity_builder import EntityGroup
    from .identity import SmartboxDeviceInfo, SmartboxNodeInfo

_LOGGER = logging.getLogger(__name__)

# Push-only platform: the connectivity entity has no update method and no
# services, so there is nothing to queue.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - HA platform signature
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox binary sensor platform")

    runtime = entry.runtime_data
    fanout = runtime.fanout

    # Box connectivity is a device-level fact: one entity on the box device
    # itself.
    def build_device(device: SmartboxDeviceInfo) -> list[EntityGroup]:
        """Build the box connectivity entity."""
        return _build_device_binary_sensors(device, entry, runtime, fanout)

    def build_node(node: SmartboxNodeInfo) -> list[EntityGroup]:
        """Build the node facts that have no existing entity of their own."""
        return _build_node_binary_sensors(node, entry, runtime, fanout)

    runtime.entity_builder.register(
        async_add_entities, build_node=build_node, build_device=build_device
    )
    for device in runtime.identity.devices:
        EntityBuilder.add_groups(build_device(device), async_add_entities)
    for node in runtime.identity.nodes:
        EntityBuilder.add_groups(build_node(node), async_add_entities)
    _LOGGER.debug("Finished setting up Smartbox binary sensor platform")


def _build_device_binary_sensors(
    device: SmartboxDeviceInfo,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
    fanout: EngineChangeFanOut,
) -> list[EntityGroup]:
    """Build the box connectivity entity."""
    return [([Connected(device, entry, runtime, fanout)], False)]


def _build_node_binary_sensors(
    node: SmartboxNodeInfo,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
    fanout: EngineChangeFanOut,
) -> list[EntityGroup]:
    """Build the live flags not already covered by a switch or the climate."""
    if not node.supported_wire_type:
        return []
    entities: list[Entity] = []
    if node.heater_surface:
        # The heater controllers read window_open from every status body; the
        # thermostat controller does not. Create the entity regardless of the
        # discovery snapshot so an offline node does not lose it until reload;
        # it reports None until the field arrives.
        entities.append(WindowDetection(node, entry, runtime, fanout))
        # PIR is a controller feature, gated on extra_options.pir_enabled in
        # the app (TevolvePIRHeaterController.getStatusIndicators).
        if _pir_enabled(node.setup):
            entities.append(PirPresence(node, entry, runtime, fanout))
    if node.node_type == SmartboxNodeType.ACM:
        # Water storage replaces CHARGING with active/water-ready states.
        if node.get_model_code() == PRODUCT_CODE_WATER_STORAGE:
            entities.append(WaterReady(node, entry, runtime, fanout))
            entities.append(WaterFullyReady(node, entry, runtime, fanout))
        else:
            entities.append(Charging(node, entry, runtime, fanout))
    # The only low_batt consumer in the app is the thermostat card's battery
    # icon; heaters are mains-powered and no controller reads the field.
    if node.node_type == SmartboxNodeType.THM:
        entities.append(LowBattery(node, entry, runtime, fanout))
    return [(entities, False)] if entities else []


def _pir_enabled(setup: Mapping[str, Any]) -> bool:
    """Whether the node's PIR presence controller is enabled in setup."""
    extra = setup.get("extra_options")
    return isinstance(extra, Mapping) and extra.get("pir_enabled") is True


class Connected(SmartboxBoxAdapterEntity, BinarySensorEntity):
    """Smartbox box connectivity sensor."""

    device_class = BinarySensorDeviceClass.CONNECTIVITY
    entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        device: SmartboxDeviceInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the connectivity sensor."""
        super().__init__(device, entry, runtime, fanout, "connected")

    @property
    def available(self) -> bool:
        """Connectivity is the STATE here, not availability.

        Available whenever the flag is known; a dead feed is the one case where
        it is not, because the flag arrives over that feed.
        """
        if not self._runtime.engine.feed.available:
            return False
        device = self._device_state()
        return device is not None and device.connected is not None

    @property
    def is_on(self) -> bool | None:
        """Return true if the box is connected."""
        device = self._device_state()
        return device.connected if device is not None else None


class SmartboxNodeBinarySensor(SmartboxNodeAdapterEntity, BinarySensorEntity):
    """Base for a node-level boolean read from one status field."""

    _key: str
    payload_key: str
    # The app compares these flags with === true; a 0/1 or string is not on.
    _strict_true = False

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the node binary sensor."""
        super().__init__(node, entry, runtime, fanout, self._key)

    @property
    def is_on(self) -> bool | None:
        """Return the field's boolean value, or None until it arrives."""
        if not self._status:
            return None
        value = self._status.get(self.payload_key)
        if value is None:
            return None
        return value is True if self._strict_true else bool(value)


class WindowDetection(SmartboxNodeBinarySensor):
    """The heater's inferred open-window event (the switch enables detection).

    This is not a physical window contact: the heater derives it from its own
    temperature/heat behaviour. It is diagnostic so it does not sit among real
    window sensors in dashboards or voice exposure.
    """

    _key = "window_detection"
    payload_key = "window_open"
    _strict_true = True
    device_class = BinarySensorDeviceClass.WINDOW
    _attr_entity_category = EntityCategory.DIAGNOSTIC


class Charging(SmartboxNodeBinarySensor):
    """Storage heater charging state."""

    _key = "charging"
    payload_key = "charging"
    _strict_true = True
    device_class = BinarySensorDeviceClass.BATTERY_CHARGING


class _WaterLevelBinarySensor(SmartboxNodeBinarySensor):
    """Water-storage tank level, compared against one wire percentage."""

    payload_key = "charge_level"
    _level: int

    @property
    def is_on(self) -> bool | None:
        """Return true when the tank is at this level."""
        if not self._status:
            return None
        level = parse_number_or_none(self._status.get(self.payload_key))
        return None if level is None else level == self._level


class WaterReady(_WaterLevelBinarySensor):
    """Water storage: tank has reached the ready level."""

    _key = "water_ready"
    _level = WATER_READY_LEVEL


class WaterFullyReady(_WaterLevelBinarySensor):
    """Water storage: tank has reached the fully ready level."""

    _key = "water_fully_ready"
    _level = WATER_FULLY_READY_LEVEL


class PirPresence(SmartboxNodeBinarySensor):
    """PIR presence-detection flag (wire key ``french_rule_pir_state``)."""

    _key = "pir_presence"
    payload_key = "french_rule_pir_state"
    _strict_true = True
    device_class = BinarySensorDeviceClass.OCCUPANCY


class LowBattery(SmartboxNodeBinarySensor):
    """Thermostat low-battery flag (the app's thermostat card reads ``low_batt``)."""

    _key = "low_battery"
    payload_key = "low_batt"
    device_class = BinarySensorDeviceClass.BATTERY
    entity_category = EntityCategory.DIAGNOSTIC
