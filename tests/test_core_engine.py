"""Tests for the core engine (snapshot ingestion, update merge, notifications).

Everything is driven through ``engine.handle_event`` with plain payload
dicts; the FakeSmartbox composition tests prove the engine's snapshot
ingestion agrees with the fake's ``snapshot_payload()`` (the wire's
homes -> devs -> devData nesting).
"""

from __future__ import annotations

import copy
import logging

import pytest
from smartbox.core.engine import SmartboxEngine
from smartbox.core.state import (
    DeviceChange,
    DeviceRemoved,
    NodeAdded,
    NodeChange,
    NodeRemoved,
    NodeState,
)
from smartbox.protocol import timing
from smartbox.protocol.client import NodeRef
from smartbox.protocol.events import Snapshot, SocketClosed, Update

from tests.fake_api import FakeSmartbox
from tests.test_utils import until

NODE_REF = NodeRef("htr", "2")
STATUS = {"stemp": "20.0", "mtemp": "19.6", "sync_status": "ok"}
SETUP = {"priority": 0}


class _FakeClock:
    """Injectable monotonic clock the tests control."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, delta: float) -> None:
        self.now += delta


class _InstantSleep:
    """Instant ``timing.sleep`` replacement (advances the fake clock)."""

    def __init__(self, clock: _FakeClock) -> None:
        self.clock = clock
        self.delays: list[float] = []

    async def __call__(self, delay=0):
        self.delays.append(delay)
        self.clock.advance(delay)


@pytest.fixture(autouse=True)
def engine_clock(monkeypatch) -> _FakeClock:
    """Pin the engine's clock so a deadline is due exactly when it elapses."""
    clock = _FakeClock()
    monkeypatch.setattr(timing, "monotonic", clock)
    return clock


def home(*devs, home_id="h1"):
    return {"id": home_id, "devs": list(devs)}


def dev(dev_id, dev_data):
    return {"dev_id": dev_id, "devData": dict(dev_data)}


def node_entry(node_type="htr", addr=2, **slots):
    return {"type": node_type, "addr": addr, **slots}


def base_dev_data(**extra):
    return {
        "connected": True,
        "away_status": {"away": False, "enabled": False, "forced": False},
        "nodes": [node_entry(status=STATUS, setup=SETUP)],
        **extra,
    }


def snapshot(*homes):
    # one list argument = the ready homes payload; otherwise wrap the homes
    payload = (
        homes[0] if len(homes) == 1 and isinstance(homes[0], list) else list(homes)
    )
    return Snapshot(payload=payload)


class Recorder:
    """Subscriber double that records every delivered change."""

    def __init__(self):
        self.changes = []

    async def __call__(self, change):
        self.changes.append(change)


def make_engine():
    engine = SmartboxEngine()
    recorder = Recorder()
    engine.subscribe(recorder)
    return engine, recorder


# --- snapshot ingestion ---


async def test_snapshot_traverses_homes_devs_nodes():
    engine, recorder = make_engine()
    payload = [
        home(
            dev(
                "d1",
                {
                    "connected": True,
                    "nodes": [
                        node_entry(status=STATUS, setup=SETUP),
                        node_entry(addr=3),
                    ],
                },
            ),
            home_id="h1",
        ),
        home(dev("d2", {"connected": False}), home_id="h2"),
    ]
    await engine.handle_event(snapshot(payload))
    assert set(engine.world.devices) == {"d1", "d2"}
    d1 = engine.device("d1")
    assert d1.home_id == "h1"
    assert d1.connected is True
    assert set(d1.node_states) == {"htr/2", "htr/3"}
    assert engine.node("d1", "htr/2").status == STATUS
    assert engine.node("d1", "htr/2").setup == SETUP
    assert engine.node("d1", "htr/3").status == {}  # bare record keyed, no data
    d2 = engine.device("d2")
    assert d2.home_id == "h2"
    assert d2.connected is False
    assert d2.node_states == {}
    assert recorder.changes


async def test_a_malformed_home_does_not_prune_its_devices():
    """An unreadable home/dev list is not authoritative for membership."""
    engine, _recorder = make_engine()
    await engine.handle_event(
        snapshot(
            [
                home(dev("d1", base_dev_data()), home_id="h1"),
                home(dev("d2", base_dev_data()), home_id="h2"),
            ]
        )
    )
    assert set(engine.world.devices) == {"d1", "d2"}
    # home h2's devs list is unreadable: d2 must survive the merge
    await engine.handle_event(
        snapshot(
            [home(dev("d1", base_dev_data()), home_id="h1"), {"id": "h2", "devs": None}]
        )
    )
    assert set(engine.world.devices) == {"d1", "d2"}


async def test_an_unreadable_home_id_does_not_clear_the_stored_one():
    engine, _recorder = make_engine()
    await engine.handle_event(
        snapshot([home(dev("d1", base_dev_data()), home_id="h1")])
    )
    await engine.handle_event(
        snapshot([{"no_id": True, "devs": [dev("d1", base_dev_data())]}])
    )
    assert engine.device("d1").home_id == "h1"


async def test_a_negative_power_limit_is_malformed():
    """The write API refuses negative limits; a frame carrying one is not a setting."""
    engine, _recorder = make_engine()
    await engine.handle_event(
        snapshot(
            [
                home(
                    dev(
                        "d1",
                        {**base_dev_data(), "htr_system": {"power_limit": -5}},
                    )
                )
            ]
        )
    )
    assert engine.device("d1").power_limit is None


async def test_first_snapshot_change_events_and_revisions():
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(
            home(
                dev(
                    "d1",
                    {**base_dev_data(), "htr_system": {"power_limit": 2000}},
                )
            )
        )
    )
    kinds = [
        type(c).__name__ + (f":{c.kind}" if hasattr(c, "kind") else "")
        for c in recorder.changes
    ]
    # device slots in field order, then the membership addition, then the
    # node's changed slots + the availability derived from the ok status slot
    assert kinds == [
        "DeviceChange:connected",
        "DeviceChange:away",
        "DeviceChange:power_limit",
        "DeviceChange:nodes",
        "NodeAdded",
        "NodeChange:status",
        "NodeChange:setup",
        "NodeChange:availability",
    ]
    device = engine.device("d1")
    node = engine.node("d1", "htr/2")
    assert device.revision == 1
    assert device.power_limit == 2000
    assert node.revision == 1
    # every change event carries the NEW state + revision; the trailing
    # availability change (derived from the ok status) carries the final
    # node object
    added = [change for change in recorder.changes if isinstance(change, NodeAdded)]
    assert added == [NodeAdded("d1", "htr/2", None)]
    for change in recorder.changes:
        if isinstance(change, NodeChange):
            assert change.node.revision == node.revision
        elif isinstance(change, DeviceChange):
            assert change.device is device
    availability_change = recorder.changes[-1]
    assert isinstance(availability_change, NodeChange)
    assert availability_change.kind == "availability"
    assert availability_change.node is node


async def test_snapshot_addition_reports_only_the_new_node():
    """A later snapshot adds membership; existing nodes are not 'added' again."""
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(addr=2)]})))
    )
    recorder.changes.clear()

    await engine.handle_event(
        snapshot(
            home(
                dev(
                    "d1",
                    {"nodes": [node_entry(addr=2), node_entry(addr=3, name="Desk")]},
                )
            )
        )
    )

    added = [change for change in recorder.changes if isinstance(change, NodeAdded)]
    assert added == [NodeAdded("d1", "htr/3", "Desk")]


async def test_partial_snapshot_does_not_report_additions():
    """Membership is trustworthy only when the whole node list parsed."""
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(addr=2)]})))
    )
    recorder.changes.clear()

    await engine.handle_event(
        snapshot(
            home(
                dev(
                    "d1",
                    {"nodes": [node_entry(addr=2), node_entry(addr=3), "bad"]},
                )
            )
        )
    )

    assert not any(isinstance(change, NodeAdded) for change in recorder.changes)
    assert set(engine.device("d1").node_states) == {"htr/2", "htr/3"}


async def test_redundant_snapshot_absorbed_silently():
    engine, recorder = make_engine()
    payload = [home(dev("d1", base_dev_data()))]
    await engine.handle_event(snapshot(payload))
    revisions = (engine.device("d1").revision, engine.node("d1", "htr/2").revision)
    assert revisions == (1, 1)
    recorder.changes.clear()
    # a genuinely fresh copy of the same payload: value dedup, not identity
    await engine.handle_event(snapshot(copy.deepcopy(payload)))
    assert recorder.changes == []
    assert (
        engine.device("d1").revision,
        engine.node("d1", "htr/2").revision,
    ) == revisions


async def test_home_id_only_change_is_stored_without_event():
    """A snapshot that only gives the device a home id still records it.

    The merge returns no change events for a home-id change (homes are
    metadata: no revision bump, no notification), so the store must not be
    gated on the event list.
    """
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"connected": True}), home_id=None))
    )
    assert engine.device("d1").home_id is None
    revision = engine.device("d1").revision
    recorder.changes.clear()

    await engine.handle_event(
        snapshot(home(dev("d1", {"connected": True}), home_id="h7"))
    )

    assert engine.device("d1").home_id == "h7"
    assert recorder.changes == []  # metadata: silent
    assert engine.device("d1").revision == revision  # metadata: no bump


async def test_snapshot_device_with_no_slots_is_registered():
    """A device entry carrying only a home id registers the device.

    Same gate, the other half: with no change events and no previous object
    the merge output was discarded, so the device never appeared at all.
    """
    engine, recorder = make_engine()
    await engine.handle_event(snapshot(home(dev("d5", {}), home_id="h3")))

    assert engine.device("d5") is not None
    assert engine.device("d5").home_id == "h3"
    assert recorder.changes == []


async def test_snapshot_node_list_reorder_is_a_no_op():
    """Membership decides events: a reordered same-membership list changes nothing."""
    engine, recorder = make_engine()
    two_nodes = [node_entry(addr=2), node_entry(addr=3)]
    await engine.handle_event(snapshot(home(dev("d1", {"nodes": two_nodes}))))
    revision = engine.device("d1").revision
    recorder.changes.clear()
    reordered = [node_entry(addr=3), node_entry(addr=2)]

    await engine.handle_event(snapshot(home(dev("d1", {"nodes": reordered}))))

    assert recorder.changes == []
    assert engine.device("d1").revision == revision
    assert set(engine.device("d1").node_states) == {"htr/2", "htr/3"}


async def test_snapshot_ignores_malformed_entries(caplog):
    engine, recorder = make_engine()
    payload = [
        "not-a-home",
        home(dev("d1", {"connected": True})),
        home(dev("d2", {"connected": True, "nodes": [node_entry(addr="3")]})),
        {"no-devs": True},
        home(dev("d3", {"connected": True, "nodes": ["junk", {"type": "htr"}]})),
        home({"devData": {"connected": True}}, home_id="h9"),  # missing dev_id
        home({"dev_id": "d4"}),  # missing devData
    ]
    with caplog.at_level(logging.WARNING):
        await engine.handle_event(snapshot(payload))
    assert set(engine.world.devices) == {"d1", "d2", "d3"}
    assert engine.node("d2", "htr/3").status == {}  # bare record keyed, no data
    assert engine.device("d3").node_states == {}  # only malformed entries
    assert recorder.changes
    assert any("ignoring" in r.message for r in caplog.records)


async def test_snapshot_payload_that_is_not_a_list_is_ignored(caplog):
    """A malformed top-level snapshot must not be iterated as a mapping."""
    engine, recorder = make_engine()
    with caplog.at_level(logging.WARNING):
        await engine.handle_event(Snapshot(payload={"homes": []}))
    assert engine.world.devices == {}
    assert recorder.changes == []
    assert any("not a list" in r.message for r in caplog.records)


async def test_snapshot_dev_entry_that_is_not_a_dict_is_skipped(caplog):
    """A malformed dev entry is skipped; its siblings still merge."""
    engine, _recorder = make_engine()
    with caplog.at_level(logging.WARNING):
        await engine.handle_event(
            snapshot(
                [home("junk", dev("d1", {"connected": True}))],
            )
        )
    assert set(engine.world.devices) == {"d1"}
    assert any("not a dict" in r.message for r in caplog.records)


async def test_snapshot_absent_slots_keep_old_values():
    engine, recorder = make_engine()
    full = home(
        dev(
            "d1",
            {
                "connected": True,
                "away_status": {"away": True},
                "nodes": [node_entry(status=STATUS)],
            },
        )
    )
    await engine.handle_event(snapshot(full))
    recorder.changes.clear()
    second = home(dev("d1", {"connected": True}))  # away/nodes omitted
    await engine.handle_event(snapshot(second))
    assert recorder.changes == []
    device = engine.device("d1")
    assert device.away == {"away": True}
    assert engine.node("d1", "htr/2").status == STATUS


async def test_empty_snapshot_slots_are_not_evidence():
    """An empty body neither blanks a slot nor marks a node reachable."""
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(
            home(
                dev(
                    "d1",
                    {
                        "away_status": {"away": True},
                        "nodes": [node_entry(status=STATUS, setup=SETUP)],
                    },
                )
            )
        )
    )
    assert engine.node("d1", "htr/2").available is True
    recorder.changes.clear()

    await engine.handle_event(
        snapshot(
            home(
                dev(
                    "d1",
                    {
                        "away_status": {},
                        "nodes": [node_entry(status={}, setup={})],
                    },
                )
            )
        )
    )
    node = engine.node("d1", "htr/2")
    assert node.status == STATUS
    assert node.setup == SETUP
    assert node.available is True
    assert engine.device("d1").away == {"away": True}
    assert recorder.changes == []


async def test_malformed_node_list_does_not_delete_stored_nodes():
    """A node-entry key drift must not wipe the map (and its deadlines)."""
    engine, _recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(status=STATUS)]})))
    )
    await engine.handle_event(
        Update(
            dev_id="d1",
            path="/mgr/nodes",
            body={"nodes": [{"node_type": "htr", "addr": 2}]},
        )
    )
    assert "htr/2" in engine.device("d1").node_states


async def test_lost_frame_for_an_unknown_node_arms_no_deadline():
    engine, _recorder = make_engine()
    await engine.handle_event(
        Update(
            dev_id="ghost",
            path="/htr/9/status",
            body={"sync_status": "lost"},
        )
    )
    assert engine.deadlines == ()


async def test_device_level_lost_marker_is_not_slot_data():
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"away_status": {"away": False}})))
    )
    recorder.changes.clear()
    await engine.handle_event(
        Update(
            dev_id="d1",
            path="/mgr/away_status",
            body={"sync_status": "lost"},
        )
    )
    assert engine.device("d1").away == {"away": False}
    assert recorder.changes == []


async def test_node_power_limit_frame_patches_the_setup_key():
    engine, _recorder = make_engine()
    await engine.handle_event(
        snapshot(
            home(
                dev(
                    "d1",
                    {
                        "nodes": [
                            node_entry(node_type="pmo", setup={"power_limit": 1000})
                        ]
                    },
                )
            )
        )
    )
    await engine.handle_event(
        Update(
            dev_id="d1",
            path="/pmo/2/power_limit",
            body={"power_limit": "2000"},
        )
    )
    assert engine.node("d1", "pmo/2").setup["power_limit"] == 2000


async def test_malformed_device_frames_do_not_blank_slots():
    """A non-bool connected / keyless power-limit body is not slot data."""
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(
            home(
                dev(
                    "d1",
                    {
                        **base_dev_data(),
                        "htr_system": {"power_limit": 2000},
                    },
                )
            )
        )
    )
    recorder.changes.clear()
    await engine.handle_event(
        Update(dev_id="d1", path="/connected", body={"connected": "yes"})
    )
    await engine.handle_event(
        Update(dev_id="d1", path="/htr_system/power_limit", body={})
    )
    await engine.handle_event(
        Update(
            dev_id="d1",
            path="/htr_system/power_limit",
            body={"power_limit": "not-a-number"},
        )
    )
    device = engine.device("d1")
    assert device.connected is True
    assert device.power_limit == 2000
    assert recorder.changes == []


async def test_malformed_snapshot_device_entry_does_not_prune():
    """A dev entry we cannot read is not evidence that the device is gone."""
    engine, _recorder = make_engine()
    await engine.handle_event(
        snapshot(
            home(dev("d1", {"nodes": [node_entry(status=STATUS)]})),
            home(dev("d2", {"nodes": [node_entry(status=STATUS)]}), home_id="h2"),
        )
    )
    await engine.handle_event(
        snapshot(
            home(dev("d1", {"nodes": [node_entry(status=STATUS)]})),
            home({"dev_id": "d2"}, home_id="h2"),  # devData missing
        )
    )
    assert set(engine.world.devices) == {"d1", "d2"}


async def test_snapshot_prunes_a_device_removed_from_the_account():
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(
            home(dev("d1", {"nodes": [node_entry(status=STATUS)]})),
            home(dev("d2", {"nodes": [node_entry(status=STATUS)]}), home_id="h2"),
        )
    )
    assert set(engine.world.devices) == {"d1", "d2"}
    recorder.changes.clear()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(status=STATUS)]})))
    )
    assert set(engine.world.devices) == {"d1"}
    assert any(
        type(change).__name__ == "DeviceChange" and change.dev_id == "d2"
        for change in recorder.changes
    )


# --- update frames: node slots ---


async def test_lost_marked_frame_changes_no_slot_and_schedules_the_flip(
    monkeypatch, engine_clock
):
    """A lost-marked frame is availability evidence, never slot data.

    The marker carries none of the slot's keys, so storing it would blank every
    entity's last known values for the length of the window - and for good if
    the frame that confirms the pending write lands on another slot.
    """
    monkeypatch.setattr(timing, "sleep", _InstantSleep(engine_clock))
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(status=STATUS)]})))
    )
    recorder.changes.clear()
    before = engine.node("d1", "htr/2")
    await engine.handle_event(
        Update(dev_id="d1", path="/htr/2/status", body={"sync_status": "lost"})
    )
    await until(lambda: engine.node("d1", "htr/2").available is False)
    # the stored body and its revision are untouched; only availability moved
    node = engine.node("d1", "htr/2")
    assert node.status == STATUS
    assert node.revision == before.revision
    assert node.available is False
    assert [c.kind for c in recorder.changes] == ["availability"]
    change = recorder.changes[0]
    assert isinstance(change, NodeChange)
    assert (change.dev_id, change.node_key, change.kind) == (
        "d1",
        "htr/2",
        "availability",
    )
    assert change.node is node
    assert change.node.revision == node.revision


@pytest.mark.parametrize(
    ("path", "body", "slot"),
    [
        ("/htr/2/status", {"mode": "heat"}, "status"),
        ("/htr/2/setup", {"window_mode_enabled": True}, "setup"),
        ("/htr/2/prog", {"prog": {"0": [2, 2]}}, "prog"),
        ("/htr/2/version", {"fw_version": "1.9", "hw_version": "1.1"}, "version"),
    ],
)
async def test_node_slot_paths(path, body, slot):
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(status=STATUS, setup=SETUP)]})))
    )
    recorder.changes.clear()
    await engine.handle_event(Update(dev_id="d1", path=path, body=body))
    node = engine.node("d1", "htr/2")
    assert getattr(node, slot) == body
    assert node.revision == 2
    expected = [("htr/2", slot)]
    # a non-lost status frame flips availability, but the snapshot's ok
    # status already marked the node available: no event fires
    assert [(c.node_key, c.kind) for c in recorder.changes] == expected


async def test_update_for_a_node_the_list_does_not_hold_is_ignored():
    """The node list is authoritative: slot frames never create nodes."""
    engine, recorder = make_engine()
    await engine.handle_event(snapshot(home(dev("d1", {}))))
    await engine.handle_event(Update(dev_id="d1", path="/htr/7/status", body=STATUS))
    assert engine.node("d1", "htr/7") is None
    assert recorder.changes == []


async def test_snapshot_int_addr_matches_update_path_key():
    engine, _ = make_engine()
    # snapshot node entries may carry a numeric addr while the update
    # path carries it as a segment - both must land on the same state
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(addr=2)]})))
    )
    before = engine.node("d1", "htr/2")
    await engine.handle_event(
        Update(dev_id="d1", path="/htr/2/status", body={"mode": "heat"})
    )
    after = engine.node("d1", "htr/2")
    assert after is not before
    assert after.revision == before.revision + 1
    assert after.status == {"mode": "heat"}


@pytest.mark.parametrize(
    ("htr_system", "expected"),
    [
        # live shape (captured 2026-10-16, api-lhz): both copies present,
        # the object one a digit string, the setup one an int
        (
            {"power_limit": {"power_limit": "3600"}, "setup": {"power_limit": 3600}},
            3600,
        ),
        # empty object copy: the mobile client's documented fallback
        ({"power_limit": {}, "setup": {"power_limit": 1500}}, 1500),
        # cleared limit (live 2026-10-16): the object copy empties and the
        # setup copy carries 0, which the no_power_limit switch reads
        ({"power_limit": {}, "setup": {"power_limit": 0}}, 0),
        # older/other family: a bare scalar under power_limit
        ({"power_limit": 2000}, 2000),
        # no limit reported: the slot keeps its default
        ({"setup": {"refresh_period": 30}}, None),
    ],
)
async def test_snapshot_power_limit_shapes(htr_system, expected):
    engine, _ = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {**base_dev_data(), "htr_system": htr_system})))
    )
    assert engine.device("d1").power_limit == expected


# --- update frames: device slots ---


@pytest.mark.parametrize(
    ("path", "body", "slot", "value"),
    [
        ("/connected", {"connected": False}, "connected", False),
        (
            "/mgr/away_status",
            {"away": True, "enabled": True, "forced": False},
            "away",
            {"away": True, "enabled": True, "forced": False},
        ),
        ("/htr_system/power_limit", {"power_limit": 2500}, "power_limit", 2500),
        # live frame body: the digit string the wire serves (E18) lands
        # in the slot as the number, never as a str
        (
            "/htr_system/power_limit",
            {"power_limit": "2500"},
            "power_limit",
            2500,
        ),
        ("/htr_system/power_limit", {"power_limit": "0"}, "power_limit", 0),
    ],
)
async def test_device_slot_paths(path, body, slot, value):
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(status=STATUS)]})))
    )
    node_before = engine.node("d1", "htr/2")
    recorder.changes.clear()
    await engine.handle_event(Update(dev_id="d1", path=path, body=body))
    device = engine.device("d1")
    assert getattr(device, slot) == value
    assert device.revision == 2
    assert node_before.revision == 1  # device frames never touch node revisions
    assert [(type(c).__name__, c.kind) for c in recorder.changes] == [
        ("DeviceChange", slot)
    ]
    assert recorder.changes[0].device is device


async def test_partial_away_frame_merges_over_stored_keys():
    """Away frames may carry any subset of {away, enabled, forced}.

    The body merges over the stored dict (the same rule the write path
    applies), never a wholesale replacement that would wipe the keys the
    frame left out.
    """
    engine, recorder = make_engine()
    await engine.handle_event(snapshot(home(dev("d1", base_dev_data()))))
    recorder.changes.clear()
    await engine.handle_event(
        Update(dev_id="d1", path="/mgr/away_status", body={"away": True})
    )
    device = engine.device("d1")
    assert device.away == {"away": True, "enabled": False, "forced": False}
    assert [(type(c).__name__, c.kind) for c in recorder.changes] == [
        ("DeviceChange", "away"),
    ]


async def test_server_controlled_strings_cannot_forge_log_lines(caplog):
    """Server-controlled strings render repr-safe: no forged log lines.

    The old %s formats embedded raw newlines from frame paths into the
    HA log; every server-controlled string now renders escaped.
    """
    engine, _recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(status=STATUS)]})))
    )
    with caplog.at_level(logging.DEBUG):
        await engine.handle_event(
            Update(dev_id="d1", path="/x\nFORGED PATH", body={"a": 1})
        )
        await engine.handle_event(
            Update(dev_id="d1\nFORGED WARNING", path="/connected", body={"a": 1})
        )
    assert not [rec.getMessage() for rec in caplog.records if "\n" in rec.getMessage()]
    # the details are still there (escaped)
    assert any("FORGED PATH" in rec.getMessage() for rec in caplog.records)
    assert any("FORGED WARNING" in rec.getMessage() for rec in caplog.records)


async def test_update_for_an_unknown_device_is_ignored():
    """Frames update what the snapshot created; they never create a device."""
    engine, recorder = make_engine()
    await engine.handle_event(
        Update(dev_id="dev-late", path="/connected", body={"connected": True})
    )
    assert engine.device("dev-late") is None
    assert recorder.changes == []


async def test_mgr_nodes_frame_reconciles_membership():
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(status=STATUS)]})))
    )
    recorder.changes.clear()
    new_list = [{"type": "htr", "addr": "3"}]
    await engine.handle_event(
        Update(dev_id="d1", path="/mgr/nodes", body={"nodes": new_list})
    )
    device = engine.device("d1")
    assert set(device.node_states) == {"htr/3"}
    assert device.node_states["htr/3"] == NodeState()
    assert device.revision == 2
    assert [type(c).__name__ for c in recorder.changes] == [
        "DeviceChange",
        "NodeRemoved",
        "NodeAdded",
    ]
    assert recorder.changes[0].kind == "nodes"
    assert recorder.changes[1] == NodeRemoved("d1", "htr/2")
    assert recorder.changes[2] == NodeAdded("d1", "htr/3", None)


async def test_snapshot_shrink_emits_node_removed():
    """A fully parsed snapshot that drops a node reports the membership loss."""
    engine, recorder = make_engine()
    two_nodes = [node_entry(addr=2), node_entry(addr=3)]
    await engine.handle_event(snapshot(home(dev("d1", {"nodes": two_nodes}))))
    recorder.changes.clear()

    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(addr=2)]})))
    )

    removed = [change for change in recorder.changes if isinstance(change, NodeRemoved)]
    assert removed == [NodeRemoved("d1", "htr/3")]
    assert removed[0].node_id == "d1_3"
    assert set(engine.device("d1").node_states) == {"htr/2"}


async def test_snapshot_device_prune_emits_node_and_device_removed():
    """A device missing from a non-empty snapshot reports its nodes and itself."""
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(
            home(
                dev("d1", {"nodes": [node_entry(addr=2)]}),
                dev("d2", {"nodes": [node_entry(addr=4)]}),
            )
        )
    )
    recorder.changes.clear()

    await engine.handle_event(
        snapshot(home(dev("d2", {"nodes": [node_entry(addr=4)]})))
    )

    assert [type(change).__name__ for change in recorder.changes] == [
        "NodeRemoved",
        "DeviceChange",
        "DeviceRemoved",
    ]
    assert recorder.changes[0] == NodeRemoved("d1", "htr/2")
    assert recorder.changes[2] == DeviceRemoved("d1")
    assert engine.device("d1") is None


async def test_empty_mgr_nodes_list_is_authoritative_and_removes_nodes():
    """An empty node list is a real shrink; the watcher confirms it separately."""
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(addr=2)]})))
    )
    recorder.changes.clear()

    await engine.handle_event(
        Update(dev_id="d1", path="/mgr/nodes", body={"nodes": []})
    )

    assert engine.device("d1").node_states == {}
    assert [
        change for change in recorder.changes if isinstance(change, NodeRemoved)
    ] == [NodeRemoved("d1", "htr/2")]


async def test_malformed_node_list_emits_no_removal():
    """A list we cannot fully parse is not membership evidence."""
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(addr=2)]})))
    )
    recorder.changes.clear()

    await engine.handle_event(
        Update(
            dev_id="d1",
            path="/mgr/nodes",
            body={"nodes": [node_entry(addr=2), {"type": "htr"}]},
        )
    )

    assert set(engine.device("d1").node_states) == {"htr/2"}
    assert not any(isinstance(change, NodeRemoved) for change in recorder.changes)


async def test_mgr_nodes_frame_reorder_fires_no_event():
    """Only MEMBERSHIP changes fire the "nodes" event; order is not state."""
    engine, recorder = make_engine()
    two_nodes = [node_entry(addr=2), node_entry(addr=3)]
    await engine.handle_event(snapshot(home(dev("d1", {"nodes": two_nodes}))))
    revision = engine.device("d1").revision
    recorder.changes.clear()
    reordered = [
        {"type": "htr", "addr": "3"},
        {"type": "htr", "addr": "2"},
    ]
    await engine.handle_event(
        Update(dev_id="d1", path="/mgr/nodes", body={"nodes": copy.deepcopy(reordered)})
    )
    assert recorder.changes == []
    assert engine.device("d1").revision == revision
    await engine.handle_event(
        Update(dev_id="d1", path="/mgr/nodes", body={"nodes": copy.deepcopy(two_nodes)})
    )
    assert recorder.changes == []
    assert set(engine.device("d1").node_states) == {"htr/2", "htr/3"}


async def test_connected_double_true_dedup():
    engine, recorder = make_engine()
    await engine.handle_event(snapshot(home(dev("d1", {"connected": True}))))
    recorder.changes.clear()
    await engine.handle_event(
        Update(dev_id="d1", path="/connected", body={"connected": True})
    )
    await engine.handle_event(
        Update(dev_id="d1", path="/connected", body={"connected": True})
    )
    assert recorder.changes == []  # the verified redundant double true
    assert engine.device("d1").revision == 1


async def test_rapid_distinct_values_all_notified_in_order():
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(status=STATUS)]})))
    )
    recorder.changes.clear()
    for mode in ("off", "heat", "off"):
        await engine.handle_event(
            Update(dev_id="d1", path="/htr/2/status", body={"mode": mode})
        )
    assert [c.node.status["mode"] for c in recorder.changes if c.kind == "status"] == [
        "off",
        "heat",
        "off",
    ]
    assert [c.node.revision for c in recorder.changes if c.kind == "status"] == [
        2,
        3,
        4,
    ]


async def test_value_equal_slot_frames_absorbed_silently():
    """Repeated full-body frames are absorbed: no event, no revision bump.

    The server re-pushes identical slots (five identical ``/mgr/nodes``
    lists on a box reboot, a double ``/connected`` true); the store's
    exact-equality dedup drops those while every DISTINCT value still
    notifies, so this is noise reduction rather than frame filtering.
    """
    engine, recorder = make_engine()
    await engine.handle_event(snapshot(home(dev("d1", base_dev_data()))))
    before = engine.node("d1", "htr/2")
    assert before is not None
    recorder.changes.clear()

    await engine.handle_event(
        Update(dev_id="d1", path="/htr/2/status", body=dict(STATUS))
    )
    assert recorder.changes == []
    assert engine.node("d1", "htr/2") is before  # not rebuilt
    assert before.revision == 1

    # a repeated node-list frame with the same membership is silent too
    await engine.handle_event(
        Update(
            dev_id="d1",
            path="/mgr/nodes",
            body={"nodes": [node_entry(status=STATUS, setup=SETUP)]},
        )
    )
    assert recorder.changes == []

    # a distinct value still notifies, bumping the revision once
    await engine.handle_event(
        Update(dev_id="d1", path="/htr/2/status", body={**STATUS, "mode": "manual"})
    )
    assert [change.kind for change in recorder.changes] == ["status"]
    assert engine.node("d1", "htr/2").revision == 2


# --- malformed frames ---


async def test_malformed_and_unknown_frames_ignored_pump_alive(caplog):
    engine, recorder = make_engine()
    await engine.handle_event(snapshot(home(dev("d1", {"connected": True}))))
    recorder.changes.clear()
    with caplog.at_level(logging.DEBUG):
        await engine.handle_event(
            Update(dev_id="", path="/connected", body={"connected": False})
        )
        await engine.handle_event(
            Update(dev_id="d1", path="/connected", body=["not", "a", "dict"])
        )
        await engine.handle_event(Update(dev_id="d1", path="/connected", body={}))
        await engine.handle_event(
            Update(dev_id="d1", path="/mgr/nodes", body={"nodes": "nope"})
        )
        await engine.handle_event(
            Update(dev_id="d1", path="/mgr/nodes", body={"nodes": ["junk"]})
        )
        await engine.handle_event(
            Update(dev_id="d1", path="/mgr/nodes", body={"nodes": [{"type": "htr"}]})
        )
        await engine.handle_event(
            Update(dev_id="d1", path="/htr_system/setup", body={"x": 1})
        )
        await engine.handle_event(Update(dev_id="d1", path="", body={}))
        # the pump is alive: a good frame after the junk still merges
        await engine.handle_event(
            Update(dev_id="d1", path="/connected", body={"connected": False})
        )
    assert [c.kind for c in recorder.changes] == ["connected"]
    assert engine.device("d1").connected is False


# --- subscriber API ---


async def test_multiple_subscribers_all_notified_in_order():
    engine = SmartboxEngine()
    a, b = Recorder(), Recorder()
    engine.subscribe(a)
    engine.subscribe(b)
    await engine.handle_event(snapshot(home(dev("d1", {"connected": True}))))
    assert a.changes
    assert b.changes
    assert [(type(c).__name__, c.kind) for c in a.changes] == [
        (type(c).__name__, c.kind) for c in b.changes
    ]


async def test_handler_error_isolated(caplog):
    engine = SmartboxEngine()

    boom = RuntimeError("boom")

    async def bad(_change):
        raise boom

    good = Recorder()
    engine.subscribe(bad)
    engine.subscribe(good)
    with caplog.at_level(logging.ERROR):
        await engine.handle_event(snapshot(home(dev("d1", {"connected": True}))))
        await engine.handle_event(
            Update(dev_id="d1", path="/connected", body={"connected": False})
        )
    # the raising handler stays subscribed; the good one gets every change
    assert [c.kind for c in good.changes] == ["connected", "connected"]
    assert any("Change subscriber raised" in r.message for r in caplog.records)


async def test_socket_closed_leaves_state_and_opens_the_grace_window():
    """A close never touches the store; it opens the feed grace window.

    Core used to treat ``SocketClosed`` as purely informational (a DEBUG line
    and nothing else): it starts the window that decides whether the feed
    counts as down, while the store stays untouched.
    """
    engine, recorder = make_engine()
    try:
        await engine.handle_event(SocketClosed(code=1006))
        assert recorder.changes == []
        assert engine.world.devices == {}
        assert engine.feed.available is True  # inside the window
        assert engine.feed.window_pending is True
    finally:
        await engine.stop()


# --- copy-on-write / immutability ---


async def test_old_revisions_immutable_after_later_merges():
    engine, _ = make_engine()
    await engine.handle_event(
        snapshot(
            home(dev("d1", {"connected": True, "nodes": [node_entry(status=STATUS)]}))
        )
    )
    old_device = engine.device("d1")
    old_node = old_device.node_states["htr/2"]
    old_world = engine.world
    await engine.handle_event(
        Update(dev_id="d1", path="/htr/2/status", body={"mode": "heat"})
    )
    await engine.handle_event(
        Update(dev_id="d1", path="/connected", body={"connected": False})
    )
    await engine.handle_event(
        Update(
            dev_id="d1",
            path="/mgr/nodes",
            body={"nodes": [{"type": "htr", "addr": "9"}]},
        )
    )
    # every captured earlier-revision object is untouched
    assert old_device.revision == 1
    assert old_device.connected is True
    assert old_node.status == STATUS
    assert old_node.revision == 1
    assert old_world.devices["d1"] is old_device
    # ...while the current revisions moved on; htr/2 was dropped by the
    # authoritative /mgr/nodes list, so only htr/9 remains in the map
    assert engine.device("d1").revision == 3  # snapshot + connected + nodes
    assert engine.device("d1").connected is False
    assert set(engine.device("d1").node_states) == {"htr/9"}
    # the status merge itself was real and ordered before the removal


# --- FakeSmartbox composition (the integration test bed) ---


async def test_fake_snapshot_agrees_with_engine_ingestion():
    fake = FakeSmartbox()
    fake.add_device("dev1", connected=True, power_limit=2000)
    fake.add_node(
        "dev1",
        NODE_REF,
        status={"stemp": "20.0", "sync_status": "ok"},
        setup={"priority": 0},
        prog={"0": [2, 2]},
    )
    engine, recorder = make_engine()
    await engine.handle_event(Snapshot(payload=fake.snapshot_payload()))
    device = engine.device("dev1")
    assert device.connected is True
    assert device.away == {"away": False, "enabled": False, "forced": False}
    assert device.power_limit == 2000
    assert device.home_id == "fake_home"
    node = engine.node("dev1", "htr/2")
    assert node.status == {"stemp": "20.0", "sync_status": "ok"}
    assert node.setup == {"priority": 0}
    assert node.prog == {"prog": {"0": [2, 2]}}
    recorder.changes.clear()
    await engine.handle_event(Snapshot(payload=fake.snapshot_payload()))
    assert recorder.changes == []  # resnapshot of an unchanged world absorbs


async def test_fake_write_then_snapshot_notifies_status_change():
    fake = FakeSmartbox()
    fake.add_device("dev1")
    fake.add_node("dev1", NODE_REF, status={"stemp": "20.0", "sync_status": "ok"})
    engine, recorder = make_engine()
    await engine.handle_event(Snapshot(payload=fake.snapshot_payload()))
    await fake.writes.set_setpoint("dev1", NODE_REF, 21.3, "C")
    recorder.changes.clear()
    await engine.handle_event(Snapshot(payload=fake.snapshot_payload()))
    assert [c.kind for c in recorder.changes] == ["status"]
    assert engine.node("dev1", "htr/2").status["stemp"] == "21.0"


# --- consumer frame rules (docs/api-ws-user.md §1.3/§1.4) ---


async def test_empty_body_frame_leaves_the_stored_slots_alone():
    """A frame applies only when path AND body are truthy (api-ws-user §1.4)."""
    engine, recorder = make_engine()
    await engine.handle_event(snapshot(home(dev("d1", base_dev_data()))))
    recorder.changes.clear()

    await engine.handle_event(Update(dev_id="d1", path="/htr/2/status", body={}))

    assert engine.node("d1", "htr/2").status == STATUS
    assert recorder.changes == []


async def test_empty_body_frame_is_not_reachability_evidence(engine_clock, monkeypatch):
    """The empty body must not flip an Unavailable node back to available."""
    monkeypatch.setattr(timing, "sleep", _InstantSleep(engine_clock))
    engine, recorder = make_engine()
    await engine.handle_event(snapshot(home(dev("d1", base_dev_data()))))
    await engine.handle_event(
        Update(dev_id="d1", path="/htr/2/status", body={"sync_status": "lost"})
    )
    await until(lambda: engine.node("d1", "htr/2").available is False)
    assert engine.node("d1", "htr/2").available is False
    recorder.changes.clear()

    await engine.handle_event(Update(dev_id="d1", path="/htr/2/status", body={}))

    assert engine.node("d1", "htr/2").available is False
    assert recorder.changes == []


async def test_frames_for_unknown_devices_and_nodes_are_ignored():
    """The snapshot and /mgr/nodes create store entries; frames only update."""
    engine, recorder = make_engine()
    await engine.handle_event(snapshot(home(dev("d1", base_dev_data()))))
    recorder.changes.clear()

    await engine.handle_event(
        Update(dev_id="ghost", path="/connected", body={"connected": True})
    )
    await engine.handle_event(
        Update(dev_id="d1", path="/htr/9/status", body={"mtemp": "1.0"})
    )

    assert set(engine.world.devices) == {"d1"}
    assert set(engine.device("d1").node_states) == {"htr/2"}
    assert recorder.changes == []


async def test_scalar_power_limit_body_is_coerced():
    """The box power-limit frame may carry the bare digit string (api-ws-user §1.4)."""
    engine, recorder = make_engine()
    await engine.handle_event(snapshot(home(dev("d1", base_dev_data()))))
    recorder.changes.clear()

    await engine.handle_event(
        Update(dev_id="d1", path="/htr_system/power_limit", body="2500")
    )

    assert engine.device("d1").power_limit == 2500
    assert [c.kind for c in recorder.changes] == ["power_limit"]


async def test_scalar_body_on_any_other_path_is_ignored():
    """Only the power-limit path may carry a scalar; the rest warn and drop."""
    engine, recorder = make_engine()
    await engine.handle_event(snapshot(home(dev("d1", base_dev_data()))))
    recorder.changes.clear()

    await engine.handle_event(Update(dev_id="d1", path="/connected", body="yes"))
    await engine.handle_event(Update(dev_id="d1", path="/htr/2/status", body="nope"))

    assert engine.device("d1").connected is True
    assert engine.node("d1", "htr/2").status == STATUS
    assert recorder.changes == []


# --- the box limit's int copy in htr_system/setup frames (live 2026-10-18) ---


async def test_htr_system_setup_frame_updates_the_power_limit():
    """A "0" clear pushes ONLY this frame, so its key feeds the same slot."""
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(
            home(dev("d1", {"htr_system": {"power_limit": {"power_limit": "2500"}}}))
        )
    )
    recorder.changes.clear()

    await engine.handle_event(
        Update(
            dev_id="d1",
            path="/htr_system/setup",
            body={"power_limit": 0, "refresh_period": 30},
        )
    )

    assert engine.device("d1").power_limit == 0
    assert [c.kind for c in recorder.changes] == ["power_limit"]


async def test_htr_system_setup_frame_without_the_key_is_a_no_op(caplog):
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(
            home(dev("d1", {"htr_system": {"power_limit": {"power_limit": "2500"}}}))
        )
    )
    recorder.changes.clear()
    with caplog.at_level(logging.WARNING):
        await engine.handle_event(
            Update(dev_id="d1", path="/htr_system/setup", body={"refresh_period": 30})
        )
    assert engine.device("d1").power_limit == 2500
    assert recorder.changes == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_snapshot_non_numeric_addr_is_ignored_without_raising():
    """A malformed address is rejected at parse time, not in NodeAdded."""
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(
            home(
                dev(
                    "d1",
                    {"nodes": [{"type": "htr", "addr": "not-a-number"}]},
                )
            )
        )
    )
    assert not any(isinstance(change, NodeAdded) for change in recorder.changes)
    assert engine.device("d1").node_states == {}


async def test_mgr_nodes_frame_reports_named_addition_despite_junk():
    """A partially parsed update still announces the valid new node."""
    engine, recorder = make_engine()
    await engine.handle_event(
        snapshot(home(dev("d1", {"nodes": [node_entry(addr=2)]})))
    )
    recorder.changes.clear()
    await engine.handle_event(
        Update(
            dev_id="d1",
            path="/mgr/nodes",
            body={
                "nodes": [
                    {"type": "htr", "addr": 3, "name": "Hall"},
                    "junk",
                ]
            },
        )
    )
    added = [change for change in recorder.changes if isinstance(change, NodeAdded)]
    assert added == [NodeAdded("d1", "htr/3", "Hall")]
    assert set(engine.device("d1").node_states) == {"htr/2", "htr/3"}
