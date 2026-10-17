"""Support for Smartbox select entities."""

import logging
from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory

from .core.node import priority_available
from .entity import SmartBoxNodeEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import SmartboxConfigEntry
    from .core.node import SmartboxNode

_LOGGER = logging.getLogger(__name__)

# Radiator priority values observed on the wire ("low") and offered by the
# web app. Enforcement under the box power limit is device-side; this is
# only the setting itself.
_PRIORITY_OPTIONS = ("low", "medium", "high")


async def async_setup_entry(
    _: HomeAssistant,
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox select platform")

    async_add_entities(
        [
            RadiatorPriority(node, entry)
            for node in entry.runtime_data.nodes
            if priority_available(node)
        ],
        update_before_add=True,
    )
    _LOGGER.debug("Finished setting up Smartbox select platform")


class RadiatorPriority(SmartBoxNodeEntity, SelectEntity):
    """Smartbox radiator priority select (low/medium/high).

    fw-1.9-family setup field only. The device takes the priority into
    account itself when enforcing the box power limit — out of our scope
    and control; this exposes the setting so it can be set.
    """

    _attr_key = "priority"
    _attr_websocket_event = "setup"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:radiator"

    def __init__(self, node: SmartboxNode, entry: SmartboxConfigEntry) -> None:
        """Initialize the select entity."""
        super().__init__(node=node, entry=entry)
        self._attr_options = list(_PRIORITY_OPTIONS)

    @property
    def current_option(self) -> str | None:
        """Return the current radiator priority."""
        return self._node.priority

    async def async_select_option(self, option: str) -> None:
        """Set the radiator priority."""
        await self._node.set_priority(option)
