"""Identity discovery.

The entity-registry surface needs wire identity facts the engine store cannot
provide: device names/model/fw/serial, home id, node names, node pids and
hw_versions, and the SETUP objects the entity-creation gates check. This module
builds them ONCE per config entry with plain REST reads over
:class:`smartbox.protocol.SmartboxClient`, and the resulting frozen dataclasses
are the only thing the platform files consume for identity; state reads and
writes stay on the engine.

Two owners of one wire slot, by rule: the gates below (``*_available``) read
the FROZEN discovery copy, because the store is still empty when platforms
create entities; entity properties read the LIVE store (``NodeState.setup``),
which frames keep fresh. A predicate both sides need takes the data as an
argument (see :func:`boost_available`) instead of reading one source.

Status is fetched per node only for the ``prog_temps_available`` gate. PMO
nodes carry no such gate, so their status stays empty.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any, cast

from smartbox.core.merging import boost_config, is_empty_slot, normalize_node_addr
from smartbox.core.state import node_device_id
from smartbox.protocol.client import NodeRef, SmartboxClient
from smartbox.protocol.errors import SmartboxError
from smartbox.protocol.values import BoostConfig, bounded_repr

from .const import HEATER_NODE_TYPES, SmartboxNodeType

if TYPE_CHECKING:
    from smartbox.protocol.reseller import SmartboxReseller

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "SmartboxDeviceInfo",
    "SmartboxIdentity",
    "SmartboxMembership",
    "SmartboxNodeInfo",
    "async_discover_identity",
    "async_discover_membership",
    "boost_available",
    "boost_config",
    "node_power_limit_available",
]


def boost_available(setup: Mapping[str, Any]) -> bool:
    """Report whether a node's setup advertises a usable boost configuration.

    Any non-zero configuration exposes boost. The write path differs by
    value: 1 and 2 use the status/setup path, 3 uses ``/boost_mode``
    (docs/api-ws-user.md §2.3).

    Cross-module contract: ``climate.py`` and ``switch.py`` import
    :func:`boost_config` for the per-unit dispatch.
    """
    return boost_config(setup) != BoostConfig.UNSUPPORTED


@dataclass(frozen=True)
class SmartboxDeviceInfo:
    """Box identity facts for the device registry (name/model/sw/serial/home)."""

    dev_id: str
    name: str
    model_id: Any
    sw_version: Any
    serial_number: Any
    home_id: str | None
    reseller: SmartboxReseller

    @property
    def configuration_url(self) -> str:
        """The reseller web URL deep link to this device's home."""
        return f"{self.reseller.web_url}#{self.home_id}"


@dataclass(frozen=True)
class SmartboxNodeInfo:
    """Node identity facts: addressing, display name, version, setup gates."""

    device: SmartboxDeviceInfo
    node_info: Mapping[str, Any]  # raw mgr/nodes entry (type/addr/name)
    status: Mapping[str, Any] = field(default_factory=dict)
    setup: Mapping[str, Any] = field(default_factory=dict)
    version: Mapping[str, str] = field(default_factory=dict)

    @property
    def dev_id(self) -> str:
        """Return the parent device id."""
        return self.device.dev_id

    @property
    def reseller(self) -> SmartboxReseller:
        """Return the parent device's reseller (manufacturer/web URL)."""
        return self.device.reseller

    @property
    def home_id(self) -> str | None:
        """Return the parent device's home id."""
        return self.device.home_id

    @property
    def node_id(self) -> str:
        """Return the registry node id (``{dev_id}_{addr}``)."""
        return f"{self.dev_id}_{self.addr}"

    @property
    def name(self) -> str:
        """Return the node name, or the device name when unset.

        The wire normally carries ``name`` but the device name is a safe
        fallback: a bare entry must not raise during platform setup, where
        the entity's device_info needs a display name.
        """
        return self.node_info.get("name") or self.device.name

    @property
    def node_type(self) -> str:
        """Return node type, e.g. 'htr' for heaters."""
        return cast("str", self.node_info["type"])

    @property
    def addr(self) -> int:
        """Return the canonical node address."""
        addr = normalize_node_addr(self.node_info.get("addr"))
        if addr is None:
            msg = "Node entry has an invalid addr"
            raise SmartboxError(msg)
        return addr

    @property
    def pid(self) -> str | None:
        """Product ID of the node."""
        return self.version.get("pid")

    @property
    def hw_version(self) -> str | None:
        """Hardware version of the node (from the node version payload)."""
        return self.version.get("hw_version")

    @property
    def heater_node(self) -> bool:
        """Is this node a heater."""
        return self.node_type in HEATER_NODE_TYPES

    @property
    def boost_available(self) -> bool:
        """Is boost available (see :func:`boost_available`)."""
        return boost_available(self.setup)

    def get_model_code(self, version: Mapping[str, Any] | None = None) -> str | None:
        """Get the model code from a node version payload.

        Characters 3-4, uppercased, matching the official app
        (``pid.toUpperCase().slice(2, 4)``; docs/webapi-spec.md §3.1):
        PID '081c' gives '1C'. ``version`` defaults to the discovery copy;
        entity properties pass the live engine slot when they have it.
        """
        payload = version or self.version
        pid = payload.get("pid")
        if not isinstance(pid, str):
            return None
        return pid[2:4].upper() or None


@dataclass(frozen=True)
class SmartboxIdentity:
    """The discovered device + node identities of one config entry.

    ``complete`` is False when discovery skipped an entry it could not read.
    Setup-time removal reconciliation only trusts a complete discovery: a
    partial identity would make live devices look stale.
    """

    devices: tuple[SmartboxDeviceInfo, ...]
    nodes: tuple[SmartboxNodeInfo, ...]
    complete: bool = True


@dataclass(frozen=True)
class SmartboxMembership:
    """A fully parsed account membership read used for removal confirmation.

    ``unconfirmed_dev_ids`` names the boxes whose node list was not fully
    parsed; a node of such a box is never removed on the strength of this
    read. ``raw_nodes`` keeps each box's node list so a contradicted shrink
    can be replayed into the engine; ``node_keys`` maps a registry node id
    back to its canonical ``type/addr`` key and ``node_devs`` to its box.
    """

    dev_ids: frozenset[str]
    node_ids: frozenset[str]
    unconfirmed_dev_ids: frozenset[str]
    raw_nodes: Mapping[str, tuple[Any, ...]]
    node_keys: Mapping[str, str]
    node_devs: Mapping[str, str]


def _bounded_keys(entry: Mapping[str, Any]) -> str:
    """Return a bounded repr of an entry's key names.

    Never embeds the payload (it can be huge, server-controlled data); the
    shared helper bounds the key text too, so an error message stays inside a
    log line.
    """
    return bounded_repr(sorted(entry))


def _device_info_from_dev(
    dev: Mapping[str, Any], home_id: str | None, reseller: SmartboxReseller
) -> SmartboxDeviceInfo:
    """Validate one home devs entry into box identity (SmartboxError on drift)."""
    dev_id = dev.get("dev_id")
    if not isinstance(dev_id, str) or not dev_id:
        msg = (
            f"Device entry missing dev_id "
            f"(type={type(dev).__name__}, keys={_bounded_keys(dev)})"
        )
        raise SmartboxError(msg)
    return SmartboxDeviceInfo(
        dev_id=dev_id,
        name=dev.get("name") or dev_id,
        model_id=dev.get("product_id"),
        sw_version=dev.get("fw_version"),
        serial_number=dev.get("serial_id"),
        home_id=home_id if isinstance(home_id, str) else None,
        reseller=reseller,
    )


def _readable_node_entries(
    entries: list[Any], dev_id: str
) -> tuple[list[Mapping[str, Any]], bool]:
    """Keep one box's installed, well-formed ``mgr/nodes`` entries.

    Empty slots (``installed: false``) are not nodes and are not read
    (hass-smartbox docs/api-contract.md §6.6); skipping them does not make
    discovery incomplete. A malformed entry does make it incomplete: removal
    reconciliation must not infer absence from a list it could not fully parse.
    """
    complete = True
    readable: list[Mapping[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            complete = False
            continue
        if is_empty_slot(entry):
            continue
        try:
            _node_ref_from_info(entry)
        except SmartboxError as ex:
            complete = False
            _LOGGER.warning("Skipping malformed node on %r: %s", dev_id, ex)
            continue
        readable.append(entry)
    return readable, complete


def _node_ref_from_info(node_info: Mapping[str, Any]) -> NodeRef:
    """Validate one ``mgr/nodes`` entry into a node reference.

    Malformed server data fails closed with a bounded ``SmartboxError``, which
    ``async_setup_entry`` maps to a retry; reading ``type`` / ``addr`` straight
    off the mapping would raise a bare ``KeyError``.
    """
    node_type = node_info.get("type")
    if not isinstance(node_type, str) or not node_type:
        msg = f"Node entry missing type (keys={_bounded_keys(node_info)})"
        raise SmartboxError(msg)
    addr = normalize_node_addr(node_info.get("addr"))
    if addr is None:
        msg = f"Node entry missing/invalid addr (keys={_bounded_keys(node_info)})"
        raise SmartboxError(msg)
    return NodeRef(node_type, str(addr))


async def _gather_reads[T](*coros: Coroutine[Any, Any, T]) -> list[T]:
    """Run the per-node reads together, raising the first failure.

    ``return_exceptions`` keeps every request awaited (no orphaned call keeps
    running after the caller has moved on), and the first exception is then
    re-raised, so the protocol-layer error contract is unchanged.
    """
    results = await asyncio.gather(*coros, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return [result for result in results if not isinstance(result, BaseException)]


async def _read_node(
    client: SmartboxClient, device: SmartboxDeviceInfo, node_info: Mapping[str, Any]
) -> SmartboxNodeInfo:
    """Read one node's setup/status/version (one round-trip window)."""
    ref = _node_ref_from_info(node_info)
    if ref.node_type == SmartboxNodeType.PMO:
        # PMO nodes carry no status gate; the engine store owns their state.
        status: Mapping[str, Any] = {}
        setup, version = await _gather_reads(
            client.get_node_setup(device.dev_id, ref),
            client.get_node_version(device.dev_id, ref),
        )
    else:
        status, setup, version = await _gather_reads(
            client.get_node_status(device.dev_id, ref),
            client.get_node_setup(device.dev_id, ref),
            client.get_node_version(device.dev_id, ref),
        )
    return SmartboxNodeInfo(
        device=device,
        node_info=node_info,
        status=_require_object(status, "status", device, ref),
        setup=_require_object(setup, "setup", device, ref),
        version=_require_object(version, "version", device, ref),
    )


def _require_object(
    payload: object,
    slot: str,
    device: SmartboxDeviceInfo,
    ref: NodeRef,
) -> Mapping[str, Any]:
    """Validate one successful node read is a JSON object (fail closed)."""
    if not isinstance(payload, Mapping):
        msg = (
            f"{slot} payload for {device.dev_id}/{ref.node_type}/{ref.addr} "
            f"is not an object (type={type(payload).__name__})"
        )
        raise SmartboxError(msg)
    return payload


async def async_discover_identity(
    client: SmartboxClient, reseller: SmartboxReseller
) -> SmartboxIdentity:
    """Discover devices and nodes with plain REST reads over the client.

    Reads grouped_devs (homes + devs), then per device mgr/nodes and per node
    setup/status/version - the per-node reads of one device run together, so
    entry setup pays one round-trip window per device instead of one per
    field. Protocol-layer errors propagate RAW for ``async_setup_entry`` to
    map: a half-discovered entry must never load.
    """
    homes = await client.get_grouped_devices()
    if not isinstance(homes, list):
        msg = (
            f"Unexpected grouped-devices payload "
            f"(type={type(homes).__name__}, "
            f"length={len(homes) if hasattr(homes, '__len__') else 'n/a'})"
        )
        raise SmartboxError(msg)
    devices: list[SmartboxDeviceInfo] = []
    nodes: list[SmartboxNodeInfo] = []
    complete = True
    for home in homes:
        if not isinstance(home, Mapping) or "id" not in home:
            complete = False
            continue
        home_id = home["id"]
        devs = home.get("devs", [])
        if not isinstance(devs, list):
            msg = f"Home entry has an ill-typed devs list (type={type(devs).__name__})"
            raise SmartboxError(msg)
        for dev in devs:
            if not isinstance(dev, Mapping):
                complete = False
                continue
            device = _device_info_from_dev(dev, home_id, reseller)
            devices.append(device)
            entries, entries_complete = _readable_node_entries(
                await client.get_nodes(device.dev_id), device.dev_id
            )
            complete = complete and entries_complete
            nodes.extend(
                await _gather_reads(
                    *(_read_node(client, device, entry) for entry in entries)
                )
            )
    return SmartboxIdentity(
        devices=tuple(devices), nodes=tuple(nodes), complete=complete
    )


def _membership_nodes(
    dev_id: str,
    entries: list[Any],
    node_ids: set[str],
    node_keys: dict[str, str],
    node_devs: dict[str, str],
) -> bool:
    """Collect one box's parsed node ids; False when an entry was malformed."""
    confirmed = True
    for entry in entries:
        if not isinstance(entry, Mapping):
            confirmed = False
            continue
        if is_empty_slot(entry):
            # An emptied slot is absent from membership: a stale registry
            # device must not cancel its own removal confirmation.
            continue
        try:
            ref = _node_ref_from_info(entry)
        except SmartboxError:
            confirmed = False
            continue
        key = f"{ref.node_type}/{ref.addr}"
        node_id = node_device_id(dev_id, key)
        node_ids.add(node_id)
        node_keys[node_id] = key
        node_devs[node_id] = dev_id
    return confirmed


async def async_discover_membership(client: SmartboxClient) -> SmartboxMembership:
    """Read the account's boxes and their full node lists.

    Stricter than identity discovery on purpose: a removal confirmation must
    never infer an absence from a payload it could not parse. An unreadable
    home or dev entry raises (the caller retries); a malformed node entry
    marks its box unconfirmed while the rest of the account stays usable.
    """
    homes = await client.get_grouped_devices()
    if not isinstance(homes, list):
        msg = (
            f"Unexpected grouped-devices payload "
            f"(type={type(homes).__name__}, "
            f"length={len(homes) if hasattr(homes, '__len__') else 'n/a'})"
        )
        raise SmartboxError(msg)
    dev_ids: set[str] = set()
    node_ids: set[str] = set()
    unconfirmed: set[str] = set()
    raw_nodes: dict[str, tuple[Any, ...]] = {}
    node_keys: dict[str, str] = {}
    node_devs: dict[str, str] = {}
    for home in homes:
        if not isinstance(home, Mapping) or "id" not in home:
            msg = "Membership home entry is not a dict with an id"
            raise SmartboxError(msg)
        devs = home.get("devs", [])
        if not isinstance(devs, list):
            msg = f"Membership home has an ill-typed devs list (type={type(devs).__name__})"
            raise SmartboxError(msg)
        for dev in devs:
            if not isinstance(dev, Mapping):
                msg = "Membership dev entry is not a dict"
                raise SmartboxError(msg)
            dev_id = dev.get("dev_id")
            if not isinstance(dev_id, str) or not dev_id:
                msg = "Membership dev entry is missing dev_id"
                raise SmartboxError(msg)
            dev_ids.add(dev_id)
            entries = await client.get_nodes(dev_id)
            raw_nodes[dev_id] = tuple(entries)
            if not _membership_nodes(dev_id, entries, node_ids, node_keys, node_devs):
                unconfirmed.add(dev_id)
    return SmartboxMembership(
        dev_ids=frozenset(dev_ids),
        node_ids=frozenset(node_ids),
        unconfirmed_dev_ids=frozenset(unconfirmed),
        raw_nodes=raw_nodes,
        node_keys=node_keys,
        node_devs=node_devs,
    )


# --- entity creation gates (setup-driven, evaluated at platform setup) ---
# They read the REST-discovered setup/status, because the engine store may
# still be empty at platform setup.


def _factory_options(node: SmartboxNodeInfo) -> dict[str, Any]:
    """Get the factory options (empty for an absent/ill-typed object)."""
    factory = node.setup.get("factory_options")
    return dict(factory) if isinstance(factory, Mapping) else {}


def window_mode_available(node: SmartboxNodeInfo) -> bool:
    """Is window mode available."""
    return bool(_factory_options(node).get("window_mode_available", False))


def true_radiant_available(node: SmartboxNodeInfo) -> bool:
    """Is true radiant available."""
    return bool(_factory_options(node).get("true_radiant_available", False))


def away_offset_available(node: SmartboxNodeInfo) -> bool:
    """Is the away offset configurable on this node."""
    return "away_offset" in node.setup


def max_temp_limit_available(node: SmartboxNodeInfo) -> bool:
    """Is the maximum target temperature limit configurable on this node.

    True whenever the setup carries the fw-1.9-family max_stemp_limit key,
    including "0.0" (limit disabled) -- the number and its on/off switch
    both exist so the limit can be re-enabled from HA.
    """
    return "max_stemp_limit" in node.setup


def node_power_limit_available(node: SmartboxNodeInfo) -> bool:
    """Is the node's OWN power limit configurable on this node.

    True whenever the setup carries a ``power_limit`` key (observed on pmo
    nodes; the app writes it through the node's own endpoint,
    docs/api-ws-user.md §2.3).
    """
    return "power_limit" in node.setup


def priority_available(node: SmartboxNodeInfo) -> bool:
    """Is the radiator priority configurable on this node.

    fw-1.9-family setup field only.
    """
    return "priority" in node.setup


def prog_temps_available(node: SmartboxNodeInfo) -> bool:
    """Are the programme profile temps (Frost/Eco/Comfort) settable.

    Plain htr/acm nodes only: the schedule profiles resolve to their own
    temperatures. htr_mod nodes manage the same feature via
    comfort_temp/eco_offset through the climate setpoint instead.
    """
    return node.node_type in (
        SmartboxNodeType.HTR,
        SmartboxNodeType.ACM,
    ) and all(
        key in node.status for key in ("ice_temp", "eco_temp", "comf_temp", "units")
    )
