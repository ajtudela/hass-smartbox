"""Select platform.

Harness shape as in test_number.py: the mock world is delivered to the
engine as a ws snapshot through the runtime's FakeTransport.
"""

from __future__ import annotations

from homeassistant.components.select import ATTR_OPTION, DOMAIN as SELECT_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, ATTR_FRIENDLY_NAME
from homeassistant.helpers import entity_registry as er
import pytest

from custom_components.smartbox.const import DOMAIN
from tests.mocks import (
    get_duty_limit_entity_id,
    get_entity_id_from_unique_id,
    get_node_unique_id,
    get_object_id,
    get_priority_entity_id,
    get_priority_entity_name,
)
from tests.stack import push_setup_update, ref, seed_stack


@pytest.fixture
def units():
    """Pin the mock world to Celsius: radiator priority is a plain enum."""
    return "C"


async def test_radiator_priority(hass, mock_smartbox, config_entry):
    """The priority select exists only where the setup advertises it."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)
    # Only the fw-1.9-family htr node carries priority in its setup.
    assert len(hass.states.async_entity_ids(SELECT_DOMAIN)) == 1

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_priority_entity_id(mock_node)
    state = hass.states.get(entity_id)
    # check basic properties
    assert state.object_id.startswith(
        get_object_id(get_priority_entity_name(mock_node))
    )
    assert state.name == f"{mock_node['name']} Radiator Priority"
    assert (
        state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Radiator Priority"
    )
    unique_id = get_node_unique_id(mock_device, mock_node, "priority")
    assert entity_id == get_entity_id_from_unique_id(hass, SELECT_DOMAIN, unique_id)
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert state.state == setup["priority"]
    assert state.attributes["options"] == ["low", "medium", "high"]

    # Selecting an option writes the setup field; the entity state picks
    # it up from the confirming websocket setup frame (same flow as the
    # window-mode switch writes).
    await hass.services.async_call(
        SELECT_DOMAIN,
        "select_option",
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "high"},
        blocking=True,
    )
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert setup["priority"] == "high"

    # A websocket setup frame refreshes the entity too.
    await push_setup_update(
        hass, runtime, fake, mock_device["dev_id"], mock_node, {"priority": "high"}
    )
    state = hass.states.get(entity_id)
    assert state.state == "high"

    await push_setup_update(
        hass, runtime, fake, mock_device["dev_id"], mock_node, {"priority": "medium"}
    )
    state = hass.states.get(entity_id)
    assert state.state == "medium"

    # The acm node's setup has no priority: no select entity.
    acm_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    assert hass.states.get(get_priority_entity_id(acm_node)) is None


async def test_control_mode_select(hass, mock_smartbox, config_entry):
    """The control-mode select exists where setup carries it and tracks frames."""
    mock_smartbox._node_info["device_1"][0]["product_id"] = "0402"
    setup = mock_smartbox._node_setup["device_1"][0]
    setup["control_mode"] = 5

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    unique_id = get_node_unique_id(mock_device, mock_node, "control_mode")
    entity_id = get_entity_id_from_unique_id(hass, SELECT_DOMAIN, unique_id)
    state = hass.states.get(entity_id)
    assert state.state == "PID (15 min. period)"
    assert state.name == f"{mock_node['name']} Control Mode"
    assert state.attributes["options"] == [
        "PID (15 min. period)",
        "PID (30 min. period)",
        "Hysteresis (0.2 °C)",
        "Hysteresis (0.3 °C)",
        "Hysteresis (0.5 °C)",
        "Hysteresis (0.7 °C)",
    ]

    await push_setup_update(
        hass, runtime, fake, mock_device["dev_id"], mock_node, {"control_mode": 6}
    )
    assert hass.states.get(entity_id).state == "PID (30 min. period)"

    # A stored index outside the relay UI keeps the entity and reads unknown.
    await push_setup_update(
        hass, runtime, fake, mock_device["dev_id"], mock_node, {"control_mode": 4}
    )
    state = hass.states.get(entity_id)
    assert state.state == "unknown"
    assert state.attributes["options"][0] == "PID (15 min. period)"

    await hass.services.async_call(
        SELECT_DOMAIN,
        "select_option",
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "Hysteresis (0.7 °C)"},
        blocking=True,
    )
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert setup["control_mode"] == 3

    # A setup frame with Fahrenheit units relabels the options.
    await push_setup_update(
        hass,
        runtime,
        fake,
        mock_device["dev_id"],
        mock_node,
        {"control_mode": 0, "units": "F"},
    )
    assert hass.states.get(entity_id).state == "Hysteresis (0.4 °F)"

    # The acm node has no control_mode key: no select entity.
    acm_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            SELECT_DOMAIN,
            DOMAIN,
            get_node_unique_id(mock_device, acm_node, "control_mode"),
        )
        is None
    )


async def test_control_mode_hidden_for_classic_ui_storage(
    hass, mock_smartbox, config_entry
):
    """The app's classic-UI hide applies at entity creation."""
    mock_smartbox._node_info["device_1"][0]["product_id"] = "0414"
    setup = mock_smartbox._node_setup["device_1"][0]
    setup["control_mode"] = 5
    setup["factory_options"] = {
        **setup.get("factory_options", {}),
        "ui_mode": 1,
    }

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id(
            SELECT_DOMAIN,
            DOMAIN,
            get_node_unique_id(mock_device, mock_node, "control_mode"),
        )
        is None
    )


def _patch_duty_limit(mock_smartbox, *, extra, factory, power="2000.0") -> None:
    """Give the first heater the duty-limit fields under test."""
    setup = mock_smartbox._node_setup["device_1"][0]
    setup["power"] = power
    setup["extra_options"] = extra
    setup["factory_options"] = {**setup["factory_options"], **factory}


def _duty_presets(**overrides) -> dict:
    presets = {
        "lst_available": True,
        "lst_value": 25,
        "bbc_available": True,
        "bbc_value": 50,
    }
    presets.update(overrides)
    return presets


async def test_power_limit_factor(hass, mock_smartbox, config_entry):
    """The power-limit factor offers the app's presets and writes the value."""
    _patch_duty_limit(
        mock_smartbox,
        extra={"user_duty_limit": 0},
        factory=_duty_presets(),
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_duty_limit_entity_id(mock_node)
    state = hass.states.get(entity_id)
    assert (
        state.name
        == state.attributes[ATTR_FRIENDLY_NAME]
        == f"{mock_node['name']} Power Limit Factor"
    )
    assert state.state == "Off"
    assert state.attributes["options"] == ["Off", "LST", "Baby Care"]
    # The two selects coexist: the duty gate does not replace the priority one.
    assert hass.states.get(get_priority_entity_id(mock_node)) is not None

    await hass.services.async_call(
        SELECT_DOMAIN,
        "select_option",
        {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: "Baby Care"},
        blocking=True,
    )
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert setup["extra_options"]["user_duty_limit"] == 50

    # A setup frame re-renders the selection.
    await push_setup_update(
        hass,
        runtime,
        fake,
        mock_device["dev_id"],
        mock_node,
        {"extra_options": {"user_duty_limit": 25}},
    )
    assert hass.states.get(entity_id).state == "LST"

    # A value outside the presets is unknown, as the app shows it blank.
    await push_setup_update(
        hass,
        runtime,
        fake,
        mock_device["dev_id"],
        mock_node,
        {"extra_options": {"user_duty_limit": 60}},
    )
    assert hass.states.get(entity_id).state == "unknown"

    # A digit-string echo still maps to its preset.
    await push_setup_update(
        hass,
        runtime,
        fake,
        mock_device["dev_id"],
        mock_node,
        {"extra_options": {"user_duty_limit": "25"}},
    )
    assert hass.states.get(entity_id).state == "LST"

    # An unreadable stored value is unknown too.
    await push_setup_update(
        hass,
        runtime,
        fake,
        mock_device["dev_id"],
        mock_node,
        {"extra_options": {"user_duty_limit": "abc"}},
    )
    assert hass.states.get(entity_id).state == "unknown"


@pytest.mark.parametrize(
    "patch",
    [
        # No preset percentages: only OFF would be offered.
        {"extra": {"user_duty_limit": 0}, "factory": {"lst_available": False}},
        # The app hides the control on a node with no rated power.
        {"extra": {"user_duty_limit": 0}, "factory": _duty_presets(), "power": "0"},
        # Legacy nodes keep the factor in factory_options.duty_limit.
        {"extra": {}, "factory": {"duty_limit": 30}},
    ],
)
async def test_power_limit_factor_absent(hass, mock_smartbox, config_entry, patch):
    """The select only appears where the app itself would offer it."""
    _patch_duty_limit(mock_smartbox, **patch)

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    assert hass.states.get(get_duty_limit_entity_id(mock_node)) is None


async def test_power_limit_factor_lst_only(hass, mock_smartbox, config_entry):
    """A unit without Baby Care offers OFF and LST only."""
    _patch_duty_limit(
        mock_smartbox,
        extra={"user_duty_limit": 25},
        factory=_duty_presets(bbc_available=False),
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    state = hass.states.get(get_duty_limit_entity_id(mock_node))
    assert state.attributes["options"] == ["Off", "LST"]
    assert state.state == "LST"


async def test_priority_absent_on_a_boxless_device(hass, mock_smartbox, config_entry):
    """The app hides radiator priority on a WiFi heater; the gate does too."""
    mock_smartbox._device_power = {**mock_smartbox._device_power, "device_1": None}

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    assert hass.states.get(get_priority_entity_id(mock_node)) is None
