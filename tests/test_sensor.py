import asyncio
from datetime import datetime
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

from dateutil import tz
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import ATTR_FRIENDLY_NAME, ATTR_LOCKED, STATE_UNAVAILABLE
from homeassistant.helpers.entity_component import async_update_entity
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from smartbox.error import APIUnavailableError, SmartboxError

from custom_components.smartbox.const import (
    CONF_HISTORY_CONSUMPTION,
    DOMAIN,
    HistoryConsumptionStatus,
    SmartboxNodeType,
)
from custom_components.smartbox.models import rtc_time_to_datetime
from custom_components.smartbox.sensor import (
    BoostEndTimeSensor,
    ChargeLevelSensor,
    PowerSensor,
    TotalConsumptionSensor,
)

from .mocks import (
    MOCK_DEVICE_RTC,
    active_or_charging_update,
    get_clock_drift_sensor_entity_id,
    get_device_unique_id,
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
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 39
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
            # Availability flips via the library's grace window (patched
            # to 0.05 s in tests), not synchronously with the frame.
            await asyncio.sleep(0.1)
            await hass.async_block_till_done()
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
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 39
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
            # Availability flips via the library's grace window (patched
            # to 0.05 s in tests), not synchronously with the frame.
            await asyncio.sleep(0.1)
            await hass.async_block_till_done()
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
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 39
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
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 39
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
            assert int(state.state) == pytest.approx(
                int(mock_node_status["charge_level"])
            )

            # Update charge level via socket
            mock_smartbox.generate_socket_status_update(
                mock_device, mock_node, {"charge_level": 5}
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            assert int(state.state) == 5

            # test unavailable
            mock_node_status = mock_smartbox.generate_socket_node_unavailable(
                mock_device, mock_node
            )
            # Availability flips via the library's grace window (patched
            # to 0.05 s in tests), not synchronously with the frame.
            await asyncio.sleep(0.1)
            await hass.async_block_till_done()
            state = hass.states.get(entity_id)
            assert state.state == STATE_UNAVAILABLE

            mock_node_status = mock_smartbox.generate_new_socket_status(
                mock_device, mock_node
            )
            await async_update_entity(hass, entity_id)
            state = hass.states.get(entity_id)
            assert state.state != STATE_UNAVAILABLE


def test_charge_level_sensor_model_1c():
    """Test that model code 1C uses current_charge_per instead of charge_level."""
    sensor = object.__new__(ChargeLevelSensor)
    sensor._node = MagicMock()
    sensor._node.get_model_code.return_value = "1C"

    sensor._status = {"current_charge_per": 75, "charge_level": 3}
    assert sensor.native_value == 75

    sensor._status = {"charge_level": 3}
    assert sensor.native_value == 0

    sensor._node.get_model_code.return_value = None
    sensor._status = {"current_charge_per": 75, "charge_level": 3}
    assert sensor.native_value == 3


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

    # boost_end_min is the minute of the day (UTC) at which boost ends
    with patch(
        "custom_components.smartbox.models.dt_util.utcnow",
        return_value=datetime(2023, 10, 10, 1, 0, tzinfo=dt_util.UTC),
    ):
        expected_time = datetime(2023, 10, 10, 1, 30, tzinfo=dt_util.UTC)
        assert sensor.native_value is not None
        assert sensor.native_value.astimezone(dt_util.UTC) == expected_time

        # Test boost end time is in the past, should return next day
        mock_node.boost_end_min = 30  # 30 minutes
        expected_time = datetime(2023, 10, 11, 0, 30, tzinfo=dt_util.UTC)
        assert sensor.native_value is not None
        assert sensor.native_value.astimezone(dt_util.UTC) == expected_time

        # Test no boost
        mock_node.boost = False
        assert sensor.native_value is None


@pytest.mark.asyncio
async def test_transient_sync_lost_frame_keeps_last_status(
    hass, mock_smartbox, config_entry, recorder_mock
):
    """Transient {"sync_status": "lost"} websocket frames must not wipe status.

    Regression test (live-hardware finding, 2026-09-26, see
    ../smartbox api-notes.md): after every accepted write the server pushes
    /htr/<addr>/status with body {"sync_status": "lost"} before the next full
    "ok" snapshot. The websocket handler used to replace the entity status
    with that 1-key frame, so properties indexing mtemp/power/duty raised
    KeyError until the "ok" frame arrived.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_sensor_entity_id(mock_node, "temperature")
    before = hass.states.get(entity_id)
    assert before is not None
    assert before.state != STATE_UNAVAILABLE

    # Transient post-write frame: 1-key body, exactly as captured live, and
    # (per the 2026-09-30 probes) NOT guaranteed to fire at all — when it
    # does, it is quickly followed by the confirming "ok" snapshot. Send
    # the pair back-to-back so the test exercises exactly the transient
    # pattern; the entity must keep its last full snapshot (no KeyError,
    # no state churn).
    # A bare lost frame is never DISPATCHED to entities (models.py only
    # forwards ok frames), so the snapshot must survive it untouched:
    # pinned by object identity. Contents must NOT be asserted here -
    # the mock's frame bodies alias its persistent socket dict and the
    # unavailable generator flips sync_status in place; production
    # frames are fresh dicts per message. The skip GUARD itself is
    # pinned in test_climate's write-to-unreachable test (the only path
    # that dispatches a lost-marked body: set_status's optimistic
    # merge).
    entity = hass.data["entity_components"]["sensor"].get_entity(entity_id)
    assert entity is not None
    status_ref = entity._status
    mock_smartbox.generate_socket_node_unavailable(mock_device, mock_node)
    await asyncio.sleep(0.1)  # grace window (patched 0.05 s) expires
    await hass.async_block_till_done()
    assert entity._status is status_ref
    assert entity.native_value is not None
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    mock_node_status = mock_smartbox.generate_new_socket_status(
        mock_device, mock_node
    )
    await hass.async_block_till_done()
    _check_temp_state(hass, mock_node_status, hass.states.get(entity_id))


@pytest.mark.asyncio
async def test_clock_drift_sensor(hass, mock_smartbox, config_entry, recorder_mock):
    """One clock drift sensor per box device, with the raw RTC as attributes."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    for mock_device in await mock_smartbox.session.get_devices():
        entity_id = get_clock_drift_sensor_entity_id(mock_device)
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.name == f"{mock_device['name']} Clock Drift"
        assert entity_id == get_entity_id_from_unique_id(
            hass, SENSOR_DOMAIN, get_device_unique_id(mock_device, "rtc_drift")
        )
        assert state.attributes["rtc"] == MOCK_DEVICE_RTC
        # The RTC is parsed and compared against HA time: the state must be
        # a number (negative for a clock far behind), not unknown/none.
        float(state.state)
        assert state.state != "unknown"


def test_rtc_time_to_datetime_0_indexed_month():
    """September must report n=8 (0-indexed month, live-verified shape)."""
    rtc = {"d": 27, "h": 9, "m": 55, "n": 8, "s": 24, "w": 0, "y": 2026}
    parsed = rtc_time_to_datetime(rtc)
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.month == 9
    assert parsed.day == 27
    assert (parsed.hour, parsed.minute, parsed.second) == (9, 55, 24)


def test_rtc_time_to_datetime_rejects_unparseable():
    """Unexpected shapes (e.g. a 1-indexed December n=12) yield None."""
    assert rtc_time_to_datetime({"d": 1, "h": 0, "m": 0, "n": 12, "s": 0, "y": 2026}) is None
    assert rtc_time_to_datetime({"y": 2026}) is None
    assert rtc_time_to_datetime(None) is None


async def test_polling_sensors_follow_node_availability(
    hass, mock_smartbox, config_entry, recorder_mock
):
    """Polling node sensors go Unavailable with their node, REST being fine."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_ids = [
        get_entity_id_from_unique_id(
            hass, SENSOR_DOMAIN, get_node_unique_id(mock_device, mock_node, key)
        )
        for key in ("total_consumption", "schedule")
    ]
    for entity_id in entity_ids:
        assert hass.states.get(entity_id).state != STATE_UNAVAILABLE

    mock_smartbox.generate_socket_node_unavailable(mock_device, mock_node)
    await asyncio.sleep(0.3)
    await hass.async_block_till_done()
    for entity_id in entity_ids:
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    mock_smartbox.generate_new_socket_status(mock_device, mock_node)
    await hass.async_block_till_done()
    for entity_id in entity_ids:
        assert hass.states.get(entity_id).state != STATE_UNAVAILABLE


@pytest.mark.asyncio
async def test_update_statistics_survives_api_error(hass, mock_smartbox, config_entry):
    """A failing import is logged and keeps the history option on START."""
    mock_node = AsyncMock()
    mock_node.get_samples.side_effect = APIUnavailableError("down")
    sensor = TotalConsumptionSensor(mock_node, config_entry)
    sensor.hass = hass
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.START,
        },
    )

    await sensor.update_statistics()

    assert (
        config_entry.options[CONF_HISTORY_CONSUMPTION] == HistoryConsumptionStatus.START
    )


@pytest.mark.asyncio
async def test_total_consumption_update_marks_api_error_unavailable(
    hass, mock_smartbox, config_entry
):
    """Any SmartboxError while polling samples makes the sensor unavailable."""
    mock_node = AsyncMock()
    mock_node.update_samples.side_effect = SmartboxError("boom")
    sensor = TotalConsumptionSensor(mock_node, config_entry)
    sensor.hass = hass

    await sensor.async_update()

    assert sensor._api_available is False


async def test_set_schedule_service_targets_schedule_entity(
    hass, mock_smartbox, config_entry, recorder_mock
):
    """smartbox.set_schedule writes the programme of the targeted heater only."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_entity_id_from_unique_id(
        hass, SENSOR_DOMAIN, get_node_unique_id(mock_device, mock_node, "schedule")
    )
    # Non-schedule sensors targeted by the same call are ignored.
    other_id = get_sensor_entity_id(mock_node, "temperature")

    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {"entity_id": [entity_id, other_id], "schedule": {"1": [2] * 24}},
        blocking=True,
    )

    mock_smartbox.session.set_node_prog.assert_awaited_once()
