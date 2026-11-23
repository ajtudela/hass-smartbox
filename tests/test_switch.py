"""Switch platform.

The mock world is delivered to the engine as a ws snapshot through the
runtime's FakeTransport (the ``smartbox_stack`` fixture from
test_climate), and writes go through a FakeSmartbox write host.
Setup writes (GET-merge-POST)
need the full-setup body frame a live device pushes after an accepted
write, delivered explicitly like the status confirms; the stack is set
up in-body so setup log lines land in caplog.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import async_update_entity
import pytest
from pytest_homeassistant_custom_component.common import mock_restore_cache
from smartbox.core.state import NodeState

from custom_components.smartbox.const import DOMAIN
from custom_components.smartbox.identity import boost_available
from custom_components.smartbox.switch import MaxTemperatureLimitSwitch
from tests.mocks import (
    get_away_status_switch_entity_id,
    get_away_status_switch_entity_name,
    get_boost_switch_entity_id,
    get_boost_switch_entity_name,
    get_device_unique_id,
    get_entity_id_from_unique_id,
    get_max_limit_switch_entity_id,
    get_max_limit_switch_entity_name,
    get_no_node_power_limit_switch_entity_id,
    get_no_power_limit_switch_entity_id,
    get_node_unique_id,
    get_object_id,
    get_true_radiant_switch_entity_id,
    get_true_radiant_switch_entity_name,
    get_window_mode_switch_entity_id,
    get_window_mode_switch_entity_name,
)
from tests.stack import (
    confirm_setup_write,
    confirm_write,
    push_setup_update,
    ref,
    seed_stack,
)
from tests.test_utils import assert_log_message

_LOGGER = logging.getLogger(__name__)


async def _switch_entity(hass, entity_id):
    """Return the live switch entity object (for internal-state access)."""
    return hass.data["entity_components"]["switch"].get_entity(entity_id)


def test_switch_limits_fall_back_to_the_discovery_pid():
    """A partial live version still uses the discovery PID for limits."""
    entity = object.__new__(MaxTemperatureLimitSwitch)
    entity._node = SimpleNamespace(
        node_type="htr",
        pid="04020000",
        version={"fw_version": "1.9"},
        setup={},
    )
    entity._node_state = lambda: NodeState(
        version={"fw_version": "1.9"}, setup={}, status={}
    )
    limits = entity._controller_limits()
    assert (limits.min_c, limits.max_c) == (0.0, 30.0)


async def test_away_status(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, _fake = await seed_stack(hass, mock_smartbox)
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 21
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    # One away switch per box device, with the full away status as attributes
    for mock_device in await mock_smartbox.session.get_devices():
        entity_id = get_away_status_switch_entity_id(mock_device)
        state = hass.states.get(entity_id)

        # check basic properties
        assert state.object_id.startswith(
            get_object_id(get_away_status_switch_entity_name(mock_device))
        )
        assert state.name == f"{mock_device['name']} Away Status"
        assert (
            state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_device['name']} Away Status"
        )
        unique_id = get_device_unique_id(mock_device, "away_status")
        assert entity_id == get_entity_id_from_unique_id(hass, SWITCH_DOMAIN, unique_id)

        away_status = await mock_smartbox.session.get_device_away_status(
            mock_device["dev_id"]
        )
        assert state.state == ("on" if away_status["away"] else "off")
        assert state.attributes["away"] == away_status["away"]
        assert state.attributes["enabled"] == away_status["enabled"]
        assert state.attributes["forced"] == away_status["forced"]

    # Set device 1 away status over the websocket (device-level frame)
    mock_device_1 = (await mock_smartbox.session.get_devices())[0]

    entity_id = get_away_status_switch_entity_id(mock_device_1)
    await runtime.transport.deliver_update(
        mock_device_1["dev_id"], "/mgr/away_status", {"away": False}
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "off"

    await runtime.transport.deliver_update(
        mock_device_1["dev_id"], "/mgr/away_status", {"away": True}
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "on"

    # Turn off via HA (optimistic write)
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "off"

    # Turn on via HA
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "on"


async def test_action_write_failure_is_translated(hass, mock_smartbox, config_entry):
    """A protocol error from an entity action reaches the user translated."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)
    mock_device = (await mock_smartbox.session.get_devices())[0]
    entity_id = get_away_status_switch_entity_id(mock_device)

    fake.fail_next(500)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_ON,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )

    assert err.value.translation_key == "action_failed"
    assert err.value.translation_placeholders["entity_id"] == entity_id
    assert err.value.__cause__ is not None


async def test_no_power_limit_switch(hass, mock_smartbox, config_entry, caplog):
    """The no-power-limit switch mirrors the wire semantics: 0 means no limit."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, _fake = await seed_stack(hass, mock_smartbox)
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 21

    mock_device_dict = (await mock_smartbox.session.get_devices())[0]
    dev_id = mock_device_dict["dev_id"]
    assert runtime.engine.device(dev_id).power_limit == 1500

    entity_id = get_no_power_limit_switch_entity_id(mock_device_dict)
    state = hass.states.get(entity_id)
    assert state.name == f"{mock_device_dict['name']} No Power Limit"
    # The box reports a 1500 W limit, so the switch starts off.
    assert state.state == "off"

    # Turn on: writes 0 (= no limit).
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert runtime.engine.device(dev_id).power_limit == 0

    # Turn off: restores the last known non-zero limit (1500, seen at seed).
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "off"
    assert runtime.engine.device(dev_id).power_limit == 1500

    # After a restart with no limit active there is no history: the box
    # reports 0 and nothing else is known. Reach that state through the real
    # event path first (so the switch state machine is in sync), then clear
    # the switch's last-non-zero memory the way a restart would.
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "on"
    switch_entity = await _switch_entity(hass, entity_id)
    switch_entity._last_nonzero_power_limit = None

    caplog.clear()
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )
    await hass.async_block_till_done()
    # The instruction is in the raised message, not only in the log: a warning
    # would be invisible to whoever flipped the toggle.
    assert err.value.translation_key == "box_power_limit_not_known"
    assert runtime.engine.device(dev_id).power_limit == 0
    # The switch honestly reports the unchanged wire state.
    state = hass.states.get(entity_id)
    assert state.state == "on"


async def test_basic_window_mode(hass, mock_smartbox, config_entry, caplog):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 21
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            entity_id = get_window_mode_switch_entity_id(mock_node)
            mock_node_setup = await mock_smartbox.session.get_node_setup(
                mock_device["dev_id"], mock_node
            )

            if "factory_options" not in mock_node_setup or not mock_node_setup[
                "factory_options"
            ].get("window_mode_available", False):
                # We shouldn't have created a switch entity for this
                assert hass.states.get(entity_id) is None
                assert_log_message(
                    caplog,
                    "custom_components.smartbox.switch",
                    logging.INFO,
                    f"Window mode not available for node {mock_node['name']!r}",
                )
                continue

            state = hass.states.get(entity_id)

            # check basic properties
            assert state.object_id.startswith(
                get_object_id(get_window_mode_switch_entity_name(mock_node))
            )
            assert state.name == f"{mock_node['name']} Window Mode"
            assert (
                state.attributes[ATTR_FRIENDLY_NAME]
                == f"{mock_node['name']} Window Mode"
            )
            unique_id = get_node_unique_id(mock_device, mock_node, "window_mode")
            assert entity_id == get_entity_id_from_unique_id(
                hass, SWITCH_DOMAIN, unique_id
            )

            # Check window_mode is correct
            assert (
                state.state == "on" if mock_node_setup["window_mode_enabled"] else "off"
            )

            # Turn on window_mode via socket
            await push_setup_update(
                hass,
                runtime,
                fake,
                mock_device["dev_id"],
                mock_node,
                {"window_mode_enabled": True},
            )
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off window_mode via socket
            await push_setup_update(
                hass,
                runtime,
                fake,
                mock_device["dev_id"],
                mock_node,
                {"window_mode_enabled": False},
            )
            state = hass.states.get(entity_id)
            assert state.state == "off"

            # Turn on via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await confirm_setup_write(
                hass, runtime, fake, mock_device["dev_id"], mock_node
            )
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await confirm_setup_write(
                hass, runtime, fake, mock_device["dev_id"], mock_node
            )
            state = hass.states.get(entity_id)
            assert state.state == "off"


async def test_basic_true_radiant(hass, mock_smartbox, config_entry, caplog):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 21
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            entity_id = get_true_radiant_switch_entity_id(mock_node)
            mock_node_setup = await mock_smartbox.session.get_node_setup(
                mock_device["dev_id"], mock_node
            )

            if "factory_options" not in mock_node_setup or not mock_node_setup[
                "factory_options"
            ].get("true_radiant_available", False):
                # We shouldn't have created a switch entity for this
                assert hass.states.get(entity_id) is None
                assert_log_message(
                    caplog,
                    "custom_components.smartbox.switch",
                    logging.INFO,
                    f"True radiant not available for node {mock_node['name']!r}",
                )
                continue

            state = hass.states.get(entity_id)
            assert state is not None

            # check basic properties
            assert state.object_id.startswith(
                get_object_id(get_true_radiant_switch_entity_name(mock_node))
            )
            assert state.name == f"{mock_node['name']} True Radiant"
            assert (
                state.attributes[ATTR_FRIENDLY_NAME]
                == f"{mock_node['name']} True Radiant"
            )
            unique_id = get_node_unique_id(mock_device, mock_node, "true_radiant")
            assert entity_id == get_entity_id_from_unique_id(
                hass, SWITCH_DOMAIN, unique_id
            )

            # Check true_radiant is correct
            assert (
                state.state == "on"
                if mock_node_setup["true_radiant_enabled"]
                else "off"
            )

            # Turn on true_radiant via socket
            await push_setup_update(
                hass,
                runtime,
                fake,
                mock_device["dev_id"],
                mock_node,
                {"true_radiant_enabled": True},
            )
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off true_radiant via socket
            await push_setup_update(
                hass,
                runtime,
                fake,
                mock_device["dev_id"],
                mock_node,
                {"true_radiant_enabled": False},
            )
            state = hass.states.get(entity_id)
            assert state.state == "off"

            # Turn on via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await confirm_setup_write(
                hass, runtime, fake, mock_device["dev_id"], mock_node
            )
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await confirm_setup_write(
                hass, runtime, fake, mock_device["dev_id"], mock_node
            )
            state = hass.states.get(entity_id)
            assert state.state == "off"


async def test_basic_boost_switch(hass, mock_smartbox, config_entry, caplog):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 21
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            entity_id = get_boost_switch_entity_id(mock_node)
            mock_node_setup = await mock_smartbox.session.get_node_setup(
                mock_device["dev_id"], mock_node
            )
            info = next(
                item
                for item in mock_smartbox._node_info[mock_device["dev_id"]]
                if int(item["addr"]) == int(mock_node["addr"])
            )
            if not boost_available(
                mock_node_setup,
                node_type=mock_node["type"],
                pid=info.get("product_id"),
                fw_version=info.get("fw_version"),
            ):
                # We shouldn't have created a switch entity for this
                assert hass.states.get(entity_id) is None
                assert_log_message(
                    caplog,
                    "custom_components.smartbox.switch",
                    logging.INFO,
                    f"Boost mode not available for node {mock_node['name']!r}",
                )
                continue

            state = hass.states.get(entity_id)
            assert state is not None

            # check basic properties
            assert state.object_id.startswith(
                get_object_id(get_boost_switch_entity_name(mock_node))
            )
            assert state.name == f"{mock_node['name']} Boost"
            assert state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Boost"
            unique_id = get_node_unique_id(mock_device, mock_node, "boost")
            assert entity_id == get_entity_id_from_unique_id(
                hass, SWITCH_DOMAIN, unique_id
            )
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            # Check boost is correct
            assert (
                state.state == "on" if mock_node_status.get("boost", False) else "off"
            )

            # Turn on boost via socket
            await runtime.transport.deliver_update(
                mock_device["dev_id"],
                f"/{mock_node['type']}/{mock_node['addr']}/status",
                {"boost": True},
            )
            await hass.async_block_till_done()
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off boost via socket
            await runtime.transport.deliver_update(
                mock_device["dev_id"],
                f"/{mock_node['type']}/{mock_node['addr']}/status",
                {"boost": False},
            )
            await hass.async_block_till_done()
            state = hass.states.get(entity_id)
            assert state.state == "off"

            # Turn on via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await confirm_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await confirm_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
            state = hass.states.get(entity_id)
            assert state.state == "off"


async def test_lock_switch(hass, mock_smartbox, config_entry):
    """Lock switches exist for heater nodes; on means the lock is engaged."""
    mock_smartbox._node_info["device_1"][0]["product_id"] = "04020000"
    mock_smartbox._node_info["device_1"][0]["fw_version"] = "1.9"
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_entity_id_from_unique_id(
        hass, SWITCH_DOMAIN, get_node_unique_id(mock_device, node, "lock")
    )
    assert (
        er.async_get(hass).async_get_entity_id(
            SELECT_DOMAIN, DOMAIN, get_node_unique_id(mock_device, node, "lock_mode")
        )
        is None
    )
    # Mock heater node starts unlocked (locked == False).
    assert hass.states.get(entity_id).state == "off"
    # Display name comes from the "Child Lock" translation.
    assert (
        hass.states.get(entity_id).attributes[ATTR_FRIENDLY_NAME].endswith("Child Lock")
    )

    # Turn on via HA: single-key status write with an optimistic local merge.
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await confirm_write(hass, runtime, fake, mock_device["dev_id"], node)
    assert hass.states.get(entity_id).state == "on"
    status = await fake.get_node_status(mock_device["dev_id"], ref(node))
    assert status["locked"] is True

    # A websocket frame turning the lock off wins over the local state.
    await runtime.transport.deliver_update(
        mock_device["dev_id"],
        f"/{node['type']}/{node['addr']}/status",
        {"locked": False},
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"


async def test_legacy_pid_setup_write_keeps_the_omitted_extra_options(
    hass, mock_smartbox, config_entry
):
    """A legacy setup POST omits extra_options without blanking the store."""
    info = mock_smartbox._node_info["device_1"][0]
    info["product_id"] = "04020000"
    info["fw_version"] = "1.9"
    setup = mock_smartbox._node_setup["device_1"][0]
    setup["extra_options"] = {"user_duty_limit": 25}

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)

    device = (await mock_smartbox.session.get_devices())[0]
    node = (await mock_smartbox.session.get_nodes(device["dev_id"]))[0]
    entity_id = get_window_mode_switch_entity_id(node)
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    posted = [
        body
        for method, path, body in fake.requests
        if method == "POST" and path.endswith("/setup")
    ]
    assert posted
    assert "extra_options" not in posted[-1]
    stored = runtime.engine.node(device["dev_id"], f"{node['type']}/{node['addr']}")
    assert stored is not None
    assert stored.setup["extra_options"] == {"user_duty_limit": 25}
    assert stored.setup["window_mode_enabled"] is True

    await confirm_setup_write(hass, runtime, fake, device["dev_id"], node)
    confirmed = runtime.engine.node(device["dev_id"], f"{node['type']}/{node['addr']}")
    assert confirmed is not None
    assert confirmed.setup["extra_options"] == {"user_duty_limit": 25}


async def test_max_temperature_limit_switch(hass, mock_smartbox, config_entry):
    """The max limit switch tracks max_stemp_limit and toggles it off/on."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 21

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_max_limit_switch_entity_id(mock_node)
    state = hass.states.get(entity_id)
    # check basic properties
    assert state.object_id.startswith(
        get_object_id(get_max_limit_switch_entity_name(mock_node))
    )
    assert state.name == f"{mock_node['name']} Maximum Limit"
    assert state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Maximum Limit"
    unique_id = get_node_unique_id(mock_device, mock_node, "max_temperature_limit")
    assert entity_id == get_entity_id_from_unique_id(hass, SWITCH_DOMAIN, unique_id)

    # Fixture: the limit is enabled (max_stemp_limit "30.0").
    assert state.state == "on"

    # The node without the fw-1.9 setup field has no switch.
    acm_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    assert hass.states.get(get_max_limit_switch_entity_id(acm_node)) is None

    # Turn off via HA: writes the wire's disabled sentinel "0.0".
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await confirm_setup_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
    state = hass.states.get(entity_id)
    assert state.state == "off"
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert float(setup["max_stemp_limit"]) == 0.0

    # A websocket frame carrying the sentinel confirms the disabled state...
    await push_setup_update(
        hass,
        runtime,
        fake,
        mock_device["dev_id"],
        mock_node,
        {"max_stemp_limit": "0.0"},
    )
    assert hass.states.get(entity_id).state == "off"

    # ...and one carrying a real value re-enables it live.
    await push_setup_update(
        hass,
        runtime,
        fake,
        mock_device["dev_id"],
        mock_node,
        {"max_stemp_limit": "27.0"},
    )
    assert hass.states.get(entity_id).state == "on"

    # Disabling again erases the value on the wire; turning the switch
    # back on restores the last non-zero limit seen (27.0).
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await confirm_setup_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
    assert hass.states.get(entity_id).state == "off"
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert float(setup["max_stemp_limit"]) == 0.0

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await confirm_setup_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
    assert hass.states.get(entity_id).state == "on"
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert float(setup["max_stemp_limit"]) == 27.0


async def test_max_temperature_limit_switch_restores_last_limit(
    hass, mock_smartbox, config_entry
):
    """A restart-seeded last limit survives a toggle cycle (not the 30 cap)."""
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_max_limit_switch_entity_id(mock_node)
    # The device reports the limit DISABLED at startup (the restart
    # scenario under test): the seed world then carries the "0.0"
    # sentinel, which must not overwrite the restored memory. Mutate the
    # mock session's view directly - no socket session exists yet.
    mock_smartbox._node_setup[mock_device["dev_id"]][mock_node["addr"]].update(
        {"max_stemp_limit": "0.0"}
    )
    # Simulate HA state: the switch was left off after disabling a 21.0 °C
    # limit (70.0 °F on an F node; before the entry loads, so the restored
    # state seeds memory).
    units = (await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node))[
        "units"
    ]
    restored_limit = "21.0" if units == "C" else "70.0"
    mock_restore_cache(
        hass,
        [State(entity_id, "off", {"last_limit": restored_limit})],
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)

    # The entity state follows the node (the world reports the limit
    # disabled); the restored attribute only seeds the memory.
    assert hass.states.get(entity_id).state == "off"

    # Simulate the restart scenario: the device reports the limit disabled.
    await push_setup_update(
        hass,
        runtime,
        fake,
        mock_device["dev_id"],
        mock_node,
        {"max_stemp_limit": "0.0"},
    )

    state = hass.states.get(entity_id)
    assert state.state == "off"
    # The restored limit is surfaced in the attributes...
    assert state.attributes["last_limit"] == restored_limit

    # ...and re-enabling writes the restored 21.0, not the 30.0 default.
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await confirm_setup_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
    assert hass.states.get(entity_id).state == "on"
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    # 21.0 °C on a C node, 70.0 °F (= 21.1 °C, snapped) on an F node.
    assert float(setup["max_stemp_limit"]) == 21.0


@pytest.mark.parametrize("bad_value", ["", "junk", None, []])
async def test_max_temperature_limit_restore_bad_value(
    hass, mock_smartbox, config_entry, bad_value
):
    """A malformed restored last_limit is ignored (falls back to default)."""
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_max_limit_switch_entity_id(mock_node)
    # The device reports the limit disabled at startup, so no nonzero
    # value reaches the memory before the malformed restore is skipped.
    mock_smartbox._node_setup[mock_device["dev_id"]][mock_node["addr"]].update(
        {"max_stemp_limit": "0.0"}
    )
    mock_restore_cache(
        hass,
        [State(entity_id, "off", {"last_limit": bad_value})],
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)

    # Re-enable writes the default cap, not a bogus value.
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await confirm_setup_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert float(setup["max_stemp_limit"]) == 30.0


async def test_boost_switch_malformed_extra_options_render_none(
    hass, mock_smartbox, config_entry
):
    """Malformed boost params in extra_options read as None, never raise."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    entity_id = get_boost_switch_entity_id(mock_node)
    entity = await _switch_entity(hass, entity_id)
    assert entity is not None
    await push_setup_update(
        hass,
        runtime,
        fake,
        mock_device["dev_id"],
        mock_node,
        {"extra_options": {"boost_temp": "garbage", "boost_time": "garbage"}},
    )
    attrs = entity.extra_state_attributes
    assert attrs["boost_temperature"] is None
    assert attrs["boost_duration_minutes"] is None


async def test_no_node_power_limit_switch(hass, mock_smartbox, config_entry):
    """The node's no-limit toggle: on writes 0, off restores the last value."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[1]  # the pmo box
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    assert mock_node["type"] == "pmo"
    entity_id = get_no_node_power_limit_switch_entity_id(mock_node)

    # Seeded from the store: the setup's 1500 is the remembered non-zero value.
    assert hass.states.get(entity_id).state == "off"

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert setup["power_limit"] == 0
    await async_update_entity(hass, entity_id)
    assert hass.states.get(entity_id).state == "on"

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert setup["power_limit"] == 1500  # the remembered value


async def test_no_node_power_limit_without_a_remembered_value_fails_loudly(
    hass, mock_smartbox, config_entry
):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[1]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_no_node_power_limit_switch_entity_id(mock_node)
    entity = await _switch_entity(hass, entity_id)
    entity._last_nonzero_power_limit = None

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            SWITCH_DOMAIN,
            SERVICE_TURN_OFF,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )
    assert err.value.translation_key == "node_power_limit_not_known"
