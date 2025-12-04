from datetime import datetime
import logging
import time
from unittest.mock import AsyncMock, patch

from dateutil import tz
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_LOCKED, STATE_UNAVAILABLE
from homeassistant.helpers.entity_component import async_update_entity
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartbox.const import (
    CONF_HISTORY_CONSUMPTION,
    DOMAIN,
    HistoryConsumptionStatus,
    SmartboxNodeType,
)
from custom_components.smartbox.sensor import (
    BoostEndTimeSensor,
    PowerSensor,
    TotalConsumptionSensor,
)

from .mocks import (
    active_or_charging_update,
    get_entity_id_from_unique_id,
    get_node_unique_id,
    get_object_id,
    get_sensor_entity_id,
    get_sensor_entity_name,
    is_heater_node,
)
from .test_utils import convert_temp, round_temp

_LOGGER = logging.getLogger(__name__)


def _check_temp_state(hass, mock_node_status, state):
    assert round_temp(hass, float(state.state)) == round_temp(
        hass,
        convert_temp(hass, mock_node_status["units"], float(mock_node_status["mtemp"])),
    )


@pytest.mark.asyncio
async def test_basic_temp(hass, mock_smartbox, config_entry, recorder_mock):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 30
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if not is_heater_node(mock_node):
                continue
            entity_id = get_sensor_entity_id(mock_node, "temperature")
            state = hass.states.get(entity_id)

            # check basic properties
            assert state.object_id.startswith(
                get_object_id(get_sensor_entity_name(mock_node, "temperature"))
            )
            assert state.entity_id.startswith(
                get_sensor_entity_id(mock_node, "temperature")
            )
            assert state.name == f"{mock_node['name']} Temperature"
            assert (
                state.attributes[ATTR_FRIENDLY_NAME]
                == f"{mock_node['name']} Temperature"
            )
            unique_id = get_node_unique_id(mock_device, mock_node, "temperature")
            assert entity_id == get_entity_id_from_unique_id(
                hass, SENSOR_DOMAIN, unique_id
            )

            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            assert state.attributes[ATTR_LOCKED] == mock_node_status["locked"]
            _check_temp_state(hass, mock_node_status, state)

            mock_node_status = mock_smartbox.generate_socket_status_update(
                mock_device,
                mock_node,
                {"mtemp": str(float(mock_node_status["mtemp"]) + 1)},
            )

            await async_update_entity(hass, entity_id)
            new_state = hass.states.get(entity_id)
            assert new_state.state != state.state
            _check_temp_state(hass, mock_node_status, new_state)

            # test unavailable
            mock_node_status = mock_smartbox.generate_socket_node_unavailable(
                mock_device, mock_node
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == STATE_UNAVAILABLE

            mock_node_status = mock_smartbox.generate_new_socket_status(
                mock_device, mock_node
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state != STATE_UNAVAILABLE


@pytest.mark.asyncio
async def test_basic_power(hass, mock_smartbox, config_entry, recorder_mock):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 30
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if (
                not is_heater_node(mock_node)
                or mock_node["type"] == SmartboxNodeType.HTR_MOD
            ):
                continue
            entity_id = get_sensor_entity_id(mock_node, "power")
            state = hass.states.get(entity_id)

            # check basic properties
            assert state.object_id.startswith(
                get_object_id(get_sensor_entity_name(mock_node, "power"))
            )
            assert state.entity_id.startswith(get_sensor_entity_id(mock_node, "power"))
            assert state.name == f"{mock_node['name']} Power"
            assert state.attributes[ATTR_FRIENDLY_NAME] == f"{mock_node['name']} Power"
            unique_id = get_node_unique_id(mock_device, mock_node, "power")
            assert entity_id == get_entity_id_from_unique_id(
                hass, SENSOR_DOMAIN, unique_id
            )

            # make sure it's active/charging
            mock_smartbox.generate_socket_status_update(
                mock_device,
                mock_node,
                active_or_charging_update(node_type=mock_node["type"], active=True),
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            assert state.attributes[ATTR_LOCKED] == mock_node_status["locked"]
            assert float(state.state) == pytest.approx(float(mock_node_status["power"]))

            # make sure it's inactive/not charging
            mock_smartbox.generate_socket_status_update(
                mock_device,
                mock_node,
                active_or_charging_update(node_type=mock_node["type"], active=False),
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            assert float(state.state) == 0

            # test unavailable
            mock_node_status = mock_smartbox.generate_socket_node_unavailable(
                mock_device, mock_node
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == STATE_UNAVAILABLE

            mock_node_status = mock_smartbox.generate_new_socket_status(
                mock_device, mock_node
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state != STATE_UNAVAILABLE


@pytest.mark.asyncio
async def test_unavailable(hass, mock_smartbox_unavailable, recorder_mock):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="test_username_1",
        data=mock_smartbox_unavailable.config[DOMAIN],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 23
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox_unavailable.session.get_devices():
        for mock_node in await mock_smartbox_unavailable.session.get_nodes(
            mock_device["dev_id"]
        ):
            if not is_heater_node(mock_node):
                continue
            sensor_types = (
                ["temperature"]
                if mock_node["type"] == SmartboxNodeType.HTR_MOD
                else ["temperature", "power"]
            )
            for _sensor_type in sensor_types:
                entity_id = get_sensor_entity_id(mock_node, "temperature")

                state = hass.states.get(entity_id)
                assert state.state == STATE_UNAVAILABLE


@pytest.mark.asyncio
async def test_basic_charge_level(hass, mock_smartbox, recorder_mock, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 30
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1

    assert DOMAIN in hass.config.components

    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            # Only supported on acm nodes
            if mock_node["type"] != SmartboxNodeType.ACM:
                continue

            entity_id = get_sensor_entity_id(mock_node, "charge_level")
            state = hass.states.get(entity_id)

            # check basic properties
            assert state.object_id.startswith(
                get_object_id(get_sensor_entity_name(mock_node, "charge_level"))
            )
            assert state.entity_id.startswith(
                get_sensor_entity_id(mock_node, "charge_level")
            )
            assert state.name == f"{mock_node['name']} Charge Level"
            assert (
                state.attributes[ATTR_FRIENDLY_NAME]
                == f"{mock_node['name']} Charge Level"
            )
            unique_id = get_node_unique_id(mock_device, mock_node, "charge_level")
            assert entity_id == get_entity_id_from_unique_id(
                hass, SENSOR_DOMAIN, unique_id
            )

            # Check charge level is correct
            mock_smartbox.generate_socket_status_update(
                mock_device,
                mock_node,
                active_or_charging_update(node_type=mock_node["type"], active=True),
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            assert state.attributes[ATTR_LOCKED] == mock_node_status["locked"]

            # Calculate expected charge level based on heater model type
            # Model 1C (storage heaters) use current_charge_per
            # Other heater models use charge_level directly

            # Extract model code from version.pid (preferred) or product_id (fallback)
            version_info = mock_node.get("version", {})
            model_code = ""
            if isinstance(version_info, dict):
                pid = version_info.get("pid", "")
                if pid and len(pid) >= 4:
                    model_code = pid.upper()[2:4]

            if not model_code:
                # Fallback to product_id
                product_id = mock_node.get("product_id", "").upper()
                model_code = product_id[2:4] if len(product_id) >= 4 else ""

            if model_code == "1C":
                # Model 1C: use current_charge_per field
                expected_charge = mock_node_status.get("current_charge_per", 0)
            else:
                expected_charge = mock_node_status.get("charge_level", 0)

            assert int(state.state) == pytest.approx(int(expected_charge))

            # Update charge level via socket
            # Use appropriate field based on heater model type
            if model_code == "1C":
                # Model 1C heaters: update current_charge_per
                mock_smartbox.generate_socket_status_update(
                    mock_device, mock_node, {"current_charge_per": 5}
                )
            else:
                # Other heater models: update charge_level directly
                mock_smartbox.generate_socket_status_update(
                    mock_device, mock_node, {"charge_level": 5}
                )

            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert int(state.state) == 5

            # test unavailable
            mock_node_status = mock_smartbox.generate_socket_node_unavailable(
                mock_device, mock_node
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state == STATE_UNAVAILABLE

            mock_node_status = mock_smartbox.generate_new_socket_status(
                mock_device, mock_node
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state != STATE_UNAVAILABLE


@pytest.mark.asyncio
async def test_update_statistics_start(hass, mock_smartbox, config_entry):
    mock_node = AsyncMock()
    mock_node.get_samples.return_value = [{"t": 1739966400, "counter": 100}]
    sensor = TotalConsumptionSensor(mock_node, config_entry)
    sensor.hass = hass
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.START,
        },
    )

    with (
        patch.object(hass.config_entries, "async_update_entry") as mock_update_entry,
        patch(
            "custom_components.smartbox.sensor.async_import_statistics"
        ) as mock_import_statistics,
    ):
        await sensor.update_statistics()

        assert mock_node.get_samples.call_count == 3
        mock_update_entry.assert_called_once_with(
            entry=config_entry,
            options={
                **config_entry.options,
                CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.AUTO,
            },
        )
        assert mock_import_statistics.called


async def test_update_statistics_auto(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_node = AsyncMock()
    mock_node.get_samples.return_value = [{"t": 1739966400, "counter": 100}]
    sensor = TotalConsumptionSensor(mock_node, config_entry)
    sensor.hass = hass
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.AUTO,
        },
    )

    with patch(
        "custom_components.smartbox.sensor.async_import_statistics"
    ) as mock_import_statistics:
        await sensor.update_statistics()

        mock_node.get_samples.assert_called_once()
        assert mock_import_statistics.called


@pytest.mark.asyncio
async def test_update_statistics_off(hass, mock_smartbox, config_entry):
    mock_node = AsyncMock()
    mock_node.get_samples = AsyncMock(return_value=[{"t": time.time(), "counter": 100}])
    sensor = TotalConsumptionSensor(mock_node, config_entry)
    sensor.hass = hass
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.OFF,
        },
    )

    with patch(
        "custom_components.smartbox.sensor.async_import_statistics"
    ) as mock_import_statistics:
        await sensor.update_statistics()

        mock_import_statistics.assert_not_called()


@pytest.mark.asyncio
async def test_async_update_pmo(hass, mock_smartbox, config_entry):
    mock_node = AsyncMock()
    mock_node.node_type = SmartboxNodeType.PMO
    mock_node.update_power = AsyncMock()
    sensor = PowerSensor(mock_node, config_entry)
    sensor.hass = hass

    with patch.object(sensor, "async_write_ha_state") as mock_write_ha_state:
        await sensor._async_update_pmo(None)

        mock_node.update_power.assert_called_once()
        mock_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_async_update_pmo_non_pmo_node(hass, mock_smartbox, config_entry):
    mock_node = AsyncMock()
    mock_node.node_type = SmartboxNodeType.HTR
    mock_node.update_power = AsyncMock()
    sensor = PowerSensor(mock_node, config_entry)
    sensor.hass = hass

    with patch.object(sensor, "async_write_ha_state") as mock_write_ha_state:
        await sensor._async_update_pmo(None)

        mock_node.update_power.assert_not_called()
        mock_write_ha_state.assert_not_called()


@pytest.mark.asyncio
async def test_adjust_short_term_statistics(hass, mock_smartbox, config_entry):
    mock_node = AsyncMock()
    sensor = TotalConsumptionSensor(mock_node, config_entry)
    sensor.hass = hass
    sensor.entity_id = "sensor.test_total_consumption"
    sensor.native_unit_of_measurement = "kWh"

    last_stat = {
        sensor.entity_id: [
            {
                "start": 1739966400,
                "sum": 50,
                "state": 100,
            }
        ]
    }

    with (
        patch("custom_components.smartbox.sensor.get_instance") as mock_get_instance,
        patch(
            "custom_components.smartbox.sensor.get_last_short_term_statistics",
            return_value=last_stat,
        ),
        patch.object(hass.loop, "run_in_executor", return_value=last_stat),
    ):
        mock_instance = mock_get_instance.return_value
        mock_instance.async_add_executor_job = AsyncMock(return_value=last_stat)
        await sensor._adjust_short_term_statistics()
        mock_instance.async_adjust_statistics.assert_called_once_with(
            statistic_id=sensor.entity_id,
            start_time=datetime.fromtimestamp(1739966400, tz.tzlocal()),
            sum_adjustment=50,
            adjustment_unit="kWh",
        )


@pytest.mark.asyncio
async def test_adjust_short_term_statistics_no_adjustment(
    hass, mock_smartbox, config_entry
):
    mock_node = AsyncMock()
    sensor = TotalConsumptionSensor(mock_node, config_entry)
    sensor.hass = hass
    sensor.entity_id = "sensor.test_total_consumption"
    sensor.native_unit_of_measurement = "kWh"

    last_stat = {
        sensor.entity_id: [
            {
                "start": 1739966400,
                "sum": 100,
                "state": 100,
            }
        ]
    }

    with (
        patch("custom_components.smartbox.sensor.get_instance") as mock_get_instance,
        patch(
            "custom_components.smartbox.sensor.get_last_short_term_statistics",
            return_value=last_stat,
        ),
        patch.object(hass.loop, "run_in_executor", return_value=last_stat),
    ):
        mock_instance = mock_get_instance.return_value
        mock_instance.async_add_executor_job = AsyncMock(return_value=last_stat)
        await sensor._adjust_short_term_statistics()

        mock_instance.async_adjust_statistics.assert_not_called()


@pytest.mark.asyncio
async def test_native_value_boost_end_time_sensor(hass, mock_smartbox, config_entry):
    mock_node = AsyncMock()
    mock_node.boost = True
    mock_node.boost_end_min = 90  # 1 hour and 30 minutes
    sensor = BoostEndTimeSensor(mock_node, config_entry)
    sensor.hass = hass

    with patch("custom_components.smartbox.sensor.dt.now") as mock_now:
        mock_now.return_value = datetime(2023, 10, 10, 0, 0, 0, tzinfo=tz.tzlocal())
        expected_time = datetime(2023, 10, 10, 1, 30, tzinfo=tz.tzlocal())
        assert sensor.native_value == expected_time

        # Test boost end time is in the past, should return next day
        mock_node.boost_end_min = 30  # 30 minutes
        mock_now.return_value = datetime(2023, 10, 10, 1, 0, 0, tzinfo=tz.tzlocal())
        expected_time = datetime(2023, 10, 11, 0, 30, tzinfo=tz.tzlocal())
        assert sensor.native_value == expected_time

        # Test no boost
        mock_node.boost = False
        assert sensor.native_value is None
