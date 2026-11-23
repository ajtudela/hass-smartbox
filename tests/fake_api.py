"""Stateful reference fake: an in-memory ``SmartboxClient`` (config-less).

Tests construct the homes/devices/nodes tree, drive GETs and writes against
the SAME mutable model, and assert on the recorded request log - so
writes are observable through the read surface exactly like the server
would expose them.

Traps this fake encodes (each one a live-server behavior):

- ws ``run()`` must PARK until close (see ``fake.FakeTransport`` - the
  two fakes compose: writes go through :class:`FakeSmartbox`, frames
  through ``FakeTransport``, one consistent in-memory world).
- setup writes must mutate the SETUP dict (the GET-merge-POST op posts
  the complete merged object - the fake REPLACES the stored setup with
  the posted body, so a buggy op that drops keys fails the round trip).
  Legacy product codes are the exception: their POSTs omit
  ``extra_options`` by design, so the server's stored object keeps it.
- prog writes merge at DAY level: the posted ``{"prog": {...}}`` object
  REPLACES the stored day-keyed schedule (the op is responsible for the
  merge; the fake only models the server).

Not modeled (deliberately): auth/token plumbing, ws frames, the write
settle lag (GETs reflect writes immediately), and the server's silent
partial application of a multi-key STATUS body. Most status ops send one
key, so ``status.update(body)`` matches the wire for them, and the two
app-shaped multi-key ops (``set_prog_temps``, ``set_htr_mod_status``) send
the bodies the app itself sends. A body that MIXES categories would be
partially applied on the real wire and wholly applied here, so the per-op
key sets stay pinned in ``tests/test_writes.py`` rather than here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartbox.protocol.client import NodeRef
from smartbox.protocol.errors import SmartboxError, TransportUnavailableError
from smartbox.protocol.writes import WriteApi

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "FakeNode",
    "FakeSmartbox",
]

_HTTP_BAD_REQUEST = 400
_HTTP_SERVER_ERROR = 500
_NODE_PATH_SEGMENTS = 5
_DEV_PATH_SEGMENTS = 4


@dataclass
class FakeNode:
    """One node's mutable state (all dicts, always mutated in place)."""

    status: dict[str, object]
    setup: dict[str, object]
    prog: dict[str, object] | None = None
    version: dict[str, object] | None = None


class FakeSmartbox:
    """In-memory Smartbox: GETs read the CURRENT state, writes mutate it."""

    def __init__(self) -> None:
        """Build an empty box: no devices, clean request log."""
        self._nodes: dict[tuple[str, str, str], FakeNode] = {}
        self._devices: dict[str, dict[str, object]] = {}
        self._connected: dict[str, bool] = {}
        self._away: dict[str, dict[str, object]] = {}
        self._power_limit: dict[str, object] = {}
        self.requests: list[tuple[str, str, object]] = []
        self._fail_next: int | None = None
        self._writes: WriteApi | None = None

    # --- construction (test-side) ---

    def add_device(
        self,
        dev_id: str,
        *,
        connected: bool = True,
        away: dict[str, object] | None = None,
        power_limit: object = None,
        rtc: dict[str, object] | None = None,
        data: dict[str, object] | None = None,
    ) -> None:
        """Add a device shell; per-dev raw payloads default to sane shapes."""
        self._devices[dev_id] = {
            "data": dict(data) if data else {},
            "rtc": dict(rtc) if rtc else {},
            "discovery": {"discovery": "off"},
            "version": {},
            "samples": [],
        }
        self._connected[dev_id] = connected
        self._away[dev_id] = (
            dict(away) if away else {"away": False, "enabled": False, "forced": False}
        )
        self._power_limit[dev_id] = power_limit

    def add_node(
        self,
        dev_id: str,
        node: NodeRef,
        *,
        status: dict[str, object] | None = None,
        setup: dict[str, object] | None = None,
        prog: dict[str, object] | None = None,
        version: dict[str, object] | None = None,
    ) -> None:
        """Add a node under a device (its key must exist - add the device first)."""
        if dev_id not in self._devices:
            msg = f"FakeSmartbox: add_device({dev_id!r}) before adding nodes"
            raise ValueError(msg)
        self._nodes[(dev_id, node.node_type, node.addr)] = FakeNode(
            status=dict(status) if status else {},
            setup=dict(setup) if setup else {},
            prog=dict(prog) if prog else None,
            version=dict(version) if version else None,
        )

    # --- SmartboxClient surface: reads ---

    async def get_devices(self) -> dict[str, object]:
        """GET ``/devs``: one minimal entry per added device."""
        self._record("GET", "devs", None)
        self._maybe_fail("GET", "devs")
        return {"devs": [{"dev_id": dev_id} for dev_id in self._devices]}

    async def get_dev_data(self, dev_id: str) -> dict[str, object]:
        """GET ``devs/<id>/dev_data``: the raw payload stored for the device."""
        self._record("GET", f"devs/{dev_id}/dev_data", None)
        self._maybe_fail("GET", f"devs/{dev_id}/dev_data")
        return self._dev(dev_id)["data"]  # type: ignore[return-value]

    async def get_nodes(self, dev_id: str) -> list[dict[str, object]]:
        """GET ``devs/<id>/mgr/nodes``, unwrapped to the node list (client parity)."""
        self._record("GET", f"devs/{dev_id}/mgr/nodes", None)
        self._maybe_fail("GET", f"devs/{dev_id}/mgr/nodes")
        self._require_dev(dev_id)
        return [
            {"type": node_type, "addr": addr}
            for (dev, node_type, addr) in self._nodes
            if dev == dev_id
        ]

    async def get_node_status(self, dev_id: str, node: NodeRef) -> dict[str, object]:
        """GET ``.../status``: a COPY of the current status (writes observable)."""
        self._record("GET", f"devs/{dev_id}/{node.node_type}/{node.addr}/status", None)
        self._maybe_fail("GET", f"devs/{dev_id}/{node.node_type}/{node.addr}/status")
        return dict(self._node(dev_id, node).status)

    async def get_node_setup(self, dev_id: str, node: NodeRef) -> dict[str, object]:
        """GET ``.../setup``: a COPY of the current setup."""
        self._record("GET", f"devs/{dev_id}/{node.node_type}/{node.addr}/setup", None)
        self._maybe_fail("GET", f"devs/{dev_id}/{node.node_type}/{node.addr}/setup")
        return dict(self._node(dev_id, node).setup)

    async def get_node_prog(self, dev_id: str, node: NodeRef) -> dict[str, object]:
        """GET ``.../prog``: the schedule wrapped under ``"prog"`` (wire shape)."""
        self._record("GET", f"devs/{dev_id}/{node.node_type}/{node.addr}/prog", None)
        self._maybe_fail("GET", f"devs/{dev_id}/{node.node_type}/{node.addr}/prog")
        prog = self._node(dev_id, node).prog or {}
        return {"prog": dict(prog)}

    async def get_connected(self, dev_id: str) -> dict[str, object]:
        """GET ``devs/<id>/connected``: ``{connected: bool}``."""
        self._record("GET", f"devs/{dev_id}/connected", None)
        self._maybe_fail("GET", f"devs/{dev_id}/connected")
        self._require_dev(dev_id)
        return {"connected": self._connected[dev_id]}

    async def get_away_status(self, dev_id: str) -> dict[str, object]:
        """GET ``devs/<id>/mgr/away_status``: the current away state."""
        self._record("GET", f"devs/{dev_id}/mgr/away_status", None)
        self._maybe_fail("GET", f"devs/{dev_id}/mgr/away_status")
        self._require_dev(dev_id)
        return dict(self._away[dev_id])

    async def get_power_limit(self, dev_id: str) -> dict[str, object]:
        """GET ``devs/<id>/htr_system/power_limit``: the current limit."""
        self._record("GET", f"devs/{dev_id}/htr_system/power_limit", None)
        self._maybe_fail("GET", f"devs/{dev_id}/htr_system/power_limit")
        self._require_dev(dev_id)
        return {"power_limit": self._power_limit[dev_id]}

    async def get_rtc_time(self, dev_id: str) -> dict[str, object]:
        """GET ``devs/<id>/mgr/rtc/time``: the raw RTC payload."""
        self._record("GET", f"devs/{dev_id}/mgr/rtc/time", None)
        self._maybe_fail("GET", f"devs/{dev_id}/mgr/rtc/time")
        return self._dev(dev_id)["rtc"]  # type: ignore[return-value]

    async def get_discovery(self, dev_id: str) -> dict[str, object]:
        """GET ``devs/<id>/mgr/discovery``: ``{discovery: ...}``."""
        self._record("GET", f"devs/{dev_id}/mgr/discovery", None)
        self._maybe_fail("GET", f"devs/{dev_id}/mgr/discovery")
        return self._dev(dev_id)["discovery"]  # type: ignore[return-value]

    async def get_node_version(self, dev_id: str, node: NodeRef) -> dict[str, object]:
        """GET ``.../version``: the stored version payload, or an empty shell."""
        self._record("GET", f"devs/{dev_id}/{node.node_type}/{node.addr}/version", None)
        self._maybe_fail("GET", f"devs/{dev_id}/{node.node_type}/{node.addr}/version")
        stored = self._node(dev_id, node).version
        return dict(stored) if stored is not None else {}

    async def get_node_samples(
        self,
        dev_id: str,
        node: NodeRef,
        from_epoch: int,
        to_epoch: int,
    ) -> dict[str, list[dict[str, object]]]:
        """GET ``.../samples``: the wire wrapper around the stored rows."""
        path = (
            f"devs/{dev_id}/{node.node_type}/{node.addr}/samples"
            f"?start={from_epoch}&end={to_epoch}"
        )
        self._record("GET", path, None)
        self._maybe_fail("GET", path)
        self._node(dev_id, node)
        return {"samples": self._dev(dev_id)["samples"]}  # type: ignore[dict-item]

    async def get_node_power(self, dev_id: str, node: NodeRef) -> int:
        """GET ``.../power``: the node's power (PMO model shortcut).

        Served from the node's stored status ``power`` key (the fake
        models the server's per-endpoint shape loosely here; the real
        wire keeps ``/power`` separate from ``/status``).
        """
        self._record("GET", f"devs/{dev_id}/{node.node_type}/{node.addr}/power", None)
        self._maybe_fail("GET", f"devs/{dev_id}/{node.node_type}/{node.addr}/power")
        status = self._node(dev_id, node).status
        try:
            return int(status["power"])  # type: ignore[call-overload]
        except (KeyError, TypeError, ValueError) as e:
            msg = f"Unexpected power response from {dev_id}/{node.node_type}/{node.addr}/power: {status!r}"
            raise SmartboxError(msg) from e

    async def close(self) -> None:
        """No session to release (config-less fake)."""

    @property
    def writes(self) -> WriteApi:
        """The same named write ops the real client exposes."""
        if self._writes is None:
            self._writes = WriteApi(self)
        return self._writes

    # --- write seam (what ``WriteApi`` calls; models the SERVER side) ---

    async def post(self, path: str, body: Mapping[str, object]) -> None:
        """Record the POST, apply scripted failure, mutate state like the server."""
        self._record("POST", path, dict(body))
        self._maybe_fail("POST", path)
        self._apply_post(path, dict(body))

    # --- test-side controls ---

    def fail_next(self, status: int) -> None:
        """Script the NEXT request (GET or POST) to fail with ``status``.

        4xx -> ``SmartboxError``, 5xx -> ``TransportUnavailableError``,
        exactly like the real error contract.
        """
        self._fail_next = status

    def snapshot_payload(self) -> list[dict[str, object]]:
        """Render the current state as a homes payload for ``FakeTransport``.

        One flat home holding every device, each dev carrying its payload
        under ``devData`` (the wire's all_data nesting: homes -> devs ->
        devData) with ``connected``/``away_status``/``htr_system`` plus
        the nodes' CURRENT status/setup (and prog when set) - so a
        snapshot delivered through the transport shows the same world
        the write ops mutated, and the core engine ingests it directly.
        """
        devs: list[dict[str, object]] = []
        for dev_id in self._devices:
            nodes = [
                {
                    "type": node_type,
                    "addr": addr,
                    "status": dict(fake_node.status),
                    "setup": dict(fake_node.setup),
                    **(
                        {"prog": {"prog": dict(fake_node.prog)}}
                        if fake_node.prog is not None
                        else {}
                    ),
                    **(
                        {"version": dict(fake_node.version)}
                        if fake_node.version is not None
                        else {}
                    ),
                }
                for (dev, node_type, addr), fake_node in self._nodes.items()
                if dev == dev_id
            ]
            dev_data: dict[str, object] = {
                **self._devices[dev_id],
                "connected": self._connected[dev_id],
                "away_status": dict(self._away[dev_id]),
                "nodes": nodes,
            }
            if self._power_limit[dev_id] is not None:
                # Live shape (captured 2026-10-16): the device carries the
                # limit twice, the object copy as the digit string the wire
                # serves and the setup copy as the int - see
                # smartbox.core.merging._snapshot_power_limit.
                limit = self._power_limit[dev_id]
                dev_data["htr_system"] = {
                    "power_limit": {"power_limit": str(limit)},
                    "setup": {
                        # the fake stores the number (POST coerces the
                        # digit string), the wire's setup copy is an int
                        "power_limit": int(str(limit)),
                        "refresh_period": 30,
                    },
                }
            devs.append({"dev_id": dev_id, "devData": dev_data})
        return [{"id": "fake_home", "devs": devs, "owner": True}]

    # --- internals ---

    def _record(self, method: str, path: str, body: object) -> None:
        self.requests.append((method, path, body))

    def _maybe_fail(self, method: str, path: str) -> None:
        status = self._fail_next
        if status is None:
            return
        self._fail_next = None
        if status >= _HTTP_BAD_REQUEST:
            if status >= _HTTP_SERVER_ERROR:
                msg = f"smartbox API unavailable ({method} {path}, status={status})"
                raise TransportUnavailableError(msg)
            msg = f"smartbox API rejected {method} {path} (status={status})"
            raise SmartboxError(msg)

    def _dev(self, dev_id: str) -> dict[str, object]:
        self._require_dev(dev_id)
        return self._devices[dev_id]

    def _require_dev(self, dev_id: str) -> None:
        if dev_id not in self._devices:
            msg = f"FakeSmartbox has no device {dev_id!r}"
            raise SmartboxError(msg)

    def _node(self, dev_id: str, node: NodeRef) -> FakeNode:
        key = (dev_id, node.node_type, node.addr)
        if key not in self._nodes:
            msg = (
                f"FakeSmartbox has no node {node.node_type}/{node.addr!r} on {dev_id!r}"
            )
            raise SmartboxError(msg)
        return self._nodes[key]

    def _apply_post(  # noqa: C901 - one branch per endpoint shape
        self, path: str, body: dict[str, object]
    ) -> None:
        """Mutate the model exactly as the server would for the posted shape."""
        segments = path.split("/")
        if len(segments) >= _NODE_PATH_SEGMENTS and segments[-1] == "status":
            self._node(segments[1], NodeRef(segments[-3], segments[-2])).status.update(
                body
            )
        elif len(segments) >= _NODE_PATH_SEGMENTS and segments[-1] == "setup":
            # the op posts the COMPLETE merged object - replace wholesale.
            # Legacy product codes are the one exception: the server drops
            # their extra_options writes, so its stored object keeps the last
            # accepted value even though every POST omits the key.
            node = self._node(segments[1], NodeRef(segments[-3], segments[-2]))
            posted = dict(body)
            stored_extra = node.setup.get("extra_options")
            version = node.version or {}
            if (
                "extra_options" not in posted
                and isinstance(stored_extra, dict)
                and not WriteApi.supports_extra_options(version.get("pid"))
            ):
                posted["extra_options"] = stored_extra
            node.setup = posted
        elif segments[-1] == "prog" and isinstance(prog := body.get("prog"), dict):
            self._node(segments[1], NodeRef(segments[-3], segments[-2])).prog = dict(
                prog
            )
        elif len(segments) == _DEV_PATH_SEGMENTS and segments[2:] == [
            "mgr",
            "away_status",
        ]:
            self._away[segments[1]] = dict(body)
        elif len(segments) >= _NODE_PATH_SEGMENTS and segments[-1] == "power_limit":
            # the node's OWN limit (the box path above is htr_system): the
            # server keeps it in the node's setup, as the live pmo capture
            # shows; the int form mirrors that snapshot copy
            value = body.get("power_limit")
            if isinstance(value, str) and value.isdigit():
                value = int(value)
            self._node(segments[1], NodeRef(segments[-3], segments[-2])).setup[
                "power_limit"
            ] = value
        elif len(segments) >= _NODE_PATH_SEGMENTS and segments[-1] == "boost":
            node = self._node(segments[1], NodeRef(segments[-3], segments[-2]))
            node.status["boost"] = body.get("boost") is True
            if body.get("boost") is True and "stemp" in body:
                node.status["stemp"] = body["stemp"]
        elif len(segments) >= _NODE_PATH_SEGMENTS and segments[-1] == "lock":
            node = self._node(segments[1], NodeRef(segments[-3], segments[-2]))
            node.status["locked"] = body.get("locked")
        elif len(segments) >= _NODE_PATH_SEGMENTS and segments[-1] == "mode":
            self._node(segments[1], NodeRef(segments[-3], segments[-2])).status.update(
                body
            )
        elif len(segments) == _DEV_PATH_SEGMENTS and segments[2:] == [
            "htr_system",
            "power_limit",
        ]:
            # the posted body is the stringified wire form (E18); the
            # model stores the number the GET/dev_data shapes serve
            value = body.get("power_limit")
            if isinstance(value, str) and value.isdigit():
                value = int(value)
            self._power_limit[segments[1]] = value
        else:
            msg = f"FakeSmartbox does not model POST {path}"
            raise SmartboxError(msg)
