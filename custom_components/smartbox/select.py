"""Support for Smartbox select entities.

Entities read state through the adapter's accessors over ``runtime.engine``
and write through its named ops; updates arrive via the shared
``EngineChangeFanOut``, one registration per node. The REST discovery identity
is kept only for registry identity, device-info fields and the ``priority`` and
``duty_limit`` creation gates.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from smartbox.protocol import VALID_PRIORITIES
from smartbox.protocol.values import parse_number_or_none

from .adapter import EngineChangeFanOut, SmartboxNodeAdapterEntity
from .control_mode import (
    control_mode_available,
    control_mode_choices,
    control_mode_label,
)
from .controller import lock_kind
from .entity_builder import EntityBuilder
from .exceptions import user_error
from .identity import (
    SmartboxNodeInfo,
    duty_limit_available,
    duty_limit_options,
    duty_limit_value,
    priority_available,
)
from .storage import (
    is_elnur_storage,
    lock_endpoint,
    ui_mode as storage_ui_mode,
    ui_mode_available as storage_ui_mode_available,
    ui_mode_options as storage_ui_mode_options,
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
        return _build_node_selects(node, entry, runtime, fanout)

    runtime.entity_builder.register(async_add_entities, build_node=build_node)
    for node in runtime.identity.nodes:
        EntityBuilder.add_groups(build_node(node), async_add_entities)
    _LOGGER.debug("Finished setting up Smartbox select platform")


class SmartboxNodeSelectEntity(SmartboxNodeAdapterEntity, SelectEntity):
    """Base for node-level select entities (identity on the node adapter base)."""

    _attr_entity_category = EntityCategory.CONFIG


def _build_node_selects(
    node: SmartboxNodeInfo,
    entry: SmartboxConfigEntry,
    runtime: SmartboxRuntime,
    fanout: EngineChangeFanOut,
) -> list[EntityGroup]:
    """Build the selects this node's setup exposes.

    Module-level so the per-capability branches do not contribute to the
    platform setup's complexity.
    """
    if not node.supported_wire_type:
        return []
    groups: list[EntityGroup] = []
    if priority_available(node):
        groups.append(([RadiatorPriority(node, entry, runtime, fanout)], True))
    if duty_limit_available(node):
        groups.append(([DutyLimit(node, entry, runtime, fanout)], True))
    if control_mode_available(
        node.pid,
        node.node_type,
        node.setup,
        node.setup.get("units"),
    ):
        groups.append(([ControlMode(node, entry, runtime, fanout)], True))
    if node.node_type == "acm" and storage_ui_mode_available(node.pid, node.setup):
        groups.append(
            ([StorageUIMode(node, entry, runtime, fanout, "storage_ui_mode")], True)
        )
    if (
        node.heater_surface
        and lock_kind(
            node.node_type,
            node.pid,
            node.version.get("fw_version"),
            node.setup,
        )
        == "int"
    ):
        groups.append(
            ([ChildLockMode(node, entry, runtime, fanout, "lock_mode")], True)
        )
    return groups


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
        """Return the node's live setup, or the discovery copy before frames."""
        state = self._node_state()
        if state is not None and state.setup:
            return state.setup
        return self._node.setup

    def _choices(self) -> tuple[tuple[str, int], ...]:
        """Return the (label, value) choices for the current setup."""
        return duty_limit_options(self._setup(), self._live_pid)

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


class ControlMode(SmartboxNodeSelectEntity):
    """Heater control algorithm (``setup.control_mode``).

    The wire value is an index into the product controller's own mode list.
    Only the products confirmed against the vendor app are offered; on the
    LHZ/EHC relay heater (PID ``0402``) the app's six visible modes map to
    wire indexes 5, 6, 0, 1, 2, 3 in the order shown
    (docs/api-contract.md §1.9). A stored index outside the offered set reads
    as unknown while the select keeps offering the app's choices.
    """

    _attr_icon = "mdi:tune-variant"

    def __init__(
        self,
        node: SmartboxNodeInfo,
        entry: SmartboxConfigEntry,
        runtime: SmartboxRuntime,
        fanout: EngineChangeFanOut,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(node, entry, runtime, fanout, "control_mode")

    def _setup(self) -> Mapping[str, Any]:
        """Return the node's live setup, or the discovery copy before frames."""
        state = self._node_state()
        if state is not None and state.setup:
            return state.setup
        return self._node.setup

    def _choices(self) -> tuple[tuple[str, int], ...]:
        """Return the (label, wire index) choices for the current setup."""
        setup = self._setup()
        return (
            control_mode_choices(
                self._live_pid,
                self._node.node_type,
                setup,
                setup.get("units") or self._status.get("units"),
            )
            or ()
        )

    @property
    def options(self) -> list[str]:
        """Return the labels the vendor app offers on this product."""
        return [label for label, _ in self._choices()]

    @property
    def current_option(self) -> str | None:
        """Return the stored control mode as its app label."""
        value = self._setup().get("control_mode")
        return control_mode_label(
            self._choices(), value if isinstance(value, int) else None
        )

    async def async_select_option(self, option: str) -> None:
        """Write the selected control-mode index into the setup."""
        values = dict(self._choices())
        await self._runtime.engine.set_control_mode(
            self._dev_id, self._ref, values[option]
        )


class ChildLockMode(SmartboxNodeSelectEntity):
    """Smartbox 0/1/2 child-lock select.

    The integer-lock families use ``status.locked`` as an enum:
    ``0`` unblocked, ``1`` locally blocked, ``2`` remotely blocked. The bool
    family keeps the separate lock switch. The vendor app exposes the local and
    remote states as two settings; HA presents the wire value as one select.
    """

    _attr_name = "Child lock"
    _attr_icon = "mdi:lock"

    _OPTION_VALUES: ClassVar[dict[str, int]] = {
        "Off": 0,
        "Local lock": 1,
        "Remote lock": 2,
    }
    _VALUE_OPTIONS: ClassVar[dict[int, str]] = {
        0: "Off",
        1: "Local lock",
        2: "Remote lock",
    }

    @property
    def options(self) -> list[str]:
        """Return the wire lock states."""
        return list(self._OPTION_VALUES)

    @property
    def current_option(self) -> str | None:
        """Return the wire lock state as a label."""
        value = self._status.get("locked")
        if isinstance(value, bool):
            return "Local lock" if value else "Off"
        number = parse_number_or_none(value)
        if number is None or not float(number).is_integer():
            return None
        return self._VALUE_OPTIONS.get(int(number))

    async def async_select_option(self, option: str) -> None:
        """Write the selected lock state as an integer."""
        value = self._OPTION_VALUES[option]
        if lock_endpoint(self._node.node_type):
            await self._runtime.engine.set_lock_endpoint(self._dev_id, self._ref, value)
        else:
            await self._runtime.engine.set_lock(self._dev_id, self._ref, value)


class StorageUIMode(SmartboxNodeSelectEntity):
    """Storage-heater UI mode (factory_options.ui_mode)."""

    _attr_name = "Storage UI mode"
    _attr_icon = "mdi:radiator"

    @property
    def _options(self) -> dict[str, int]:
        """Return this product's accepted UI-mode labels and values."""
        return dict(storage_ui_mode_options(self._live_pid))

    @property
    def options(self) -> list[str]:
        """Return the app's accepted storage UI modes for this product."""
        return list(self._options)

    @property
    def current_option(self) -> str | None:
        """Return the configured UI mode."""
        state = self._node_state()
        setup = state.setup if state is not None else self._node.setup
        value = storage_ui_mode(setup)
        for label, index in self._options.items():
            if index == value:
                return label
        return None

    async def async_select_option(self, option: str) -> None:
        """Write the selected storage UI mode."""
        if not is_elnur_storage(self._live_pid):
            msg = "storage_ui_mode_unsupported"
            raise user_error(msg)
        await self._runtime.engine.set_storage_ui_mode(
            self._dev_id, self._ref, self._options[option]
        )
