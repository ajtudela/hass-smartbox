"""Identity-discovery error contract (``identity.async_discover_identity``).

Plain pytest with an AsyncMock client: pins what discovery does with
drifted/malformed payloads (exact exception TYPES, never messages) and
that protocol-layer errors propagate RAW for setup to map.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from smartbox.protocol.errors import AuthError, SmartboxError, TransportUnavailableError
from smartbox.protocol.values import BoostConfig

from custom_components.smartbox.const import SmartboxNodeType
from custom_components.smartbox.identity import (
    SmartboxDeviceInfo,
    SmartboxNodeInfo,
    async_discover_identity,
    async_discover_membership,
    boost_available,
    boost_config,
    duty_limit_options,
    duty_limit_value,
    node_power_limit_available,
    priority_available,
)

HOME = {"id": "h1", "devs": [{"dev_id": "dev1", "name": "Box"}]}
NODE = {"node_type": "htr", "type": "htr", "addr": 2, "name": "Heater"}
PMO_NODE = {"node_type": "pmo", "type": "pmo", "addr": 9, "name": "PMO"}
UNINSTALLED_NODE = {
    "node_type": "htr_mod",
    "type": "htr_mod",
    "addr": 5,
    "name": "",
    "installed": False,
}

STATUS = {"mtemp": "190", "units": "C"}
SETUP = {"mode": "auto"}
VERSION = {"pid": "081c", "hw_version": "1.0"}


def make_client(
    homes: object = None,
    nodes: object = None,
) -> AsyncMock:
    """AsyncMock SmartboxClient serving one home + the given nodes list."""
    client = AsyncMock()
    client.get_grouped_devices.return_value = [HOME] if homes is None else homes
    client.get_power_limit.return_value = {"power_limit": "0"}
    client.get_dev_data.return_value = {
        "nodes": [],
        "connected": True,
        "away_status": {},
        # Boxed with the limit cleared: setup.power_limit stays 0.
        "htr_system": {"power_limit": {}, "setup": {"power_limit": 0}},
    }
    client.get_nodes.return_value = [NODE, PMO_NODE] if nodes is None else nodes
    client.get_node_status.return_value = STATUS
    client.get_node_setup.return_value = SETUP
    client.get_node_version.return_value = VERSION
    return client


# --- happy path anchor + the PMO status skip ---


async def test_discovery_reads_all_node_facts():
    client = make_client()
    identity = await async_discover_identity(client, None)
    assert len(identity.devices) == 1
    assert len(identity.nodes) == 2
    assert identity.complete is True
    assert identity.devices[0].box_power_limit_available is True
    client.get_power_limit.assert_awaited_once_with("dev1")
    node = identity.nodes[0]
    assert isinstance(node, SmartboxNodeInfo)
    assert isinstance(node.device, SmartboxDeviceInfo)
    assert node.status == STATUS
    assert node.setup == SETUP
    assert node.version == VERSION
    assert client.get_node_status.call_count == 1
    assert client.get_node_setup.call_count == 2
    assert client.get_node_version.call_count == 2


async def test_pmo_node_skips_the_status_read():
    client = make_client()
    await async_discover_identity(client, None)
    called_addrs = [
        call.args[1].addr for call in client.get_node_status.await_args_list
    ]
    assert called_addrs == ["2"]  # PMO (addr 9) never status-polled
    pmo = (await async_discover_identity(client, None)).nodes[1]
    assert pmo.node_type == SmartboxNodeType.PMO
    assert pmo.status == {}


async def test_unknown_wire_type_skips_reads_and_warns(caplog):
    """A product category is not a wire type: do not probe its paths."""
    unknown = {
        "node_type": "towel_rail_heater",
        "type": "towel_rail_heater",
        "addr": 11,
        "name": "Towel rail",
    }
    client = make_client(nodes=[unknown])
    identity = await async_discover_identity(client, None)

    node = identity.nodes[0]
    assert node.supported_wire_type is False
    assert node.status == {}
    assert node.setup == {}
    assert node.version == {}
    assert client.get_node_status.await_count == 0
    assert client.get_node_setup.await_count == 0
    assert client.get_node_version.await_count == 0
    assert "wire node type" in caplog.text


async def test_discovery_marks_a_device_without_box_power_limit():
    """A rejected probe plus a boxless snapshot means no box power limit.

    The payload is the REST shape: no ``connected`` key, which that endpoint
    never serves (docs/api-contract.md §3.2 E46).
    """
    client = make_client()
    client.get_power_limit.side_effect = SmartboxError("not found")
    client.get_dev_data.return_value = {
        "nodes": [],
        "away_status": {},
    }

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is False


async def test_snapshot_with_connected_still_confirms_absence():
    """The ws snapshot's extra ``connected`` key is tolerated, not required."""
    client = make_client()
    client.get_power_limit.side_effect = SmartboxError("not found")
    client.get_dev_data.return_value = {
        "nodes": [],
        "connected": True,
        "away_status": {},
    }

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is False


async def test_payload_without_away_status_keeps_the_surface():
    """A truncated payload must not confirm absence: both keys are needed."""
    client = make_client()
    client.get_power_limit.side_effect = SmartboxError("not found")
    client.get_dev_data.return_value = {"nodes": []}

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is True


async def test_empty_htr_system_confirms_absence():
    """An empty htr_system counts as absent (pinned behaviour)."""
    client = make_client()
    client.get_power_limit.side_effect = SmartboxError("not found")
    client.get_dev_data.return_value = {
        "nodes": [],
        "away_status": {},
        "htr_system": {},
    }

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is False


@pytest.mark.parametrize(
    "htr_system",
    [
        # The boxless HJM shape: a placeholder object with no setup copy.
        {"power_limit": {}},
        {"power_limit": {"power_limit": ""}, "setup": {}},
    ],
)
async def test_htr_system_without_a_limit_confirms_absence(htr_system):
    """A non-empty htr_system with no limit configured is still boxless."""
    client = make_client()
    client.get_power_limit.side_effect = SmartboxError("not found")
    client.get_dev_data.return_value = {
        "nodes": [],
        "away_status": {},
        "htr_system": htr_system,
    }

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is False


@pytest.mark.parametrize(
    "htr_system",
    [
        # A box whose limit was cleared keeps setup.power_limit: 0.
        {"power_limit": {}, "setup": {"power_limit": 0}},
        {"setup": {"power_limit": 0}},
        {"power_limit": {"power_limit": 0}},
        {"power_limit": {"power_limit": "1500"}},
    ],
)
async def test_configured_box_power_limit_keeps_the_surface(htr_system):
    """Any readable limit copy marks a real box, including a cleared limit."""
    client = make_client()
    client.get_power_limit.side_effect = SmartboxError("forbidden")
    client.get_dev_data.return_value = {
        "nodes": [],
        "connected": True,
        "away_status": {},
        "htr_system": htr_system,
    }

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is True


async def test_discovery_probes_the_box_while_reading_nodes():
    """The capability probe and the nodes read share one round trip."""
    client = make_client()
    gate = asyncio.Event()

    async def get_power_limit(_dev_id):
        await asyncio.wait_for(gate.wait(), timeout=1.0)
        return {"power_limit": "0"}

    async def get_nodes(_dev_id):
        gate.set()
        return [NODE, PMO_NODE]

    client.get_power_limit.side_effect = get_power_limit
    client.get_nodes.side_effect = get_nodes

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is True


async def test_discovery_keeps_box_power_limit_when_snapshot_has_htr_system():
    """A broad 4xx on a boxed device does not remove its power limit."""
    client = make_client()
    client.get_power_limit.side_effect = SmartboxError("forbidden")

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is True


@pytest.mark.parametrize(
    "dev_data",
    [
        {},
        [],
        {"devData": {"nodes": [], "away_status": {}}},
        {"nodes": [], "htr_system": "drifted"},
        # both snapshot keys present, but htr_system is not a mapping
        {"nodes": [], "away_status": {}, "htr_system": "drifted"},
        {"nodes": []},  # missing away_status: a truncated snapshot
        {"away_status": {}},  # missing nodes: a truncated snapshot
        {"nodes": None, "away_status": {}},  # ill-typed nodes
        {"nodes": [], "away_status": None},  # ill-typed away_status
        # A present but unreadable limit copy is a box with drifted data.
        {"nodes": [], "away_status": {}, "htr_system": {"power_limit": "abc"}},
        {"nodes": [], "away_status": {}, "htr_system": {"power_limit": -5}},
    ],
)
async def test_discovery_keeps_box_power_limit_when_confirmation_is_inconclusive(
    dev_data,
):
    """Only a snapshot-shaped, htr_system-free payload confirms absence."""
    client = make_client()
    client.get_power_limit.return_value = {}
    client.get_dev_data.return_value = dev_data

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is True


@pytest.mark.parametrize("error", [SmartboxError("no data"), OSError("disk")])
async def test_confirmation_read_failure_keeps_box_power_limit(error):
    """A failed confirmation read keeps the existing power-limit surface."""
    client = make_client()
    client.get_power_limit.side_effect = SmartboxError("not found")
    client.get_dev_data.side_effect = error

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is True


async def test_empty_success_body_uses_snapshot_confirmation():
    """A 2xx without power_limit is not absent until the snapshot agrees."""
    client = make_client()
    client.get_power_limit.return_value = {}
    client.get_dev_data.return_value = {
        "nodes": [],
        "away_status": {},
    }

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is False


@pytest.mark.parametrize("value", [None, "", {}, []])
async def test_value_less_success_body_uses_snapshot_confirmation(value):
    """A 2xx with an unusable power_limit is not positive evidence."""
    client = make_client()
    client.get_power_limit.return_value = {"power_limit": value}
    client.get_dev_data.return_value = {
        "nodes": [],
        "away_status": {},
    }

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is False


@pytest.mark.parametrize("payload", [None, []])
async def test_non_mapping_success_body_uses_snapshot_confirmation(payload):
    """A 2xx whose body is not a mapping is not positive evidence."""
    client = make_client()
    client.get_power_limit.return_value = payload
    client.get_dev_data.return_value = {
        "nodes": [],
        "away_status": {},
    }

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is False


@pytest.mark.parametrize("value", [0, "0", "1500"])
async def test_real_power_limit_value_skips_confirmation(value):
    """Zero is a valid 'no limit' value and stays positive."""
    client = make_client()
    client.get_power_limit.return_value = {"power_limit": value}

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is True
    client.get_dev_data.assert_not_awaited()


@pytest.mark.parametrize(
    "error", [AuthError("auth"), TransportUnavailableError("network")]
)
async def test_discovery_keeps_box_power_limit_when_the_probe_is_inconclusive(error):
    """Auth or transport failures keep the existing box-power-limit surface."""
    client = make_client()
    client.get_power_limit.side_effect = error

    identity = await async_discover_identity(client, None)

    assert identity.devices[0].box_power_limit_available is True
    client.get_dev_data.assert_not_awaited()


async def test_discovery_skips_uninstalled_slots():
    """An empty slot is not a node; discovery must not read it."""
    client = make_client(nodes=[UNINSTALLED_NODE, NODE])

    identity = await async_discover_identity(client, None)

    assert [node.node_id for node in identity.nodes] == ["dev1_2"]
    assert identity.complete is True
    assert client.get_node_status.call_count == 1
    assert client.get_node_setup.call_count == 1
    assert client.get_node_version.call_count == 1


# --- drifted payloads: exact exception types ---


@pytest.mark.parametrize("homes", [{"unexpected": 1}, "nope", 42])
async def test_grouped_devices_not_a_list_raises_smartbox_error(homes):
    client = make_client(homes=homes)
    with pytest.raises(SmartboxError):
        await async_discover_identity(client, None)


@pytest.mark.parametrize(
    ("dev", "expect"),
    [
        ({}, "error"),  # missing dev_id -> SmartboxError
        ({"dev_id": 5}, "error"),  # non-string dev_id -> SmartboxError
        ({"dev_id": ""}, "error"),  # blank dev_id -> SmartboxError
        ("not-a-mapping", "skip"),  # non-mapping entry skipped
    ],
)
async def test_drifted_dev_entries(dev, expect):
    client = make_client(homes=[{"id": "h1", "devs": [dev]}])
    if expect == "error":
        with pytest.raises(SmartboxError):
            await async_discover_identity(client, None)
    else:
        identity = await async_discover_identity(client, None)
        assert identity.devices == ()


async def test_home_without_id_is_skipped():
    client = make_client(homes=[{"no_id": 1}, {"id": "h2", "devs": []}])
    identity = await async_discover_identity(client, None)
    assert identity.devices == ()
    assert client.get_nodes.await_count == 0


async def test_nodes_payload_missing_the_nodes_key_is_defensive():
    # a raw (un-unwrapped) nodes payload: non-Mapping entries are skipped,
    # never a crash from discovery itself
    client = make_client(nodes={"nope": []})
    identity = await async_discover_identity(client, None)
    assert identity.nodes == ()


@pytest.mark.parametrize("slot", ["status", "setup", "version"])
async def test_a_non_object_node_read_fails_closed(slot):
    """A successful read that is not a JSON object is a bounded failure."""
    client = make_client()
    getattr(client, f"get_node_{slot}").return_value = ["not", "an", "object"]
    with pytest.raises(SmartboxError, match=rf"{slot} payload"):
        await async_discover_identity(client, None)


async def test_a_home_with_an_ill_typed_devs_list_fails_closed():
    """A home whose devs is not a list is not silently treated as empty."""
    client = make_client(homes=[{"id": "h1", "devs": None}])
    with pytest.raises(SmartboxError, match="ill-typed devs list"):
        await async_discover_identity(client, None)


async def test_ill_typed_factory_options_do_not_raise():
    """A null/ill-typed factory_options object reads as no options."""
    client = make_client()
    client.get_node_setup.return_value = {"factory_options": None}
    node = (await async_discover_identity(client, None)).nodes[0]
    assert node.boost_available is False


def test_boost_config_uses_the_app_enum():
    """The vendor app's 0/1/2/3 boost_config values are the integration's."""
    assert [int(c) for c in BoostConfig] == [0, 1, 2, 3]
    assert boost_config({"factory_options": {"boost_config": 3}}) == 3
    assert boost_available({"factory_options": {"boost_config": 3}}) is True


@pytest.mark.parametrize(
    "entry",
    [
        {k: v for k, v in NODE.items() if k != "type"},
        {k: v for k, v in NODE.items() if k != "addr"},
        {**NODE, "type": ""},
        {**NODE, "type": 7},
        {**NODE, "addr": None},
        {**NODE, "addr": True},  # a bool is not an address
        {**NODE, "addr": "²"},  # isdigit but not int()-safe
        {**NODE, "addr": "03"},  # leading zero would collide with addr 3
    ],
)
async def test_node_entry_drift_is_skipped_with_a_bounded_warning(entry, caplog):
    """One malformed node entry is skipped instead of hiding the account."""
    client = make_client(nodes=[entry])
    identity = await async_discover_identity(client, None)
    assert identity.nodes == ()
    assert "Skipping malformed node" in caplog.text
    assert "keys=" in caplog.text
    assert all(len(record.message) < 200 for record in caplog.records)


async def test_node_entry_without_a_name_falls_back_to_the_device_name():
    """A name-less node entry must not raise during platform setup.

    The identity's ``name`` feeds the entity's device_info; the device name
    is a safe fallback, where indexing the key would take setup down with a
    bare KeyError.
    """
    client = make_client(nodes=[{"type": "htr", "addr": 2}])
    node = (await async_discover_identity(client, None)).nodes[0]
    assert node.name == "Box"


async def test_get_model_code_from_the_pid():
    """The model code is PID characters 3-4, uppercased (app rule)."""
    for version, expected in (
        ({"pid": "081c", "hw_version": "1.0"}, "1C"),
        ({"pid": "0217"}, "17"),
        ({"pid": "12345"}, "34"),
        ({"pid": "ab"}, None),
        ({"pid": "x"}, None),
        ({"pid": ""}, None),
        ({}, None),
    ):
        client = make_client()
        client.get_node_version.return_value = version
        node = (await async_discover_identity(client, None)).nodes[0]
        assert node.pid == version.get("pid")
        assert node.hw_version == version.get("hw_version")
        assert node.get_model_code() == expected


async def test_get_model_code_uses_the_given_version_payload():
    """A consumer's live version slot overrides the discovery copy."""
    client = make_client()
    client.get_node_version.return_value = {"pid": "0217"}
    node = (await async_discover_identity(client, None)).nodes[0]
    assert node.get_model_code() == "17"
    assert node.get_model_code({"pid": "081c"}) == "1C"
    assert node.get_model_code({}) == "17"  # empty live slot -> discovery
    assert node.get_model_code({"pid": "1C"}) is None  # short PID


# --- protocol-layer errors propagate RAW ---


@pytest.mark.parametrize(
    ("seam", "error"),
    [
        ("get_grouped_devices", TransportUnavailableError("down")),
        ("get_nodes", SmartboxError("no nodes")),
        ("get_node_status", SmartboxError("bad status")),
        ("get_node_setup", SmartboxError("bad setup")),
        ("get_node_version", SmartboxError("bad version")),
    ],
)
async def test_protocol_errors_propagate_raw(seam, error):
    client = make_client()
    getattr(client, seam).side_effect = error
    with pytest.raises(type(error)):
        await async_discover_identity(client, None)


async def test_priority_gate_requires_a_box():
    """The app hides radiator priority on a WiFi heater; the gate agrees."""
    client = make_client()
    client.get_node_setup.side_effect = [{"priority": "low"}, {}]
    identity = await async_discover_identity(client, None)
    htr = next(
        node for node in identity.nodes if node.node_type == SmartboxNodeType.HTR
    )
    assert priority_available(htr) is True

    boxless = make_client()
    boxless.get_power_limit.side_effect = SmartboxError("not found")
    boxless.get_dev_data.return_value = {"nodes": [], "away_status": {}}
    boxless.get_node_setup.side_effect = [{"priority": "low"}, {}]
    identity = await async_discover_identity(boxless, None)
    htr = next(
        node for node in identity.nodes if node.node_type == SmartboxNodeType.HTR
    )
    assert identity.devices[0].box_power_limit_available is False
    assert priority_available(htr) is False


def test_duty_limit_options_keeps_only_usable_presets():
    """Preset values must parse and stay distinct; a missing factory yields OFF."""
    base = {"power": "2000.0", "extra_options": {"user_duty_limit": 0}}

    unusable = {
        "lst_available": True,
        "lst_value": "abc",
        "bbc_available": True,
        "bbc_value": 0,
    }
    assert duty_limit_options({**base, "factory_options": unusable}) == (("Off", 0),)

    duplicate = {
        "lst_available": True,
        "lst_value": 50,
        "bbc_available": True,
        "bbc_value": 50,
    }
    assert duty_limit_options({**base, "factory_options": duplicate}) == (
        ("Off", 0),
        ("LST", 50),
    )

    assert duty_limit_options(dict(base)) == (("Off", 0),)
    assert duty_limit_options({**base, "power": 0}) == ()
    assert duty_limit_options({"extra_options": {"user_duty_limit": 0}}) == ()
    assert duty_limit_options({}) == ()

    assert duty_limit_value({"extra_options": {"user_duty_limit": "25"}}) == 25
    assert duty_limit_value({"extra_options": {"user_duty_limit": "abc"}}) is None
    assert duty_limit_value({"extra_options": {}}) is None
    assert duty_limit_value({"extra_options": 5}) is None


async def test_node_power_limit_gate_reads_the_setup_key():
    """The gate is the setup key the wire carries (observed on pmo nodes)."""
    client = make_client()
    client.get_node_setup.side_effect = [
        {"priority": "low"},  # a heater's setup does not carry the key
        {"power_limit": 1500},  # the pmo node's setup carries it
    ]
    identity = await async_discover_identity(client, None)
    by_type = {node.node_type: node for node in identity.nodes}
    assert node_power_limit_available(by_type[SmartboxNodeType.PMO]) is True
    assert node_power_limit_available(by_type[SmartboxNodeType.HTR]) is False


async def test_numeric_string_addr_is_canonicalized():
    """A numeric string address keeps the integer registry identity."""
    client = make_client(nodes=[{**NODE, "addr": "2"}])
    node = (await async_discover_identity(client, None)).nodes[0]
    assert node.addr == 2
    assert node.node_id == "dev1_2"


async def test_discovery_is_incomplete_when_a_node_entry_is_skipped():
    """A skipped entry blocks setup-time removal reconciliation."""
    client = make_client(nodes=[NODE, {"type": "htr"}])

    identity = await async_discover_identity(client, None)

    assert identity.complete is False
    assert len(identity.nodes) == 1


async def test_discovery_is_incomplete_when_a_home_is_skipped():
    client = make_client(homes=[HOME, {"devs": []}])

    identity = await async_discover_identity(client, None)

    assert identity.complete is False
    assert len(identity.devices) == 1


# --- membership read (removal confirmation) ---


async def test_membership_reads_boxes_node_ids_and_raw_lists():
    client = make_client()

    membership = await async_discover_membership(client)

    assert membership.dev_ids == frozenset({"dev1"})
    assert membership.node_ids == frozenset({"dev1_2", "dev1_9"})
    assert membership.unconfirmed_dev_ids == frozenset()
    assert membership.node_keys == {"dev1_2": "htr/2", "dev1_9": "pmo/9"}
    assert membership.raw_nodes == {"dev1": (NODE, PMO_NODE)}


async def test_membership_ignores_uninstalled_slots():
    """An empty slot is absent from membership, so it cannot block removal."""
    client = make_client(nodes=[UNINSTALLED_NODE, NODE, PMO_NODE])

    membership = await async_discover_membership(client)

    assert membership.node_ids == frozenset({"dev1_2", "dev1_9"})
    assert membership.unconfirmed_dev_ids == frozenset()


async def test_membership_marks_a_box_unconfirmed_for_a_malformed_node():
    """A list we could not fully parse never proves one of its nodes gone."""
    client = make_client(nodes=[NODE, {"type": "htr"}])

    membership = await async_discover_membership(client)

    assert membership.dev_ids == frozenset({"dev1"})
    assert membership.node_ids == frozenset({"dev1_2"})
    assert membership.unconfirmed_dev_ids == frozenset({"dev1"})


async def test_membership_marks_a_box_unconfirmed_for_a_non_dict_node():
    client = make_client(nodes=[NODE, "junk"])

    membership = await async_discover_membership(client)

    assert membership.node_ids == frozenset({"dev1_2"})
    assert membership.unconfirmed_dev_ids == frozenset({"dev1"})


@pytest.mark.parametrize(
    "homes",
    [
        "junk",  # not a list at all
        [{"devs": []}],  # home without an id
        ["junk"],  # home not a mapping
        [{"id": "h1", "devs": "junk"}],  # ill-typed devs list
        [{"id": "h1", "devs": ["junk"]}],  # dev not a mapping
        [{"id": "h1", "devs": [{"name": "no id"}]}],  # dev without dev_id
    ],
)
async def test_membership_rejects_an_unreadable_account(homes):
    """An unreadable account entry raises rather than looking like an absence."""
    with pytest.raises(SmartboxError):
        await async_discover_membership(make_client(homes=homes))


async def test_membership_propagates_a_nodes_read_error():
    client = make_client()
    client.get_nodes.side_effect = TransportUnavailableError("down")

    with pytest.raises(TransportUnavailableError):
        await async_discover_membership(client)
