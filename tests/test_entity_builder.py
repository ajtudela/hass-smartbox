"""Tests for the dynamic entity builder registry."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.smartbox.entity_builder import EntityBuilder
from custom_components.smartbox.services import ServiceTargets


def _identity(dev_ids: list[str], node_ids: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        devices=[SimpleNamespace(dev_id=dev_id) for dev_id in dev_ids],
        nodes=[
            SimpleNamespace(node_id=node_id, dev_id=node_id.split("_")[0])
            for node_id in node_ids
        ],
    )


async def test_builder_adds_new_devices_then_nodes_and_deduplicates():
    """A new box and its node are built once; a repeat is a no-op."""
    builder = EntityBuilder(_identity(["dev_1"], ["dev_1_0"]), ServiceTargets())
    add_entities = MagicMock()
    builder.register(
        add_entities,
        build_device=lambda device: [([f"box:{device.dev_id}"], False)],
        build_node=lambda node: [([f"node:{node.node_id}"], False)],
    )

    added = await builder.async_add_identity(
        _identity(["dev_1", "dev_2"], ["dev_1_0", "dev_2_1"])
    )

    assert added == (1, 1)
    assert add_entities.call_count == 2
    assert [call.args[0] for call in add_entities.call_args_list] == [
        ["box:dev_2"],
        ["node:dev_2_1"],
    ]
    assert all(
        call.kwargs == {"update_before_add": False}
        for call in add_entities.call_args_list
    )

    added = await builder.async_add_identity(
        _identity(["dev_1", "dev_2"], ["dev_1_0", "dev_2_1"])
    )
    assert added == (0, 0)
    assert add_entities.call_count == 2


async def test_builder_keeps_group_fetch_flags():
    """The total-consumption group's update flag survives the registry."""
    builder = EntityBuilder(_identity([], []), ServiceTargets())
    add_entities = MagicMock()
    builder.register(
        add_entities,
        build_node=lambda _node: [(["node"], True)],
    )

    await builder.async_add_identity(_identity(["dev_1"], ["dev_1_0"]))

    add_entities.assert_called_once_with(["node"], update_before_add=True)


async def test_builder_failure_rolls_back_published_targets_and_known_sets():
    """A failed build adds nothing, including side-effect service targets."""
    services = ServiceTargets()
    builder = EntityBuilder(_identity([], []), services)
    add_entities = MagicMock()
    fail = True

    def build_once(node):
        nonlocal fail
        services.schedule.append(node)
        services.thermostat_schedule.append(node)
        services.charging.append(node)
        services.boost.append(node)
        services.climate.append(node)
        if fail:
            fail = False
            msg = "builder failed"
            raise RuntimeError(msg)
        return [([node], False)]

    builder.register(add_entities, build_node=build_once)
    with pytest.raises(RuntimeError, match="builder failed"):
        await builder.async_add_identity(_identity(["dev_1"], ["dev_1_0"]))

    assert services.schedule == []
    assert services.thermostat_schedule == []
    assert services.charging == []
    assert services.boost == []
    assert services.climate == []
    assert add_entities.call_count == 0

    assert await builder.async_add_identity(_identity(["dev_1"], ["dev_1_0"])) == (
        1,
        1,
    )
    add_entities.assert_called_once()


async def test_builder_forgets_a_confirmed_removal_so_it_rebuilds():
    """A node/box confirmed gone is built again when the account lists it."""
    builder = EntityBuilder(_identity(["dev_1"], ["dev_1_0"]), ServiceTargets())
    add_entities = MagicMock()
    builder.register(
        add_entities,
        build_device=lambda device: [([f"box:{device.dev_id}"], False)],
        build_node=lambda node: [([f"node:{node.node_id}"], False)],
    )

    assert await builder.async_add_identity(_identity(["dev_1"], ["dev_1_0"])) == (0, 0)
    assert add_entities.call_count == 0

    await builder.forget_node("dev_1_0")
    await builder.forget_device("dev_1")

    assert await builder.async_add_identity(_identity(["dev_1"], ["dev_1_0"])) == (1, 1)
    assert [call.args[0] for call in add_entities.call_args_list] == [
        ["box:dev_1"],
        ["node:dev_1_0"],
    ]


def test_service_targets_discard_removes_only_that_entity():
    services = ServiceTargets()
    keep = SimpleNamespace(entity_id="climate.keep")
    drop = SimpleNamespace(entity_id="climate.drop")
    services.climate.extend([keep, drop])
    services.boost.append(drop)
    services.schedule.append(keep)

    services.discard(drop)

    assert services.climate == [keep]
    assert services.boost == []
    assert services.schedule == [keep]
    services.discard(SimpleNamespace(entity_id="climate.drop"))
