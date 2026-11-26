"""Dynamic entity builders for nodes/devices discovered after setup.

Platforms create their entities once from the REST discovery identity. The
websocket snapshot can add membership later; this registry lets each platform
publish a builder that turns one discovered device or node into its entities.
The membership watcher refreshes identity, then hands the result here.

Builders return ``(entities, update_before_add)`` groups so a platform can keep
its per-entity fetch flags (the total-consumption sensor, for example). Known
sets are seeded from the setup identity: a previously seen node is never built
twice while it is present. A node confirmed removed is forgotten, so a later
reappearance is built again and HA can restore its deleted registry entries.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .identity import SmartboxDeviceInfo, SmartboxIdentity, SmartboxNodeInfo
    from .services import ServiceTargets

    type EntityGroup = tuple[list[Entity], bool]
    type NodeBuilder = Callable[[SmartboxNodeInfo], list[EntityGroup]]
    type DeviceBuilder = Callable[[SmartboxDeviceInfo], list[EntityGroup]]


@dataclass(frozen=True)
class _PlatformBuilder:
    """One platform's entity constructors and its add callback."""

    add_entities: AddEntitiesCallback
    build_node: NodeBuilder | None
    build_device: DeviceBuilder | None


class EntityBuilder:
    """Build and register entities for newly discovered membership."""

    def __init__(self, identity: SmartboxIdentity, services: ServiceTargets) -> None:
        """Seed the known sets from the setup-time discovery."""
        self._builders: list[_PlatformBuilder] = []
        self._known_nodes = {node.node_id for node in identity.nodes}
        self._known_devices = {device.dev_id for device in identity.devices}
        self._services = services
        self._ready = asyncio.Event()
        self._lock = asyncio.Lock()

    def register(
        self,
        add_entities: AddEntitiesCallback,
        *,
        build_node: NodeBuilder | None = None,
        build_device: DeviceBuilder | None = None,
    ) -> None:
        """Register one platform's constructors."""
        self._builders.append(
            _PlatformBuilder(
                add_entities=add_entities,
                build_node=build_node,
                build_device=build_device,
            )
        )

    def mark_ready(self) -> None:
        """Allow the membership watcher to materialize entities."""
        self._ready.set()

    async def wait_ready(self) -> None:
        """Wait until every platform has registered its builders."""
        await self._ready.wait()

    async def forget_node(self, node_id: str) -> None:
        """Forget a node confirmed removed, so a reappearance is built again."""
        async with self._lock:
            self._known_nodes.discard(node_id)

    async def forget_device(self, dev_id: str) -> None:
        """Forget a box confirmed removed, so a reappearance is built again."""
        async with self._lock:
            self._known_devices.discard(dev_id)

    @staticmethod
    def add_groups(
        groups: list[EntityGroup], add_entities: AddEntitiesCallback
    ) -> None:
        """Add builder groups, preserving each group's fetch flag."""
        for entities, update_before_add in groups:
            if entities:
                add_entities(entities, update_before_add=update_before_add)

    async def async_add_identity(self, identity: SmartboxIdentity) -> tuple[int, int]:
        """Add entities for devices/nodes this registry has not seen.

        Returns ``(devices_added, nodes_added)``. Build everything first, so a
        builder failure adds nothing and leaves the sets unchanged for a
        later retry (or the repair-issue fallback).
        """
        async with self._lock:
            new_devices = [
                device
                for device in identity.devices
                if device.dev_id not in self._known_devices
            ]
            new_nodes = [
                node for node in identity.nodes if node.node_id not in self._known_nodes
            ]
            lengths = (
                len(self._services.schedule),
                len(self._services.thermostat_schedule),
                len(self._services.charging),
                len(self._services.boost),
                len(self._services.climate),
            )
            try:
                groups = self._build_groups(new_devices, new_nodes)
                for add_entities, (entities, update_before_add) in groups:
                    if entities:
                        add_entities(entities, update_before_add=update_before_add)
            except Exception:
                # Builders publish service targets while constructing; a
                # failed attempt must not leave those targets behind.
                del self._services.schedule[lengths[0] :]
                del self._services.thermostat_schedule[lengths[1] :]
                del self._services.charging[lengths[2] :]
                del self._services.boost[lengths[3] :]
                del self._services.climate[lengths[4] :]
                raise
            self._known_devices.update(device.dev_id for device in new_devices)
            self._known_nodes.update(node.node_id for node in new_nodes)
            return len(new_devices), len(new_nodes)

    def _build_groups(
        self,
        new_devices: list[SmartboxDeviceInfo],
        new_nodes: list[SmartboxNodeInfo],
    ) -> list[tuple[AddEntitiesCallback, EntityGroup]]:
        """Build every group for the new membership before adding any."""
        groups: list[tuple[AddEntitiesCallback, EntityGroup]] = []
        for device in new_devices:
            for builder in self._builders:
                if builder.build_device is not None:
                    groups.extend(
                        (builder.add_entities, group)
                        for group in builder.build_device(device)
                    )
        for node in new_nodes:
            for builder in self._builders:
                if builder.build_node is not None:
                    groups.extend(
                        (builder.add_entities, group)
                        for group in builder.build_node(node)
                    )
        return groups
