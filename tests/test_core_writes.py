"""Core engine write path: optimistic merge + pending-write confirmation.

Composition tests run engine writes against a FakeSmartbox write host
with frames delivered through FakeTransport - the same supervised
wiring the adapter will use. The fake's confirming ok frame is the
full post-write status dict; the store's dedup must absorb it.
"""

from __future__ import annotations

import asyncio

import pytest
from smartbox.core.engine import SmartboxEngine
from smartbox.core.state import DeviceChange, NodeChange
from smartbox.protocol import timing
from smartbox.protocol.client import NodeRef
from smartbox.protocol.errors import SmartboxError, TransportUnavailableError
from smartbox.protocol.events import Snapshot

from tests.fake_api import FakeSmartbox
from tests.fake_transport import FakeTransport
from tests.test_utils import armed_deadlines

NODE_REF = NodeRef("htr", "2")
LOCKED = True  # FBT003-friendly bool literal for write calls
ON = True  # FBT003-friendly bool literals for write calls
STATUS = {"stemp": "20.0", "mtemp": "19.6", "sync_status": "ok"}
LOST = {"sync_status": "lost"}


class FakeClock:
    """Injectable monotonic clock the tests control."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


class InstantSleep:
    """Instant ``timing.sleep`` replacement (advances the fake clock)."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.delays: list[float] = []

    async def __call__(self, delay=0):
        self.delays.append(delay)
        self.clock.advance(delay)


class HoldSleep:
    """``timing.sleep`` replacement that parks every call until released.

    The clock only advances when a gate is released, so a parked deadline
    stays due-but-unfired exactly like a real timer waiting on the loop.
    """

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.gates: list[tuple[asyncio.Event, float]] = []

    async def __call__(self, delay=0):
        gate = asyncio.Event()
        self.gates.append((gate, delay))
        await gate.wait()
        self.clock.advance(delay)

    async def release_all(self):
        for gate, _delay in self.gates:
            gate.set()
        for _ in range(3):
            await asyncio.sleep(0)


class Recorder:
    """Subscriber double that records every delivered change."""

    def __init__(self):
        self.changes = []

    async def __call__(self, change):
        self.changes.append(change)


@pytest.fixture
def engine_clock() -> FakeClock:
    """Return the fake clock the autouse sleep seam advances."""
    return FakeClock()


@pytest.fixture(autouse=True)
async def _no_real_sleeps(monkeypatch, engine_clock):
    """No real waits: sleeps resolve instantly and advance the clock."""
    monkeypatch.setattr(timing, "monotonic", engine_clock)
    monkeypatch.setattr(timing, "sleep", InstantSleep(engine_clock))


class AcceptingHost:
    """Minimal WriteHost double that accepts every POST (any node)."""

    def __init__(self):
        self.posts = []

    async def post(self, path, body):
        self.posts.append((path, dict(body)))

    async def get_node_status(self, dev_id, node):  # noqa: ARG002 - seam shape
        return {}

    async def get_node_setup(self, dev_id, node):  # noqa: ARG002 - seam shape
        return {}

    async def get_node_prog(self, dev_id, node):  # noqa: ARG002 - seam shape
        return {"prog": {}}


class FrameDuringPostHost:
    """WriteHost that pushes one frame while its POST is in flight.

    Models the ordering the vendor server can produce: the write is applied,
    the box pushes its frame, and the HTTP response arrives afterwards. The
    body defaults to the fake's full-setup payload (the confirming frame of
    a ``setup`` write); ``path``/``body`` select another case.
    """

    def __init__(self, fake, transport, path="/htr/2/setup", body=None):
        self._fake = fake
        self._transport = transport
        self._path = path
        self._body = body

    async def post(self, path, body):
        await self._fake.post(path, body)  # the server applies the write
        payload = self._body
        if payload is None:
            payload = await self._fake.get_node_setup("dev1", NODE_REF)
        await self._transport.deliver_update("dev1", self._path, dict(payload))

    async def get_node_status(self, dev_id, node):
        return await self._fake.get_node_status(dev_id, node)

    async def get_node_setup(self, dev_id, node):
        return await self._fake.get_node_setup(dev_id, node)

    async def get_node_prog(self, dev_id, node):
        return await self._fake.get_node_prog(dev_id, node)


async def tick():
    """Let pending engine timer tasks run to completion."""
    for _ in range(3):
        await asyncio.sleep(0)


async def make_engine(*, prog=None, host=None, setup=None, **engine_kwargs):
    """Wire engine + fake + transport and ingest the fake's snapshot.

    ``host`` optionally wraps the fake (``host(fake, transport)``): the
    mid-POST cases need a write host that pushes a frame while the POST is
    in flight. ``setup`` overrides the node's setup body.
    """
    fake = FakeSmartbox()
    fake.add_device("dev1")
    fake.add_node(
        "dev1",
        NODE_REF,
        status=dict(STATUS),
        setup={"priority": 0} if setup is None else setup,
        prog=prog,
    )
    transport = FakeTransport()
    write_host = fake if host is None else host(fake, transport)
    engine = SmartboxEngine(write_host, **engine_kwargs)
    recorder = Recorder()
    engine.subscribe(recorder)
    engine.supervise(transport)
    await engine.handle_event(Snapshot(payload=fake.snapshot_payload()))
    recorder.changes.clear()
    return engine, recorder, fake, transport


async def make_host_engine(host, *, setup=None, prog=None):
    """Engine over a CUSTOM write host, with the fake's snapshot ingested.

    The store must already hold the node: a write merges only into state the
    wire delivered, so the fake's model is not a merge target on its own.
    """
    fake = FakeSmartbox()
    fake.add_device("dev1")
    fake.add_node(
        "dev1",
        NODE_REF,
        status=dict(STATUS),
        setup={"priority": 0} if setup is None else setup,
        prog=prog,
    )
    engine = SmartboxEngine(host)
    recorder = Recorder()
    engine.subscribe(recorder)
    transport = FakeTransport()
    engine.supervise(transport)
    await engine.handle_event(Snapshot(payload=fake.snapshot_payload()))
    recorder.changes.clear()
    return engine, recorder, fake, transport


async def deliver_status(transport, body, path="/htr/2/status", dev_id="dev1"):
    await transport.deliver_update(dev_id, path, dict(body))


async def establish_ok(recorder, transport):
    """Deliver one ok status frame (flips availability True, confirms)."""
    await deliver_status(transport, STATUS)
    recorder.changes.clear()


# --- optimistic merge: status ops ---


async def test_write_optimistic_merge_immediately_visible_and_frame_deduped():
    engine, recorder, fake, transport = await make_engine()
    await establish_ok(recorder, transport)
    try:
        await engine.set_setpoint("dev1", NODE_REF, 21.3, "C")
        # visible to subscribers immediately: exactly ONE status notification
        status_changes = [
            c
            for c in recorder.changes
            if isinstance(c, NodeChange) and c.kind == "status"
        ]
        assert len(status_changes) == 1
        node = engine.node("dev1", "htr/2")
        assert status_changes[0].node is node
        # a MERGE over the stored status, quantized onto the C grid
        assert node.status == {
            "stemp": "21.0",
            "units": "C",
            "mtemp": "19.6",
            "sync_status": "ok",
        }
        assert node.revision == 2
        assert node.available is True
        # the write went through the host's WriteApi (fake model mutated)
        assert ("POST", "devs/dev1/htr/2/status", {"stemp": "21.0", "units": "C"}) in (
            fake.requests
        )
        assert (await fake.get_node_status("dev1", NODE_REF))["stemp"] == "21.0"
        # the confirming ok frame (fake's post-write status) is absorbed:
        # dedup + already-True availability + confirmed pending = no events
        confirming = await fake.get_node_status("dev1", NODE_REF)
        recorder.changes.clear()
        await deliver_status(transport, confirming)
        await tick()
        assert recorder.changes == []
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


async def test_write_to_an_unknown_node_is_not_merged():
    """A write never creates a node: the node list is the only source.

    The POST still goes out (the caller asked for it), but there is nothing in
    the store to merge into and nothing to confirm, so a node the wire has not
    reported stays absent instead of appearing as a shell.
    """
    host = AcceptingHost()
    engine = SmartboxEngine(host)
    recorder = Recorder()
    engine.subscribe(recorder)
    try:
        await engine.set_mode("dev1", NodeRef("htr", "9"), "manual")
        assert engine.node("dev1", "htr/9") is None
        assert engine.device("dev1") is None
        assert recorder.changes == []
        assert armed_deadlines(engine, "write") == []
        assert host.posts == [("devs/dev1/htr/9/status", {"mode": "manual"})]
    finally:
        await engine.stop()


async def test_device_write_to_an_unknown_device_is_not_merged():
    engine, recorder, _fake, _transport = await make_engine()
    try:
        await engine.set_away("dev-late", ON)
        assert engine.device("dev-late") is None
        assert recorder.changes == []
    finally:
        await engine.stop()


@pytest.mark.parametrize(
    ("op", "args", "kwargs", "written"),
    [
        ("set_mode", ("manual",), {}, {"mode": "manual"}),
        ("set_lock", (True,), {}, {"locked": True}),
        ("set_boost", (True,), {}, {"boost": True}),
        (
            "engage_modified_auto",
            (19.5, "C"),
            {},
            {"stemp": "19.5", "units": "C", "mode": "modified_auto"},
        ),
        (
            "set_prog_temps",
            (),
            {"ice_temp": 5.0, "eco_temp": None, "comf_temp": None, "units": "C"},
            {"ice_temp": "5.0", "units": "C"},
        ),
    ],
)
async def test_status_ops_merge_written_keys(op, args, kwargs, written):
    engine, recorder, fake, transport = await make_engine()
    await establish_ok(recorder, transport)
    try:
        await getattr(engine, op)("dev1", NODE_REF, *args, **kwargs)
        node = engine.node("dev1", "htr/2")
        # only the written keys were merged over the stored status
        assert node.status == {**STATUS, **written}
        assert [(c.kind, c.node_key) for c in recorder.changes] == [("status", "htr/2")]
        assert (await fake.get_node_status("dev1", NODE_REF)) == node.status
    finally:
        await engine.stop()


# --- optimistic merge: setup / prog / device ops ---


async def test_setup_write_replaces_setup_slot_with_posted_object():
    engine, recorder, fake, _transport = await make_engine()
    try:
        await engine.set_window_mode("dev1", NODE_REF, ON)
        node = engine.node("dev1", "htr/2")
        # the COMPLETE posted object replaced the slot, merged over the store's
        # own setup body (a REST read still lags the write)
        assert node.setup == {"priority": 0, "window_mode_enabled": True}
        assert [(c.kind, c.node_key) for c in recorder.changes] == [("setup", "htr/2")]
        assert (
            "POST",
            "devs/dev1/htr/2/setup",
            {"priority": 0, "window_mode_enabled": True},
        ) in fake.requests
    finally:
        await engine.stop()


async def test_extra_options_merge_keeps_unknown_keys():
    host = AcceptingHost()
    engine, recorder, _fake, _transport = await make_host_engine(
        host, setup={"priority": 0, "extra_options": {"a": 1}}
    )
    try:
        await engine.set_extra_options("dev1", NODE_REF, {"b": 2})
        node = engine.node("dev1", "htr/2")
        assert node.setup == {
            "priority": 0,
            "extra_options": {"a": 1, "b": 2},
        }
        assert [(c.kind, c.node_key) for c in recorder.changes] == [("setup", "htr/2")]
        # the store's own object is the merge base, never the (lagging) read
        assert host.posts == [
            (
                "devs/dev1/htr/2/setup",
                {"priority": 0, "extra_options": {"a": 1, "b": 2}},
            )
        ]
    finally:
        await engine.stop()


async def test_prog_write_replaces_prog_slot_with_merged_object():
    engine, recorder, _fake, _transport = await make_engine()
    try:
        await engine.set_prog("dev1", NODE_REF, {"1": [5] * 24})
        node = engine.node("dev1", "htr/2")
        # day-level merge over the stored schedule, wrapped wire shape
        assert node.prog == {"prog": {"1": [5] * 24}}
        assert [(c.kind, c.node_key) for c in recorder.changes] == [("prog", "htr/2")]
    finally:
        await engine.stop()


async def test_prog_write_merges_over_current_schedule():
    engine, recorder, _fake, _transport = await make_engine(prog={"0": [2] * 24})
    try:
        # the op GETs the server's schedule, merges the caller's day over
        # it and posts the complete day-keyed object - the engine stores
        # exactly that posted object
        await engine.set_prog("dev1", NODE_REF, {"1": [5] * 24})
        node = engine.node("dev1", "htr/2")
        assert node.prog == {"prog": {"0": [2] * 24, "1": [5] * 24}}
        assert [(c.kind, c.node_key) for c in recorder.changes] == [("prog", "htr/2")]
    finally:
        await engine.stop()


async def test_device_writes_merge_and_arm_nothing():
    engine, recorder, _fake, _transport = await make_engine()
    try:
        await engine.set_away("dev1", ON)
        device = engine.device("dev1")
        # the app-shaped body merges into the stored away dict
        assert device.away == {"away": True, "enabled": True, "forced": False}
        assert [c.kind for c in recorder.changes] == ["away"]
        assert isinstance(recorder.changes[0], DeviceChange)
        await engine.set_power_limit("dev1", 2000)
        assert engine.device("dev1").power_limit == 2000
        assert [c.kind for c in recorder.changes] == ["away", "power_limit"]
        # device-level writes arm NO confirmation and touch no node
        # (availability stays True from the snapshot's ok status)
        assert armed_deadlines(engine, "write") == []
        await tick()
        assert engine.node("dev1", "htr/2").available is True
        assert recorder.changes[-1].kind == "power_limit"
    finally:
        await engine.stop()


# --- failed writes: no merge, no arming ---


@pytest.mark.parametrize(
    ("status", "error"),
    [(400, SmartboxError), (500, TransportUnavailableError)],
)
async def test_failed_write_no_optimistic_merge_no_pending(status, error):
    engine, recorder, fake, _transport = await make_engine()
    try:
        fake.fail_next(status)
        with pytest.raises(error):
            await engine.set_setpoint("dev1", NODE_REF, 21.3, "C")
        # nothing merged, nothing armed, nothing notified
        assert engine.node("dev1", "htr/2").status == STATUS
        assert recorder.changes == []
        assert armed_deadlines(engine, "write") == []
        await tick()
        assert recorder.changes == []
        # the failed write never touched availability (snapshot True)
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


class GatedSetupHost(AcceptingHost):
    """Host whose first POST parks until the test releases it."""

    def __init__(self):
        super().__init__()
        self.first_started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def post(self, path, body):
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            await self.release.wait()
        self.posts.append((path, dict(body)))


async def test_concurrent_setup_writes_do_not_drop_each_others_keys():
    """Two writes to one node are serialized, so neither key is lost.

    A setup write re-posts the WHOLE object, and the REST read it would merge
    over still shows the pre-write state for several seconds
    (docs/api-contract.md §5.4). Without serialization both bodies would be
    built from the same base and the later POST would post the earlier key back
    at its old value.
    """
    host = GatedSetupHost()
    engine, _recorder, _fake, _transport = await make_host_engine(host)
    try:
        first = asyncio.create_task(engine.set_window_mode("dev1", NODE_REF, ON))
        await host.first_started.wait()
        second = asyncio.create_task(engine.set_away_offset("dev1", NODE_REF, 2.0))
        await tick()
        # the second write waits for the first POST instead of interleaving
        assert host.calls == 1
        host.release.set()
        await asyncio.gather(first, second)
        assert host.posts == [
            ("devs/dev1/htr/2/setup", {"priority": 0, "window_mode_enabled": True}),
            (
                "devs/dev1/htr/2/setup",
                {"priority": 0, "window_mode_enabled": True, "away_offset": "2.0"},
            ),
        ]
        assert engine.node("dev1", "htr/2").setup == {
            "priority": 0,
            "window_mode_enabled": True,
            "away_offset": "2.0",
        }
    finally:
        await engine.stop()


class SlowPostHost(AcceptingHost):
    """Host whose POST takes longer than the confirmation window.

    Models the token-refresh half of the REST call: the engine arms before
    the POST, and the 6 s countdown must not start until the request lands.
    """

    def __init__(self, clock: FakeClock, delay: float = 10.0) -> None:
        super().__init__()
        self.clock = clock
        self.delay = delay

    async def post(self, path, body):
        self.clock.advance(self.delay)
        self.posts.append((path, dict(body)))


class SecondPostFailsHost(AcceptingHost):
    """Host whose SECOND POST fails after the first write already armed."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    async def post(self, path, body):
        self.calls += 1
        if self.calls == 2:
            msg = "smartbox API unavailable (POST status, status=503)"
            raise TransportUnavailableError(msg)
        self.posts.append((path, dict(body)))


async def test_a_slow_post_restarts_the_confirmation_countdown(engine_clock):
    """A POST slower than the window does not flip the node mid-write.

    The engine arms before the request so an echo already in flight is
    caught. The in-flight hold outlasts the client's 30 s call budget; the
    confirmation countdown starts when the POST returns.
    """
    host = SlowPostHost(engine_clock)
    engine, _recorder, _fake, _transport = await make_host_engine(host)
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        assert engine.node("dev1", "htr/2").available is True
        (deadline,) = engine.deadlines
        assert deadline.expires_in < 10.0  # refreshed, not the 36 s hold
        await tick()
        # the timed-out write is verified by GET; the fake applied it
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


async def test_full_boost_uses_the_boost_mode_endpoint():
    """boost_config 3 is the app's /boost_mode path, not the status flag."""
    host = AcceptingHost()
    engine, _recorder, _fake, transport = await make_host_engine(
        host, setup={"factory_options": {"boost_config": 3}}
    )
    try:
        await engine.set_boost(
            "dev1", NODE_REF, on=True, temperature=21.5, time_minutes=120
        )
        assert host.posts == [
            (
                "devs/dev1/htr/2/boost_mode",
                {"active": True, "mode": "temp_and_time", "temp": "21.5", "time": 120},
            )
        ]
        node = engine.node("dev1", "htr/2")
        assert node.status["boost"] is True
        assert node.status["boost_mode"] == "temp_and_time"
        assert armed_deadlines(engine, "write") == [
            ("dev1", "htr/2", "write", "status")
        ]
        # the pushed status body is the confirmation (boost + boost_mode)
        await transport.deliver_update(
            "dev1", "/htr/2/status", {"boost": True, "boost_mode": "temp_and_time"}
        )
        assert armed_deadlines(engine, "write") == []

        await engine.set_boost("dev1", NODE_REF, on=False)
        assert host.posts[-1] == (
            "devs/dev1/htr/2/boost_mode",
            {"active": False},
        )
        assert engine.node("dev1", "htr/2").status["boost"] is False
    finally:
        await engine.stop()


async def test_set_temp_and_time_boost_rewrites_the_setup_then_the_status():
    """boost_config 2 is the app's legacy setup+status pair."""
    host = AcceptingHost()
    engine, _recorder, _fake, _transport = await make_host_engine(
        host, setup={"factory_options": {"boost_config": 2}}
    )
    try:
        await engine.set_boost(
            "dev1", NODE_REF, on=True, temperature=21.0, time_minutes=60
        )
        assert [path for path, _body in host.posts] == [
            "devs/dev1/htr/2/setup",
            "devs/dev1/htr/2/status",
        ]
        assert host.posts[0][1]["extra_options"] == {
            "boost_temp": "21.0",
            "boost_time": 60,
        }
        assert host.posts[1][1] == {"boost": True}
    finally:
        await engine.stop()


async def test_value_equal_write_keeps_an_earlier_write_confirmation():
    """A no-op second write must not drop the first write's window.

    A write that changes nothing arms nothing (the wire pushes no frame for a
    no-op), so it never supersedes the earlier pending and the earlier still
    unconfirmed write keeps its Unavailable path.
    """
    engine, recorder, _fake, transport = await make_engine()
    await establish_ok(recorder, transport)
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        assert len(armed_deadlines(engine, "write")) == 1
        await engine.set_mode("dev1", NODE_REF, "manual")  # value-equal now
        assert armed_deadlines(engine, "write") == [
            ("dev1", "htr/2", "write", "status")
        ]
        recorder.changes.clear()
        await tick()
        # the restored window is verified by GET; the fake applied the write
        assert engine.node("dev1", "htr/2").available is True
        assert armed_deadlines(engine, "write") == []
    finally:
        await engine.stop()


async def test_failed_write_keeps_an_earlier_write_confirmation():
    """A failed second write must not drop the first write's window either."""
    host = SecondPostFailsHost()
    engine, _recorder, _fake, _transport = await make_host_engine(host)
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        assert len(armed_deadlines(engine, "write")) == 1
        with pytest.raises(TransportUnavailableError):
            await engine.set_lock("dev1", NODE_REF, ON)
        # the earlier write's window, restored
        assert armed_deadlines(engine, "write") == [
            ("dev1", "htr/2", "write", "status")
        ]
        await tick()
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


class BlockingPostHost(AcceptingHost):
    """Host whose POST parks until the test releases it."""

    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def post(self, path, body):
        self.started.set()
        await self.release.wait()
        self.posts.append((path, dict(body)))


async def test_write_completing_after_node_removal_does_not_resurrect_it():
    """A node removed mid-POST must not be rebuilt by the write's merge.

    The node list is authoritative (see ``_forget_node``): a write whose
    POST spans the removal must not re-add the node as a shell.
    """
    host = BlockingPostHost()
    engine = SmartboxEngine(host)
    recorder = Recorder()
    engine.subscribe(recorder)
    transport = FakeTransport()
    engine.supervise(transport)
    fake = FakeSmartbox()
    fake.add_device("dev1")
    fake.add_node("dev1", NODE_REF, status=dict(STATUS), setup={"priority": 0})
    await engine.handle_event(Snapshot(payload=fake.snapshot_payload()))
    try:
        task = asyncio.create_task(engine.set_mode("dev1", NODE_REF, "manual"))
        await host.started.wait()
        await transport.deliver_update("dev1", "/mgr/nodes", {"nodes": []})
        assert engine.node("dev1", "htr/2") is None
        recorder.changes.clear()
        host.release.set()
        await task
        assert engine.node("dev1", "htr/2") is None
        assert recorder.changes == []
    finally:
        await engine.stop()


class GatedGetHost(AcceptingHost):
    """Host whose setup GET runs a test callback before it answers."""

    def __init__(self):
        super().__init__()
        self.on_get = None

    async def get_node_setup(self, dev_id, node):  # noqa: ARG002 - seam shape
        if self.on_get is not None:
            await self.on_get()
        return {"priority": 0}


async def test_setup_write_whose_read_spans_a_node_removal_does_not_re_add_it():
    """A node dropped while the write reads its setup is not resurrected.

    The setup op reads the node's setup over REST when the store has no body
    for it, and the node list can drop the node while that read is in flight.
    The write still POSTs, but existence is re-checked at the POST and at the
    merge, so nothing arms and nothing is merged.
    """
    host = GatedGetHost()
    engine, recorder, _fake, transport = await make_host_engine(host, setup={})
    host.on_get = lambda: transport.deliver_update("dev1", "/mgr/nodes", {"nodes": []})
    try:
        await engine.set_window_mode("dev1", NODE_REF, ON)
        assert engine.node("dev1", "htr/2") is None
        # the node list going empty reports the membership change and the
        # removed node; the write arms nothing and merges nothing
        assert [type(change).__name__ for change in recorder.changes] == [
            "DeviceChange",
            "NodeRemoved",
        ]
        assert recorder.changes[0].kind == "nodes"
        assert armed_deadlines(engine, "write") == []
        assert host.posts == [
            ("devs/dev1/htr/2/setup", {"priority": 0, "window_mode_enabled": True})
        ]
    finally:
        await engine.stop()


async def test_setup_write_does_not_post_after_stop():
    """A write whose read spans stop() must not POST.

    Teardown can begin while the write is still being prepared (the op awaits
    its read), and a POST after that would land on a runtime nothing owns.
    """
    host = GatedGetHost()
    engine, _recorder, _fake, _transport = await make_host_engine(host, setup={})
    host.on_get = engine.stop
    with pytest.raises(RuntimeError, match="stopping"):
        await engine.set_window_mode("dev1", NODE_REF, ON)
    assert host.posts == []


async def test_a_frame_with_the_old_value_does_not_confirm_the_write():
    """Only a frame carrying the written value confirms the write.

    A status frame can already be in flight when the write arms, and it still
    carries the pre-write value. Treating its arrival as the confirmation would
    drop the write's Unavailable path even though the server never applied it -
    the store keeps the newest frame either way.
    """
    engine, recorder, _fake, transport = await make_engine()
    await establish_ok(recorder, transport)
    try:
        await engine.set_setpoint("dev1", NODE_REF, 21.3, "C")  # posts "21.0"
        assert len(armed_deadlines(engine, "write")) == 1
        # the pre-write status arrives: it merges (newest frame wins) but is
        # not the echo of this write
        await deliver_status(transport, STATUS)
        assert engine.node("dev1", "htr/2").status == STATUS
        assert len(armed_deadlines(engine, "write")) == 1
        # ...so the timeout verifies it with a GET; the fake applied it
        recorder.changes.clear()
        await tick()
        assert engine.node("dev1", "htr/2").available is True
        assert [c.kind for c in recorder.changes] == ["status"]
    finally:
        await engine.stop()


async def test_write_after_stop_is_refused():
    """A write issued during teardown is refused, arming nothing."""
    engine, recorder, _fake, _transport = await make_engine()
    await engine.stop()
    with pytest.raises(RuntimeError, match="stopping"):
        await engine.set_mode("dev1", NODE_REF, "manual")
    assert armed_deadlines(engine, "write") == []
    assert recorder.changes == []


async def test_in_flight_write_does_not_notify_after_stop():
    """A write that outlives stop() must not drive subscribers afterwards.

    HA unloads platforms before teardown and does not await in-flight service
    calls, so a POST can land after stop(): its optimistic merge must not
    notify entities that are already being removed.
    """
    host = BlockingPostHost()
    engine = SmartboxEngine(host)
    recorder = Recorder()
    engine.subscribe(recorder)
    transport = FakeTransport()
    engine.supervise(transport)
    fake = FakeSmartbox()
    fake.add_device("dev1")
    fake.add_node("dev1", NODE_REF, status=dict(STATUS), setup={"priority": 0})
    await engine.handle_event(Snapshot(payload=fake.snapshot_payload()))
    task = asyncio.create_task(engine.set_mode("dev1", NODE_REF, "manual"))
    try:
        await host.started.wait()
        recorder.changes.clear()
        await engine.stop()
        host.release.set()
        await task
        assert recorder.changes == []
    finally:
        await engine.stop()


# --- pending-write confirmation ---


async def test_write_arms_pending_and_ok_frame_confirms():
    engine, recorder, _fake, transport = await make_engine()
    await establish_ok(recorder, transport)
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        assert len(armed_deadlines(engine, "write")) == 1
        confirming = {**STATUS, "mode": "manual"}
        recorder.changes.clear()
        await deliver_status(transport, confirming)
        assert armed_deadlines(engine, "write") == []  # confirmed (pending cancelled)
        await tick()
        assert recorder.changes == []  # merged, available already True
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


async def test_write_confirm_timeout_verifies_with_a_status_get():
    engine, recorder, fake, _transport = await make_engine()
    await establish_ok(recorder, _transport)
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        recorder.changes.clear()
        await tick()  # no frame ever confirms -> one GET decides
        node = engine.node("dev1", "htr/2")
        assert node.available is True
        assert armed_deadlines(engine, "write") == []
        assert node.status["mode"] == "manual"
        assert not any(c.kind == "availability" for c in recorder.changes)
        assert fake.requests[-1][0] == "GET"
    finally:
        await engine.stop()


async def test_write_confirm_timeout_get_old_value_keeps_available(caplog):
    engine, recorder, fake, _transport = await make_engine()
    await establish_ok(recorder, _transport)

    async def old_status(_dev_id, _node):
        return dict(STATUS)

    fake.get_node_status = old_status
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        recorder.changes.clear()
        with caplog.at_level("WARNING"):
            await tick()
        node = engine.node("dev1", "htr/2")
        assert node.available is True
        assert node.status == STATUS  # the GET reverted the ignored write
        assert not any(c.kind == "availability" for c in recorder.changes)
        assert "posted={'mode': 'manual'}" in caplog.text
        assert "read=" in caplog.text
    finally:
        await engine.stop()


async def test_write_confirm_timeout_get_lost_marks_unavailable():
    engine, recorder, fake, _transport = await make_engine()
    await establish_ok(recorder, _transport)

    async def lost_status(_dev_id, _node):
        return dict(LOST)

    fake.get_node_status = lost_status
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        recorder.changes.clear()
        await tick()
        node = engine.node("dev1", "htr/2")
        assert node.available is False
        assert [c.kind for c in recorder.changes] == ["availability"]
    finally:
        await engine.stop()


async def test_write_confirm_timeout_get_failure_keeps_available():
    engine, recorder, fake, _transport = await make_engine()
    await establish_ok(recorder, _transport)

    async def failed_status(_dev_id, _node):
        msg = "network"
        raise TransportUnavailableError(msg)

    fake.get_node_status = failed_status
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        recorder.changes.clear()
        await tick()
        node = engine.node("dev1", "htr/2")
        assert node.available is True
        assert not any(c.kind == "availability" for c in recorder.changes)
    finally:
        await engine.stop()


async def test_prog_verification_rejects_malformed_get_data():
    engine, recorder, fake, _transport = await make_engine(prog={"0": [2] * 24})
    await establish_ok(recorder, _transport)

    async def malformed_prog(_dev_id, _node):
        return {"prog": "bad"}

    fake.get_node_prog = malformed_prog
    try:
        await engine.set_prog("dev1", NODE_REF, {"1": [5] * 24})
        recorder.changes.clear()
        await tick()
        node = engine.node("dev1", "htr/2")
        assert node.available is True
        assert node.prog == {"prog": {"0": [2] * 24, "1": [5] * 24}}
        assert not any(c.kind == "availability" for c in recorder.changes)
    finally:
        await engine.stop()


class GatedStatusHost(AcceptingHost):
    """Host whose status GET blocks until the test releases it."""

    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def get_node_status(self, dev_id, node):  # noqa: ARG002 - seam shape
        self.started.set()
        await self.release.wait()
        return dict(STATUS)


async def _gated_engine_with_node():
    host = GatedStatusHost()
    engine = SmartboxEngine(host)
    recorder = Recorder()
    engine.subscribe(recorder)
    transport = FakeTransport()
    engine.supervise(transport)
    fake = FakeSmartbox()
    fake.add_device("dev1")
    fake.add_node("dev1", NODE_REF, status=dict(STATUS), setup={"priority": 0})
    await engine.handle_event(Snapshot(payload=fake.snapshot_payload()))
    recorder.changes.clear()
    return engine, recorder, host, transport


async def test_verification_get_after_node_removal_does_not_re_add_it():
    engine, _recorder, host, transport = await _gated_engine_with_node()
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        await tick()
        await host.started.wait()
        await transport.deliver_update("dev1", "/mgr/nodes", {"nodes": []})
        assert engine.node("dev1", "htr/2") is None
        host.release.set()
        await tick()
        assert engine.node("dev1", "htr/2") is None
    finally:
        await engine.stop()


async def test_verification_get_does_not_overwrite_a_newer_frame():
    engine, _recorder, host, transport = await _gated_engine_with_node()
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        await tick()
        await host.started.wait()
        await transport.deliver_update(
            "dev1", "/htr/2/status", {**STATUS, "stemp": "21.0"}
        )
        host.release.set()
        await tick()
        node = engine.node("dev1", "htr/2")
        assert node.available is True
        assert node.status["stemp"] == "21.0"
    finally:
        await engine.stop()


async def test_lost_marked_frame_does_not_confirm_or_replace_the_body():
    engine, recorder, _fake, transport = await make_engine()
    await establish_ok(recorder, transport)
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        recorder.changes.clear()
        # a lost-marked payload NEVER confirms a write, and never becomes slot
        # data either: it carries the sync marker, not the slot's keys
        await deliver_status(transport, {**STATUS, "sync_status": "lost"})
        assert engine.node("dev1", "htr/2").status == {**STATUS, "mode": "manual"}
        # the lost window owns availability; the timeout GET still verifies
        await tick()
        node = engine.node("dev1", "htr/2")
        assert node.available is False
        assert [c.kind for c in recorder.changes] == ["availability"]
    finally:
        await engine.stop()


async def test_value_equal_write_is_silent_and_keeps_the_node_available():
    engine, recorder, _fake, transport = await make_engine()
    await establish_ok(recorder, transport)
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        confirming = {**STATUS, "mode": "manual"}
        await deliver_status(transport, confirming)
        recorder.changes.clear()
        # value-equal write: NO server frames, NO store change...
        await engine.set_mode("dev1", NODE_REF, "manual")
        assert recorder.changes == []
        # ...and no pending survives either: the wire pushes no frame for a
        # value-equal write, so a confirmation could only ever time out and
        # flip a reachable node Unavailable
        assert armed_deadlines(engine, "write") == []
        await tick()
        node = engine.node("dev1", "htr/2")
        assert node.available is True
        assert recorder.changes == []
    finally:
        await engine.stop()


# --- per-slot confirmation ---


async def test_setup_write_confirmed_by_setup_frame():
    engine, recorder, fake, transport = await make_engine()
    await establish_ok(recorder, transport)
    try:
        await engine.set_window_mode("dev1", NODE_REF, ON)
        assert len(armed_deadlines(engine, "write")) == 1
        recorder.changes.clear()
        # the /version frame that follows a setup write confirms NOTHING
        await transport.deliver_update(
            "dev1", "/htr/2/version", {"fw_version": "1.9", "hw_version": "1.1"}
        )
        assert len(armed_deadlines(engine, "write")) == 1
        recorder.changes.clear()
        # the fake's non-lost FULL-SETUP frame is the confirming signal
        confirming = await fake.get_node_setup("dev1", NODE_REF)
        await transport.deliver_update("dev1", "/htr/2/setup", dict(confirming))
        assert armed_deadlines(engine, "write") == []  # confirmed by the setup frame
        await tick()
        # no Unavailable at the timeout, no flip ever scheduled
        assert recorder.changes == []  # dedup absorbed the echo
        assert engine.node("dev1", "htr/2").available is True
        assert armed_deadlines(engine, "lost") == []
    finally:
        await engine.stop()


async def test_prog_write_confirmed_by_prog_frame():
    engine, recorder, _fake, transport = await make_engine(prog={"0": [2] * 24})
    await establish_ok(recorder, transport)
    try:
        await engine.set_prog("dev1", NODE_REF, {"1": [5] * 24})
        assert len(armed_deadlines(engine, "write")) == 1
        recorder.changes.clear()
        # a non-lost prog frame (the merged schedule) confirms the write
        await transport.deliver_update(
            "dev1", "/htr/2/prog", {"prog": {"0": [2] * 24, "1": [5] * 24}}
        )
        assert armed_deadlines(engine, "write") == []
        await tick()
        assert recorder.changes == []  # dedup absorbed; no flip scheduled
        assert engine.node("dev1", "htr/2").available is True
        assert armed_deadlines(engine, "lost") == []
    finally:
        await engine.stop()


async def test_stale_prog_frame_does_not_confirm_a_prog_write():
    """The whole-week echo proves only the days the write changed.

    A frame already in flight (or a schedule-tick frame) carries the old
    week. Confirming on the slot's arrival alone would retire the write's
    Unavailable path even though the schedule never landed.
    """
    engine, recorder, _fake, transport = await make_engine(prog={"0": [2] * 24})
    await establish_ok(recorder, transport)
    try:
        await engine.set_prog("dev1", NODE_REF, {"1": [5] * 24})
        assert armed_deadlines(engine, "write") == [("dev1", "htr/2", "write", "prog")]
        recorder.changes.clear()
        # the pre-write week: day 1 still carries its old profile
        await transport.deliver_update(
            "dev1", "/htr/2/prog", {"prog": {"0": [2] * 24, "1": [2] * 24}}
        )
        assert len(armed_deadlines(engine, "write")) == 1  # not confirmed
        await tick()
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


async def test_ok_status_frame_does_not_confirm_a_setup_write():
    """Per-slot: a setup write's echo is a full-setup body frame."""
    engine, recorder, _fake, transport = await make_engine()
    await establish_ok(recorder, transport)
    try:
        await engine.set_window_mode("dev1", NODE_REF, ON)
        assert armed_deadlines(engine, "write") == [("dev1", "htr/2", "write", "setup")]
        recorder.changes.clear()
        # an ok STATUS frame is the confirming signal for a STATUS write only
        await deliver_status(transport, {**STATUS, "mode": "heat"})
        assert len(armed_deadlines(engine, "write")) == 1
        await tick()
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


async def test_ok_setup_frame_does_not_confirm_a_status_write():
    """Per-slot: the reverse direction (setup frame vs status write)."""
    engine, recorder, _fake, transport = await make_engine()
    await establish_ok(recorder, transport)
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        assert armed_deadlines(engine, "write") == [
            ("dev1", "htr/2", "write", "status")
        ]
        recorder.changes.clear()
        await transport.deliver_update("dev1", "/htr/2/setup", {"priority": 0})
        assert len(armed_deadlines(engine, "write")) == 1
        await tick()
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


async def test_ok_status_frame_does_not_confirm_a_prog_write():
    """Per-slot: a prog write's echo is a prog frame, never a status one."""
    engine, recorder, _fake, transport = await make_engine(prog={"0": [2] * 24})
    await establish_ok(recorder, transport)
    try:
        await engine.set_prog("dev1", NODE_REF, {"1": [5] * 24})
        assert armed_deadlines(engine, "write") == [("dev1", "htr/2", "write", "prog")]
        recorder.changes.clear()
        await deliver_status(transport, {**STATUS, "mode": "heat"})
        assert len(armed_deadlines(engine, "write")) == 1
        await tick()
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


# --- confirmation armed at the POST ---


async def test_confirming_frame_during_the_post_confirms_the_write():
    """A frame arriving while the POST is in flight confirms that write.

    The confirmation is armed before the POST precisely for this ordering
    (server applies the write, box pushes, HTTP response last). Without the
    pre-arm the frame is orphaned and the timeout then flips a healthy node
    Unavailable.
    """
    engine, recorder, _fake, transport = await make_engine(host=FrameDuringPostHost)
    await establish_ok(recorder, transport)
    try:
        await engine.set_window_mode("dev1", NODE_REF, ON)
        assert armed_deadlines(engine, "write") == []  # confirmed while the POST ran
        await tick()
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


async def test_frame_during_the_post_wins_over_the_optimistic_merge():
    """A frame that changed the slot mid-POST is newer than the sent body.

    The whole-object setup write would otherwise put the pre-frame body back
    until the next frame arrived.
    """
    engine, recorder, _fake, transport = await make_engine(
        host=lambda fake, transport: FrameDuringPostHost(
            fake,
            transport,
            path="/htr/2/setup",
            body={"priority": 1, "window_mode_enabled": True},
        )
    )
    await establish_ok(recorder, transport)
    try:
        await engine.set_window_mode("dev1", NODE_REF, ON)
        node = engine.node("dev1", "htr/2")
        assert node.setup == {"priority": 1, "window_mode_enabled": True}
        assert node.available is True
    finally:
        await engine.stop()


async def test_away_frame_during_the_post_keeps_the_server_forced_flag():
    """The server owns ``forced``; neither the body nor the merge overwrites it."""
    engine, recorder, _fake, transport = await make_engine(
        host=lambda fake, transport: FrameDuringPostHost(
            fake,
            transport,
            path="/mgr/away_status",
            body={"away": True, "enabled": True, "forced": True},
        )
    )
    await establish_ok(recorder, transport)
    try:
        await engine.set_away("dev1", away=True)
        device = engine.device("dev1")
        assert device.away["away"] is True
        assert device.away["forced"] is True
    finally:
        await engine.stop()


async def test_lost_frame_during_the_post_arms_a_lost_window():
    """A lost frame landing mid-POST is write-correlated noise, as afterwards.

    The pre-arm also means the pending exists when that frame arrives, so
    the lost-window flip is suppressed and only the pending timeout resolves
    the write (no double flip, doctrine unchanged for mid-POST frames).
    """
    engine, recorder, _fake, transport = await make_engine(
        host=lambda fake, transport: FrameDuringPostHost(
            fake, transport, path="/htr/2/status", body=LOST
        )
    )
    await establish_ok(recorder, transport)
    try:
        await engine.set_window_mode("dev1", NODE_REF, ON)
        assert armed_deadlines(engine, "write") != []  # a lost frame confirms nothing
        assert len(armed_deadlines(engine, "lost")) == 1  # ...but arms its window
        await tick()
        assert armed_deadlines(engine, "write") == []
        # the lost window, not the write timeout, owns availability
        assert engine.node("dev1", "htr/2").available is False
    finally:
        await engine.stop()


class SlowGetHost(AcceptingHost):
    """Host whose setup GET blocks until the test releases it."""

    def __init__(self):
        super().__init__()
        self.get_started = asyncio.Event()
        self.get_release = asyncio.Event()

    async def get_node_setup(self, dev_id, node):  # noqa: ARG002 - seam shape
        self.get_started.set()
        await self.get_release.wait()
        return {"window_mode_enabled": False}


async def test_confirmation_window_opens_at_the_post_not_at_the_get():
    """The GET half of a GET-merge-POST op stays outside the window.

    Arming at the top of the op would put that GET - the slow half of the
    op - inside the confirmation window, so the watcher could fire before
    the POST went out and flip a healthy node Unavailable.
    """
    host = SlowGetHost()
    engine = SmartboxEngine(host)
    try:
        task = asyncio.create_task(engine.set_window_mode("dev1", NODE_REF, ON))
        await host.get_started.wait()
        assert armed_deadlines(engine, "write") == []  # GET in flight, nothing armed
        host.get_release.set()
        await task
        # the write went out through the POST seam (see the pre-arm test for
        # the window being live while the POST runs)
        assert host.posts == [("devs/dev1/htr/2/setup", {"window_mode_enabled": True})]
    finally:
        await engine.stop()


# --- seams ---


async def test_write_without_host_raises():
    engine = SmartboxEngine()
    with pytest.raises(RuntimeError, match="without a write host"):
        await engine.set_mode("dev1", NODE_REF, "manual")


async def test_a_stale_frame_carrying_the_key_does_not_confirm():
    """A frame with the write's key at its PRE-write value confirms nothing.

    Key presence alone is not evidence; the frame must carry the value the
    write set, or a stale frame already in flight would retire the timeout of
    a write the server has not applied.
    """
    engine, recorder, _fake, transport = await make_engine()
    await establish_ok(recorder, transport)
    try:
        await engine.set_lock("dev1", NODE_REF, LOCKED)
        assert armed_deadlines(engine, "write") == [
            ("dev1", "htr/2", "write", "status")
        ]
        recorder.changes.clear()

        # the frame was already in flight: it still carries locked=False
        await deliver_status(transport, {**STATUS, "locked": False})
        assert len(armed_deadlines(engine, "write")) == 1
        await tick()
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


async def test_node_power_limit_write_patches_the_setup_and_arms_nothing():
    """The node's own limit lives in the setup; its echo shape is unverified.

    The dedicated endpoint posts one key, so the optimistic merge patches the
    stored setup dict, and the write arms no confirmation window: a wrong slot
    guess would flip a healthy node Unavailable.
    """
    engine, recorder, _fake, transport = await make_engine(
        setup={"priority": 0, "power_limit": 1500}
    )
    await establish_ok(recorder, transport)
    try:
        await engine.set_node_power_limit("dev1", NODE_REF, 2000)
        node = engine.node("dev1", "htr/2")
        assert node.setup == {"priority": 0, "power_limit": 2000}
        assert [c.kind for c in recorder.changes] == ["setup"]
        assert armed_deadlines(engine, "write") == []  # no confirmation window
        await tick()
        assert node.available is True
    finally:
        await engine.stop()


async def test_node_power_limit_value_equal_write_is_a_no_op():
    engine, recorder, _fake, transport = await make_engine(
        setup={"priority": 0, "power_limit": 1500}
    )
    await establish_ok(recorder, transport)
    try:
        await engine.set_node_power_limit("dev1", NODE_REF, 1500)
        assert recorder.changes == []
    finally:
        await engine.stop()


async def test_prog_temps_are_clamped_like_the_vendor_app():
    """The engine applies the app's per-profile ceiling before the POST."""
    host = AcceptingHost()
    engine, _recorder, _fake, transport = await make_host_engine(
        host, setup={"max_stemp_limit": "20.0"}
    )
    await deliver_status(
        transport,
        {
            "ice_temp": "18.5",
            "eco_temp": "19.5",
            "comf_temp": "20.5",
            "units": "C",
            "stemp": "20.0",
            "sync_status": "ok",
        },
    )
    try:
        await engine.set_prog_temps(
            "dev1",
            NODE_REF,
            ice_temp=18.5,
            eco_temp=19.5,
            comf_temp=25.0,
            units="C",
            changed="comf_temp",
        )
        assert host.posts[-1] == (
            "devs/dev1/htr/2/status",
            {"ice_temp": "18.5", "eco_temp": "19.5", "comf_temp": "20.0", "units": "C"},
        )
    finally:
        await engine.stop()


async def test_prog_temp_changed_key_pushes_neighbours_down():
    """A lowered changed profile drags the lower neighbours with it."""
    host = AcceptingHost()
    engine, _recorder, _fake, transport = await make_host_engine(host)
    await deliver_status(
        transport,
        {
            "ice_temp": "18.5",
            "eco_temp": "19.5",
            "comf_temp": "20.5",
            "units": "C",
            "stemp": "20.0",
            "sync_status": "ok",
        },
    )
    try:
        await engine.set_prog_temps(
            "dev1",
            NODE_REF,
            ice_temp=18.5,
            eco_temp=19.5,
            comf_temp=15.0,
            units="C",
            changed="comf_temp",
        )
        assert host.posts[-1] == (
            "devs/dev1/htr/2/status",
            {"ice_temp": "14.0", "eco_temp": "14.5", "comf_temp": "15.0", "units": "C"},
        )
    finally:
        await engine.stop()


async def test_lowering_max_limit_posts_corrected_prog_temps():
    """The app follows a lowered ceiling with a corrected 4-key status body."""
    host = AcceptingHost()
    engine, _recorder, _fake, transport = await make_host_engine(
        host, setup={"max_stemp_limit": "24.0"}
    )
    await deliver_status(
        transport,
        {
            "ice_temp": "18.5",
            "eco_temp": "19.5",
            "comf_temp": "20.5",
            "units": "C",
            "stemp": "20.0",
            "sync_status": "ok",
        },
    )
    try:
        await engine.set_max_stemp_limit("dev1", NODE_REF, 20.0, "C")
        assert [path for path, _body in host.posts] == [
            "devs/dev1/htr/2/setup",
            "devs/dev1/htr/2/status",
        ]
        assert host.posts[-1][1] == {
            "ice_temp": "18.5",
            "eco_temp": "19.5",
            "comf_temp": "20.0",
            "units": "C",
        }
    finally:
        await engine.stop()


async def test_prog_temp_clamp_uses_the_fahrenheit_grid():
    """A Celsius ceiling converts to the F ceiling before clamping."""
    host = AcceptingHost()
    engine, _recorder, _fake, transport = await make_host_engine(
        host, setup={"max_stemp_limit": "24.0"}
    )
    await deliver_status(
        transport,
        {
            "ice_temp": "65.0",
            "eco_temp": "68.0",
            "comf_temp": "70.0",
            "units": "F",
            "stemp": "68.0",
            "sync_status": "ok",
        },
    )
    try:
        await engine.set_prog_temps(
            "dev1",
            NODE_REF,
            ice_temp=65.0,
            eco_temp=68.0,
            comf_temp=80.0,
            units="F",
            changed="comf_temp",
        )
        assert host.posts[-1][1] == {
            "ice_temp": "65.0",
            "eco_temp": "68.0",
            "comf_temp": "75.0",
            "units": "F",
        }
    finally:
        await engine.stop()
