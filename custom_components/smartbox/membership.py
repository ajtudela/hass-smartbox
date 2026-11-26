"""Membership watch: materialize nodes that appear and clean up ones that leave.

Entity creation happens once, when the platforms set up from the REST
discovery snapshot. The websocket snapshot is authoritative for membership
and can introduce a node later (a newly paired heater, for example). This
watcher refreshes the REST identity when that happens and asks the
:class:`EntityBuilder` to create the missing devices/nodes in place. A node
that the refreshed identity still lacks is retried with a bounded backoff,
then surfaced as one repair issue per entry asking for a reload.

A node or box that disappears from a fully parsed membership list starts a
removal candidate instead of vanishing silently. After a grace window the
watcher re-reads the account membership and removes the device only when the
absence is confirmed again; an unreadable read, a malformed list, or the
member reappearing cancels the removal. The device registry's deleted entries
then restore the same device and entity ids (with area, name and history) if
the member is paired again later. This is what the ``stale-devices`` quality
rule asks for: automatic removal when the full list proves the device gone,
with the standard device-page delete path as the user's fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Literal, cast

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from smartbox.core.state import (
    DeviceRemoved,
    NodeAdded,
    NodeChange,
    NodeRemoved,
    node_key,
)
from smartbox.device.codes import product_code
from smartbox.protocol import timing
from smartbox.protocol.events import Update

from .const import DOMAIN
from .devices import async_ensure_box_devices
from .identity import async_discover_identity, async_discover_membership

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant
    from smartbox.core.engine import SmartboxEngine
    from smartbox.core.state import Change
    from smartbox.protocol.client import SmartboxClient
    from smartbox.protocol.reseller import SmartboxReseller

    from . import SmartboxConfigEntry
    from .entity_builder import EntityBuilder
    from .history import HistoryImport
    from .identity import SmartboxIdentity, SmartboxMembership

_LOGGER = logging.getLogger(__name__)

_NODE_ADDED_ISSUE = "node_added"
_MAX_LABELS = 10
_MAX_LABEL_LENGTH = 64
_REFRESH_ATTEMPTS = 3
_REFRESH_BACKOFF_SECONDS = (5.0, 15.0, 45.0)
# A membership shrink is evidence, not proof: the backend can omit a live
# node from one list. The candidate waits this long, then a fresh full
# membership read decides; repeated read failures keep it pending.
_REMOVAL_GRACE_SECONDS = 10 * 60.0
_REMOVAL_RETRY_SECONDS = (5 * 60.0, 15 * 60.0, 45 * 60.0)
# A permanently broken account read must not keep a confirmation task alive
# forever: after this many consecutive failures the task ends and the
# candidates stay pending (diagnostics) until the next reload re-arms them.
_REMOVAL_MAX_FAILURES = 3

__all__ = [
    "LatePidWatcher",
    "MembershipWatcher",
    "clear_node_added_issue",
    "node_added_issue_id",
]


@dataclass(frozen=True)
class _PendingNode:
    """A node announced by membership but not yet materialized."""

    label: str
    dev_id: str
    node_key: str


@dataclass(frozen=True)
class _RemovalCandidate:
    """A member (node or box) absent from an authoritative list.

    ``kind`` is known for live evidence (``NodeRemoved``/``DeviceRemoved``)
    and ``unknown`` for a registry device found stale at setup, where the
    identifier alone decides the presence check.
    """

    identifier: str
    dev_id: str
    node_key: str | None
    kind: Literal["node", "box", "unknown"]
    label: str
    first_missing: float


@callback
def node_added_issue_id(entry: SmartboxConfigEntry) -> str:
    """Per-entry repair issue id for nodes added after setup."""
    return f"{entry.entry_id}_{_NODE_ADDED_ISSUE}"


@callback
def clear_node_added_issue(hass: HomeAssistant, entry: SmartboxConfigEntry) -> None:
    """Clear the reload hint (a successful setup has materialized the nodes)."""
    ir.async_delete_issue(hass, DOMAIN, node_added_issue_id(entry))


def _bounded_label(label: str) -> str:
    """Bound one server-controlled node label for logs, issues and diagnostics."""
    cleaned = "".join(character for character in label if character.isprintable())
    if len(cleaned) <= _MAX_LABEL_LENGTH:
        return cleaned
    return f"{cleaned[: _MAX_LABEL_LENGTH - 1]}…"


def _format_labels(labels: list[str]) -> str:
    """Join node labels, bounding both the count and each label.

    Truncation uses an ellipsis instead of an English "and N more" so the
    placeholder stays inside the translated sentence.
    """
    shown = ", ".join(_bounded_label(label) for label in labels[:_MAX_LABELS])
    if len(labels) > _MAX_LABELS:
        shown += ", …"
    return shown


def _device_by_identifier(
    registry: dr.DeviceRegistry,
    identifier: tuple[str, str],
    entry_id: str,
) -> dr.DeviceEntry | None:
    """Look up one entry's device on both supported registry API lines.

    Home Assistant 2026.10 added ``async_get_device_by_identifier`` and
    deprecated ``async_get_device``; the 2026.5 floor only has the latter.
    """
    by_identifier = getattr(registry, "async_get_device_by_identifier", None)
    if by_identifier is not None:
        return cast("dr.DeviceEntry | None", by_identifier(identifier, entry_id))
    return registry.async_get_device(identifiers={identifier})


class MembershipWatcher:
    """Materialize ``NodeAdded`` membership and confirm ``NodeRemoved`` ones."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SmartboxConfigEntry,
        engine: SmartboxEngine,
        identity: SmartboxIdentity,
        builder: EntityBuilder,
        client: SmartboxClient,
        reseller: SmartboxReseller,
        history: HistoryImport,
    ) -> None:
        """Seed the known sets from discovery and subscribe to the engine."""
        self._hass = hass
        self._entry = entry
        self._engine = engine
        self._identity = identity
        self._builder = builder
        self._client = client
        self._reseller = reseller
        self._history = history
        self._known_nodes = {node.node_id for node in identity.nodes}
        self._known_devices = {device.dev_id for device in identity.devices}
        self._pending: dict[str, _PendingNode] = {}
        self._removals: dict[str, _RemovalCandidate] = {}
        self._refresh_task: asyncio.Task[None] | None = None
        self._removal_task: asyncio.Task[None] | None = None
        self._closed = False
        self._suspended = False
        engine.subscribe(self._on_change)

    @callback
    def async_start(self) -> None:
        """Seed registry devices absent from discovery, then confirm them.

        Called once every platform is ready. Setup discovery is a full list,
        so a device of this entry whose identifier is absent from it was
        removed while Home Assistant was stopped (or by an earlier run).
        """
        if self._closed:
            return
        if not self._identity.complete:
            _LOGGER.debug(
                "Discovery skipped an unreadable entry; stale-device "
                "reconciliation is deferred to a later reload"
            )
            return
        live = {device.dev_id for device in self._identity.devices} | {
            node.node_id for node in self._identity.nodes
        }
        registry = dr.async_get(self._hass)
        for device_entry in dr.async_entries_for_config_entry(
            registry, self._entry.entry_id
        ):
            identifiers = {
                identifier
                for domain, identifier in device_entry.identifiers
                if domain == DOMAIN
            }
            if not identifiers or identifiers & live:
                continue
            identifier = min(identifiers)
            self._add_removal(
                identifier=identifier,
                dev_id="",
                node_key=None,
                kind="unknown",
                label=device_entry.name_by_user or device_entry.name or identifier,
            )

    @property
    def live_identifiers(self) -> frozenset[str]:
        """Registry identifiers the latest membership evidence holds."""
        return frozenset(self._known_nodes | self._known_devices)

    async def _on_change(self, change: Change) -> None:
        """Queue a removal, cancel one, or materialize a new node."""
        if self._closed:
            return
        if isinstance(change, NodeRemoved):
            self._queue_node_removal(change)
            return
        if isinstance(change, DeviceRemoved):
            self._queue_box_removal(change.dev_id)
            return
        if not isinstance(change, NodeAdded):
            return
        # A node of a box proves the box is still a member: cancel both the
        # node's and the box's pending removal before the addition logic.
        self._cancel_removal(change.node_id)
        self._cancel_removal(change.dev_id)
        node_id = change.node_id
        if node_id in self._known_nodes or node_id in self._pending:
            # The first snapshot reports every known node too; setup's
            # membership is the baseline, so only genuinely new keys count.
            return
        label = change.name or change.node_key
        if change.dev_id not in self._known_devices:
            label = f"{label} ({change.dev_id})"
        self._pending[node_id] = _PendingNode(
            label=_bounded_label(label),
            dev_id=change.dev_id,
            node_key=change.node_key,
        )
        _LOGGER.debug(
            "New Smartbox node %s detected; refreshing identity",
            _bounded_label(node_id),
        )
        self._schedule_refresh()

    def _queue_node_removal(self, change: NodeRemoved) -> None:
        """Start (or leave alone) a candidate for a node that left a list."""
        registry = dr.async_get(self._hass)
        device = _device_by_identifier(
            registry, (DOMAIN, change.node_id), self._entry.entry_id
        )
        if device is None:
            return
        self._add_removal(
            identifier=change.node_id,
            dev_id=change.dev_id,
            node_key=change.node_key,
            kind="node",
            label=device.name_by_user or device.name or change.node_id,
        )

    def _queue_box_removal(self, dev_id: str) -> None:
        """Start (or leave alone) a candidate for a box that left the account."""
        registry = dr.async_get(self._hass)
        device = _device_by_identifier(registry, (DOMAIN, dev_id), self._entry.entry_id)
        if device is None:
            return
        self._add_removal(
            identifier=dev_id,
            dev_id=dev_id,
            node_key=None,
            kind="box",
            label=device.name_by_user or device.name or dev_id,
        )

    def _add_removal(
        self,
        *,
        identifier: str,
        dev_id: str,
        node_key: str | None,
        kind: Literal["node", "box", "unknown"],
        label: str,
    ) -> None:
        """Record one candidate and ensure the confirmation task runs."""
        if self._closed or identifier in self._removals:
            return
        self._removals[identifier] = _RemovalCandidate(
            identifier=identifier,
            dev_id=dev_id,
            node_key=node_key,
            kind=kind,
            label=_bounded_label(label),
            first_missing=timing.monotonic(),
        )
        _LOGGER.debug(
            "Membership no longer holds %s; confirming removal",
            _bounded_label(identifier),
        )
        self._schedule_removal()

    def _cancel_removal(self, identifier: str) -> None:
        """Drop one candidate; stop the task when nothing is left to confirm."""
        if self._removals.pop(identifier, None) is None:
            return
        if not self._removals:
            self._maybe_cancel_removal_task()

    def _maybe_cancel_removal_task(self) -> None:
        """Cancel an idle/sleeping confirmation task, never the running one."""
        task = self._removal_task
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()

    def _schedule_removal(self) -> None:
        """Start one confirmation task when there is something to confirm."""
        if (
            self._closed
            or self._suspended
            or self._removal_task is not None
            or not self._removals
        ):
            return
        # A normal entry task: the grace is bounded and tests rely on
        # async_block_till_done settling it. Unload cancels it in close().
        self._removal_task = self._entry.async_create_task(
            self._hass, self._async_confirm_removals(), "smartbox_removals"
        )

    async def _async_confirm_removals(self) -> None:
        """Wait out the grace, then confirm candidates against a full read.

        A failed or malformed read keeps every candidate and retries with a
        capped backoff: this path never removes a device on anything but two
        fully parsed full-list observations. The task ends when the ledger
        empties or the watcher closes.
        """
        try:
            await timing.sleep(_REMOVAL_GRACE_SECONDS)
            attempt = 0
            failures = 0
            while self._removals and not self._closed:
                try:
                    membership = await async_discover_membership(self._client)
                except asyncio.CancelledError:
                    raise
                except Exception as ex:  # noqa: BLE001 - a failed read removes nothing
                    failures += 1
                    _LOGGER.warning(
                        "Could not confirm a Smartbox membership removal: %s", ex
                    )
                    if self._removal_failed(failures, ex):
                        return
                else:
                    try:
                        await self._apply_membership(membership)
                    except Exception as ex:  # keep confirming; remove nothing else
                        failures += 1
                        _LOGGER.exception("Smartbox membership removal failed")
                        if self._removal_failed(failures, ex):
                            return
                    else:
                        failures = 0
                if not self._removals:
                    return
                delay = _REMOVAL_RETRY_SECONDS[
                    min(attempt, len(_REMOVAL_RETRY_SECONDS) - 1)
                ]
                attempt += 1
                await timing.sleep(delay)
        finally:
            self._removal_task = None

    def _removal_failed(self, failures: int, error: Exception) -> bool:
        """Report whether repeated failures should end the confirmation task."""
        if failures < _REMOVAL_MAX_FAILURES:
            return False
        _LOGGER.warning(
            "Giving up confirming a Smartbox membership removal after %d failed "
            "attempts; %d device(s) stay pending until the next reload: %s",
            failures,
            len(self._removals),
            error,
        )
        return True

    async def _apply_membership(self, membership: SmartboxMembership) -> None:
        """Resolve every candidate against one full membership read."""
        # Boxes first: a confirmed box removes its node devices in one sweep,
        # so their own candidates are then already gone.
        ordered = sorted(
            self._removals.items(),
            key=lambda item: (item[1].kind != "box", item[0]),
        )
        for identifier, candidate in ordered:
            if self._closed or identifier not in self._removals:
                continue
            if identifier in membership.dev_ids or identifier in membership.node_ids:
                await self._handle_present(candidate, membership)
                self._cancel_removal(identifier)
                continue
            if self._is_unconfirmed(candidate, membership):
                continue
            await self._remove_candidate(candidate)

    @staticmethod
    def _is_unconfirmed(
        candidate: _RemovalCandidate, membership: SmartboxMembership
    ) -> bool:
        """Whether the candidate's box list was not fully parsed."""
        dev_id = candidate.dev_id
        if not dev_id:
            dev_id, _separator, tail = candidate.identifier.rpartition("_")
            if not tail.isdigit():
                return False
        return dev_id in membership.unconfirmed_dev_ids

    async def _handle_present(
        self, candidate: _RemovalCandidate, membership: SmartboxMembership
    ) -> None:
        """Cancel a removal; heal a node the store dropped on the bad list."""
        if candidate.kind == "box":
            return
        dev_id = candidate.dev_id or membership.node_devs.get(candidate.identifier)
        node_key = candidate.node_key or membership.node_keys.get(candidate.identifier)
        if dev_id is None or node_key is None:
            return
        if self._engine.node(dev_id, node_key) is not None:
            return
        raw_nodes = membership.raw_nodes.get(dev_id)
        if raw_nodes is None:
            return
        # The REST node list is the same authoritative list /mgr/nodes
        # carries; replaying it restores the shell so the node's frames (and
        # its entities) become live again instead of waiting for a reconnect.
        await self._engine.handle_event(
            Update(dev_id=dev_id, path="/mgr/nodes", body={"nodes": list(raw_nodes)})
        )

    async def _remove_candidate(self, candidate: _RemovalCandidate) -> None:
        """Remove one confirmed device and forget it for a future re-add."""
        if candidate.kind == "box":
            await self._remove_box(candidate)
            return
        registry = dr.async_get(self._hass)
        device = _device_by_identifier(
            registry, (DOMAIN, candidate.identifier), self._entry.entry_id
        )
        if device is not None:
            registry.async_remove_device(device.id)
        # Forget first: if this fails the candidate stays and the retry is a
        # no-op registry-wise, instead of leaving a stale known set behind.
        await self._forget_identifier(candidate.identifier)
        self._removals.pop(candidate.identifier, None)
        _LOGGER.info(
            "Removed stale Smartbox device %s (%s)",
            candidate.label,
            candidate.identifier,
        )

    async def _remove_box(self, candidate: _RemovalCandidate) -> None:
        """Remove a confirmed box and every node device that belongs to it."""
        dev_id = candidate.dev_id or candidate.identifier
        prefix = f"{dev_id}_"
        registry = dr.async_get(self._hass)
        removed_nodes = 0
        for device_entry in dr.async_entries_for_config_entry(
            registry, self._entry.entry_id
        ):
            matches = {
                identifier
                for domain, identifier in device_entry.identifiers
                if domain == DOMAIN
                and (
                    identifier == dev_id
                    or (
                        identifier.startswith(prefix)
                        and identifier[len(prefix) :].isdigit()
                    )
                )
            }
            if not matches:
                continue
            registry.async_remove_device(device_entry.id)
            for identifier in matches:
                if identifier == dev_id:
                    continue
                removed_nodes += 1
                self._known_nodes.discard(identifier)
                self._pending.pop(identifier, None)
                await self._builder.forget_node(identifier)
        # Forget before dropping the candidates: a failure keeps them for the
        # next confirmation, which then just repeats the registry sweep.
        await self._forget_identifier(dev_id)
        self._removals.pop(dev_id, None)
        for identifier in list(self._removals):
            if identifier.startswith(prefix) and identifier[len(prefix) :].isdigit():
                self._removals.pop(identifier, None)
        _LOGGER.info(
            "Removed stale Smartbox box %s (%s) and %d node device(s)",
            candidate.label,
            dev_id,
            removed_nodes,
        )

    async def _forget_identifier(self, identifier: str) -> None:
        """Forget a removed identifier in the watcher and the builder."""
        self._known_nodes.discard(identifier)
        self._known_devices.discard(identifier)
        self._pending.pop(identifier, None)
        await self._builder.forget_node(identifier)
        await self._builder.forget_device(identifier)

    def _schedule_refresh(self) -> None:
        """Start one refresh task when the watcher is active."""
        if self._closed or self._suspended or self._refresh_task is not None:
            return
        self._refresh_task = self._entry.async_create_task(
            self._hass, self._async_refresh(), "smartbox_membership"
        )

    async def _async_refresh(self) -> None:
        """Refresh identity and add the missing entities, with bounded retries.

        A snapshot can announce several nodes at once; the zero sleep lets the
        remaining ``NodeAdded`` events land before the first network call, so
        one refresh covers the whole addition.
        """
        try:
            await self._builder.wait_ready()
            await timing.sleep(0)
            for attempt in range(_REFRESH_ATTEMPTS):
                if self._closed:
                    return
                try:
                    identity = await self._refresh_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if attempt + 1 >= _REFRESH_ATTEMPTS:
                        _LOGGER.exception(
                            "Automatic entity creation failed after %d attempts",
                            _REFRESH_ATTEMPTS,
                        )
                    else:
                        _LOGGER.warning(
                            "Automatic entity creation failed (attempt %d/%d)",
                            attempt + 1,
                            _REFRESH_ATTEMPTS,
                        )
                else:
                    try:
                        if self._reconcile(identity):
                            return
                    except Exception:
                        _LOGGER.exception("Membership reconciliation failed")
                        self._raise_issue()
                        return
                if attempt + 1 >= _REFRESH_ATTEMPTS:
                    self._raise_issue()
                    return
                await timing.sleep(
                    _REFRESH_BACKOFF_SECONDS[
                        min(attempt, len(_REFRESH_BACKOFF_SECONDS) - 1)
                    ]
                )
        finally:
            self._refresh_task = None

    async def _refresh_once(self) -> SmartboxIdentity:
        """Discover identity and add every missing device/node entity."""
        identity = await async_discover_identity(self._client, self._reseller)
        # A box discovered here gets its registry entry before its node
        # entities are added, so their device_info can link to it.
        async_ensure_box_devices(self._hass, self._entry.entry_id, identity.devices)
        for node in identity.nodes:
            if node.node_id not in self._known_nodes:
                # Register before the sensor's update_before_add can run.
                self._history.track(node.node_id)
        await self._builder.async_add_identity(identity)
        return identity

    def _reconcile(self, identity: SmartboxIdentity) -> bool:
        """Update the known sets; True when nothing is left pending."""
        before = set(self._known_nodes)
        found = {node.node_id for node in identity.nodes}
        self._known_nodes.update(found)
        self._known_devices.update(device.dev_id for device in identity.devices)
        for node_id in found - before:
            # A node materialized after setup gets the same first-import
            # bookkeeping as a setup-time node.
            self._history.track(node_id)
        for node_id in list(self._pending):
            if node_id in found:
                self._pending.pop(node_id)
        self._drop_vanished_nodes()
        if not self._pending:
            clear_node_added_issue(self._hass, self._entry)
            return True
        return False

    def _drop_vanished_nodes(self) -> None:
        """Forget pending nodes the engine store no longer holds.

        A transient phantom addition can disappear before REST catches up;
        without this the reload hint would live forever.
        """
        for node_id, pending in list(self._pending.items()):
            if self._engine.node(pending.dev_id, pending.node_key) is None:
                self._pending.pop(node_id)

    def _raise_issue(self) -> None:
        """Create or refresh the reload hint with the missing nodes."""
        self._drop_vanished_nodes()
        if not self._pending:
            clear_node_added_issue(self._hass, self._entry)
            return
        labels = sorted(pending.label for pending in self._pending.values())
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            node_added_issue_id(self._entry),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=_NODE_ADDED_ISSUE,
            translation_placeholders={"nodes": _format_labels(labels)},
        )

    @property
    def new_nodes(self) -> tuple[str, ...]:
        """Display labels for nodes still awaiting materialization."""
        return tuple(sorted(pending.label for pending in self._pending.values()))

    @property
    def facts(self) -> dict[str, object]:
        """Membership facts for config-entry diagnostics."""
        issue_id = (DOMAIN, node_added_issue_id(self._entry))
        return {
            "new_nodes": list(self.new_nodes),
            "repair_issue_raised": issue_id in ir.async_get(self._hass).issues,
            "pending_removals": [
                {
                    "identifier": candidate.identifier,
                    "label": candidate.label,
                    "kind": candidate.kind,
                    "missing_s": round(
                        max(0.0, timing.monotonic() - candidate.first_missing), 1
                    ),
                }
                for candidate in sorted(
                    self._removals.values(), key=lambda item: item.identifier
                )
            ],
        }

    async def _cancel_refresh(self) -> None:
        """Cancel and await the in-flight refresh task, if any."""
        task = self._refresh_task
        self._refresh_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _cancel_removals(self) -> None:
        """Cancel and await the in-flight removal confirmation task, if any."""
        task = self._removal_task
        self._removal_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def async_pause(self) -> None:
        """Stop materializing before the platforms unload."""
        self._suspended = True
        await self._cancel_refresh()
        await self._cancel_removals()

    def resume(self) -> None:
        """Allow materialization again after a refused platform unload."""
        self._suspended = False
        if self._pending:
            self._schedule_refresh()
        self._schedule_removal()

    @callback
    def close(self) -> None:
        """Stop the watcher when the entry unloads."""
        self._closed = True
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = None
        if self._removal_task is not None and not self._removal_task.done():
            self._removal_task.cancel()
        self._removal_task = None


_PID_RELOAD_KEY = f"{DOMAIN}_pid_reloads"


class LatePidWatcher:
    """Reload once when a discovery PID was missing and the live one arrives.

    Entity creation uses the discovery snapshot. A node whose PID was not
    readable at discovery would otherwise keep the wrong surface until the
    user reloads the entry; this watches the version slot and schedules one
    reload per entry. A differing-but-present discovery PID is left alone, and
    a discovery source that never carries the PID cannot cause a reload loop.

    The watcher is seeded from the engine at start, so a version slot ingested
    before the platforms finish setting up is not missed. Reloads are deferred
    until the entry is LOADED and coalesced per entry.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SmartboxConfigEntry,
        identity: SmartboxIdentity,
        engine: SmartboxEngine,
    ) -> None:
        """Capture the discovery snapshot and the engine to watch."""
        self._hass = hass
        self._entry = entry
        self._engine = engine
        self._discovered = {
            (node.dev_id, node_key(node.node_type, node.addr)): node.version.get("pid")
            for node in identity.nodes
            if node.supported_wire_type
        }
        self._pending = False
        self._pending_marker: tuple[str, str, str, str] | None = None
        self._scheduled = False
        self._closed = False

    def async_start(self) -> None:
        """Subscribe and seed from the world already ingested at setup."""
        self._engine.subscribe(self._on_change)
        for key, pid in self._discovered.items():
            if pid:
                continue
            dev_id, node_key_value = key
            state = self._engine.node(dev_id, node_key_value)
            if state is not None and state.version:
                self._consider(dev_id, node_key_value, state.version)
        if self._pending and not self._scheduled:
            self._hass.async_create_task(self._schedule_when_loaded())

    def close(self) -> None:
        """Stop reacting after the entry unloads."""
        self._closed = True

    async def _on_change(self, change: object) -> None:
        if self._closed or not isinstance(change, NodeChange):
            return
        if change.kind != "version":
            return
        self._consider(change.dev_id, change.node_key, change.node.version or {})

    def _consider(self, dev_id: str, key: str, version: Mapping[str, object]) -> None:
        """Record a late PID and request one coalesced reload."""
        pid = version.get("pid")
        if not isinstance(pid, str) or not pid:
            return
        lookup = (dev_id, key)
        if lookup not in self._discovered or self._discovered[lookup]:
            return
        if product_code(pid) is None:
            return
        marker = (self._entry.entry_id, dev_id, key, pid)
        seen: set[tuple[str, str, str, str]] = self._hass.data.setdefault(
            _PID_RELOAD_KEY, set()
        )
        if marker in seen:
            return
        seen.add(marker)
        self._pending = True
        self._pending_marker = marker
        if self._entry.state is ConfigEntryState.LOADED:
            self._schedule()
        else:
            self._hass.async_create_task(self._schedule_when_loaded())

    async def _schedule_when_loaded(self) -> None:
        while not self._closed and self._entry.state is not ConfigEntryState.LOADED:
            if self._entry.state in {
                ConfigEntryState.NOT_LOADED,
                ConfigEntryState.SETUP_ERROR,
                ConfigEntryState.MIGRATION_ERROR,
                ConfigEntryState.SETUP_RETRY,
                ConfigEntryState.FAILED_UNLOAD,
                ConfigEntryState.UNLOAD_IN_PROGRESS,
            }:
                self._discard_pending_marker()
                return
            await asyncio.sleep(0.1)
        if self._closed:
            self._discard_pending_marker()
            return
        if self._pending:
            self._schedule()

    def _discard_pending_marker(self) -> None:
        """Forget a marker whose deferred reload was abandoned."""
        marker = self._pending_marker
        self._pending_marker = None
        self._pending = False
        if marker is None:
            return
        seen = self._hass.data.get(_PID_RELOAD_KEY)
        if isinstance(seen, set):
            seen.discard(marker)

    def _schedule(self) -> None:
        if self._scheduled or self._closed:
            return
        self._scheduled = True
        _LOGGER.info(
            "A late PID was discovered for entry %s; reloading once",
            self._entry.entry_id,
        )
        self._hass.config_entries.async_schedule_reload(self._entry.entry_id)

    @staticmethod
    def async_clear(hass: HomeAssistant, entry_id: str) -> None:
        """Drop the entry's reload markers after removal."""
        seen = hass.data.get(_PID_RELOAD_KEY)
        if isinstance(seen, set):
            for marker in [item for item in seen if item[0] == entry_id]:
                seen.discard(marker)
