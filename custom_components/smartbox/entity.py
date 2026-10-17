"""Generic entity."""

from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import CONF_DISPLAY_ENTITY_PICTURES, DOMAIN

if TYPE_CHECKING:
    from . import SmartboxConfigEntry
    from .core.device import SmartboxDevice
    from .core.node import SmartboxNode


def box_device_info(device: SmartboxDevice) -> DeviceInfo:
    """Return the registry info of a box (shared by setup and box entities)."""
    reseller = device.session.reseller
    return DeviceInfo(
        identifiers={(DOMAIN, device.dev_id)},
        name=device.name,
        manufacturer=reseller.name,
        model_id=str(device.model_id),
        sw_version=str(device.sw_version),
        serial_number=str(device.serial_number),
        configuration_url=f"{reseller.web_url}#{device.home['id']}",
    )


def _entity_picture(entry: SmartboxConfigEntry, web_url: str) -> str | None:
    """Return the reseller logo when the entry asks for entity pictures."""
    if entry.options.get(CONF_DISPLAY_ENTITY_PICTURES, False) is True:
        return f"{web_url}img/favicon.ico"
    return None


class DefaultSmartBoxEntity(Entity):
    """Default Smartbox Entity (node device)."""

    _node: SmartboxNode
    _attr_key: str
    _attr_websocket_event: str
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, entry: SmartboxConfigEntry) -> None:
        """Initialize the default Device Entity."""
        self._node_id = self._node.node_id
        self._attr_translation_key = self._attr_key
        self._reseller = self._node.session.reseller
        self._configuration_url = f"{self._reseller.web_url}#/{self._node.device.home['id']}/dev/{self._node_id}/{self._node.node_type}/{self._node.addr}/setup"
        self._attr_entity_picture = _entity_picture(entry, self._reseller.web_url)

    @property
    def unique_id(self) -> str:
        """Return Unique ID string."""
        return f"{self._node_id}_{self._attr_key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info of the node."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._node_id)},
            name=self._node.name,
            manufacturer=self._reseller.name,
            model_id=str(self._node.pid),
            hw_version=str(self._node.hw_version),
            configuration_url=self._configuration_url,
        )

    @property
    def _status(self) -> dict[str, Any]:
        """Return the node's status: the single source of truth for state.

        Entities never keep their own copy. The node merges every websocket
        frame and optimistic write into this dict (keeping the last full
        snapshot when a transient ``lost`` frame arrives) and the dispatcher
        only tells entities that something changed.
        """
        return self._node.status

    @callback
    def _async_update(self) -> None:
        """Write the state after a node event."""
        self.async_write_ha_state()


class SmartboxBoxEntity(Entity):
    """Entity attached to the Smartbox box device itself.

    The box (the physical gateway the nodes talk through) is its own HA
    device; device-level data (connectivity, away status, power limit,
    RTC) belongs there instead of being replicated on every node device.
    """

    _device: SmartboxDevice
    _attr_key: str
    _attr_websocket_event: str | None = None
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, device: SmartboxDevice, entry: SmartboxConfigEntry) -> None:
        """Initialize the box entity."""
        self._device = device
        self._device_id = device.dev_id
        self._attr_translation_key = self._attr_key
        self._reseller = device.session.reseller
        self._attr_entity_picture = _entity_picture(entry, self._reseller.web_url)

    @property
    def unique_id(self) -> str:
        """Return Unique ID string."""
        return f"{self._device_id}_{self._attr_key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the box device info."""
        return box_device_info(self._device)

    @callback
    def _async_update(self, _data: Any) -> None:  # noqa: ANN401
        """Update the state from a websocket event."""
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        await super().async_added_to_hass()
        if (
            self._attr_should_poll is False
            and (websocket_event := self._attr_websocket_event) is not None
        ):
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    f"{DOMAIN}_{self._device_id}_{websocket_event}",
                    self._async_update,
                )
            )


class SmartBoxNodeEntity(DefaultSmartBoxEntity):
    """BaseClass for SmartBoxNodeEntity."""

    def __init__(self, node: SmartboxNode, entry: SmartboxConfigEntry) -> None:
        """Initialize the Node Entity."""
        self._node = node
        # The box keeps reporting connected while a NODE is unreachable:
        # node availability comes from the library's tracking (bare lost
        # frames / unconfirmed writes), see api-notes.md 2026-09-30.
        self._node_available = node.available is not False
        # Polling entities flip this on REST failures; the cloud answers
        # even when the node itself is disconnected, so it is only ever an
        # additional reason to be unavailable.
        self._api_available = True
        super().__init__(entry=entry)

    @property
    def available(self) -> bool:
        """Return True when both the node and the REST API are reachable."""
        return self._node_available and self._api_available

    @callback
    def _async_availability_update(self, available: bool) -> None:
        """Update availability from a node-availability event."""
        self._node_available = available
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        await super().async_added_to_hass()
        # Polling entities need the node signal too: their REST calls
        # succeed while the node is disconnected.
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_{self._node.node_id}_availability",
                self._async_availability_update,
            )
        )
        if self._attr_should_poll is False:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    f"{DOMAIN}_{self._node.node_id}_{self._attr_websocket_event}",
                    self._async_update,
                )
            )
