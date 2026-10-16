import logging

from homeassistant.components.switch import (
    DOMAIN as SWITCH_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME
from homeassistant.core import State
from homeassistant.helpers.entity_component import async_update_entity
import pytest
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.smartbox.const import DOMAIN

from .const import MOCK_SMARTBOX_NODE_AWAY
from .mocks import (
    get_away_status_switch_entity_id,
    get_away_status_switch_entity_name,
    get_boost_switch_entity_id,
    get_boost_switch_entity_name,
    get_device_unique_id,
    get_entity_id_from_unique_id,
    get_max_limit_switch_entity_id,
    get_max_limit_switch_entity_name,
    get_no_power_limit_switch_entity_id,
    get_node_unique_id,
    get_object_id,
    get_true_radiant_switch_entity_id,
    get_true_radiant_switch_entity_name,
    get_window_mode_switch_entity_id,
    get_window_mode_switch_entity_name,
)
from .test_utils import assert_log_message


async def test_away_status(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 25
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

        away_status = MOCK_SMARTBOX_NODE_AWAY[mock_device["dev_id"]]
        assert state.state == ("on" if away_status["away"] else "off")
        assert state.attributes["away"] == away_status["away"]
        assert state.attributes["enabled"] == away_status["enabled"]
        assert state.attributes["forced"] == away_status["forced"]

    # Set device 1 to away over the websocket
    mock_device_1 = (await mock_smartbox.session.get_devices())[0]
    mock_smartbox.dev_data_update(mock_device_1, {"away_status": {"away": False}})

    entity_id = get_away_status_switch_entity_id(mock_device_1)
    state = hass.states.get(entity_id)
    assert state.state == "off"

    mock_smartbox.dev_data_update(mock_device_1, {"away_status": {"away": True}})
    state = hass.states.get(entity_id)
    assert state.state == "on"

    # Turn off via HA (optimistic write)
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
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
    state = hass.states.get(entity_id)
    assert state.state == "on"


async def test_no_power_limit_switch(hass, mock_smartbox, config_entry, caplog):
    """The no-power-limit switch mirrors the wire semantics: 0 means no limit."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 25

    device_1 = hass.config_entries.async_entries(DOMAIN)[0].runtime_data.devices[0]
    mock_device_dict = (await mock_smartbox.session.get_devices())[0]
    assert device_1.dev_id == mock_device_dict["dev_id"]
    assert device_1.power_limit == 1500

    entity_id = get_no_power_limit_switch_entity_id(mock_device_dict)
    state = hass.states.get(entity_id)
    assert state.name == f"{mock_device_dict['name']} No Power Limit"
    # The box reports a 1500 W limit, so the switch starts off.
    assert state.state == "off"

    # Turn on: writes 0 (= no limit) and updates the number's value.
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert device_1.power_limit == 0
    assert device_1.no_power_limit is True

    # Turn off: restores the last known non-zero limit.
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    state = hass.states.get(entity_id)
    assert state.state == "off"
    assert device_1.power_limit == 1500

    # After a restart with no limit active there is no history: the box
    # reports 0 and nothing else is known. Reach that state through the real
    # event path first (so the switch state machine is in sync), then clear
    # the restore history the way a restart would.
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    state = hass.states.get(entity_id)
    assert state.state == "on"
    device_1._last_nonzero_power_limit = None

    caplog.clear()
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    assert_log_message(
        caplog,
        "custom_components.smartbox.switch",
        logging.WARNING,
        f"Cannot restore a power limit for device {device_1.dev_id}: "
        "no previous limit known since startup. Set a value on the power "
        "limit number entity instead.",
    )
    assert device_1.power_limit == 0
    # The switch honestly reports the unchanged wire state.
    state = hass.states.get(entity_id)
    assert state.state == "on"


async def test_basic_window_mode(hass, mock_smartbox, config_entry, caplog):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 25
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
                    f"Window mode not available for node {mock_node['name']}",
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
            mock_smartbox.generate_socket_setup_update(
                mock_device, mock_node, {"window_mode_enabled": True}
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off window_mode via socket
            mock_smartbox.generate_socket_setup_update(
                mock_device, mock_node, {"window_mode_enabled": False}
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "off"

            # Turn on via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "off"


async def test_basic_true_radiant(hass, mock_smartbox, config_entry, caplog):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 25
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
                    f"True radiant not available for node {mock_node['name']}",
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
            mock_smartbox.generate_socket_setup_update(
                mock_device, mock_node, {"true_radiant_enabled": True}
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off true_radiant via socket
            mock_smartbox.generate_socket_setup_update(
                mock_device, mock_node, {"true_radiant_enabled": False}
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "off"

            # Turn on via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "off"


async def test_basic_boost_switch(hass, mock_smartbox, config_entry, caplog):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 25
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            entity_id = get_boost_switch_entity_id(mock_node)
            mock_node_setup = await mock_smartbox.session.get_node_setup(
                mock_device["dev_id"], mock_node
            )
            if "factory_options" not in mock_node_setup or not mock_node_setup[
                "factory_options"
            ].get("boost_config", 2):
                # if not mock_node_setup.get("boost_available", False):
                # We shouldn't have created a switch entity for this
                assert hass.states.get(entity_id) is None
                assert_log_message(
                    caplog,
                    "custom_components.smartbox.switch",
                    logging.INFO,
                    f"Boost mode not available for node {mock_node['name']}",
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
            mock_smartbox.generate_socket_status_update(
                mock_device, mock_node, {"boost": True}
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off boost via socket
            mock_smartbox.generate_socket_status_update(
                mock_device, mock_node, {"boost": False}
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "off"

            # Turn on via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_ON,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "on"

            # Turn off via HA
            await hass.services.async_call(
                SWITCH_DOMAIN,
                SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: entity_id},
                blocking=True,
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == "off"


async def test_lock_switch(hass, mock_smartbox, config_entry):
    """Lock switches exist for heater nodes; on means the lock is engaged."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    device_dict = (await mock_smartbox.session.get_devices())[0]
    node = (await mock_smartbox.session.get_nodes(device_dict["dev_id"]))[0]
    entity_id = get_entity_id_from_unique_id(
        hass, SWITCH_DOMAIN, get_node_unique_id(device_dict, node, "lock")
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
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"

    # A websocket frame turning the lock off wins over the local state.
    mock_smartbox.generate_socket_status_update(device_dict, node, {"locked": False})
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"


async def test_max_temperature_limit_switch(hass, mock_smartbox, config_entry):
    """The max limit switch tracks max_stemp_limit and toggles it off/on."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SWITCH_DOMAIN)) == 25

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
    await async_update_entity(hass, entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "off"
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    assert float(setup["max_stemp_limit"]) == 0.0

    # A websocket frame carrying the sentinel confirms the disabled state...
    mock_smartbox.generate_socket_setup_update(
        mock_device, mock_node, {"max_stemp_limit": "0.0"}
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"

    # ...and one carrying a real value re-enables it live.
    mock_smartbox.generate_socket_setup_update(
        mock_device, mock_node, {"max_stemp_limit": "27.0"}
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"

    # Disabling again erases the value on the wire; turning the switch
    # back on restores the last non-zero limit seen (27.0).
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await async_update_entity(hass, entity_id)
    assert hass.states.get(entity_id).state == "off"
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    assert float(setup["max_stemp_limit"]) == 0.0

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await async_update_entity(hass, entity_id)
    assert hass.states.get(entity_id).state == "on"
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    assert float(setup["max_stemp_limit"]) == 27.0


async def test_max_temperature_limit_switch_restores_last_limit(
    hass, mock_smartbox, config_entry
):
    """A restart-seeded last limit survives a toggle cycle (not the 30 cap)."""
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_max_limit_switch_entity_id(mock_node)
    # Simulate HA state: the switch was left off after disabling a 21.0 limit.
    mock_restore_cache(
        hass,
        [State(entity_id, "off", {"last_limit": "21.0"})],
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # The entity state follows the node (fixture: limit enabled), not the
    # restored "off"; the restored attribute only seeds the memory.
    assert hass.states.get(entity_id).state == "on"

    # Simulate the restart scenario: the device reports the limit disabled.
    mock_smartbox.generate_socket_setup_update(
        mock_device, mock_node, {"max_stemp_limit": "0.0"}
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == "off"
    # The restored limit is surfaced in the attributes...
    assert state.attributes["last_limit"] == "21.0"

    # ...and re-enabling writes the restored 21.0, not the 30.0 default.
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await async_update_entity(hass, entity_id)
    assert hass.states.get(entity_id).state == "on"
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    assert float(setup["max_stemp_limit"]) == 21.0


@pytest.mark.parametrize("bad_value", ["", "junk", None, []])
async def test_max_temperature_limit_restore_bad_value(
    hass, mock_smartbox, config_entry, bad_value
):
    """A malformed restored last_limit is ignored (falls back to default)."""
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_max_limit_switch_entity_id(mock_node)
    mock_restore_cache(
        hass,
        [State(entity_id, "off", {"last_limit": bad_value})],
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Re-enable writes the default cap, not a bogus value.
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await async_update_entity(hass, entity_id)
    setup = await mock_smartbox.session.get_node_setup(mock_device["dev_id"], mock_node)
    assert float(setup["max_stemp_limit"]) == 30.0
