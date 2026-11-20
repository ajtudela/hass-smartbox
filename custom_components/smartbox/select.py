"""Support for Smartbox select entities.

Entities read state through the adapter's accessors over ``runtime.engine``
and write through its named ops; updates arrive via the shared
``EngineChangeFanOut``, one registration per node. The REST discovery identity
is kept only for registry identity, device-info fields and the ``priority`` and
``duty_limit`` creation gates.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from smartbox.protocol import VALID_PRIORITIES

from .adapter import EngineChangeFanOut, SmartboxNodeAdapterEntity
from .entity_builder import EntityBuilder
from .identity import (
    SmartboxNodeInfo,
    duty_limit_available,
    duty_limit_options,
    duty_limit_value,
    priority_available,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import SmartboxConfigEntry, SmartboxRuntime
    from .entity_builder import EntityGroup

_LOGGER = logging.getLogger(__name__)

# Updates arrive by push, and actions ride the single ws_user connection,
# whose frames are already ordered; the engine serializes writes per node.
# A client-side queue would only add latency to scenes and scripts.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - HA platform signature
    entry: SmartboxConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up platform."""
    _LOGGER.debug("Setting up Smartbox select platform")

    runtime = entry.runtime_data
    fanout = runtime.fanout

    def build_node(node: SmartboxNodeInfo) -> list[EntityGroup]:
        """Build the selects this node's setup exposes."""
        groups: list[EntityGroup] = []
        if priority_available(node):
            groups.append(([RadiatorPriority(node, entry, runtime, fanout)], True))
        if duty_limit_available(node):
            groups.append(([DutyLimit(node, entry, runtime, fanout)], True))
        return groups

    runtime.entity_builder.register(async_add_entities, build_node=build_node)
    for node in runtime.identity.nodes:
        EntityBuilder.add_groups(build_node(node), async_add_entities)
    _LOGGER.debug("Finished setting up Smartbox select platform")


class SmartboxNodeSelectEntity(SmartboxNodeAdapterEntity, SelectEntity):
    """Base for node-level select entities (identity on the node adapter base)."""

    _attr_entity_category = EntityCategory.CONFIG


class RadiatorPriority(SmartboxNodeSelectEntity):
    """Smartbox radiator priority select (low/medium/high).

    fw-1.9-family setup field only. The options are single-sourced from the
    write layer's ``VALID_PRIORITIES`` (the values observed on the wire and
    offered by the web app). The device takes the priority into account
    itself when enforcing the box power limit; this exposes the setting.
    """

    _attr_icon = "mdi:radiator"

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(node, entry, runtime, fanout, "priority")
        self._attr_options = list(VALID_PRIORITIES)

    @property
    def current_option(self) -> str | None:
        """Return the current radiator priority."""
        state = self._node_state()
        value = (state.setup if state is not None else {}).get("priority")
        return value if isinstance(value, str) else None

    async def async_select_option(self, option: str) -> None:
        """Set the radiator priority."""
        await self._runtime.engine.set_priority(self._dev_id, self._ref, option)


class DutyLimit(SmartboxNodeSelectEntity):
    """Smartbox per-heater power-limit factor (Off / LST / Baby Care).

    The choices are the node's own preset percentages from ``factory_options``
    and the set the vendor app offers; a stored value outside them renders as
    unknown, which matches the app's empty selection
    (docs/api-contract.md §5.7). The write merges
    ``extra_options.user_duty_limit`` into the full setup.
    """

    _attr_icon = "mdi:gauge"

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(node, entry, runtime, fanout, "duty_limit")

    def _setup(self) -> Mapping[str, Any]:
        """Return the node's live setup ({} until the first frame)."""
        state = self._node_state()
        return state.setup if state is not None else {}

    def _choices(self) -> tuple[tuple[str, int], ...]:
        """Return the (label, value) choices for the current setup."""
        return duty_limit_options(self._setup())

    @property
    def options(self) -> list[str]:
        """Return the labels the app offers on this node."""
        return [label for label, _ in self._choices()]

    @property
    def current_option(self) -> str | None:
        """Return the stored factor as its label, or None when unmapped."""
        value = duty_limit_value(self._setup())
        if value is None:
            return None
        for label, candidate in self._choices():
            if candidate == value:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        """Write the selected factor into the node's extra options."""
        values = dict(self._choices())
        await self._runtime.engine.set_extra_options(
            self._dev_id, self._ref, {"user_duty_limit": values[option]}
        )
