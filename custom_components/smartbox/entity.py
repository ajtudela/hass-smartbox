"""Draft of generic entity"""

from homeassistant.helpers.entity import DeviceInfo, Entity

from custom_components.smartbox.const import DOMAIN
from custom_components.smartbox.model import SmartboxDevice, SmartboxNode


class DefaultSmartBoxEntity(Entity):
    """Default Smartbox Entity."""

    def __init__(self) -> None:
        """Initialize the default Device Entity."""
        self._device_id = self._node.node_id
        self._attr_has_entity_name = True
        self._attr_translation_key = self._attr_key
        self._attr_unique_id = self._node.node_id

    @property
    def unique_id(self) -> str:
        """Return Unique ID string."""
        return f"{self._device_id}_{self._attr_key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._node.name,
            model_id=self._node.device.model_id,
            sw_version=self._node.device.sw_version,
            serial_number=self._node.device.serial_number,
        )


class SmartBoxDeviceEntity(DefaultSmartBoxEntity):
    """BaseClass for SmartBoxDeviceEntity."""

    def __init__(self, device: SmartboxDevice) -> None:
        """Initialize the Device Entity."""
        self._node = list(device.get_nodes())[0]
        self._device = device
        super().__init__()


class SmartBoxNodeEntity(DefaultSmartBoxEntity):
    """BaseClass for SmartBoxNodeEntity."""

    def __init__(self, node: SmartboxNode) -> None:
        """Initialize the Node Entity."""
        self._node = node
        super().__init__()
