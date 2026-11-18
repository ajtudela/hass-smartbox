"""Core engine availability windows, pending confirmation, supervision.

All timing runs through the shared timing seam: a FakeClock plus an
AdvanceSleep replacement for ``timing.sleep`` that records the delay and
advances the fake clock - deterministic, no real waits, no lingering timer
tasks.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from smartbox.core import engine as engine_mod
from smartbox.core.engine import SmartboxEngine
from smartbox.core.feed import FEED_GRACE_SECONDS
from smartbox.core.state import FeedChange, NodeChange
from smartbox.protocol import timing
from smartbox.protocol.client import NodeRef
from smartbox.protocol.errors import (
    AuthError,
    TransportUnavailableError,
    TransportUnsupportedError,
)
from smartbox.protocol.events import Snapshot, SocketClosed

from tests.fake_api import FakeSmartbox
from tests.fake_transport import FakeTransport
from tests.test_utils import armed_deadlines

NODE_REF = NodeRef("htr", "2")
ON = True  # FBT003-friendly bool literal for write calls
STATUS = {"stemp": "20.0", "mtemp": "19.6", "sync_status": "ok"}
LOST = {"sync_status": "lost"}
LOST_SETUP = {"sync_status": "lost", "priority": 0}


class FakeClock:
    """Injectable monotonic clock the tests control."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, delta):
        self.now += delta


class AdvanceSleep:
    """``timing.sleep`` replacement: records the delay, advances the clock."""

    def __init__(self, clock):
        self.clock = clock
        self.delays = []

    async def __call__(self, delay=0):
        self.delays.append(delay)
        self.clock.advance(delay)


class GatedSleep:
    """``timing.sleep`` replacement that suspends each call until released.

    ``AdvanceSleep`` completes every call as soon as the task runs, so it
    cannot express "this watcher's deadline elapses after that re-arm".
    The gates are FIFO: ``release(0)`` finishes the first sleep call.
    """

    def __init__(self, clock):
        self.clock = clock
        self.gates = []

    async def __call__(self, delay=0):
        self.clock.advance(delay)
        gate = asyncio.get_running_loop().create_future()
        self.gates.append(gate)
        await gate

    def release(self, index):
        """Let the index-th sleep call finish (no-op when it was cancelled).

        Cancelling a task cancels the future it is awaiting, so a superseded
        watcher's gate is already cancelled by the time a test releases it.
        """
        gate = self.gates[index]
        if not gate.done():
            gate.set_result(None)


class Recorder:
    def __init__(self):
        self.changes = []

    async def __call__(self, change):
        self.changes.append(change)


@pytest.fixture(autouse=True)
async def seams(monkeypatch):
    """Patch the shared timing seam; hand tests the clock + recorded delays.

    The engine's node timer and the feed monitor's grace window both sleep
    through the one seam, so a single advance-sleep resolves either, and
    ``timing.monotonic`` is the same fake clock: a deadline armed at ``now``
    is due exactly when the sleep has advanced the clock past it.
    """
    clock = FakeClock()
    sleeper = AdvanceSleep(clock)
    monkeypatch.setattr(timing, "sleep", sleeper)
    monkeypatch.setattr(timing, "monotonic", clock)
    monkeypatch.setattr(timing, "sleep", sleeper)
    return SimpleNamespace(clock=clock, sleep=sleeper)


async def tick():
    """Let pending engine timer tasks run to completion."""
    for _ in range(3):
        await asyncio.sleep(0)


async def make_engine(**engine_kwargs):
    """Wire engine + fake + transport and ingest the fake's snapshot."""
    fake = FakeSmartbox()
    fake.add_device("dev1")
    fake.add_node("dev1", NODE_REF, status=dict(STATUS), setup={"priority": 0})
    engine = SmartboxEngine(fake, **engine_kwargs)
    recorder = Recorder()
    engine.subscribe(recorder)
    transport = FakeTransport()
    engine.supervise(transport)
    await engine.handle_event(Snapshot(payload=fake.snapshot_payload()))
    recorder.changes.clear()
    return engine, recorder, transport


async def deliver_status(transport, body, path="/htr/2/status", dev_id="dev1"):
    await transport.deliver_update(dev_id, path, dict(body))


def node_entry(node_type="htr", addr="2", **slots):
    return {"type": node_type, "addr": addr, **slots}


def make_snapshot(node_entries, dev_id="dev1"):
    """Homes -> devs -> devData nesting for direct Snapshot payloads."""
    return [
        {
            "id": "h1",
            "devs": [
                {
                    "dev_id": dev_id,
                    "devData": {"connected": True, "nodes": list(node_entries)},
                }
            ],
        }
    ]


# --- availability windows: lost/ok ---


async def test_lost_frame_alone_flips_after_delay():
    engine, recorder, transport = await make_engine()
    try:
        before = engine.node("dev1", "htr/2")
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        # the snapshot marked the node available; the lost window is running
        assert engine.node("dev1", "htr/2").available is True
        # a lost marker is availability evidence, not slot data: the stored
        # body and its revision are untouched
        assert engine.node("dev1", "htr/2").status == STATUS
        assert engine.node("dev1", "htr/2").revision == before.revision
        await tick()
        node = engine.node("dev1", "htr/2")
        assert node.available is False
        # only the derived availability flip is reported
        assert [(c.kind, c.node_key) for c in recorder.changes] == [
            ("availability", "htr/2"),
        ]
        change = recorder.changes[0]
        assert isinstance(change, NodeChange)
        assert change.node is node  # the NEW state with available=False
        assert change.node.revision == node.revision  # no revision bump
    finally:
        await engine.stop()


async def test_lost_and_recovery_transitions_logged(caplog):
    """Lost flips + recoveries carry one DEBUG line each (why + which frame)."""
    caplog.set_level(logging.DEBUG, logger=engine_mod.__name__)
    engine, _recorder, transport = await make_engine()
    try:
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        await tick()
        flips = [
            rec.getMessage()
            for rec in caplog.records
            if "marked False" in rec.getMessage()
        ]
        assert flips == ["Node 'dev1'/'htr/2' marked False (6.0s lost window elapsed)"]
        caplog.clear()
        await transport.deliver_update("dev1", "/htr/2/status", dict(STATUS))
        await tick()
        recoveries = [
            rec.getMessage()
            for rec in caplog.records
            if "marked True" in rec.getMessage()
        ]
        assert recoveries == ["Node 'dev1'/'htr/2' marked True (non-lost status frame)"]
    finally:
        await engine.stop()


async def test_lost_then_ok_within_window_no_flip():
    engine, recorder, transport = await make_engine()
    try:
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        # ok frame during the window supersedes the scheduled flip
        await transport.deliver_update("dev1", "/htr/2/status", dict(STATUS))
        await tick()
        node = engine.node("dev1", "htr/2")
        assert node.available is True
        assert node.status == STATUS
        # nothing to report: the lost marker was never stored, so the ok body
        # equals the stored one and the node never left the available state
        assert recorder.changes == []
    finally:
        await engine.stop()


async def test_lost_marked_full_payload_schedules_flip_too():
    engine, recorder, transport = await make_engine()
    try:
        # full-field but lost-marked: the marker makes the whole body suspect,
        # so it is availability evidence and never slot data
        await transport.deliver_update(
            "dev1", "/htr/2/status", {**STATUS, "sync_status": "lost"}
        )
        await tick()
        assert engine.node("dev1", "htr/2").available is False
        assert engine.node("dev1", "htr/2").status == STATUS
        assert [c.kind for c in recorder.changes] == ["availability"]
    finally:
        await engine.stop()


async def test_repeated_lost_frames_do_not_extend_the_window():
    """One window per episode: repeats neither extend nor re-arm it.

    The guard is an in-flight check rather than a dict-size one, so the
    assertions have to survive the window being cancelled: the ok frame
    cancels the ONE armed watcher, and no second watcher may still be
    pending (an orphan would flip the recovered node).
    """
    engine, recorder, transport = await make_engine()
    try:
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        assert (
            len(armed_deadlines(engine, "lost")) == 1
        )  # one window, never rescheduled
        # an ok frame inside the window supersedes that single flip
        await deliver_status(transport, STATUS)
        assert armed_deadlines(engine, "lost") == []
        recorder.changes.clear()
        await tick()
        assert engine.node("dev1", "htr/2").available is True
        assert recorder.changes == []
    finally:
        await engine.stop()


async def test_idle_bare_lost_frame_handled_like_any_lost_frame():
    engine, _recorder, transport = await make_engine()
    try:
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        await tick()
        assert engine.node("dev1", "htr/2").available is False
        # the bare marker never becomes the stored status
        assert engine.node("dev1", "htr/2").status == STATUS
    finally:
        await engine.stop()


# --- pending-write confirmation ---


async def test_ok_frame_confirms_write_and_stays_available():
    engine, recorder, transport = await make_engine()
    try:
        # recover availability first: one ok frame
        await transport.deliver_update("dev1", "/htr/2/status", dict(STATUS))
        recorder.changes.clear()
        await engine.set_mode("dev1", NODE_REF, "manual")
        recorder.changes.clear()
        await engine.set_mode("dev1", NODE_REF, "manual")
        recorder.changes.clear()
        # the confirming ok frame is the FULL post-write status (equal to
        # the optimistic merge) -> dedup absorbs it, pending cancelled
        await transport.deliver_update(
            "dev1", "/htr/2/status", {**STATUS, "mode": "manual"}
        )
        await tick()
        assert armed_deadlines(engine, "write") == []  # cancelled by the ok frame
        assert engine.node("dev1", "htr/2").available is True
        assert recorder.changes == []
    finally:
        await engine.stop()


async def test_confirming_frame_cancels_a_co_armed_lost_window():
    """The confirming frame clears both deadlines and leaves no live timer."""
    engine, recorder, transport = await make_engine()
    try:
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        assert armed_deadlines(engine, "lost")  # armed before the write
        await engine.set_mode("dev1", NODE_REF, "manual")
        assert armed_deadlines(engine, "write")
        recorder.changes.clear()
        await transport.deliver_update(
            "dev1", "/htr/2/status", {**STATUS, "mode": "manual"}
        )
        await tick()
        assert armed_deadlines(engine) == []
        assert engine.timer_armed is False
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


async def test_stop_makes_late_events_inert():
    """A frame or close after stop() must not re-arm deadlines or the feed."""
    engine, _recorder, transport = await make_engine()
    await transport.deliver_update("dev1", "/htr/2/status", dict(STATUS))
    await engine.stop()
    await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
    await engine.handle_event(SocketClosed(code=1006))
    assert engine.deadlines == ()
    assert engine.timer_armed is False
    assert engine.feed.window_pending is False


async def test_lost_marked_frame_does_not_cancel_pending():
    engine, recorder, transport = await make_engine()
    try:
        await transport.deliver_update("dev1", "/htr/2/status", dict(STATUS))
        recorder.changes.clear()
        await engine.set_mode("dev1", NODE_REF, "manual")
        recorder.changes.clear()
        # lost-marked frames NEVER confirm a write: the pending survives
        await transport.deliver_update(
            "dev1", "/htr/2/status", {**STATUS, "sync_status": "lost"}
        )
        await tick()
        node = engine.node("dev1", "htr/2")
        assert node.available is False  # the lost window owns availability
        assert node.status == {**STATUS, "mode": "manual"}
        assert [c.kind for c in recorder.changes] == ["availability"]
        # ...and a later ok frame recovers
        recorder.changes.clear()
        await transport.deliver_update("dev1", "/htr/2/status", dict(STATUS))
        await tick()
        assert engine.node("dev1", "htr/2").available is True
        assert [c.kind for c in recorder.changes] == ["status", "availability"]
    finally:
        await engine.stop()


async def test_rearm_on_second_write_single_pending():
    engine, recorder, _transport = await make_engine()
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        await engine.set_lock("dev1", NODE_REF, ON)
        assert len(armed_deadlines(engine, "write")) == 1  # refreshed, never doubled
        await tick()
        flips = [c for c in recorder.changes if c.kind == "availability"]
        assert flips == []
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


# --- pending suppression of lost flips ---


async def test_transient_lost_frames_during_pending_arm_a_window():
    engine, recorder, transport = await make_engine()
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        assert len(armed_deadlines(engine, "write")) == 1
        # post-write transient noise: lost status + lost prog frames
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        await transport.deliver_update(
            "dev1", "/htr/2/prog", {"prog": {}, "sync_status": "lost"}
        )
        assert len(armed_deadlines(engine, "lost")) == 1  # the window owns availability
        recorder.changes.clear()
        # the ok status frame confirms the write and cancels the window
        await transport.deliver_update(
            "dev1", "/htr/2/status", {**STATUS, "mode": "manual"}
        )
        await tick()
        assert armed_deadlines(engine, "write") == []
        assert armed_deadlines(engine, "lost") == []
        # the lost frames changed no slot, and the confirming ok body equals
        # what the optimistic merge already stored: the whole sequence is silent
        assert recorder.changes == []
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


async def test_setup_write_pending_lost_window_is_cancelled_by_confirmation():
    engine, recorder, transport = await make_engine()
    try:
        await engine.set_window_mode("dev1", NODE_REF, ON)
        assert len(armed_deadlines(engine, "write")) == 1
        # a lost frame while the setup write is pending arms the lost window
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        assert len(armed_deadlines(engine, "lost")) == 1
        recorder.changes.clear()
        # no ok STATUS frame ever comes - the full-setup frame confirms
        await transport.deliver_update(
            "dev1", "/htr/2/setup", {"priority": 0, "window_mode_enabled": ON}
        )
        await tick()
        assert armed_deadlines(engine, "write") == []
        assert armed_deadlines(engine, "lost") == []
        # the confirming setup frame cancels the lost window; no availability event
        assert [c.kind for c in recorder.changes] == []
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


async def test_lost_frame_while_pending_does_not_prevent_timeout():
    engine, recorder, transport = await make_engine()
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        assert len(armed_deadlines(engine, "lost")) == 1
        await tick()
        # the lost window, not the write timeout, owns availability
        node = engine.node("dev1", "htr/2")
        assert node.available is False
        flips = [c for c in recorder.changes if c.kind == "availability"]
        assert len(flips) == 1
    finally:
        await engine.stop()


async def test_knobs_are_shrinkable(seams):
    engine, _recorder, transport = await make_engine(
        unavailable_delay=0.25, write_confirm_timeout=0.5
    )
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        await tick()
        assert seams.sleep.delays == [0.5, 1.0]  # confirm timeout + settle
        assert engine.node("dev1", "htr/2").available is True
        # the lost window uses its own knob
        recorder = Recorder()
        engine.subscribe(recorder)
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        await tick()
        assert seams.sleep.delays == [0.5, 1.0, 0.25]
        assert engine.node("dev1", "htr/2").available is False
        assert [c.kind for c in recorder.changes] == ["availability"]
    finally:
        await engine.stop()


# --- snapshot availability (design change: snapshots derive availability) ---


async def test_snapshot_ok_status_recovers_unavailable_node():
    """A node unavailable before a disconnect recovers from the snapshot."""
    engine, recorder, transport = await make_engine()
    try:
        # mark unavailable via the frame path
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        await tick()
        assert engine.node("dev1", "htr/2").available is False
        recorder.changes.clear()
        # the reconnect snapshot carries a non-lost status slot: recovers
        await engine.handle_event(
            Snapshot(
                payload=make_snapshot(
                    [node_entry(status=dict(STATUS), setup={"priority": 0})]
                )
            )
        )
        node = engine.node("dev1", "htr/2")
        assert node.available is True
        # the lost marker never replaced the stored body, so the snapshot's ok
        # status is value-equal: only the recovery is reported
        assert node.status == STATUS
        assert [(c.kind, c.node_key) for c in recorder.changes] == [
            ("availability", "htr/2"),
        ]
    finally:
        await engine.stop()


async def test_snapshot_ok_status_cancels_armed_lost_flip():
    """Lost frame schedules the flip, reconnect snapshot's ok status cancels it."""
    engine, recorder, transport = await make_engine()
    try:
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        assert armed_deadlines(engine, "lost")  # lost-flip armed
        recorder.changes.clear()
        await engine.handle_event(
            Snapshot(payload=make_snapshot([node_entry(status=dict(STATUS))]))
        )
        assert armed_deadlines(engine, "lost") == []  # the snapshot cancelled it
        await tick()
        assert engine.node("dev1", "htr/2").available is True
        # nothing to report: the lost marker was never stored, so the ok
        # snapshot status equals the stored body and no blip happened
        assert recorder.changes == []
    finally:
        await engine.stop()


async def test_snapshot_lost_status_marks_unavailable():
    engine, recorder, _transport = await make_engine()
    try:
        await engine.handle_event(
            Snapshot(
                payload=make_snapshot(
                    [node_entry(status={"sync_status": "lost"}, setup={"priority": 0})]
                )
            )
        )
        node = engine.node("dev1", "htr/2")
        assert node.available is False
        assert ("availability", "htr/2") in [
            (c.kind, c.node_key) for c in recorder.changes
        ]
    finally:
        await engine.stop()


async def test_snapshot_bare_lost_status_marks_unavailable():
    engine, _recorder, _transport = await make_engine()
    try:
        await engine.handle_event(
            Snapshot(payload=make_snapshot([node_entry(status=dict(LOST))]))
        )
        assert engine.node("dev1", "htr/2").available is False
    finally:
        await engine.stop()


async def test_snapshot_without_status_uses_setup_as_evidence():
    """Setup is reachability evidence too; no slot at all leaves None."""
    engine, recorder, _transport = await make_engine()
    try:
        # first: a snapshot WITH status -> available
        await engine.handle_event(
            Snapshot(payload=make_snapshot([node_entry(status=dict(STATUS))]))
        )
        assert engine.node("dev1", "htr/2").available is True
        # a fresh dev whose node entry carries a non-lost setup but no status
        # slot is still reachable (§6.3: an offline node's setup carries the
        # lost marker, so a non-lost setup is ok evidence).
        recorder.changes.clear()
        await engine.handle_event(
            Snapshot(payload=make_snapshot([node_entry(setup={"priority": 0})]))
        )
        node = engine.node("dev1", "htr/2")
        assert node.available is True  # not cleared by the absent status
        assert not any(c.kind == "availability" for c in recorder.changes)
        # a node with no availability signal ever: stays None
        await engine.handle_event(Snapshot(payload=make_snapshot([node_entry(addr=7)])))
        assert engine.node("dev1", "htr/7").available is None
    finally:
        await engine.stop()


async def test_snapshot_lost_setup_marks_an_ok_status_unavailable():
    """A lost setup slot wins over a stale ok status.

    The node's status GET can keep serving the last ok body for minutes
    after the node is gone, while /setup already carries the lost marker.
    """
    engine, _recorder, _transport = await make_engine()
    try:
        await engine.handle_event(
            Snapshot(
                payload=make_snapshot(
                    [
                        node_entry(
                            status=dict(STATUS),
                            setup={"sync_status": "lost", "priority": 0},
                        )
                    ]
                )
            )
        )
        assert engine.node("dev1", "htr/2").available is False
    finally:
        await engine.stop()


async def test_snapshot_lost_setup_arms_a_lost_window_while_a_write_is_pending():
    """A lost setup marker mid-write arms the normal lost window."""
    engine, recorder, _transport = await make_engine()
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        recorder.changes.clear()
        await engine.handle_event(
            Snapshot(
                payload=make_snapshot(
                    [
                        node_entry(
                            status=dict(STATUS),
                            setup={"sync_status": "lost", "priority": 0},
                        )
                    ]
                )
            )
        )
        assert engine.node("dev1", "htr/2").available is True
        assert not any(c.kind == "availability" for c in recorder.changes)
    finally:
        await engine.stop()


async def test_snapshot_lost_status_arms_a_lost_window_while_a_write_is_pending():
    """A snapshot lost marker mid-write arms the normal lost window.

    The snapshot is a full account view, so its lost marker is availability
    evidence; the write timeout separately verifies the write with a GET.
    """
    engine, recorder, _transport = await make_engine()
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        assert len(armed_deadlines(engine, "write")) == 1
        recorder.changes.clear()
        await engine.handle_event(
            Snapshot(payload=make_snapshot([node_entry(status=dict(LOST))]))
        )
        assert engine.node("dev1", "htr/2").available is True
        assert not any(c.kind == "availability" for c in recorder.changes)
        # the lost window owns availability; the write itself is verified
        assert len(armed_deadlines(engine, "lost")) == 1
        await tick()
        assert engine.node("dev1", "htr/2").available is False
    finally:
        await engine.stop()


async def test_snapshot_lost_status_with_confirming_setup_arms_a_window():
    """A lost status plus a confirming setup keeps the grace window."""
    engine, recorder, _transport = await make_engine()
    try:
        await engine.set_window_mode("dev1", NODE_REF, ON)
        recorder.changes.clear()
        await engine.handle_event(
            Snapshot(
                payload=make_snapshot(
                    [
                        node_entry(
                            status=dict(LOST),
                            setup={"priority": 0, "window_mode_enabled": ON},
                        )
                    ]
                )
            )
        )
        assert engine.node("dev1", "htr/2").available is True
        assert len(armed_deadlines(engine, "lost")) == 1
        await tick()
        assert engine.node("dev1", "htr/2").available is False
    finally:
        await engine.stop()


async def test_snapshot_confirms_a_pending_write():
    """A reconnect snapshot carrying the written slot confirms the write.

    The frame path confirms a pending write on a non-lost body for the
    written slot; the snapshot is the reconnect's version of that frame, so
    it must confirm too - otherwise the pending times out and flips a node
    that just reported itself alive.
    """
    engine, recorder, _transport = await make_engine()
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        assert len(armed_deadlines(engine, "write")) == 1
        recorder.changes.clear()
        await engine.handle_event(
            Snapshot(
                payload=make_snapshot(
                    [
                        node_entry(
                            status={**STATUS, "mode": "manual"},
                            setup={"priority": 0},
                        )
                    ]
                )
            )
        )
        assert armed_deadlines(engine, "write") == []  # the snapshot confirmed it
        await tick()  # a surviving pending would time out and flip here
        assert engine.node("dev1", "htr/2").available is True
        assert not any(c.kind == "availability" for c in recorder.changes)
    finally:
        await engine.stop()


async def test_snapshot_without_the_written_slot_does_not_confirm():
    """Only the pending's own slot confirms - the snapshot included."""
    engine, _recorder, _transport = await make_engine()
    try:
        await engine.set_mode("dev1", NODE_REF, "manual")
        # a snapshot carrying only the setup slot confirms a status write: no
        await engine.handle_event(
            Snapshot(payload=make_snapshot([node_entry(setup={"priority": 0})]))
        )
        assert len(armed_deadlines(engine, "write")) == 1
        await tick()
        assert engine.node("dev1", "htr/2").available is True
    finally:
        await engine.stop()


async def test_removed_node_takes_its_timers_with_it():
    """A node the box stops reporting must not be rebuilt by its own timers."""
    engine, recorder, transport = await make_engine()
    try:
        await deliver_status(transport, LOST)  # arms the lost-window flip
        await engine.set_mode("dev1", NODE_REF, "manual")  # arms a confirmation
        assert len(armed_deadlines(engine, "lost")) == 1
        assert len(armed_deadlines(engine, "write")) == 1
        # the node list is authoritative: the node and its timers go away
        await transport.deliver_update("dev1", "/mgr/nodes", {"nodes": []})
        assert engine.node("dev1", "htr/2") is None
        assert armed_deadlines(engine, "lost") == []
        assert armed_deadlines(engine, "write") == []
        recorder.changes.clear()
        await tick()  # a surviving timer would rebuild the node here
        assert engine.node("dev1", "htr/2") is None
        assert recorder.changes == []
    finally:
        await engine.stop()


async def test_terminal_transport_exit_marks_the_feed_down():
    """A dead transport means stale values, so the feed must report down.

    Nothing will push frames after a terminal exit, and the adapter's
    availability gate trusts the feed: leaving it up would render every
    entity's last value as available with no data behind it.
    """
    engine = SmartboxEngine()
    recorder = Recorder()
    engine.subscribe(recorder)
    try:
        transport = ScriptedTransport([AuthError("revoked")])
        engine.supervise(transport)
        await tick()

        assert engine.feed.available is False
        assert engine.feed.facts["reason"] == "transport stopped (AuthError)"
        assert engine.feed.facts["outages"] == 1
        assert any(
            isinstance(change, FeedChange) and change.available is False
            for change in recorder.changes
        )
    finally:
        await engine.stop()


# --- slot table + re-arm window ---


async def test_lost_setup_frame_schedules_the_flip():
    """A lost-marked SETUP frame is an availability signal too.

    ``_AVAIL_LOST_SLOTS`` covers status and setup: a node whose box pushes
    lost-marked setup frames only must still go Unavailable.
    """
    engine, recorder, transport = await make_engine()
    try:
        await transport.deliver_update("dev1", "/htr/2/setup", dict(LOST))
        assert len(armed_deadlines(engine, "lost")) == 1
        await tick()
        assert engine.node("dev1", "htr/2").available is False
        # the setup slot keeps the body the snapshot delivered
        assert engine.node("dev1", "htr/2").setup == {"priority": 0}
        assert [c.kind for c in recorder.changes] == ["availability"]
    finally:
        await engine.stop()


async def test_ok_setup_frame_recovers_an_unavailable_node():
    """An ok setup frame is reachability evidence, like an ok status one."""
    engine, _recorder, transport = await make_engine()
    try:
        await transport.deliver_update("dev1", "/htr/2/setup", LOST_SETUP)
        await tick()
        assert engine.node("dev1", "htr/2").available is False

        await transport.deliver_update("dev1", "/htr/2/setup", {"priority": 1})
        await tick()
        assert engine.node("dev1", "htr/2").available is True
        assert engine.node("dev1", "htr/2").setup == {"priority": 1}
    finally:
        await engine.stop()


async def test_ok_setup_frame_cancels_an_armed_lost_window():
    """The ok frame lands inside the window: the flip never fires."""
    engine, recorder, transport = await make_engine()
    try:
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        await transport.deliver_update("dev1", "/htr/2/setup", {"priority": 1})
        await tick()
        assert engine.node("dev1", "htr/2").available is True
        assert [c.kind for c in recorder.changes] == ["setup"]
    finally:
        await engine.stop()


async def test_feed_stays_up_when_a_frame_arrives_inside_the_window():
    """A close that is answered within the grace window is not an outage."""
    engine, recorder, transport = await make_engine()
    try:
        await transport.deliver(SocketClosed(code=1006))
        assert engine.feed.window_pending is True
        # the reconnect delivered data (a changed value, so the store keeps it)
        await deliver_status(transport, {**STATUS, "mtemp": "19.1"})
        await tick()
        assert engine.feed.available is True
        assert engine.feed.window_pending is False
        assert [c.kind for c in recorder.changes] == ["status"]
        assert engine.feed.facts["outages"] == 0
    finally:
        await engine.stop()


async def test_feed_flips_down_after_the_window_and_recovers_once():
    """Silence past the window flips the feed down; a frame reports recovery."""
    engine, recorder, transport = await make_engine()
    try:
        await transport.deliver(SocketClosed(code=1006))
        recorder.changes.clear()
        await tick()  # the window expires
        assert engine.feed.available is False
        feed_down = [c for c in recorder.changes if isinstance(c, FeedChange)]
        assert len(feed_down) == 1
        assert feed_down[0].available is False
        assert "code=1006" in feed_down[0].reason
        assert engine.feed.facts["outages"] == 1
        assert engine.feed.facts["down_s"] is not None

        recorder.changes.clear()
        await deliver_status(transport, STATUS)
        recovered = [c for c in recorder.changes if isinstance(c, FeedChange)]
        assert [c.available for c in recovered] == [True]
        assert recovered[0].outage_s is not None
        assert engine.feed.available is True
        assert engine.feed.facts["reason"] is None
        assert engine.feed.facts["last_outage_s"] is not None
    finally:
        await engine.stop()


async def test_expected_token_expiry_cut_is_not_an_outage():
    """The vendor's scheduled cut is not an outage: the reconnect clears it.

    The cut arms the same grace window as any other close, so a frame from the
    fresh-token reconnect cancels it and nothing is reported.
    """
    engine, recorder, transport = await make_engine()
    try:
        await engine.handle_event(SocketClosed(code=1006, expected=True))
        assert engine.feed.window_pending is True  # the cut armed the window
        assert engine.feed.facts["last_close_expected"] is True
        await deliver_status(transport, STATUS)  # the reconnect's first frame
        await tick()
        assert engine.feed.available is True
        assert engine.feed.facts["outages"] == 0
        assert [c for c in recorder.changes if isinstance(c, FeedChange)] == []
    finally:
        await engine.stop()


async def test_failed_reconnect_after_the_token_cut_reports_the_outage():
    """A scheduled cut whose reconnect never lands still becomes an outage.

    The 4-hourly refresh can fail exactly at the cut (token endpoint 5xx, the
    network gone). With the window armed by the close, the silence past the
    grace window flips the feed, so entities stop rendering frozen values as
    available; skipping the window for the scheduled cut would hide that.
    """
    engine, recorder, _transport = await make_engine()
    try:
        await engine.handle_event(SocketClosed(code=1006, expected=True))
        recorder.changes.clear()
        await tick()  # the grace window elapses with no frame
        assert engine.feed.available is False
        assert engine.feed.facts["outages"] == 1
        assert [c.available for c in recorder.changes if isinstance(c, FeedChange)] == [
            False
        ]
    finally:
        await engine.stop()


async def test_feed_window_is_not_restarted_by_repeated_closes(seams, monkeypatch):
    """One window per outage: a second close must not extend it.

    The gated sleep freezes the window open, so the second close arrives
    while it is still running: a re-arm would queue a SECOND sleep and the
    flip would land later than the first close's window.
    """
    gated = GatedSleep(seams.clock)
    monkeypatch.setattr(timing, "sleep", gated)
    engine, _recorder, transport = await make_engine()
    try:
        await transport.deliver(SocketClosed(code=1006))
        await tick()
        assert len(gated.gates) == 1  # the window's own sleep
        await transport.deliver(SocketClosed(code=1006))
        await tick()
        assert len(gated.gates) == 1  # NOT re-armed by the second close
        gated.release(0)
        await tick()
        assert engine.feed.available is False
        assert engine.feed.facts["outages"] == 1
    finally:
        for gate in gated.gates:
            if not gate.done():
                gate.cancel()
        await engine.stop()
        await tick()


async def test_stop_cancels_a_pending_feed_window():
    """A teardown must not leave the feed watcher running."""
    engine, _recorder, transport = await make_engine()
    await transport.deliver(SocketClosed(code=1006))
    await engine.stop()
    assert engine.feed.window_pending is False
    assert engine.feed.available is True  # never flipped


async def test_terminal_stop_after_the_feed_is_down_reports_nothing_more():
    """One change and one outage count per episode, however it is reported.

    The transport can also stop terminally while the feed is already down
    (the grace window expired first), and that path reports the outage
    through the same transition - it must be absorbed, not repeated.
    """
    engine, recorder, transport = await make_engine()
    try:
        await transport.deliver(SocketClosed(code=1006))
        await tick()  # the window expires: one outage, reported once
        assert engine.feed.available is False
        recorder.changes.clear()

        await engine.feed.mark_down("transport stopped (AuthError)")
        await tick()

        assert recorder.changes == []
        assert engine.feed.facts["outages"] == 1
        assert engine.feed.available is False
    finally:
        await engine.stop()


# --- supervision ---


class ScriptedTransport:
    """Duck-typed transport: scripted run() outcomes, records subscribe.

    Outcome items: an Exception is raised from run(); a float/int is
    awaited through the (patched) sleep seam first (for long-lived-run
    scenarios); None parks run() until close().
    """

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.handlers = []
        self.runs = 0
        self._park = asyncio.Event()

    def subscribe(self, handler):
        self.handlers.append(handler)

    async def run(self):
        self.runs += 1
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, (int, float)):
            await timing.sleep(outcome)
            outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        await self._park.wait()

    async def close(self):
        self._park.set()


async def test_supervise_subscribes_and_holds_run_task():
    engine = SmartboxEngine()
    transport = ScriptedTransport([])
    engine.supervise(transport)
    try:
        assert transport.handlers == [engine.handle_event]
        task = engine.supervision_task
        assert task is not None
        assert not task.done()
        await tick()
        assert transport.runs == 1
        assert engine.unsupported is False
        assert engine.auth_failed is False
    finally:
        await engine.stop()


async def test_stop_cancels_run_task_bounded_and_idempotent():
    engine = SmartboxEngine()
    transport = ScriptedTransport([])
    engine.supervise(transport)
    await tick()
    await engine.stop()
    task = engine.supervision_task
    assert task is None
    await tick()
    assert transport.runs == 1
    await engine.stop()  # idempotent


async def test_terminal_unsupported_error_no_restart():
    engine = SmartboxEngine()
    error = TransportUnsupportedError("ws_user rejected")
    transport = ScriptedTransport([error])
    engine.supervise(transport)
    await tick()
    task = engine.supervision_task
    assert task is not None
    assert task.done()
    assert task.exception() is error  # surfaced on the task
    assert engine.unsupported is True
    assert engine.auth_failed is False
    assert engine.terminal_error is error
    assert transport.runs == 1  # terminal: no restart


async def test_terminal_auth_error_no_restart():
    engine = SmartboxEngine()
    error = AuthError("bad credentials")
    transport = ScriptedTransport([error])
    engine.supervise(transport)
    await tick()
    task = engine.supervision_task
    assert task is not None
    assert task.done()
    assert task.exception() is error  # retrieved, or asyncio logs it at GC
    assert engine.auth_failed is True
    assert engine.unsupported is False
    assert transport.runs == 1


async def test_unexpected_exit_restarts_with_backoff():
    engine = SmartboxEngine()
    transport = ScriptedTransport([RuntimeError("boom"), None])
    engine.supervise(transport)
    await tick()
    assert transport.runs == 2  # restarted once
    await engine.stop()


async def test_unexpected_exit_opens_the_feed_grace_window(seams):
    """A run that dies without a close event still opens the outage window.

    The transport's SocketClosed is the feed's only outage signal; a run
    that dies before dispatch would otherwise leave stale values marked
    available across every restart.
    """
    engine = SmartboxEngine()
    transport = ScriptedTransport([RuntimeError("boom"), None])
    engine.supervise(transport)
    try:
        await tick()
        assert transport.runs == 2  # it restarted
        assert engine.feed.available is False
    finally:
        await engine.stop()


async def test_transport_unavailable_restarts_warning_only(caplog):
    """A transient run() failure is one WARNING line, no traceback.

    The restart ladder still runs (unlike terminal Auth/Unsupported).
    """
    caplog.set_level(logging.DEBUG, logger=engine_mod.__name__)
    engine = SmartboxEngine()
    transport = ScriptedTransport(
        [TransportUnavailableError("token source down"), None]
    )
    engine.supervise(transport)
    await tick()
    assert transport.runs == 2  # restarted on the ladder
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "unavailable" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert warnings[0].exc_info is None  # no traceback attached
    await engine.stop()


async def test_stop_then_supervise_restarts_again():
    """supervise() clears the stopping flag: stop() then a new supervise().

    The restarted ladder runs again on an unexpected exit (a reused
    engine works).
    """
    engine = SmartboxEngine()
    transport = ScriptedTransport([None])
    engine.supervise(transport)
    await engine.stop()  # sets _stopping + cancels supervision
    # run() parks forever in this scripted transport after the first run;
    # stopping must not leak into the new supervision.
    transport2 = ScriptedTransport([RuntimeError("boom"), None])
    engine.supervise(transport2)
    await tick()
    assert transport2.runs == 2  # the unexpected exit restarted
    await engine.stop()


async def test_stop_then_supervise_re_arms_the_feed_monitor():
    """A restarted engine can still report a feed outage.

    ``stop()`` shuts the feed monitor down for good (a late window must not
    notify after teardown); a later ``supervise()`` re-arms it, or a reused
    engine would render stale values as available forever.
    """
    engine = SmartboxEngine()
    recorder = Recorder()
    engine.subscribe(recorder)
    engine.supervise(ScriptedTransport([None]))
    await engine.stop()
    engine.supervise(ScriptedTransport([None]))
    try:
        await engine.handle_event(SocketClosed(code=1006))
        assert engine.feed.window_pending is True
        await tick()
        assert engine.feed.available is False
        assert engine.feed.facts["outages"] == 1
    finally:
        await engine.stop()


async def test_backoff_ladder_capped_and_reset_after_long_run(seams, monkeypatch):
    engine = SmartboxEngine()
    # The run-duration reset reads wall-clock time.monotonic() (not a
    # test seam); patch the stdlib function to the fake clock so the
    # scripted "long-lived run" is still deterministic.
    monkeypatch.setattr("time.monotonic", seams.clock)
    transport = ScriptedTransport(
        [RuntimeError("a"), 100.0, RuntimeError("b"), RuntimeError("c"), None]
    )
    engine.supervise(transport)
    await tick()
    await engine.stop()
    assert transport.runs == 4
    # run1 fails -> 1s; run2 lives 100s (long-lived) then fails -> ladder
    # RESET -> 1s (not 2s); run3 fails immediately -> 2s. The feed's grace
    # window is its own sleep; ignore that delay here.
    delays = [d for d in seams.sleep.delays if d != FEED_GRACE_SECONDS]
    assert delays == [1.0, 100.0, 1.0, 2.0]


async def test_backoff_ladder_45s_runs_escalate_then_long_run_resets(
    seams, monkeypatch
):
    """Medium runs (~45 s) must NOT reset the ladder (fixed 60 s threshold).

    The ladder escalates through the 45 s deaths; one long-ish (60 s)
    run converges it back to the 1 s base.
    """
    monkeypatch.setattr("time.monotonic", seams.clock)
    engine = SmartboxEngine()
    transport = ScriptedTransport(
        [RuntimeError("a"), 45.0, RuntimeError("b"), 60.0, RuntimeError("c"), None]
    )
    engine.supervise(transport)
    await tick()
    await engine.stop()
    assert transport.runs == 4
    # 45 s deaths do NOT reset (climb 1,2); the 60 s run resets -> 1 s.
    # The feed's grace window is its own sleep; ignore that delay here.
    delays = [d for d in seams.sleep.delays if d != FEED_GRACE_SECONDS]
    assert delays == [1.0, 45.0, 2.0, 60.0, 1.0]


async def test_supervise_twice_raises():
    engine = SmartboxEngine()
    transport = ScriptedTransport([])
    engine.supervise(transport)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            engine.supervise(ScriptedTransport([]))
    finally:
        await engine.stop()


async def test_repeated_lost_frames_leave_the_first_deadline_standing(
    seams, monkeypatch
):
    """Repeats leave the FIRST deadline standing (they never re-arm it).

    Asserted through the outcome: the released watcher must flip the node
    even though more lost frames arrived after it was armed.
    """
    gated = GatedSleep(seams.clock)
    monkeypatch.setattr(timing, "sleep", gated)
    engine, recorder, transport = await make_engine()
    try:
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        await tick()
        assert len(gated.gates) == 1
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        await transport.deliver_update("dev1", "/htr/2/status", dict(LOST))
        await tick()
        assert len(gated.gates) == 1  # no re-arm: one watcher, one deadline
        recorder.changes.clear()

        gated.release(0)  # the FIRST deadline elapses
        await tick()
        assert engine.node("dev1", "htr/2").available is False
        assert [c.kind for c in recorder.changes] == ["availability"]
    finally:
        for gate in gated.gates:
            if not gate.done():
                gate.cancel()
        await engine.stop()
        await tick()
