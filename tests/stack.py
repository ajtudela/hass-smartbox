"""Stack-seeding helpers shared by the platform test suites.

The harness delivers the mock world to the engine as a ws snapshot
through the runtime's FakeTransport (``seed_stack``) and lets tests
deliver the confirming frames a live device pushes after accepted
writes (``confirm_write`` / ``confirm_setup_write`` / the setup
delta ``push_setup_update`` / raw ``push_status`` frames). The
FakeSmartbox models the SERVER side only: the fixture routes the
client's POSTs to it (see ``build_fake_world`` and conftest), so writes
mutate its state and are asserted through ``fake.requests`` - the fake
pushes no frames itself, so tests deliver the confirming ones
explicitly.
"""

from __future__ import annotations

from smartbox.protocol.client import NodeRef
from smartbox.protocol.values import is_lost

from custom_components.smartbox.const import DOMAIN, SmartboxNodeType
from tests.fake_api import FakeSmartbox


def ref(node_info) -> NodeRef:
    """NodeRef for a mock node-info dict."""
    return NodeRef(node_info["type"], str(node_info["addr"]))


def status_path(node_info) -> str:
    return f"/{node_info['type']}/{node_info['addr']}/status"


async def build_fake_world(mock_smartbox):
    """Build the FakeSmartbox server state from the mock world.

    The fixture owns this so the client's POST routing exists before any
    test writes; ``seed_stack`` then delivers the fake's snapshot to the
    runtime the fixture's seams built.
    """
    fake = FakeSmartbox()
    for device in await mock_smartbox.session.get_devices():
        dev_id = device["dev_id"]
        fake.add_device(
            dev_id,
            away=await mock_smartbox.session.get_device_away_status(dev_id),
            power_limit=await mock_smartbox.session.get_device_power_limit(dev_id),
        )
        for node_info in await mock_smartbox.session.get_nodes(dev_id):
            fake.add_node(
                dev_id,
                ref(node_info),
                status=await mock_smartbox.session.get_status(dev_id, node_info),
                setup=await mock_smartbox.session.get_node_setup(dev_id, node_info),
                prog=(await mock_smartbox.session.get_node_prog(dev_id, node_info))[
                    "prog"
                ]
                if node_info["type"] != SmartboxNodeType.PMO
                else None,
                version=await mock_smartbox.session.get_node_version(dev_id, node_info),
            )
    return fake


async def seed_stack(hass, mock_smartbox):
    """Deliver the mock world to the engine as the ws snapshot.

    Returns the runtime and the fixture's FakeSmartbox, which the client's
    POSTs are routed to.
    """
    runtime = hass.config_entries.async_entries(DOMAIN)[0].runtime_data
    fake = await build_fake_world(mock_smartbox)
    mock_smartbox.write_router.host = fake
    await runtime.transport.deliver_snapshot(fake.snapshot_payload())
    # Snapshots leave the availability slot unknown by design; a live
    # device's first ok push marks it available, so deliver that push.
    for device in await mock_smartbox.session.get_devices():
        dev_id = device["dev_id"]
        for node_info in await mock_smartbox.session.get_nodes(dev_id):
            body = await fake.get_node_status(dev_id, ref(node_info))
            if not is_lost(body):
                await runtime.transport.deliver_update(
                    dev_id, status_path(node_info), body
                )
    await hass.async_block_till_done()
    return runtime, fake


async def push_status(hass, runtime, dev_id, node_info, body) -> None:
    await runtime.transport.deliver_update(dev_id, status_path(node_info), body)
    await hass.async_block_till_done()


async def confirm_write(hass, runtime, fake, dev_id, node_info) -> None:
    """Deliver the ok status frame that confirms a write (live behavior).

    The core arms a pending-write confirmation per write; without the
    confirming frame the window expires into Unavailable - exactly what
    a live unreachable node does. A live reachable device pushes this
    frame; the fake (state-only) does not, so the tests deliver it.
    """
    node_ref = ref(node_info)
    body = await fake.get_node_status(dev_id, node_ref)
    await push_status(hass, runtime, dev_id, node_info, body)


async def push_setup_update(hass, runtime, fake, dev_id, node_info, changes):
    """Deliver a setup delta frame (the wire pushes the FULL setup body)."""
    node_ref = ref(node_info)
    body = await fake.get_node_setup(dev_id, node_ref)
    body.update(changes)
    await runtime.transport.deliver_update(
        dev_id, f"/{node_info['type']}/{node_info['addr']}/setup", body
    )
    await hass.async_block_till_done()


async def confirm_setup_write(hass, runtime, fake, dev_id, node_info):
    """Deliver the full-setup body frame that confirms a setup write."""
    node_ref = ref(node_info)
    body = await fake.get_node_setup(dev_id, node_ref)
    await runtime.transport.deliver_update(
        dev_id, f"/{node_info['type']}/{node_info['addr']}/setup", body
    )
    await hass.async_block_till_done()
