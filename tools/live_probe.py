#!/usr/bin/env python
"""Live-wire probe CLI (wire investigation from this repo).

Drives the REAL hosts through the vendored protocol layer, using the
user's own ``.env`` at the repo root (keys: ``SMARTBOX_API_NAME``,
``SMARTBOX_USERNAME``, ``SMARTBOX_PASSWORD``; gitignored). The .env file
is the user's credential file: agents must never read or print it.

Commands: ``auth`` | ``probe`` | ``reads`` | ``ws`` | ``write`` | ``all``
(see the per-command help). ``write`` is guarded: it refuses without
``--i-know-this-writes-to-my-heater``.

Hygiene rules (hard, same as the layers): never print/log tokens,
basic_auth, passwords or any URL carrying a token; masked identifiers
only; statuses only for errors. This tool must not undermine the
layers' guarantee with its own prints.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
import contextlib
import datetime as dt
import json
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING, Any, Self, cast

from aiohttp import ClientSession

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smartbox.protocol import (
    VALID_BOOST_MODES,
    AuthConfig,
    AuthError,
    AvailableResellers,
    GrantCallback,
    ManagedTokenSource,
    NodeRef,
    ResellerNotExistError,
    SmartboxClient,
    SmartboxError,
    Snapshot,
    SocketClosed,
    TokenBundle,
    TokenManager,
    TransportUnavailableError,
    TransportUnsupportedError,
    Update,
    WriteApi,
    WsUserTransport,
    api_base_url,
    check_ws_user_support,
    jwt_user_id,
)
from smartbox.token_cache import FileTokenCache, account_stamp

if TYPE_CHECKING:
    from smartbox.protocol.reseller import SmartboxReseller

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
REQUIRED_KEYS = ("SMARTBOX_API_NAME", "SMARTBOX_USERNAME", "SMARTBOX_PASSWORD")

# Missing-keys + auth-failure hint (wrong api_name for the account = auth
# failure).
RESELLER_HINT = (
    "check SMARTBOX_USERNAME / SMARTBOX_PASSWORD, and that "
    "SMARTBOX_API_NAME is the brand your account was created with "
    "(a reseller mismatch shows up as an auth failure)"
)

_SAMPLES_DEFAULT_WINDOW_S = 3600  # reads: default samples window = last hour
_PREVIEW_CHARS = 300  # bounded preview of raw payloads
_UPDATE_PATH_PREVIEW_MAX = 20  # distinct update paths kept for the ws summary

GUARD_FLAG = "--i-know-this-writes-to-my-heater"
_NODELESS_OPS = frozenset({"set_away", "set_power_limit"})


def load_env(path: Path) -> dict[str, str]:
    """Parse a minimal KEY=VALUE .env file (no third-party dependency)."""
    env: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def missing_keys(env: dict[str, str]) -> list[str]:
    """Return the required .env keys that are absent or blank."""
    return [key for key in REQUIRED_KEYS if not env.get(key, "").strip()]


def mask(value: str, keep: int = 4) -> str:
    """Mask an identifier, keeping only a short prefix."""
    return (value[:keep] + "…") if len(value) > keep else "…"


def jwt_payload(token: str) -> dict[str, Any]:
    """Decode the payload part of a JWT (no signature verification)."""
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return cast("dict[str, Any]", json.loads(base64.urlsafe_b64decode(part)))


def build_config(env: dict[str, str]) -> tuple[SmartboxReseller, AuthConfig]:
    """Resolve the reseller from SMARTBOX_API_NAME and build the AuthConfig.

    No basic-auth key is needed: the reseller table supplies
    basic_auth/serial_id/referer (reseller.py does the lookup). An
    unknown reseller key raises ResellerNotExistError.
    """
    reseller = AvailableResellers(api_url=env["SMARTBOX_API_NAME"].strip()).reseller
    config = AuthConfig(
        # the host the vendor apps dial; the reseller row is the brand key
        api_host=api_base_url(),
        basic_auth_creds=reseller.basic_auth,
        serial_id=str(reseller.serial_id),
        username=env["SMARTBOX_USERNAME"].strip(),
        password=env["SMARTBOX_PASSWORD"].strip(),
        referer=reseller.web_url or None,
    )
    return reseller, config


class Stack:
    """One shared builder: (TokenManager, SmartboxClient, transport factory)."""

    def __init__(
        self,
        config: AuthConfig,
        *,
        initial_tokens: TokenBundle | None = None,
        on_grant: GrantCallback | None = None,
    ) -> None:
        """Hold the config; the session/client are built on ``__aenter__``."""
        self.config = config
        self._initial_tokens = initial_tokens
        self._on_grant = on_grant
        self._session: ClientSession | None = None
        self.client: SmartboxClient | None = None

    async def __aenter__(self) -> Self:
        """Open the HTTP session + client (shared, closed on ``__aexit__``)."""
        self._session = ClientSession()
        self.client = SmartboxClient(
            self.config,
            session=self._session,
            initial_tokens=self._initial_tokens,
            on_grant=self._on_grant,
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the shared session (the client itself is stateless here)."""
        self.client = None
        if self._session is not None:
            session, self._session = self._session, None
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(session.close(), 5.0)

    @property
    def session(self) -> ClientSession:
        """The shared HTTP session (live only inside the async context)."""
        if self._session is None:
            msg = "Stack used outside its async context"
            raise RuntimeError(msg)
        return self._session

    @property
    def auth(self) -> TokenManager:
        """TokenManager (client-owned)."""
        if self.client is None:
            msg = "Stack used outside its async context"
            raise RuntimeError(msg)
        return self.client.auth

    def build_transport(self, heartbeat: float) -> WsUserTransport:
        """One ws_user transport over the stack's token source and session."""
        if self.client is None:
            msg = "Stack used outside its async context"
            raise RuntimeError(msg)
        return WsUserTransport(
            ManagedTokenSource(self.auth),
            self.config.api_host,
            heartbeat=heartbeat,
            session=self.session,
        )


def _print_json(payload: Any) -> None:
    # The version payload carries the node uid; keep device identifiers
    # masked even in the raw read output.
    if isinstance(payload, dict) and "uid" in payload:
        payload = {**payload, "uid": mask(str(payload["uid"]))}
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def _preview(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True)
    if len(text) > _PREVIEW_CHARS:
        text = f"{text[:_PREVIEW_CHARS]}…"
    return text


def _relative(moment: dt.datetime) -> str:
    delta = moment - dt.datetime.now(dt.UTC)
    total_s = int(delta.total_seconds())
    sign = "-" if total_s < 0 else ""
    total_s = abs(total_s)
    h, rem = divmod(total_s, 3600)
    m = rem // 60
    if h:
        return f"{sign}{h}h{m:02d}m"
    return f"{sign}{m}m"


def _parse_node(text: str) -> NodeRef:
    """Parse ``TYPE:ADDR`` into a NodeRef."""
    node_type, sep, addr = text.partition(":")
    if not sep or not node_type or not addr:
        msg = f"node must look like TYPE:ADDR, got: {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return NodeRef(node_type, addr)


def _parse_bool(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    msg = f"boolean must be true or false, got: {text!r}"
    raise argparse.ArgumentTypeError(msg)


def _parse_day(text: str) -> tuple[str, list[int]]:
    """Parse ``DAY=n,n,...`` (day key 0..6, comma-separated 48-slot ints)."""
    day, sep, values = text.partition("=")
    if not sep or not values:
        msg = f"day must look like 0=1,2,3, got: {text!r}"
        raise argparse.ArgumentTypeError(msg)
    try:
        return day, [int(value) for value in values.split(",")]
    except ValueError as e:
        msg = f"bad prog values in {text!r}: ints only"
        raise argparse.ArgumentTypeError(msg) from e


# --- commands ---


def _print_token_facts(bundle: Any) -> None:
    # Presence, length and expiry, never the token itself: the first characters
    # of a JWT are the same for every token ("eyJ…"), so a prefix would look
    # like a leak while carrying no information.
    print(f"access token: present (length {len(bundle.access_token)})")
    expires_at = bundle.expires_at.astimezone(dt.UTC)
    print(f"expires_at: {expires_at.isoformat()} ({_relative(expires_at)})")
    print(f"user_id claim: {mask(jwt_user_id(bundle.access_token))}")


async def _auth_command(stack: Stack) -> int:
    bundle = await stack.auth.get_token()
    print("== password grant ==")
    _print_token_facts(bundle)
    await stack.auth.invalidate(bundle.access_token)
    refreshed = await stack.auth.get_token()
    print("== refresh grant (rotation check) ==")
    rotated = refreshed.access_token != bundle.access_token
    print(f"access token: rotated: {rotated}")
    _print_token_facts(refreshed)
    return 0


async def _probe_command(stack: Stack, *, seeded: bool = False) -> int:
    """Probe the endpoint; a seeded cache gets one retry with a fresh grant."""

    async def probe(token: str) -> None:
        await check_ws_user_support(token, stack.config.api_host, stack.session)

    bundle = await stack.auth.get_token()
    try:
        await probe(bundle.access_token)
    except TransportUnsupportedError as e:
        if not seeded:
            print(f"ws_user: UNSUPPORTED ({e})")
            return 1
        print("note: cached token rejected; retrying the probe with a fresh grant")
        await stack.auth.invalidate(bundle.access_token)
        bundle = await stack.auth.get_token()
        try:
            await probe(bundle.access_token)
        except TransportUnsupportedError as retry_err:
            print(f"ws_user: UNSUPPORTED ({retry_err})")
            return 1
        except TransportUnavailableError as retry_err:
            print(f"ws_user: UNAVAILABLE ({retry_err})")
            return 1
    except TransportUnavailableError as e:
        print(f"ws_user: UNAVAILABLE ({e})")
        return 1
    print("ws_user: supported")
    return 0


def _selected_devs(
    homes: list[dict[str, Any]], dev_filter: str | None
) -> list[dict[str, Any]]:
    return [
        dev
        for home in homes
        for dev in home.get("devs") or []
        if dev_filter is None or dev.get("dev_id") == dev_filter
    ]


async def _read_one_node(client: SmartboxClient, dev_id: str, node: NodeRef) -> None:
    """Status / setup / version for one node, raw, one per section."""
    header = f"node type={node.node_type} addr={node.addr}"
    if node.node_type != "pmo":  # pmo nodes carry no status gate (wire fact)
        status = await client.get_node_status(dev_id, node)
        print(f"-- {header} status --")
        _print_json(status)
    setup = await client.get_node_setup(dev_id, node)
    print(f"-- {header} setup --")
    _print_json(setup)
    version = await client.get_node_version(dev_id, node)
    print(f"-- {header} version --")
    _print_json(version)


async def _reads_command(stack: Stack, args: argparse.Namespace) -> int:
    client = stack.client
    assert client is not None
    homes = await client.get_grouped_devices()
    devs = _selected_devs(homes, args.dev)
    if args.dev and not devs:
        print(f"no device matching --dev {mask(args.dev)} in {len(homes)} homes")
        return 1
    print(f"homes: {len(homes)}, devices selected: {len(devs)}")
    for dev in devs:
        dev_id = dev["dev_id"]
        print(f"== device {mask(dev_id)} name={dev.get('name')!r} ==")
        nodes = await client.get_nodes(dev_id)
        print(f"nodes: {len(nodes)}")
        for node_info in nodes:
            ref = NodeRef(node_info["type"], str(node_info["addr"]))
            if args.node is not None and ref != args.node:
                continue
            await _read_one_node(client, dev_id, ref)
            if args.samples_from or args.samples_to:
                now = int(dt.datetime.now(dt.UTC).timestamp())
                samples_from = args.samples_from or now - _SAMPLES_DEFAULT_WINDOW_S
                samples_to = args.samples_to or now
                samples = await client.get_node_samples(
                    dev_id, ref, samples_from, samples_to
                )
                print(
                    "-- samples "
                    f"start={samples_from} end={samples_to} "
                    f"count={len(samples)} (preview) --"
                )
                print(_preview(samples))
    return 0


class _WsObserver:
    """Printing handler: counts by event type; bounded previews."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.close_codes: Counter[int] = Counter()
        self.snapshot_homes = 0
        self.snapshot_devs = 0
        self.snapshot_nodes = 0
        self.update_paths: dict[str, list[Any]] = {}
        self._t0 = time.monotonic()

    async def __call__(self, event: Any) -> None:
        kind = type(event).__name__
        self.counts[kind] += 1
        if isinstance(event, Snapshot):
            homes = list(event.payload)
            self.snapshot_homes = len(homes)
            for home in homes:
                for dev in home.get("devs") or []:
                    self.snapshot_devs += 1
                    dev_data = dev.get("devData") or {}
                    self.snapshot_nodes += len(dev_data.get("nodes") or [])
            print(
                f"snapshot: homes={self.snapshot_homes} "
                f"devs={self.snapshot_devs} nodes={self.snapshot_nodes}"
            )
        elif isinstance(event, Update):
            path = event.path
            if path not in self.update_paths:
                self.update_paths[path] = [0, ""]
            self.update_paths[path][0] += 1
            self.update_paths[path][1] = _preview(event.body)
            highlight = ""
            if isinstance(event.body, dict) and event.path.endswith("/status"):
                interesting = {
                    k: event.body[k]
                    for k in (
                        "mode",
                        "active",
                        "duty",
                        "mtemp",
                        "window_open",
                        "presence",
                    )
                    if k in event.body
                }
                highlight = f"  << {json.dumps(interesting, sort_keys=True)}"
            print(
                f"[+{time.monotonic() - self._t0:6.1f}s] update "
                f"{mask(event.dev_id)} {path}: {self.update_paths[path][1]}"
                f"{highlight}"
            )
        elif isinstance(event, SocketClosed):
            self.close_codes[event.code] += 1
            print(f"socket closed (code={event.code})")

    def summary(self) -> str:
        lines = [
            f"events: {dict(sorted(self.counts.items()))}",
            f"close codes: {dict(sorted(self.close_codes.items()))}",
            (
                f"snapshot: homes={self.snapshot_homes} "
                f"devs={self.snapshot_devs} nodes={self.snapshot_nodes}"
            ),
            (
                "update counts by path: "
                f"{ {p: c for p, (c, _) in sorted(self.update_paths.items())} }"
            ),
        ]
        return "\n".join(lines)


async def _ws_command(stack: Stack, args: argparse.Namespace) -> int:
    observer = _WsObserver()
    transport = stack.build_transport(args.heartbeat)
    transport.subscribe(observer)
    run_task = asyncio.create_task(transport.run())
    interrupted = False
    try:
        await asyncio.wait({run_task}, timeout=args.duration)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        await transport.close()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task
    return _ws_summary(observer, transport, interrupted)


def _ws_summary(observer: _WsObserver, transport: Any, interrupted: bool) -> int:
    print(f"== ws summary (interrupted: {interrupted}) ==")
    print(observer.summary())
    print(f"transport facts: {transport.facts}")
    return 0


# --- write command ---


class _PrintingWriteHost:
    """WriteHost that prints the exact body before delegating to the client.

    The printed path masks the account-scoped device id, like every other line
    of this tool: the raw id appeared in the POST line only.
    """

    def __init__(self, client: SmartboxClient, dev_id: str | None = None) -> None:
        self._client = client
        self._dev_id = dev_id

    async def post(self, path: str, body: Any) -> None:
        shown = path
        if self._dev_id:
            shown = path.replace(self._dev_id, mask(self._dev_id))
        print(f"POST /api/v2/{shown}")
        _print_json(body)
        await self._client.post(path, body)

    async def get_node_setup(self, dev_id: str, node: NodeRef) -> dict[str, Any]:
        return await self._client.get_node_setup(dev_id, node)

    async def get_node_prog(self, dev_id: str, node: NodeRef) -> dict[str, Any]:
        return await self._client.get_node_prog(dev_id, node)


async def _run_write_op(api: WriteApi, args: argparse.Namespace) -> None:
    """Dispatch ONE WriteApi op from parsed CLI args (explicit, small)."""
    dev_id = args.dev
    node: NodeRef = args.node
    op = args.op
    if op in _NODELESS_OPS:
        if node is not None:
            print(
                f"NOTE: {op} is device-level; ignoring --node {node.node_type}:{node.addr}"
            )
        node = None  # type: ignore[assignment]
    elif node is None:
        print(
            f"FAIL: {op} needs --node TYPE:ADDR (device-level ops only: {sorted(_NODELESS_OPS)})"
        )
        raise SystemExit(2)
    if op == "set_mode":
        await api.set_mode(dev_id, node, args.mode)
    elif op == "set_setpoint":
        await api.set_setpoint(dev_id, node, args.temp, args.units)
    elif op == "engage_modified_auto":
        await api.engage_modified_auto(dev_id, node, args.temp, args.units)
    elif op == "set_lock":
        await api.set_lock(dev_id, node, args.locked)
    elif op == "set_boost":
        await api.set_boost(dev_id, node, args.on)
    elif op == "set_boost_mode":
        await api.set_boost_mode(
            dev_id,
            node,
            active=args.active,
            mode=args.mode,
            temperature=args.temp,
            time_minutes=args.time_minutes,
        )
    elif op == "set_prog_temps":
        await api.set_prog_temps(
            dev_id,
            node,
            ice_temp=args.ice_temp,
            eco_temp=args.eco_temp,
            comf_temp=args.comf_temp,
            units=args.units,
        )
    elif op == "set_window_mode":
        await api.set_window_mode(dev_id, node, args.enabled)
    elif op == "set_true_radiant":
        await api.set_true_radiant(dev_id, node, args.enabled)
    elif op == "set_away_offset":
        await api.set_away_offset(dev_id, node, args.offset)
    elif op == "set_max_stemp_limit":
        await api.set_max_stemp_limit(dev_id, node, args.limit, args.units)
    elif op == "set_priority":
        await api.set_priority(dev_id, node, args.priority)
    elif op == "set_away":
        await api.set_away(dev_id, args.away)
    elif op == "set_power_limit":
        await api.set_power_limit(dev_id, args.value)
    elif op == "set_node_power_limit":
        await api.set_node_power_limit(dev_id, node, args.value)
    elif op == "set_prog":
        await api.set_prog(dev_id, node, dict(args.day or []))
    else:
        msg = f"unknown write op: {op!r}"
        raise ValueError(msg)


# op -> which resource to GET back after the write (settle-lag caveat).
_WRITE_READBACK = {
    "set_mode": "status",
    "set_setpoint": "status",
    "engage_modified_auto": "status",
    "set_lock": "status",
    "set_boost": "status",
    "set_boost_mode": "status",
    "set_prog_temps": "status",
    "set_window_mode": "setup",
    "set_true_radiant": "setup",
    "set_away_offset": "setup",
    "set_max_stemp_limit": "setup",
    "set_priority": "setup",
    "set_prog": "prog",
    "set_away": "away",
    "set_power_limit": "power_limit",
    "set_node_power_limit": "setup",
}


async def _print_write_readback(
    client: SmartboxClient, args: argparse.Namespace
) -> None:
    """GET the affected resource and print it (server read-through lags)."""
    node: NodeRef = args.node
    dev_id = args.dev
    kind = _WRITE_READBACK[args.op]
    if kind in ("away", "power_limit"):
        node = None  # type: ignore[assignment]
    if kind == "status":
        if node is None:
            print("== readback skipped (no node for a status readback) ==")
            return
        payload: Any = await client.get_node_status(dev_id, node)
    elif kind == "setup":
        payload = await client.get_node_setup(dev_id, node)
    elif kind == "prog":
        payload = await client.get_node_prog(dev_id, node)
    elif kind == "away":
        payload = await client.get_away_status(dev_id)
    else:
        payload = await client.get_power_limit(dev_id)
    print(
        f"== readback ({kind}; note: server read-through may lag several "
        "seconds and show the old value) =="
    )
    _print_json(payload)


async def _write_command(stack: Stack, args: argparse.Namespace) -> int:
    assert stack.client is not None
    api = WriteApi(_PrintingWriteHost(stack.client, args.dev))
    await _run_write_op(api, args)
    await _print_write_readback(stack.client, args)
    if args.sleep:
        print(f"sleeping {args.sleep}s (spacing the next write)")
        await asyncio.sleep(args.sleep)
    return 0


# --- argparse + runner ---


def _add_read_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dev", help="dev_id filter (as on the wire)")
    parser.add_argument(
        "--node", type=_parse_node, metavar="TYPE:ADDR", help="single node"
    )
    parser.add_argument(
        "--samples-from", type=int, default=None, help="samples window start (epoch)"
    )
    parser.add_argument(
        "--samples-to", type=int, default=None, help="samples window end (epoch)"
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the six-command CLI surface."""
    parser = argparse.ArgumentParser(
        prog="live_probe.py",
        description="Live-wire probe against the real hosts (.env-driven).",
    )
    parser.add_argument(
        "--token-cache",
        metavar="PATH",
        default=None,
        help="Persist the refresh token between runs (ignored by 'auth').",
    )
    cache_option = argparse.ArgumentParser(add_help=False)
    cache_option.add_argument(
        "--token-cache",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="Persist the refresh token between runs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="password grant + one refresh grant")

    sub.add_parser(
        "probe", help="ws_user capability probe verdict", parents=[cache_option]
    )

    reads = sub.add_parser(
        "reads", help="devices + nodes raw reads", parents=[cache_option]
    )
    _add_read_selection(reads)

    ws = sub.add_parser(
        "ws", help="subscribe the ws_user transport", parents=[cache_option]
    )
    ws.add_argument("--duration", type=float, default=60.0)
    ws.add_argument("--heartbeat", type=float, default=60.0)

    write = sub.add_parser(
        "write",
        help=f"WriteApi ops (guarded; pass {GUARD_FLAG})",
        parents=[cache_option],
    )
    write.add_argument("--dev", required=True, help="target dev_id")
    write.add_argument(
        "--sleep",
        type=int,
        default=0,
        metavar="SECONDS",
        help="sleep this long after the write + readback (spacing consecutive writes)",
    )
    write.add_argument(
        "--node",
        type=_parse_node,
        metavar="TYPE:ADDR",
        help="target node TYPE:ADDR (device-level ops excepted)",
    )
    write.add_argument(
        GUARD_FLAG,
        dest="i_know",
        action="store_true",
        help="required: acknowledge this writes to your heater",
    )
    op_sub = write.add_subparsers(dest="op", required=True)
    for name in _WRITE_READBACK:
        _build_write_op_parser(op_sub, name)

    all_cmd = sub.add_parser(
        "all", help="probe + reads (daily sanity menu)", parents=[cache_option]
    )
    _add_read_selection(all_cmd)

    return parser


def _build_write_op_parser(op_sub: Any, name: str) -> None:
    """One subparser per WriteApi op (explicit, small)."""
    p = op_sub.add_parser(name)
    p.set_defaults(op=name)
    if name == "set_mode":
        p.add_argument("mode")
    elif name in ("set_setpoint", "engage_modified_auto"):
        p.add_argument("temp", type=float)
        p.add_argument("--units", default="C", choices=["C", "F"])
    elif name in ("set_lock", "set_boost"):
        p.add_argument("locked" if name == "set_lock" else "on", type=_parse_bool)
    elif name == "set_boost_mode":
        p.add_argument("active", type=_parse_bool)
        p.add_argument("--mode", choices=list(VALID_BOOST_MODES))
        p.add_argument("--temp", type=float)
        p.add_argument("--time", type=int, dest="time_minutes")
    elif name == "set_prog_temps":
        p.add_argument("--ice-temp", type=float)
        p.add_argument("--eco-temp", type=float)
        p.add_argument("--comf-temp", type=float)
        p.add_argument("--units", default=None, choices=["C", "F"])
    elif name in ("set_window_mode", "set_true_radiant"):
        p.add_argument("enabled", type=_parse_bool)
    elif name == "set_away_offset":
        p.add_argument("offset", type=float)
    elif name == "set_max_stemp_limit":
        p.add_argument("limit", type=float, nargs="?", default=None)
        p.add_argument("--units", default=None, choices=["C", "F"])
    elif name == "set_priority":
        p.add_argument("priority", choices=["low", "medium", "high"])
    elif name == "set_away":
        p.add_argument("away", type=_parse_bool)
    elif name in ("set_power_limit", "set_node_power_limit"):
        p.add_argument("value", type=int)
    elif name == "set_prog":
        p.add_argument("--day", action="append", type=_parse_day, metavar="DAY=n,n,...")


def _load_and_check_env() -> dict[str, str]:
    """Load .env and fail with the missing-keys message + reseller hint."""
    if not ENV_PATH.is_file():
        print(f"FAIL: no .env at {ENV_PATH} (keys: {', '.join(REQUIRED_KEYS)})")
        raise SystemExit(2)
    env = load_env(ENV_PATH)
    missing = missing_keys(env)
    if missing:
        print(f"FAIL: .env is missing {', '.join(missing)}\nhint: {RESELLER_HINT}")
        raise SystemExit(2)
    return env


async def _dispatch(args: argparse.Namespace) -> int:
    """Open one shared stack and run the selected command."""
    env = _load_and_check_env()
    reseller, config = build_config(env)
    cache: FileTokenCache | None = None
    stored: TokenBundle | None = None
    if args.token_cache and args.command != "auth":
        cache = FileTokenCache(
            Path(args.token_cache),
            account=account_stamp(
                env["SMARTBOX_API_NAME"],
                config.serial_id,
                config.username,
                config.api_host,
            ),
        )
        stored = await cache.async_load()
        print(
            f"cache: {cache.path} ({'loaded' if stored is not None else 'no record'})"
        )
    elif args.token_cache:
        print("note: 'auth' exercises fresh grants; --token-cache ignored")
    async with Stack(
        config,
        initial_tokens=stored,
        on_grant=cache.async_save if cache is not None else None,
    ) as stack:
        print(f"== reseller: {reseller.name} (key: {env['SMARTBOX_API_NAME']}) ==")
        seeded = stack.auth.seed_accepted and stack.auth.last_grant != "password"
        if args.command == "auth":
            return await _auth_command(stack)
        if args.command == "probe":
            return await _probe_command(stack, seeded=seeded)
        if args.command == "reads":
            return await _reads_command(stack, args)
        if args.command == "ws":
            return await _ws_command(stack, args)
        if args.command == "write":
            return await _write_command(stack, args)
        if args.command == "all":
            code = await _probe_command(stack, seeded=seeded)
            return await _reads_command(stack, args) if code == 0 else code
        msg = f"unknown command: {args.command!r}"
        raise ValueError(msg)


def _fail(err: Exception) -> int:
    """One-line failure print (layer messages never carry token URLs)."""
    print(f"FAIL: {type(err).__name__}: {err}")
    if isinstance(err, AuthError):
        print(f"hint: {RESELLER_HINT}")
    return 1


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse, guard, dispatch; map layer errors to FAIL lines."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "write" and not args.i_know:
        print(
            f"REFUSED: writes go to your real heater. Pass {GUARD_FLAG} "
            "to confirm you understand."
        )
        return 2
    try:
        return asyncio.run(_dispatch(args))
    except ResellerNotExistError as e:
        known = ", ".join(sorted(AvailableResellers.resellers))
        print(f"FAIL: unknown reseller key. Valid keys: {known}\n({e})")
        return 2
    except (AuthError, TransportUnavailableError, TransportUnsupportedError) as e:
        return _fail(e)
    except (SmartboxError, OSError) as e:
        return _fail(e)
    except KeyboardInterrupt:
        print("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
