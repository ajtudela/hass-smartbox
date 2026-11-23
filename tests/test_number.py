"""Number platform.

The mock world is delivered to the engine as a ws snapshot through the
runtime's FakeTransport (the ``seed_stack`` harness from
test_climate), writes go through a FakeSmartbox write host (asserted via
its state/requests), and setup writes (GET-merge-POST) get the
full-setup body frame a live device pushes after an accepted write.
The stack is set up in-body (like the other platform suites) so setup
log lines land in caplog.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

from homeassistant.components.number import ATTR_VALUE, DOMAIN as NUMBER_DOMAIN
from homeassistant.components.number.const import SERVICE_SET_VALUE
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_component import async_update_entity
from homeassistant.util.unit_conversion import TemperatureConverter
import pytest
from smartbox.core.state import NodeState
from smartbox.protocol.values import celsius_to_node_units, node_units_to_celsius

from custom_components.smartbox.compat import vol
from custom_components.smartbox.const import DOMAIN, SERVICE_SET_BOOST_PARAMS
from custom_components.smartbox.number import MaxTemperature
from tests.const import MOCK_SMARTBOX_DEVICE_POWER
from tests.mocks import (
    get_away_offset_entity_id,
    get_away_offset_entity_name,
    get_boost_duration_entity_id,
    get_boost_duration_entity_name,
    get_boost_switch_entity_id,
    get_boost_temperature_entity_id,
    get_boost_temperature_entity_name,
    get_device_unique_id,
    get_entity_id_from_unique_id,
    get_max_temperature_entity_id,
    get_max_temperature_entity_name,
    get_node_power_limit_entity_id,
    get_node_unique_id,
    get_object_id,
    get_power_limit_number_entity_id,
    get_power_limit_number_entity_name,
    get_prog_temp_entity_id,
)
from tests.stack import (
    confirm_setup_write,
    confirm_write,
    push_setup_update,
    ref,
    seed_stack,
)
from tests.test_utils import convert_temp, max_limit_value, round_temp


async def test_power_limit(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, _fake = await seed_stack(hass, mock_smartbox)
    assert len(hass.states.async_entity_ids(NUMBER_DOMAIN)) == 19
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components
    # One power limit entity per box device, always created (0 = no limit).
    for mock_device in await mock_smartbox.session.get_devices():
        entity_id = get_entity_id_from_unique_id(
            hass, NUMBER_DOMAIN, get_device_unique_id(mock_device, "power_limit")
        )
        state = hass.states.get(entity_id)
        # check basic properties
        assert state.object_id.startswith(
            get_object_id(get_power_limit_number_entity_name(mock_device))
        )
        assert state.name == f"{mock_device['name']} Power Limit"
        assert (
            state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_device['name']} Power Limit"
        )
        assert entity_id == get_power_limit_number_entity_id(mock_device)
        assert state.attributes["max"] == 60000
        assert state.attributes["min"] == 0
        # the wire semantic "0 means no limit" travels with the entity, since HA
        # has no entity-level help-text key
        assert state.attributes["unlimited_value"] == 0
        assert state.state == str(MOCK_SMARTBOX_DEVICE_POWER[mock_device["dev_id"]])

    mock_device_1 = (await mock_smartbox.session.get_devices())[0]
    dev_id = mock_device_1["dev_id"]
    assert (
        runtime.engine.device(dev_id).power_limit == MOCK_SMARTBOX_DEVICE_POWER[dev_id]
    )

    entity_id = get_power_limit_number_entity_id(mock_device_1)
    await async_update_entity(hass, entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "1500"

    entity_id = get_power_limit_number_entity_id(mock_device_1)
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 500},
        blocking=True,
    )
    state = hass.states.get(entity_id)
    assert state.state == "500"
    assert runtime.engine.device(dev_id).power_limit == 500


def test_number_limits_fall_back_to_the_discovery_pid():
    """A partial live version still uses the discovery PID for limits."""
    entity = object.__new__(MaxTemperature)
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


async def test_boost_temperature(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)
    assert len(hass.states.async_entity_ids(NUMBER_DOMAIN)) == 19
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    entity_id = get_boost_temperature_entity_id(mock_node)
    state = hass.states.get(entity_id)
    # check basic properties
    assert state.object_id.startswith(
        get_object_id(get_boost_temperature_entity_name(mock_node))
    )
    assert state.entity_id.startswith(get_boost_temperature_entity_id(mock_node))
    assert state.name == f"{mock_node['name']} Boost Temperature"
    assert (
        state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Boost Temperature"
    )
    unique_id = get_node_unique_id(mock_device, mock_node, "config_boost_temperature")
    assert entity_id == get_entity_id_from_unique_id(hass, NUMBER_DOMAIN, unique_id)
    min_val = state.attributes.get("min")
    max_val = state.attributes.get("max")
    test_value = round((min_val + max_val) / 2, 1)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: test_value},
        blocking=True,
    )
    await confirm_setup_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
    await async_update_entity(hass, entity_id)
    state = hass.states.get(entity_id)
    assert state.state == str(test_value)


async def test_boost_duration(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)
    assert len(hass.states.async_entity_ids(NUMBER_DOMAIN)) == 19
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    entity_id = get_boost_duration_entity_id(mock_node)
    state = hass.states.get(entity_id)
    # check basic properties
    assert state.object_id.startswith(
        get_object_id(get_boost_duration_entity_name(mock_node))
    )
    assert state.entity_id.startswith(get_boost_duration_entity_id(mock_node))
    assert state.name == f"{mock_node['name']} Boost Duration"
    assert state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Boost Duration"
    unique_id = get_node_unique_id(mock_device, mock_node, "config_boost_duration")
    assert entity_id == get_entity_id_from_unique_id(hass, NUMBER_DOMAIN, unique_id)
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 120.0},
        blocking=True,
    )
    # Faut simuler le retour de la websocket
    await confirm_setup_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
    await async_update_entity(hass, entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "120.0"
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert setup["extra_options"]["boost_time"] == 120

    entity = hass.data["entity_components"]["number"].get_entity(entity_id)
    assert entity.native_max_value == 1440
    with pytest.raises(ServiceValidationError) as err:
        await entity.async_set_native_value(10)
    assert err.value.translation_key == "boost_duration_out_of_range"
    assert set(err.value.translation_placeholders) == {"min", "max", "unit"}


async def test_legacy_pid_has_no_boost_number_entities(
    hass, mock_smartbox, config_entry
):
    """Legacy codes reject extra_options, so they get no boost parameters."""
    info = mock_smartbox._node_info["device_1"][0]
    info["product_id"] = "04020000"
    info["fw_version"] = "1.9"
    setup = mock_smartbox._node_setup["device_1"][0]
    setup["factory_options"] = {**setup.get("factory_options", {}), "boost_config": 2}
    setup["extra_options"] = {"user_duty_limit": 25}

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, _fake = await seed_stack(hass, mock_smartbox)

    device = (await mock_smartbox.session.get_devices())[0]
    node = (await mock_smartbox.session.get_nodes(device["dev_id"]))[0]
    assert hass.states.get(get_boost_temperature_entity_id(node)) is None
    assert hass.states.get(get_boost_duration_entity_id(node)) is None
    # The base ElectricHeater family exposes no boost mode either.
    assert hass.states.get(get_boost_switch_entity_id(node)) is None


async def test_storage_boost_numbers_exist_without_a_boost_config(
    hass, mock_smartbox, config_entry
):
    """An absent boost_config keeps the legacy boost parameters on storage."""
    mock_smartbox._node_info["device_1"][1]["product_id"] = "081C0000"
    mock_smartbox._node_info["device_1"][1]["fw_version"] = "1.8.0"
    setup = mock_smartbox._node_setup["device_1"][1]
    setup["factory_options"].pop("boost_config", None)

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, _fake = await seed_stack(hass, mock_smartbox)

    device = (await mock_smartbox.session.get_devices())[0]
    node = (await mock_smartbox.session.get_nodes(device["dev_id"]))[1]
    assert hass.states.get(get_boost_temperature_entity_id(node)) is not None
    assert hass.states.get(get_boost_duration_entity_id(node)) is not None
    assert hass.states.get(get_boost_switch_entity_id(node)) is not None


@pytest.mark.parametrize(
    ("product_id", "boost_config"),
    [("01170000", 1), ("011D0000", 2), ("01470000", 3)],
)
async def test_fixed_time_boost_has_no_parameter_numbers(
    hass, mock_smartbox, config_entry, product_id, boost_config
):
    """Haverland fixed-time boost has no editable temperature or duration."""
    info = mock_smartbox._node_info["device_1"][0]
    info["product_id"] = product_id
    info["fw_version"] = "1.9"
    setup = mock_smartbox._node_setup["device_1"][0]
    setup["factory_options"] = {
        **setup.get("factory_options", {}),
        "boost_config": boost_config,
    }

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, _fake = await seed_stack(hass, mock_smartbox)

    device = (await mock_smartbox.session.get_devices())[0]
    node = (await mock_smartbox.session.get_nodes(device["dev_id"]))[0]
    assert hass.states.get(get_boost_temperature_entity_id(node)) is None
    assert hass.states.get(get_boost_duration_entity_id(node)) is None
    assert hass.states.get(get_boost_switch_entity_id(node)) is not None


async def test_set_boost_params_service_targets_entities(
    hass, mock_smartbox, config_entry
):
    """Test the smartbox.set_boost_params service with entity_id targets."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    temp_entity_id = get_boost_temperature_entity_id(mock_node)
    duration_entity_id = get_boost_duration_entity_id(mock_node)
    mock_node_status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    boost_temp = 21.5 if mock_node_status["units"] == "C" else 70.0

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_BOOST_PARAMS,
        {
            ATTR_ENTITY_ID: [temp_entity_id, duration_entity_id],
            "temperature": boost_temp,
            "duration": 120,
        },
        blocking=True,
    )
    await confirm_setup_write(hass, runtime, fake, mock_device["dev_id"], mock_node)

    # Simulate the websocket state refresh
    await async_update_entity(hass, temp_entity_id)
    await async_update_entity(hass, duration_entity_id)
    state = hass.states.get(temp_entity_id)
    expected_temp = round_temp(
        hass, convert_temp(hass, mock_node_status["units"], boost_temp)
    )
    assert float(state.state) == expected_temp
    state = hass.states.get(duration_entity_id)
    assert state.state == "120.0"

    # The service must send merged extra_options, preserving unrelated keys
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert setup["extra_options"]["boost_temp"] == str(boost_temp)
    assert setup["extra_options"]["boost_time"] == 120


async def test_set_boost_params_service_requires_a_target(
    hass, mock_smartbox, config_entry
):
    """A call that resolves to no entity fails loudly instead of no-opping."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BOOST_PARAMS,
            {"temperature": 21.5},
            blocking=True,
        )
    assert err.value.translation_key == "service_no_target"
    assert err.value.translation_placeholders == {"service": SERVICE_SET_BOOST_PARAMS}


async def test_set_boost_params_service_refuses_a_non_boost_target(
    hass, mock_smartbox, config_entry
):
    """A target outside the boost numbers fails instead of silently no-opping.

    The pickers cannot filter by capability, so a non-boost heater can be
    picked; the service must refuse it rather than report success while
    writing nothing.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

    # device_1 node 0 advertises boost_config 0: no boost number entities.
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    away_entity_id = get_away_offset_entity_id(mock_node)
    assert hass.states.get(away_entity_id) is not None

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BOOST_PARAMS,
            {ATTR_ENTITY_ID: [away_entity_id], "temperature": 21.5},
            blocking=True,
        )
    assert err.value.translation_key == "boost_params_wrong_temperature_target"
    assert err.value.translation_placeholders == {"service": SERVICE_SET_BOOST_PARAMS}


async def test_set_boost_params_service_refuses_a_param_without_a_target(
    hass, mock_smartbox, config_entry
):
    """A parameter whose boost entity is not targeted fails before any write."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    temp_entity_id = get_boost_temperature_entity_id(mock_node)
    before = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BOOST_PARAMS,
            {
                ATTR_ENTITY_ID: [temp_entity_id],
                "temperature": 21.5,
                "duration": 120,
            },
            blocking=True,
        )
    assert err.value.translation_key == "boost_params_wrong_duration_target"
    assert err.value.translation_placeholders == {"service": SERVICE_SET_BOOST_PARAMS}

    # The refusal is atomic: the temperature write must not have gone out.
    after = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert after == before


async def test_set_boost_params_service_rejects_an_out_of_range_duration(
    hass, mock_smartbox, config_entry
):
    """The service cannot write a duration outside the entity's bounds."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    duration_entity_id = get_boost_duration_entity_id(mock_node)

    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BOOST_PARAMS,
            {ATTR_ENTITY_ID: [duration_entity_id], "duration": 10},
            blocking=True,
        )


async def test_set_boost_params_service_maps_api_failure(
    hass, mock_smartbox, config_entry
):
    """An API failure reaches the caller as a HomeAssistantError."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    entity_id = get_boost_temperature_entity_id(mock_node)
    status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    boost_temp = 21.5 if status["units"] == "C" else 70.0

    fake.fail_next(500)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_BOOST_PARAMS,
            {ATTR_ENTITY_ID: [entity_id], "temperature": boost_temp},
            blocking=True,
        )
    assert err.value.translation_key == "boost_params_write_failed"
    assert err.value.translation_placeholders["entity_id"] == entity_id


async def test_unload_removes_the_boost_service(hass, mock_smartbox, config_entry):
    """The service must not outlive the entities it acts on."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, SERVICE_SET_BOOST_PARAMS)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_BOOST_PARAMS)


async def test_away_offset(hass, mock_smartbox, config_entry):
    """The away offset number tracks the setup field and writes it back."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_away_offset_entity_id(mock_node)
    state = hass.states.get(entity_id)
    # check basic properties
    assert state.object_id.startswith(
        get_object_id(get_away_offset_entity_name(mock_node))
    )
    assert state.name == f"{mock_node['name']} Away Offset"
    assert state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Away Offset"
    unique_id = get_node_unique_id(mock_device, mock_node, "away_offset")
    assert entity_id == get_entity_id_from_unique_id(hass, NUMBER_DOMAIN, unique_id)
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert float(state.state) == float(setup["away_offset"])
    # No device class: bounds are native and not unit-converted; they
    # differ per device scale (app-verified: 0-5 °C in 0.5 steps, or
    # 0-9 °F in whole steps).
    status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    if status["units"] == "C":
        assert state.attributes["min"] == 0
        assert state.attributes["max"] == 5
        assert state.attributes["step"] == 0.5
    else:
        assert state.attributes["min"] == 0
        assert state.attributes["max"] == 9
        assert state.attributes["step"] == 1

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 4.5},
        blocking=True,
    )
    await confirm_setup_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
    await async_update_entity(hass, entity_id)
    state = hass.states.get(entity_id)
    assert state.state == "4.5"
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert setup["away_offset"] == "4.5"


async def test_away_offset_malformed_value_renders_unknown(
    hass, mock_smartbox, config_entry
):
    """Server-controlled setup data must not break every state write."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_away_offset_entity_id(mock_node)

    await push_setup_update(
        hass,
        runtime,
        fake,
        mock_device["dev_id"],
        mock_node,
        {"away_offset": "not-a-number"},
    )
    assert hass.states.get(entity_id).state == STATE_UNKNOWN


async def test_boost_temperature_bounds_follow_the_node_scale(
    hass, mock_smartbox, config_entry
):
    """The boost slider uses the node's scale, and writes reject off-scale values."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    entity_id = get_boost_temperature_entity_id(mock_node)
    entity = hass.data["entity_components"]["number"].get_entity(entity_id)
    status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    # ``native_*`` is the entity's wire scale; HA state attributes are
    # converted to the HA unit system, so check the native surface here.
    if status["units"] == "C":
        assert (entity.native_min_value, entity.native_max_value) == (5, 30)
        assert entity.native_step == 0.5
        with pytest.raises(ServiceValidationError) as err:
            await entity.async_set_native_value(70.0)
    else:
        assert (entity.native_min_value, entity.native_max_value) == (41, 86)
        assert entity.native_step == 1
        assert entity.native_unit_of_measurement == UnitOfTemperature.FAHRENHEIT
        with pytest.raises(ServiceValidationError) as err:
            await entity.async_set_native_value(21.5)
    assert err.value.translation_key == "boost_temperature_out_of_range"
    assert set(err.value.translation_placeholders) == {"min", "max", "unit"}


async def test_max_temperature(hass, mock_smartbox, config_entry):
    """The max temperature number exists only where the setup advertises it."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_max_temperature_entity_id(mock_node)
    state = hass.states.get(entity_id)
    # check basic properties
    assert state.object_id.startswith(
        get_object_id(get_max_temperature_entity_name(mock_node))
    )
    assert state.name == f"{mock_node['name']} Maximum"
    assert state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Maximum"
    unique_id = get_node_unique_id(mock_device, mock_node, "max_temperature")
    assert entity_id == get_entity_id_from_unique_id(hass, NUMBER_DOMAIN, unique_id)

    status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    units = status["units"]
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    # TEMPERATURE-class numbers display in the HA instance's unit system;
    # the wire's max_stemp_limit is Celsius and converts to the node's unit.
    expected_initial = round_temp(
        hass,
        convert_temp(
            hass,
            units,
            max_limit_value(units, float(setup["max_stemp_limit"])),
        ),
    )
    assert float(state.state) == expected_initial
    # TEMPERATURE-class numbers display bounds in the HA instance's unit
    # system. Native: 16-30 °C in 1-degree steps, or 60-86 °F in
    # 2-degree steps.
    if units == "C":
        assert state.attributes["min"] == 16
        assert state.attributes["max"] == 30
        assert state.attributes["step"] == 1
    else:
        assert state.attributes["min"] == 15.5  # floor(15.6 °C from 60 °F)
        assert state.attributes["max"] == 30.0  # ceil(30.0 °C from 86 °F)
        assert state.attributes["step"] == 2

    # Write via HA's service: HA units in, stored in the device's scale.
    min_val = state.attributes["min"]
    max_val = state.attributes["max"]
    test_value = round((min_val + max_val) / 2, 1)
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: test_value},
        blocking=True,
    )
    await confirm_setup_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
    await async_update_entity(hass, entity_id)
    state = hass.states.get(entity_id)
    device_unit = (
        UnitOfTemperature.CELSIUS if units == "C" else UnitOfTemperature.FAHRENHEIT
    )
    native = TemperatureConverter.convert(
        test_value, hass.config.units.temperature_unit, device_unit
    )
    # The wire's field is Celsius; an F input converts (and quantizes).
    expected_wire = node_units_to_celsius(native, units)
    # The entity renders the stored (Celsius) value back in the node's unit.
    expected_display = round_temp(
        hass, convert_temp(hass, units, celsius_to_node_units(expected_wire, units))
    )
    assert float(state.state) == expected_display
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert float(setup["max_stemp_limit"]) == pytest.approx(expected_wire)

    # The acm node's setup has no max_stemp_limit: no entity.
    acm_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    assert hass.states.get(get_max_temperature_entity_id(acm_node)) is None


async def test_prog_temps(hass, mock_smartbox, config_entry):
    """Frost/Eco/Comfort number entities track and write the status profiles."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    units = status["units"]
    status_keys = {"frost": "ice_temp", "eco": "eco_temp", "comfort": "comf_temp"}
    entity_ids = {
        profile: get_prog_temp_entity_id(mock_node, profile) for profile in status_keys
    }
    for profile, entity_id in entity_ids.items():
        state = hass.states.get(entity_id)
        assert state.name == f"{mock_node['name']} {profile.capitalize()}"
        assert (
            state.attributes[ATTR_FRIENDLY_NAME]
            == f"{mock_node['name']} {profile.capitalize()}"
        )
        expected_display = round_temp(
            hass, convert_temp(hass, units, float(status[status_keys[profile]]))
        )
        assert float(state.state) == expected_display

    # Setting one profile temp sends the app-shaped 4-key body: the
    # untouched profiles go out with their current values plus units.
    # Write via HA's service (HA units), stored in the device's scale.
    eco_entity_id = entity_ids["eco"]
    eco_state = hass.states.get(eco_entity_id)
    test_value = round(
        (eco_state.attributes["min"] + eco_state.attributes["max"]) / 2, 1
    )
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: eco_entity_id, ATTR_VALUE: test_value},
        blocking=True,
    )
    await confirm_write(hass, runtime, fake, mock_device["dev_id"], mock_node)
    for entity_id in entity_ids.values():
        await async_update_entity(hass, entity_id)
    new_status = await fake.get_node_status(mock_device["dev_id"], ref(mock_node))
    device_unit = (
        UnitOfTemperature.CELSIUS if units == "C" else UnitOfTemperature.FAHRENHEIT
    )
    expected_native = TemperatureConverter.convert(
        test_value, hass.config.units.temperature_unit, device_unit
    )
    if units == "F":
        # Fahrenheit quantizes to whole degrees (the vendor app's F grid)
        expected_native = math.floor(expected_native + 0.5)
    assert float(new_status["eco_temp"]) == pytest.approx(expected_native)
    # Untouched values were sent back with the same value, quantized onto the
    # device's setpoint grid (the client formats every posted setpoint; a real
    # F node already holds whole degrees).
    grid = 0.5 if units == "C" else 1.0
    assert float(new_status["ice_temp"]) == pytest.approx(
        float(status["ice_temp"]), abs=grid
    )
    assert float(new_status["comf_temp"]) == pytest.approx(
        float(status["comf_temp"]), abs=grid
    )
    assert new_status["units"] == units
    assert float(hass.states.get(eco_entity_id).state) == round_temp(
        hass, convert_temp(hass, units, expected_native)
    )
    for profile in ("frost", "comfort"):
        # The entity renders what the device now holds: the untouched profiles
        # were re-posted through the setpoint formatter (grid-quantized).
        expected_display = round_temp(
            hass, convert_temp(hass, units, float(new_status[status_keys[profile]]))
        )
        assert float(hass.states.get(entity_ids[profile]).state) == expected_display

    # htr_mod nodes manage profiles via comfort_temp/eco_offset: no entities.
    other_device = (await mock_smartbox.session.get_devices())[1]
    other_node = (await mock_smartbox.session.get_nodes(other_device["dev_id"]))[1]
    assert hass.states.get(get_prog_temp_entity_id(other_node, "eco")) is None


async def test_boost_numbers_malformed_extra_options_render_none(
    hass, mock_smartbox, config_entry
):
    """Malformed boost params in extra_options read as None, never raise."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime, fake = await seed_stack(hass, mock_smartbox)
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[1]
    await push_setup_update(
        hass,
        runtime,
        fake,
        mock_device["dev_id"],
        mock_node,
        {"extra_options": {"boost_temp": "garbage", "boost_time": "garbage"}},
    )
    number_entities = hass.data["entity_components"]["number"]
    temp_entity = number_entities.get_entity(get_boost_temperature_entity_id(mock_node))
    duration_entity = number_entities.get_entity(
        get_boost_duration_entity_id(mock_node)
    )
    assert temp_entity is not None
    assert duration_entity is not None
    assert temp_entity.native_value is None
    assert duration_entity.native_value is None


async def test_node_power_limit(hass, mock_smartbox, config_entry):
    """The node's OWN limit: a number + no-limit switch pair on the pmo node."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[1]  # the pmo box
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    assert mock_node["type"] == "pmo"
    entity_id = get_node_power_limit_entity_id(mock_node)

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["unlimited_value"] == 0
    assert float(state.state) == 1500  # the setup's power_limit

    # A write goes to the node's own endpoint and lands in its setup.
    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: 2000},
        blocking=True,
    )
    await hass.async_block_till_done()
    setup = await fake.get_node_setup(mock_device["dev_id"], ref(mock_node))
    assert setup["power_limit"] == 2000
    await async_update_entity(hass, entity_id)
    assert float(hass.states.get(entity_id).state) == 2000
