"""Sensor platform.

The mock world is delivered to the engine as a ws snapshot through the
runtime's FakeTransport (``seed_stack`` from test_climate), updates
arrive via the fan-out, and the polled client reads the platform still
performs (samples for the consumption statistics, box RTC for the drift
sensor) are stubbed at the client seam so the suite stays offline. The
statistics machinery keeps its recorder assertions.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from dateutil import tz
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_AREA_ID,
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_LOCKED,
    STATE_UNAVAILABLE,
    UnitOfEnergy,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.entity_component import async_update_entity
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from smartbox.core.state import NodeState
from smartbox.protocol import (
    AuthError,
    NodeRef,
    SmartboxError,
    TransportUnavailableError,
)
from smartbox.protocol.client import SmartboxClient
from smartbox.protocol.events import SocketClosed

from custom_components.smartbox import sensor as sensor_mod
from custom_components.smartbox.const import (
    CONF_HISTORY_CONSUMPTION,
    DOMAIN,
    FIELD_RESOLUTION,
    FIELD_SCHEDULE,
    SERVICE_SET_CHARGING_SCHEDULE,
    SERVICE_SET_SCHEDULE,
    SERVICE_SET_SCHEDULE_RESOLUTION,
    HistoryConsumptionStatus,
    SmartboxNodeType,
)
from custom_components.smartbox.history import HistoryImport
from custom_components.smartbox.identity import SmartboxNodeInfo
from custom_components.smartbox.mapping import rtc_time_to_datetime
from custom_components.smartbox.sensor import (
    BoostEndTimeSensor,
    ChargeLevelSensor,
    DutyCycleSensor,
    PowerSensor,
    ScheduleSensor,
    SmartboxNodeSensorEntity,
    TemperatureSensor,
    TotalConsumptionSensor,
    _usable_sample_rows,
)
from custom_components.smartbox.services import ServiceTargets
from tests.mocks import (
    MOCK_DEVICE_RTC,
    active_or_charging_update,
    get_clock_drift_sensor_entity_id,
    get_clock_drift_sensor_entity_name,
    get_connected_binary_sensor_entity_id,
    get_device_by_identifier,
    get_device_unique_id,
    get_entity_id_from_unique_id,
    get_node_unique_id,
    get_object_id,
    get_sensor_entity_id,
    get_sensor_entity_name,
    is_heater_node,
)
from tests.stack import push_status, seed_stack
from tests.test_utils import (
    assert_no_log_errors,
    convert_temp,
    numeric_state,
    round_temp,
    until,
)

_LOGGER = logging.getLogger(__name__)


async def _first_heater_schedule_id(mock_smartbox):
    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if is_heater_node(mock_node):
                return (
                    mock_device["dev_id"],
                    mock_node,
                    get_sensor_entity_id(mock_node, "schedule"),
                )
    pytest.fail("mock world has no heater node")


async def _first_htr_node(mock_smartbox):
    for mock_device in await mock_smartbox.session.get_devices():
        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if mock_node["type"] == SmartboxNodeType.HTR:
                return mock_device, mock_node
    pytest.fail("mock world has no htr node")


def _enable_clock_drift_sensor(hass, config_entry, mock_device):
    """Pre-enable the opt-in clock-drift sensor, as a user would."""
    er.async_get(hass).async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        get_device_unique_id(mock_device, "rtc_drift"),
        config_entry=config_entry,
        suggested_object_id=get_object_id(
            get_clock_drift_sensor_entity_name(mock_device)
        ),
        disabled_by=None,
    )


@pytest.fixture(autouse=True)
def stub_client_reads():
    """Stub the client reads the sensor platform polls on its own cadence.

    The engine carries no samples/RTC data (the ws never pushes them), so
    the platform's own client reads are served canned values here. The
    dedicated tests stub the entity-level seam (``_get_samples``) with
    concrete payloads; the drift test wants a RTC payload, so it is stubbed
    here.
    """
    with (
        patch(
            "smartbox.protocol.client.SmartboxClient.get_node_samples",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "smartbox.protocol.client.SmartboxClient.get_rtc_time",
            new=AsyncMock(return_value=dict(MOCK_DEVICE_RTC)),
        ),
    ):
        yield


def _check_temp_state(hass, mock_node_status, state):
    assert round_temp(hass, float(state.state)) == round_temp(
        hass,
        convert_temp(hass, mock_node_status["units"], float(mock_node_status["mtemp"])),
    )


async def test_basic_temp(hass, mock_smartbox, config_entry, caplog):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 33
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
            assert state.attributes[ATTR_LOCKED] is bool(mock_node_status["locked"])
            _check_temp_state(hass, mock_node_status, state)

            # mtemp bump arrives as a websocket status frame
            frame = dict(mock_node_status)
            frame["mtemp"] = str(float(mock_node_status["mtemp"]) + 1)
            await push_status(
                hass, runtime_of(hass), mock_device["dev_id"], mock_node, frame
            )
            new_state = hass.states.get(entity_id)
            assert new_state.state != state.state
            _check_temp_state(hass, frame, new_state)

            # test unavailable
            await push_status(
                hass,
                runtime_of(hass),
                mock_device["dev_id"],
                mock_node,
                {"sync_status": "lost"},
            )
            # Availability flips via the engine's grace window (patched
            # to 0.05 s in tests), not synchronously with the frame.
            await until(
                lambda eid=entity_id: hass.states.get(eid).state == STATE_UNAVAILABLE
            )
            state = hass.states.get(entity_id)
            assert state.state == STATE_UNAVAILABLE

            ok_status = dict(
                await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node)
            )
            await push_status(
                hass, runtime_of(hass), mock_device["dev_id"], mock_node, ok_status
            )
            state = hass.states.get(entity_id)
            _check_temp_state(hass, ok_status, state)

    # the lost-frame round-trip must be silent at ERROR level (the climate
    # round fixed the slot-replacement "Change subscriber raised" noise)
    assert_no_log_errors(caplog)


async def test_basic_power(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 33
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
            await _push_active(hass, mock_smartbox, mock_device, mock_node, active=True)
            state = hass.states.get(entity_id)
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            assert state.attributes[ATTR_LOCKED] is bool(mock_node_status["locked"])
            assert float(state.state) == pytest.approx(float(mock_node_status["power"]))

            # Storage controllers keep reporting status.power while idle;
            # ElectricHeater reports 0 unless active.
            await _push_active(
                hass, mock_smartbox, mock_device, mock_node, active=False
            )
            state = hass.states.get(entity_id)
            if mock_node["type"] == SmartboxNodeType.ACM:
                assert float(state.state) == pytest.approx(
                    float(mock_node_status["power"])
                )
            else:
                assert float(state.state) == 0

            # test unavailable
            await push_status(
                hass,
                runtime_of(hass),
                mock_device["dev_id"],
                mock_node,
                {"sync_status": "lost"},
            )
            # Availability flips via the engine's grace window (patched
            # to 0.05 s in tests), not synchronously with the frame.
            await until(
                lambda eid=entity_id: hass.states.get(eid).state == STATE_UNAVAILABLE
            )
            state = hass.states.get(entity_id)
            assert state.state == STATE_UNAVAILABLE

            ok_status = dict(
                await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node)
            )
            await push_status(
                hass, runtime_of(hass), mock_device["dev_id"], mock_node, ok_status
            )
            # a real number, not just "not unavailable": unknown and any
            # non-numeric rendering fail here
            numeric_state(hass, entity_id)


async def _push_active(hass, mock_smartbox, mock_device, mock_node, active: bool):
    """Push the full status frame with the active/charging flag set."""
    runtime = runtime_of(hass)
    dev_id = mock_device["dev_id"]
    body = dict(await mock_smartbox.session.get_status(dev_id, mock_node))
    body.update(active_or_charging_update(node_type=mock_node["type"], active=active))
    await push_status(hass, runtime, dev_id, mock_node, body)


def runtime_of(hass):
    """Return the entry's SmartboxRuntime (the new-stack wiring)."""
    return hass.config_entries.async_entries(DOMAIN)[0].runtime_data


async def test_unavailable(hass, mock_smartbox_unavailable):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="test_username_1",
        data=mock_smartbox_unavailable.config[DOMAIN],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox_unavailable)
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 33
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
            for sensor_type in sensor_types:
                entity_id = get_sensor_entity_id(mock_node, sensor_type)

                state = hass.states.get(entity_id)
                assert state.state == STATE_UNAVAILABLE


async def test_basic_charge_level(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)
    assert len(hass.states.async_entity_ids(SENSOR_DOMAIN)) == 33
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
            await _push_active(hass, mock_smartbox, mock_device, mock_node, active=True)
            state = hass.states.get(entity_id)
            mock_node_status = await mock_smartbox.session.get_status(
                mock_device["dev_id"], mock_node
            )
            assert state.attributes[ATTR_LOCKED] is bool(mock_node_status["locked"])
            assert int(state.state) == pytest.approx(
                int(mock_node_status["charge_level"])
            )

            # Update charge level via socket
            frame = dict(
                await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node)
            )
            frame["charge_level"] = 5
            await push_status(
                hass, runtime_of(hass), mock_device["dev_id"], mock_node, frame
            )
            state = hass.states.get(entity_id)
            assert int(state.state) == 5

            # test unavailable
            await push_status(
                hass,
                runtime_of(hass),
                mock_device["dev_id"],
                mock_node,
                {"sync_status": "lost"},
            )
            # Availability flips via the engine's grace window (patched
            # to 0.05 s in tests), not synchronously with the frame.
            await until(
                lambda eid=entity_id: hass.states.get(eid).state == STATE_UNAVAILABLE
            )
            state = hass.states.get(entity_id)
            assert state.state == STATE_UNAVAILABLE

            ok_status = dict(
                await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node)
            )
            await push_status(
                hass, runtime_of(hass), mock_device["dev_id"], mock_node, ok_status
            )
            # a real number, not just "not unavailable"
            numeric_state(hass, entity_id)


def test_water_storage_is_not_a_charging_service_target():
    """Water storage omits the charging schedule target; storage keeps it."""
    node = SimpleNamespace(
        node_type="acm",
        supported_wire_type=True,
        heater_surface=False,
        thermostat_surface=False,
        heater_node=True,
        boost_available=False,
        pid="082A0000",
        version={"pid": "082A0000"},
        setup={},
    )
    runtime = SimpleNamespace(services=ServiceTargets())
    with (
        patch.object(sensor_mod, "PowerSensor"),
        patch.object(sensor_mod, "TotalConsumptionSensor"),
        patch.object(sensor_mod, "ChargeLevelSensor"),
        patch.object(sensor_mod, "TargetChargeSensor"),
    ):
        sensor_mod._build_node_sensors(node, MagicMock(), runtime, MagicMock())
    assert runtime.services.charging == []

    node.pid = "08140000"
    node.version = {"pid": "08140000"}
    with (
        patch.object(sensor_mod, "PowerSensor"),
        patch.object(sensor_mod, "TotalConsumptionSensor"),
        patch.object(sensor_mod, "ChargeLevelSensor"),
        patch.object(sensor_mod, "TargetChargeSensor"),
    ):
        sensor_mod._build_node_sensors(node, MagicMock(), runtime, MagicMock())
    assert len(runtime.services.charging) == 1


def test_charge_level_sensor_model_1c():
    """The Helki storage controllers use current_charge_per."""
    sensor = object.__new__(ChargeLevelSensor)
    sensor._node = MagicMock()
    sensor._node.pid = "081C0000"
    sensor._node_state = lambda: NodeState(
        status={"current_charge_per": 75, "charge_level": 3}
    )
    assert sensor.native_value == 75

    sensor._node_state = lambda: NodeState(status={"charge_level": 3})
    assert sensor.native_value is None

    sensor._node.pid = "082E0000"
    sensor._node_state = lambda: NodeState(
        status={"current_charge_per": 75, "charge_level": 3}
    )
    assert sensor.native_value == 75

    sensor._node.pid = "08990000"
    sensor._node_state = lambda: NodeState(
        status={"current_charge_per": 75, "charge_level": 3}
    )
    assert sensor.native_value == 3


def test_charge_level_sensor_elnur_code_14_scales_stepped_level():
    """Elnur storage code 14 reports charge_level in 0..5 steps."""
    sensor = object.__new__(ChargeLevelSensor)
    sensor._node = MagicMock()
    sensor._node.pid = "08140000"
    sensor._node_state = lambda: NodeState(
        status={"current_charge_per": 75, "charge_level": 3}
    )
    assert sensor.native_value == 60

    sensor._node_state = lambda: NodeState(status={"charge_level": None})
    assert sensor.native_value is None


def test_charge_level_sensor_prefers_live_version_slot():
    """The live version slot overrides the discovery PID; empty falls back."""
    sensor = object.__new__(ChargeLevelSensor)
    sensor._node = SmartboxNodeInfo(
        device=MagicMock(),
        node_info={"type": "acm", "addr": 1},
        version={"pid": "0217"},
    )
    sensor._node_state = lambda: NodeState(
        status={"current_charge_per": 75, "charge_level": 3},
        version={"pid": "081c"},
    )
    assert sensor.native_value == 75

    sensor._node_state = lambda: NodeState(
        status={"current_charge_per": 75, "charge_level": 3},
        version=None,
    )
    assert sensor.native_value == 3


def test_charge_level_sensor_lost_body_renders_unknown():
    """A lost body renders None (unknown), never a stale 0 (climate round's rule)."""
    sensor = object.__new__(ChargeLevelSensor)
    sensor._node = MagicMock()
    sensor._node.get_model_code.return_value = "1C"
    sensor._node_state = lambda: NodeState(status={"sync_status": "lost"})
    assert sensor.native_value is None


def test_schedule_profiles_follow_a_late_version_pid():
    """A version frame supplies the PID a failed discovery read missed."""
    sensor = object.__new__(ScheduleSensor)
    sensor._node = SimpleNamespace(
        node_type="acm",
        pid=None,
        version={},
        setup={"operational_mode": 0},
    )
    sensor._node_state = lambda: NodeState(
        status={"units": "C"},
        setup={"operational_mode": 0},
        version={"pid": "081C0000", "fw_version": "1.8.0"},
    )
    sensor._profiles = ("frost", "eco", "comfort")
    sensor._attr_options = list(sensor._profiles)
    sensor._unsub_boundary = None
    sensor._refresh_profiles()
    assert sensor._profiles == ("frost", "eco", "comfort", "off")
    assert sensor.options == ["frost", "eco", "comfort", "off"]


def test_power_sensor_lost_body_renders_unknown():
    """A lost body renders None (unknown), never the 0 idle fallback."""
    sensor = object.__new__(PowerSensor)
    sensor._node = MagicMock()
    sensor._node.node_type = SmartboxNodeType.HTR
    sensor._pmo_power = None
    sensor._node_state = lambda: NodeState(status={"sync_status": "lost"})
    assert sensor.native_value is None


def test_power_sensor_active_body_without_power_reads_zero():
    """The app's active-path power read treats a missing field as zero."""
    sensor = object.__new__(PowerSensor)
    sensor._node = MagicMock()
    sensor._node.node_type = SmartboxNodeType.HTR
    sensor._pmo_power = None
    sensor._node_state = lambda: NodeState(status={"sync_status": "ok", "active": True})
    assert sensor.native_value == 0
    sensor._node_state = lambda: NodeState(
        status={"sync_status": "ok", "active": True, "power": "bad"}
    )
    assert sensor.native_value is None


def test_node_sensor_locked_attribute_omitted_when_absent():
    """The ``locked`` attribute key appears only when the wire carried it."""
    sensor = object.__new__(SmartboxNodeSensorEntity)
    sensor._node_state = lambda: NodeState(status={"stemp": "20.0", "units": "C"})
    assert sensor.extra_state_attributes == {}
    sensor._node_state = lambda: NodeState(status={"locked": True})
    assert sensor.extra_state_attributes[ATTR_LOCKED] is True
    sensor._node_state = lambda: NodeState(status={"locked": 0})
    assert sensor.extra_state_attributes[ATTR_LOCKED] is False
    sensor._node_state = lambda: NodeState(status={"locked": 1})
    assert sensor.extra_state_attributes[ATTR_LOCKED] is True


async def test_update_statistics_start(hass, mock_smartbox, config_entry):
    sensor = _consumption_sensor(hass, config_entry)
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.START,
        },
    )

    with (
        patch.object(
            sensor,
            "_get_samples",
            AsyncMock(return_value=[{"t": 1739966400, "counter": 100}]),
        ) as mock_get_samples,
        patch.object(hass.config_entries, "async_update_entry") as mock_update_entry,
        patch(
            "custom_components.smartbox.sensor.async_import_statistics"
        ) as mock_import_statistics,
    ):
        await sensor.update_statistics()

        assert mock_get_samples.call_count == 3
        mock_update_entry.assert_called_once_with(
            entry=config_entry,
            options={
                **config_entry.options,
                CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.AUTO,
            },
        )
        assert mock_import_statistics.called


async def test_update_statistics_without_a_recorder_defers_the_import(
    hass, config_entry
):
    """No recorder: no 3-year read, no KeyError, and a backoff is armed."""
    sensor = _consumption_sensor(hass, config_entry, recorder_available=False)
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.START,
        },
    )

    with (
        patch.object(sensor, "_get_samples", AsyncMock()) as get_samples,
        patch(
            "custom_components.smartbox.sensor.async_import_statistics"
        ) as mock_import_statistics,
    ):
        await sensor.update_statistics()

    get_samples.assert_not_called()
    mock_import_statistics.assert_not_called()
    node_key = sensor._node.node_id
    assert sensor._runtime.history.retry_at(node_key) > 0
    assert sensor._runtime.history.failure_count(node_key) == 1


async def test_update_statistics_skips_off_grid_samples(hass, config_entry):
    """An off-grid sample is dropped, never handed to the recorder.

    The recorder accepts only top-of-the-hour rows and raises on the rest,
    aborting the whole import; only the minute was checked before, so a
    minute-0/second-52 sample crashed the import task.
    """
    sensor = _consumption_sensor(hass, config_entry)
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.START,
        },
    )
    # minute 0, second 52 after the -1 h shift
    off_grid = int(time.time()) // 3600 * 3600 + 52

    with (
        patch.object(
            sensor,
            "_get_samples",
            AsyncMock(return_value=[{"t": off_grid, "counter": 100}]),
        ),
        patch(
            "custom_components.smartbox.sensor.async_import_statistics"
        ) as mock_import_statistics,
    ):
        await sensor.update_statistics()

    mock_import_statistics.assert_not_called()


async def test_first_setup_import_flips_the_flag_once_without_reloading(
    hass, mock_smartbox, config_entry, recorder_mock
):
    """The first-setup import runs off the setup path and flips the flag once.

    Every node imports in its own background task, the last one flips the
    entry-global flag, and the entry is NOT reloaded for it: a reload would tear
    down the websocket and cancel the imports still in flight.
    """
    assert (
        config_entry.options.get(
            CONF_HISTORY_CONSUMPTION, HistoryConsumptionStatus.START
        )
        == HistoryConsumptionStatus.START
    )
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        await until(
            lambda: (
                config_entry.options.get(CONF_HISTORY_CONSUMPTION)
                == HistoryConsumptionStatus.AUTO
            )
        )

    assert config_entry.state is ConfigEntryState.LOADED
    mock_reload.assert_not_called()


async def test_update_statistics_keeps_the_flag_while_a_node_is_pending(
    hass, config_entry
):
    """The flag flips only when EVERY node of the entry has imported.

    Flipping it from the first node to finish would leave the others reading a
    day of history on their next attempt, with the 3-year range never re-read.
    """
    sensor = _consumption_sensor(hass, config_entry, pending=("node_1", "node_2"))
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.START,
        },
    )

    with (
        patch.object(
            sensor,
            "_get_samples",
            AsyncMock(return_value=[{"t": 1739966400, "counter": 100}]),
        ) as mock_get_samples,
        patch.object(hass.config_entries, "async_update_entry") as mock_update_entry,
        patch("custom_components.smartbox.sensor.async_import_statistics"),
    ):
        await sensor.update_statistics()

        assert mock_get_samples.call_count == 3  # this node still owes 3 years
        mock_update_entry.assert_not_called()  # node_2 has not finished


async def test_update_statistics_keeps_the_node_pending_when_the_import_fails(
    hass, config_entry
):
    """A node is marked imported only once its rows have landed.

    Marking it before the import would let the entry flag flip on a failed
    import, and that node would only ever backfill a day afterwards.
    """
    sensor = _consumption_sensor(hass, config_entry, pending=("node_1",))
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.START,
        },
    )
    failure = "sample was not on a whole hour"

    with (
        patch.object(
            sensor,
            "_get_samples",
            AsyncMock(return_value=[{"t": 1739966400, "counter": 100}]),
        ),
        patch(
            "custom_components.smartbox.sensor.async_import_statistics",
            side_effect=HomeAssistantError(failure),
        ),
        patch.object(hass.config_entries, "async_update_entry") as mock_update_entry,
        pytest.raises(HomeAssistantError),
    ):
        await sensor.update_statistics()

    mock_update_entry.assert_not_called()  # the flag never flips
    assert sensor._runtime.history.pending("node_1") is True  # so it retries


async def test_update_statistics_backfills_a_day_for_an_imported_node(
    hass, config_entry
):
    """A node that already imported backfills the last day, once per interval."""
    sensor = _consumption_sensor(hass, config_entry, pending=())
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.START,
        },
    )

    with (
        patch.object(
            sensor,
            "_get_samples",
            AsyncMock(return_value=[{"t": 1739966400, "counter": 100}]),
        ) as mock_get_samples,
        patch("custom_components.smartbox.sensor.async_import_statistics"),
    ):
        await sensor.update_statistics()

        assert mock_get_samples.call_count == 1  # the last-day range only


async def test_update_statistics_debug_log_bounded(
    hass, mock_smartbox, config_entry, caplog
):
    """The statistics DEBUG line carries dev/node + row count, not payloads."""
    caplog.set_level(logging.DEBUG, logger="custom_components.smartbox.sensor")
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    sensor = _consumption_sensor(hass, config_entry)
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.AUTO,
        },
    )

    samples = [{"t": 1739966400 + i * 3600, "counter": 100 + i} for i in range(24)]
    with (
        patch.object(sensor, "_get_samples", AsyncMock(return_value=samples)),
        patch("custom_components.smartbox.sensor.async_import_statistics"),
    ):
        await sensor.update_statistics()

    lines = [
        r.getMessage() for r in caplog.records if "Insert statistics" in r.getMessage()
    ]
    assert len(lines) == 1
    assert "(24 rows)" in lines[0]
    assert "counter" not in lines[0]  # never the sample payload


async def test_update_statistics_auto(hass, mock_smartbox, config_entry):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    sensor = _consumption_sensor(hass, config_entry)
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.AUTO,
        },
    )

    with (
        patch.object(
            sensor,
            "_get_samples",
            AsyncMock(return_value=[{"t": 1739966400, "counter": 100}]),
        ) as mock_get_samples,
        patch(
            "custom_components.smartbox.sensor.async_import_statistics"
        ) as mock_import_statistics,
    ):
        await sensor.update_statistics()

        mock_get_samples.assert_called_once()
        assert mock_import_statistics.called


async def test_update_statistics_imports_aligned_hourly_rows(
    hass, mock_smartbox, config_entry
):
    """The import carries aligned hourly sum/state rows, in order.

    The recorder consumes these rows verbatim, so the alignment (sample time
    minus one hour, on the hour), the ordering and the sum/state values are
    the payload contract - asserting the call happened proves neither.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    sensor = _consumption_sensor(hass, config_entry)
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.AUTO,
        },
    )
    base = datetime(2026, 2, 19, 0, 0, tzinfo=tz.UTC)
    samples = [
        {"t": int(base.timestamp()) + 3600 * index, "counter": 100 + index}
        for index in (2, 0, 1)  # deliberately unsorted
    ]
    with (
        patch.object(sensor, "_get_samples", AsyncMock(return_value=samples)),
        patch(
            "custom_components.smartbox.sensor.async_import_statistics"
        ) as mock_import_statistics,
    ):
        await sensor.update_statistics()

    _hass, metadata, rows = mock_import_statistics.call_args.args
    assert metadata["statistic_id"] == "sensor.test_total_consumption"
    assert metadata["unit_of_measurement"] == UnitOfEnergy.WATT_HOUR
    # the conversion class the recorder needs to convert these rows at all
    assert metadata["unit_class"] == "energy"
    assert [row["start"].timestamp() for row in rows] == [
        base.timestamp() + 3600 * (index - 1) for index in (0, 1, 2)
    ]
    assert [row["sum"] for row in rows] == [100.0, 101.0, 102.0]
    assert [row["state"] for row in rows] == [100.0, 101.0, 102.0]


async def test_update_statistics_keeps_hourly_rows_in_a_half_hour_timezone(
    hass, mock_smartbox, config_entry
):
    """A +05:30 HA timezone must not turn every UTC hour into :30 and drop it."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    sensor = _consumption_sensor(hass, config_entry)
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.AUTO,
        },
    )
    base = datetime(2026, 2, 19, 0, 0, tzinfo=tz.UTC)
    samples = [
        {"t": int(base.timestamp()) + 3600, "counter": 101},
    ]
    with (
        patch.object(dt_util, "DEFAULT_TIME_ZONE", ZoneInfo("Asia/Kolkata")),
        patch.object(sensor, "_get_samples", AsyncMock(return_value=samples)),
        patch(
            "custom_components.smartbox.sensor.async_import_statistics"
        ) as mock_import_statistics,
    ):
        await sensor.update_statistics()

    _hass, _metadata, rows = mock_import_statistics.call_args.args
    assert [row["start"].timestamp() for row in rows] == [base.timestamp()]


async def test_a_failing_backfill_does_not_escape_the_interval_job(
    hass, mock_smartbox, config_entry
):
    """A transient last-day read surfaces as unavailable, not an exception."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    sensor = _consumption_sensor(hass, config_entry)
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.AUTO,
        },
    )
    with patch.object(
        sensor, "_get_samples", AsyncMock(side_effect=SmartboxError("boom"))
    ):
        await sensor.update_statistics()  # must not raise out of the interval
    assert sensor._samples_ok is False


async def test_update_statistics_keeps_aligned_rows_beside_off_grid(hass, config_entry):
    """An off-grid row is dropped per row; the aligned rows still import."""
    sensor = _consumption_sensor(hass, config_entry)
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.AUTO,
        },
    )
    base = 1739966400
    samples = [
        {"t": base, "counter": 100},
        {"t": base + 52, "counter": 101},  # off the hour grid
        {"t": base + 3600, "counter": 102},
    ]

    with (
        patch.object(sensor, "_get_samples", AsyncMock(return_value=samples)),
        patch(
            "custom_components.smartbox.sensor.async_import_statistics"
        ) as mock_import_statistics,
    ):
        await sensor.update_statistics()

    _hass, _metadata, rows = mock_import_statistics.call_args.args
    assert [row["sum"] for row in rows] == [100.0, 102.0]


async def test_update_statistics_off(hass, mock_smartbox, config_entry):
    """OFF reads no samples and imports nothing (the option's whole point)."""
    sensor = _consumption_sensor(hass, config_entry)
    hass.config_entries.async_update_entry(
        entry=config_entry,
        options={
            **config_entry.options,
            CONF_HISTORY_CONSUMPTION: HistoryConsumptionStatus.OFF,
        },
    )

    with (
        patch.object(
            sensor,
            "_get_samples",
            AsyncMock(return_value=[{"t": time.time(), "counter": 100}]),
        ) as mock_get_samples,
        patch(
            "custom_components.smartbox.sensor.async_import_statistics"
        ) as mock_import_statistics,
    ):
        await sensor.update_statistics()

    mock_get_samples.assert_not_called()
    mock_import_statistics.assert_not_called()


async def test_clock_drift_poll_failure_marks_unavailable(
    hass, mock_smartbox, config_entry
):
    """A failing RTC read surfaces unavailable instead of stale drift."""
    for mock_device in await mock_smartbox.session.get_devices():
        _enable_clock_drift_sensor(hass, config_entry, mock_device)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)
    mock_device = (await mock_smartbox.session.get_devices())[0]
    entity_id = get_clock_drift_sensor_entity_id(mock_device)
    numeric_state(hass, entity_id)

    with patch.object(
        SmartboxClient, "get_rtc_time", new=AsyncMock(side_effect=SmartboxError("boom"))
    ):
        await async_update_entity(hass, entity_id)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    with patch.object(
        SmartboxClient,
        "get_rtc_time",
        new=AsyncMock(return_value=dict(MOCK_DEVICE_RTC)),
    ):
        await async_update_entity(hass, entity_id)
    # the drift is a real number again, not merely "not unavailable"
    numeric_state(hass, entity_id)


async def test_set_schedule_service_errors(hass, mock_smartbox, config_entry):
    """Wrong targets, missing targets, bad days and API failures all raise."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)
    _dev_id, mock_node, entity_id = await _first_heater_schedule_id(mock_smartbox)

    # A target that is not a schedule sensor fails instead of no-opping.
    temperature_id = get_sensor_entity_id(mock_node, "temperature")
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_SCHEDULE,
            {ATTR_ENTITY_ID: [temperature_id], FIELD_SCHEDULE: {"0": [1] * 24}},
            blocking=True,
        )
    assert err.value.translation_key == "schedule_wrong_target"
    assert err.value.translation_placeholders == {"service": SERVICE_SET_SCHEDULE}

    # A call that resolves to no entity fails loudly instead of no-opping.
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_SCHEDULE,
            {FIELD_SCHEDULE: {"0": [1] * 24}},
            blocking=True,
        )
    assert err.value.translation_key == "service_no_target"
    assert err.value.translation_placeholders == {"service": SERVICE_SET_SCHEDULE}

    # An empty day is a validation error, not a ValueError.
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_SCHEDULE,
            {ATTR_ENTITY_ID: [entity_id], FIELD_SCHEDULE: {"0": []}},
            blocking=True,
        )
    assert err.value.translation_key == "schedule_invalid"
    assert err.value.translation_placeholders["entity_id"] == entity_id

    # A profile index outside the controller catalogue is rejected client-side.
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_SCHEDULE,
            {ATTR_ENTITY_ID: [entity_id], FIELD_SCHEDULE: {"0": [3] * 24}},
            blocking=True,
        )
    assert err.value.translation_key == "schedule_invalid"
    assert "profile index out of range" in err.value.translation_placeholders["error"]

    # An API failure on the write reaches the caller, not the log.
    fake.fail_next(500)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_SCHEDULE,
            {ATTR_ENTITY_ID: [entity_id], FIELD_SCHEDULE: {"0": [1] * 24}},
            blocking=True,
        )
    assert err.value.translation_key == "schedule_api_unavailable"
    assert err.value.translation_placeholders["entity_id"] == entity_id


async def test_set_schedule_service_targets_devices_and_areas(
    hass, mock_smartbox, config_entry
):
    """The service resolves device and area targets, not entity ids only."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)
    dev_id, mock_node, _entity_id = await _first_heater_schedule_id(mock_smartbox)

    device_registry = dr.async_get(hass)
    area = ar.async_get(hass).async_create("Heaters")
    node_device = get_device_by_identifier(
        device_registry, (DOMAIN, f"{dev_id}_{mock_node['addr']}")
    )
    assert node_device is not None
    for registry_entry in dr.async_entries_for_config_entry(
        device_registry, config_entry.entry_id
    ):
        device_registry.async_update_device(registry_entry.id, area_id=area.id)

    for index, target in enumerate(
        ({ATTR_DEVICE_ID: [node_device.id]}, {ATTR_AREA_ID: [area.id]})
    ):
        fake.requests.clear()
        # A different day per call keeps the second write a real change; a
        # value-equal schedule is deliberately not posted (it pushes no
        # confirming frame).
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_SCHEDULE,
            {**target, FIELD_SCHEDULE: {str(index): [1] * 24}},
            blocking=True,
        )
        assert [
            path
            for method, path, _body in fake.requests
            if method == "POST" and path.endswith(f"/{mock_node['addr']}/prog")
        ], target


async def test_unload_removes_the_schedule_service(hass, mock_smartbox, config_entry):
    """The service must not outlive the entities it acts on."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE)


def _consumption_sensor(
    hass,
    config_entry,
    *,
    node_id="node_1",
    pending=("node_1",),
    recorder_available=True,
):
    """Return a TotalConsumptionSensor on mocked node/runtime for unit tests.

    ``pending`` seeds the entry's first-setup import state, so a test can pick
    between "this node still owes the 3-year import" and "it is done".
    Recorder availability is stubbed by default: these unit tests patch
    ``async_import_statistics`` and do not start a recorder. Pass
    ``recorder_available=False`` to exercise the real guard.
    """
    runtime = MagicMock()
    runtime.history = HistoryImport(pending, {})
    node = MagicMock()
    node.node_id = node_id
    sensor = TotalConsumptionSensor(node, config_entry, runtime, MagicMock())
    sensor.hass = hass
    sensor.entity_id = "sensor.test_total_consumption"
    if recorder_available:
        sensor._recorder_available = lambda: True
    return sensor


async def test_async_update_pmo_only_polls_pmo_nodes(hass, mock_smartbox, config_entry):
    """The PMO poll reads power for pmo nodes and skips other node types."""
    sensor, client = _power_sensor(hass, config_entry, SmartboxNodeType.PMO)
    with patch.object(sensor, "async_write_ha_state") as mock_write_ha_state:
        await sensor._async_update_pmo(None)
        client.get_node_power.assert_called_once()
        assert sensor.native_value == 850
        mock_write_ha_state.assert_called_once()

    sensor, client = _power_sensor(hass, config_entry, SmartboxNodeType.HTR)
    with patch.object(sensor, "async_write_ha_state") as mock_write_ha_state:
        await sensor._async_update_pmo(None)
        client.get_node_power.assert_not_called()
        mock_write_ha_state.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        SmartboxError("malformed /power payload"),
        AuthError("re-auth rejected"),
        TransportUnavailableError("api down"),
    ],
)
async def test_async_update_pmo_poll_failure_unavailable(
    hass, mock_smartbox, config_entry, error
):
    """A failing PMO poll goes unavailable and never escapes the job.

    The client raises ``SmartboxError`` for a malformed ``/power`` payload and
    ``AuthError`` after a rejected re-auth; both subclass ``SmartboxError`` and
    both used to escape the ``async_track_time_interval`` job, i.e. an ERROR
    traceback per tick with the stale wattage still advertised as available.
    """
    sensor, client = _power_sensor(hass, config_entry, SmartboxNodeType.PMO)
    sensor._node_state = lambda: NodeState(status={"sync_status": "ok"}, available=True)
    client.get_node_power = AsyncMock(side_effect=error)

    with patch.object(sensor, "async_write_ha_state") as mock_write_ha_state:
        await sensor._async_update_pmo(None)  # must not raise

    assert sensor.available is False  # the stale wattage stops being advertised
    mock_write_ha_state.assert_called_once()


async def test_async_update_pmo_recovers_on_the_next_good_poll(
    hass, mock_smartbox, config_entry
):
    """A failed poll leaves the entity recoverable by the next good read."""
    sensor, client = _power_sensor(hass, config_entry, SmartboxNodeType.PMO)
    # a node the engine reports available, so `available` tracks the poll
    # and not the harness-less base entity
    sensor._node_state = lambda: NodeState(status={"sync_status": "ok"}, available=True)
    client.get_node_power = AsyncMock(
        side_effect=SmartboxError("malformed /power payload")
    )

    with patch.object(sensor, "async_write_ha_state"):
        await sensor._async_update_pmo(None)
    assert sensor.available is False

    client.get_node_power = AsyncMock(return_value=850)
    with patch.object(sensor, "async_write_ha_state"):
        await sensor._async_update_pmo(None)
    assert sensor.available is True
    assert sensor.native_value == 850


# --- feed outage ---


async def test_feed_outage_renders_everything_unavailable(
    hass, mock_smartbox, config_entry
):
    """A dead feed renders values unavailable, connectivity included.

    Entities used to keep advertising their last values as available while the
    websocket was dead, and the box connectivity sensor kept claiming
    "Connected" (that flag arrives over the same dead feed). The vendor's
    scheduled token-expiry cut is excluded from this signal - the engine-level
    feed tests pin that - so only a socket that stays silent reacts here.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

    runtime = runtime_of(hass)
    # shrink the grace window for the test (the live default is 10 s)
    runtime.engine.feed.grace_s = 0.05

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    temp_id = get_sensor_entity_id(mock_node, "temperature")
    connected_id = get_connected_binary_sensor_entity_id(mock_device)
    numeric_state(hass, temp_id)
    assert hass.states.get(connected_id).state == "on"

    await runtime.engine.handle_event(SocketClosed(code=1006))
    await until(lambda: hass.states.get(temp_id).state == STATE_UNAVAILABLE)
    assert hass.states.get(connected_id).state == STATE_UNAVAILABLE

    # the first frame after the outage restores the values
    status = dict(
        await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node)
    )
    await push_status(
        hass,
        runtime,
        mock_device["dev_id"],
        mock_node,
        {**status, "mtemp": "19.1"},
    )
    await until(lambda: hass.states.get(temp_id).state != STATE_UNAVAILABLE)
    assert runtime.engine.feed.available is True


# --- entity lifecycle (removal) ---


async def test_schedule_sensor_renders_and_unsubscribes(
    hass, mock_smartbox, config_entry
):
    """The value follows the clock, re-arms at the slot and releases on removal.

    The adapter base releases the shared subscription in
    ``async_will_remove_from_hass``. This entity overrides that hook to cancel
    its slot-boundary timer, so it must still call ``super()``: skipping it
    would keep a removed entity subscribed, and every later node change would
    invoke a callback on a dead entity and re-arm its boundary timer.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

    runtime = runtime_of(hass)
    dev_id, mock_node, entity_id = await _first_heater_schedule_id(mock_smartbox)
    entity = hass.data["entity_components"]["sensor"].get_entity(entity_id)
    assert entity is not None
    assert entity._unsub_boundary is not None  # boundary tracker armed at setup

    monday_7 = datetime(2026, 2, 16, 7, 0, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    with patch.object(sensor_mod.dt_util, "now", return_value=monday_7):
        entity._track_next_change()
        entity.async_write_ha_state()
        await hass.async_block_till_done()
        state = hass.states.get(entity_id)
        assert state.state == "eco"
        assert state.attributes[FIELD_SCHEDULE]
        # the profile names the card renders, in wire-index order
        assert state.attributes["options"] == ["frost", "eco", "comfort"]
        assert entity._unsub_boundary is not None

    monday_18 = monday_7.replace(hour=18)
    with patch.object(sensor_mod.dt_util, "now", return_value=monday_18):
        async_fire_time_changed(hass, monday_18)
        await hass.async_block_till_done()
        state = hass.states.get(entity_id)
        assert state.state == "comfort"
        assert entity._unsub_boundary is not None  # re-armed for 22:00

    # Removing the entity releases its fan-out registration and timer.
    registrations = len(runtime.fanout._regs)
    await entity.async_remove()
    await hass.async_block_till_done()
    assert len(runtime.fanout._regs) == registrations - 1
    assert entity._unsub_boundary is None

    # a later node change must neither reach the removed entity nor re-arm it
    changed = dict(await mock_smartbox.session.get_status(dev_id, mock_node))
    changed["mtemp"] = "19.1"  # a value the store does not hold yet
    with patch.object(entity, "async_write_ha_state") as state_write:
        await push_status(hass, runtime, dev_id, mock_node, changed)
    state_write.assert_not_called()
    assert entity._unsub_boundary is None


def _power_sensor(hass, config_entry, node_type):
    """Return a PowerSensor on a mocked node/runtime for unit tests."""
    node = MagicMock()
    node.node_type = node_type
    runtime = MagicMock()
    runtime.client.get_node_power = AsyncMock(return_value=850)
    sensor = PowerSensor(node, config_entry, runtime, MagicMock())
    sensor.hass = hass
    return sensor, runtime.client


async def test_adjust_short_term_statistics(hass, mock_smartbox, config_entry):
    sensor = _consumption_sensor(hass, config_entry)
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


async def test_adjust_short_term_statistics_no_adjustment(
    hass, mock_smartbox, config_entry
):
    sensor = _consumption_sensor(hass, config_entry)
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
    ):
        mock_instance = mock_get_instance.return_value
        mock_instance.async_add_executor_job = AsyncMock(return_value=last_stat)
        await sensor._adjust_short_term_statistics()

        mock_instance.async_adjust_statistics.assert_not_called()


async def test_native_value_boost_end_time_sensor(hass, mock_smartbox, config_entry):
    sensor = BoostEndTimeSensor(MagicMock(), config_entry, MagicMock(), MagicMock())
    sensor.hass = hass

    # boost_end_min is the home-local minute of the day at which boost ends
    status = {"boost": True, "boost_end_min": 90}  # 1 hour and 30 minutes
    with (
        patch.object(sensor, "_node_state", return_value=NodeState(status=status)),
        patch(
            "custom_components.smartbox.mapping.dt_util.now",
            return_value=datetime(2023, 10, 10, 1, 0, tzinfo=dt_util.UTC),
        ),
    ):
        expected_time = datetime(2023, 10, 10, 1, 30, tzinfo=dt_util.UTC)
        assert sensor.native_value is not None
        assert sensor.native_value.astimezone(dt_util.UTC) == expected_time

        # Test boost end time is in the past, should return next day
        status = {"boost": True, "boost_end_min": 30}  # 30 minutes
        sensor._node_state = lambda: NodeState(status=status)
        expected_time = datetime(2023, 10, 11, 0, 30, tzinfo=dt_util.UTC)
        assert sensor.native_value is not None
        assert sensor.native_value.astimezone(dt_util.UTC) == expected_time

        # Test no boost
        sensor._node_state = lambda: NodeState(status={"boost": False})
        assert sensor.native_value is None


async def test_total_consumption_value_follows_samples(
    hass, mock_smartbox, config_entry, recorder_mock
):
    """The energy sensor's value is the last sample counter (3 h window)."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_sensor_entity_id(mock_node, "total_consumption")

    samples = [
        {"t": int(time.time()) - 3600, "counter": 100},
        {"t": int(time.time()) - 600, "counter": 150},
    ]
    with patch.object(
        hass.data["entity_components"]["sensor"].get_entity(entity_id),
        "_get_samples",
        AsyncMock(return_value=samples),
    ):
        await async_update_entity(hass, entity_id)
        await hass.async_block_till_done()

    assert float(hass.states.get(entity_id).state) == 150


async def test_platform_scan_interval_is_fifteen_minutes(hass, config_entry):
    """The platform's HA polling runs on the 15-minute interval.

    The energy sensor is the only entity here that uses HA polling (PMO
    power and box RTC schedule their own ``async_track_time_interval``),
    and the sensor domain default is 30 s, so losing the constant would
    multiply its samples reads and recorder queries by 30. The value is
    pinned here.
    """
    assert timedelta(minutes=15) == sensor_mod.SCAN_INTERVAL
    assert _consumption_sensor(hass, config_entry).should_poll is True


async def test_consumption_value_present_at_setup(
    hass, mock_smartbox, config_entry, recorder_mock
):
    """The energy value is read when the entity is added, not at first poll.

    Without ``update_before_add`` the state is ``unknown`` until the first
    platform poll (15 minutes away), i.e. after every restart.
    """
    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = (await mock_smartbox.session.get_nodes(mock_device["dev_id"]))[0]
    entity_id = get_sensor_entity_id(mock_node, "total_consumption")

    # Top of the current hour: the wire's samples are hour-aligned and the
    # recorder imports only top-of-the-hour rows, so the fixture must not
    # depend on the wall clock's minutes and seconds.
    samples = [{"t": int(time.time()) // 3600 * 3600, "counter": 150}]
    with patch.object(
        TotalConsumptionSensor, "_get_samples", AsyncMock(return_value=samples)
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    entity = hass.data["entity_components"]["sensor"].get_entity(entity_id)
    assert entity is not None
    assert entity.native_value == 150


async def test_transient_lost_frame_recovers_from_ok_frame(
    hass, mock_smartbox, config_entry
):
    """Lost frame + confirming ok frame: sensors end on the ok status.

    Port of test_transient_sync_lost_frame_keeps_last_status: on the new
    stack the engine replaces the status slot with the (lost-marked)
    frame body - the entity renders unknown through the availability
    window, and the following ok frame (docs/api-contract.md §6.3)
    restores the full status and availability.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

    mock_device = (await mock_smartbox.session.get_devices())[0]
    mock_node = next(
        node
        for node in await mock_smartbox.session.get_nodes(mock_device["dev_id"])
        if is_heater_node(node)
    )
    entity_id = get_sensor_entity_id(mock_node, "temperature")

    runtime = runtime_of(hass)
    await push_status(
        hass, runtime, mock_device["dev_id"], mock_node, {"sync_status": "lost"}
    )
    ok_status = dict(
        await mock_smartbox.session.get_status(mock_device["dev_id"], mock_node)
    )
    await push_status(hass, runtime, mock_device["dev_id"], mock_node, ok_status)
    _check_temp_state(hass, ok_status, hass.states.get(entity_id))


async def test_clock_drift_sensor(hass, mock_smartbox, config_entry):
    """One clock drift sensor per box device, with the raw RTC as attributes."""
    for mock_device in await mock_smartbox.session.get_devices():
        _enable_clock_drift_sensor(hass, config_entry, mock_device)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

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


async def test_diagnostic_sensors_disabled_by_default(
    hass, mock_smartbox, config_entry
):
    """Duty cycle and clock drift are opt-in diagnostic telemetry."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

    registry = er.async_get(hass)
    for mock_device in await mock_smartbox.session.get_devices():
        entity_id = get_entity_id_from_unique_id(
            hass,
            SENSOR_DOMAIN,
            get_device_unique_id(mock_device, "rtc_drift"),
        )
        assert entity_id is not None
        assert (
            registry.async_get(entity_id).disabled_by
            is er.RegistryEntryDisabler.INTEGRATION
        )
        assert hass.states.get(entity_id) is None

        for mock_node in await mock_smartbox.session.get_nodes(mock_device["dev_id"]):
            if mock_node["type"] != SmartboxNodeType.HTR:
                continue
            entity_id = get_entity_id_from_unique_id(
                hass,
                SENSOR_DOMAIN,
                get_node_unique_id(mock_device, mock_node, "duty_cycle"),
            )
            assert entity_id is not None
            assert (
                registry.async_get(entity_id).disabled_by
                is er.RegistryEntryDisabler.INTEGRATION
            )
            assert hass.states.get(entity_id) is None


async def test_duty_cycle_sensor_can_be_enabled(hass, mock_smartbox, config_entry):
    """The opt-in duty-cycle sensor renders once the user enables it."""
    mock_device, mock_node = await _first_htr_node(mock_smartbox)
    unique_id = get_node_unique_id(mock_device, mock_node, "duty_cycle")
    er.async_get(hass).async_get_or_create(
        SENSOR_DOMAIN,
        DOMAIN,
        unique_id,
        config_entry=config_entry,
        suggested_object_id=get_object_id(f"{mock_node['name']} Duty Cycle"),
        disabled_by=None,
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)

    entity_id = get_entity_id_from_unique_id(hass, SENSOR_DOMAIN, unique_id)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    # The wire carries 50 percent; HA normalises the power-factor state to 0.5.
    assert float(state.state) == 0.5


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
    assert (
        rtc_time_to_datetime({"d": 1, "h": 0, "m": 0, "n": 12, "s": 0, "y": 2026})
        is None
    )
    assert rtc_time_to_datetime({"y": 2026}) is None
    assert rtc_time_to_datetime(None) is None


# --- set_schedule service (write path) ---


async def test_set_schedule_service_accepts_48_half_hourly_day(
    hass, mock_smartbox, config_entry
):
    """The service accepts 48-half-hourly days end to end (no exact-24 check)."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)
    _dev_id, mock_node, entity_id = await _first_heater_schedule_id(mock_smartbox)

    half_hourly = [1, 2] * 24
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_SCHEDULE,
        {ATTR_ENTITY_ID: [entity_id], FIELD_SCHEDULE: {"0": half_hourly}},
        blocking=True,
    )
    prog_posts = [
        (path, body)
        for method, path, body in fake.requests
        if method == "POST" and path.endswith("/prog")
    ]
    assert len(prog_posts) == 1
    path, body = prog_posts[0]
    assert path == f"devs/{_dev_id}/htr/{mock_node['addr']}/prog"
    # GET-merge-POST: the complete merged object, 48 slots kept verbatim
    posted_days = body["prog"]
    assert posted_days["0"] == half_hourly


async def test_set_schedule_resolution_service_writes_both_setup_keys(
    hass, mock_smartbox, config_entry
):
    """The resolution service posts the vendor app's two-key setup body."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)
    _dev_id, _mock_node, entity_id = await _first_heater_schedule_id(mock_smartbox)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_SCHEDULE_RESOLUTION,
        {ATTR_ENTITY_ID: [entity_id], FIELD_RESOLUTION: 1},
        blocking=True,
    )
    setup_posts = [
        body
        for method, path, body in fake.requests
        if method == "POST" and path.endswith("/setup")
    ]
    assert setup_posts
    assert setup_posts[-1]["prog_resol"] == 1
    assert setup_posts[-1]["prog_resolution"] == 1


async def test_total_consumption_failing_samples_unavailable(
    hass, mock_smartbox, config_entry
):
    """A failing samples read surfaces unavailable, keeping the cached value.

    No stale-but-available value and no uncaught traceback; a later good
    read restores availability (mirrors the PMO poll's).
    """
    node = MagicMock()
    runtime = MagicMock()
    # a node the engine reports available (the mock default would not)
    runtime.engine.node.return_value = NodeState(available=True)
    sensor = TotalConsumptionSensor(node, config_entry, runtime, MagicMock())
    sensor.hass = hass
    sensor.entity_id = "sensor.test_total_consumption"
    sensor._total_energy = 100.0
    for error in (SmartboxError("api error"), TransportUnavailableError("down")):
        with patch.object(sensor, "_get_samples", AsyncMock(side_effect=error)):
            await sensor.async_update()  # must not raise
        assert sensor.available is False
        assert sensor.native_value == 100.0  # cached value kept

    with (
        patch.object(
            sensor,
            "_get_samples",
            AsyncMock(return_value=[{"t": time.time(), "counter": 150}]),
        ),
        patch("custom_components.smartbox.sensor.get_instance") as mock_get_instance,
        patch(
            "custom_components.smartbox.sensor.get_last_short_term_statistics",
            return_value=None,
        ),
    ):
        mock_get_instance.return_value.async_add_executor_job = AsyncMock(
            return_value=None
        )
        await sensor.async_update()
    assert sensor.available is True
    assert sensor.native_value == 150.0


async def test_energy_entity_survives_a_failing_first_import(
    hass, mock_smartbox, config_entry, recorder_mock
):
    """A transient samples failure at add time must not strand the entities.

    An exception out of ``async_added_to_hass`` leaves HA's entity in its
    ADDING state: no state is ever written and the statistics interval is
    never registered, so the first (slowest) import failing would freeze
    every consumption sensor for the life of the runtime. The failure marks
    them unavailable instead, and a later good read restores them.
    """
    with patch.object(
        SmartboxClient,
        "get_node_samples",
        new=AsyncMock(side_effect=SmartboxError("boom")),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        await seed_stack(hass, mock_smartbox)

    energy_ids = [
        entity_id
        for entity_id in hass.states.async_entity_ids(SENSOR_DOMAIN)
        if "total_consumption" in entity_id
    ]
    assert energy_ids  # added and stateful, not stuck in ADDING
    for entity_id in energy_ids:
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    # a later good read (the interval's job) restores availability
    with patch.object(
        SmartboxClient, "get_node_samples", new=AsyncMock(return_value=[])
    ):
        for entity_id in energy_ids:
            await async_update_entity(hass, entity_id)
    for entity_id in energy_ids:
        # Available again. The value stays ``unknown`` (the empty samples
        # serve no counter), which is the correct rendering here - so
        # "not unavailable" IS the meaningful assertion for this one.
        assert hass.states.get(entity_id).state != STATE_UNAVAILABLE


async def test_malformed_sample_rows_are_dropped_not_fatal(
    hass, mock_smartbox, config_entry, recorder_mock, caplog
):
    """One bad sample row must not kill the import for the whole runtime.

    The rows are server-controlled; the entity-level validator drops the
    unusable ones (and warns once) so the good rows still import.
    """
    rows = [
        {"t": 1739966400, "counter": 100},
        "not-a-row",
        {"t": "nope", "counter": 5},
        {"t": 1739970000, "counter": "110"},  # numeric strings are usable
    ]
    with (
        patch.object(
            SmartboxClient, "get_node_samples", new=AsyncMock(return_value=rows)
        ),
        caplog.at_level(logging.WARNING),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        await seed_stack(hass, mock_smartbox)

    energy_ids = [
        entity_id
        for entity_id in hass.states.async_entity_ids(SENSOR_DOMAIN)
        if "total_consumption" in entity_id
    ]
    assert energy_ids
    for entity_id in energy_ids:
        assert hass.states.get(entity_id).state != STATE_UNAVAILABLE
    assert any("malformed sample row" in record.message for record in caplog.records)


def test_sample_rows_use_the_accumulator_counters_list():
    """An accumulator row carries ``counters``; the app's first entry is the total."""
    rows = [
        {"t": 100, "counters": [2400, 120, 30, 5], "valley": False},
        {"t": 200, "counters": ["2500", 130, 30, 5]},
        {"t": 300, "counters": []},
        {"t": 325, "counters": ["nope"], "counter": 2650},  # bad entry, scalar wins
        {"t": 350, "counters": [], "counter": 2700},  # app falls back to counter
        {"t": 400, "counter": 2600},
    ]
    assert _usable_sample_rows(rows, "sensor.x") == [
        {"t": 100.0, "counter": 2400.0},
        {"t": 200.0, "counter": 2500.0},
        {"t": 325.0, "counter": 2650.0},
        {"t": 350.0, "counter": 2700.0},
        {"t": 400.0, "counter": 2600.0},
    ]


async def test_accumulator_counters_rows_reach_the_energy_sensor(hass, config_entry):
    """The sensor path accepts an accumulator's ``counters`` list."""
    sensor = _consumption_sensor(hass, config_entry)
    rows = [{"t": 1739966400, "counters": [2400, 120, 30, 5], "valley": False}]
    with patch.object(
        sensor._runtime.client, "get_node_samples", new=AsyncMock(return_value=rows)
    ):
        samples = await sensor._get_samples(0, 999)
    assert samples == [{"t": 1739966400.0, "counter": 2400.0}]


async def test_a_failing_first_import_is_not_retried_on_every_tick(hass, config_entry):
    """The 3-year import backs off after a failure instead of re-reading.

    Its ranges are the heaviest reads the integration makes; with the node
    still pending, a retry on every 15-minute tick would only add load to an
    endpoint that is already failing.
    """
    sensor = _consumption_sensor(hass, config_entry)
    node_key = sensor._node.node_id
    history = sensor._runtime.history
    with patch.object(
        sensor, "_get_samples", AsyncMock(side_effect=SmartboxError("boom"))
    ):
        await sensor.update_statistics()  # must not escape the interval job
    assert sensor._samples_ok is False
    assert history.failure_count(node_key) == 1
    assert history.retry_at(node_key) > time.monotonic()

    with patch.object(sensor, "_get_samples", AsyncMock(return_value=[])) as reads:
        await sensor.update_statistics()
        reads.assert_not_called()  # still inside the backoff window

        # Once the window passes, the import runs again (and resets the streak).
        history._retry_at[node_key] = 0.0
        await sensor.update_statistics()
        assert reads.call_count == 3  # the three year ranges
    assert history.failure_count(node_key) == 0


def test_temperature_and_duty_sensors_render_malformed_values_unknown():
    """Malformed wire strings read as None instead of raising from a property."""
    temp = object.__new__(TemperatureSensor)
    temp._node_state = lambda: NodeState(status={"mtemp": "bad"})
    assert temp.native_value is None

    duty = object.__new__(DutyCycleSensor)
    duty._node_state = lambda: NodeState(status={"duty": "bad"})
    assert duty.native_value is None


def test_power_sensor_renders_malformed_power_unknown():
    """A malformed power value reads as None when the heater is active."""
    sensor = object.__new__(PowerSensor)
    sensor._node = MagicMock()
    sensor._node.node_type = SmartboxNodeType.HTR
    sensor._pmo_power = None
    sensor._node_state = lambda: NodeState(
        status={"sync_status": "ok", "active": True, "power": "bad"}
    )
    assert sensor.native_value is None


def test_charge_level_renders_malformed_values_unknown():
    """Both charge fields parse defensively."""
    sensor = object.__new__(ChargeLevelSensor)
    sensor._node = MagicMock()
    sensor._node.pid = "081C0000"
    sensor._node_state = lambda: NodeState(
        status={"current_charge_per": "bad", "charge_level": 3}
    )
    assert sensor.native_value is None

    sensor._node.pid = "08990000"
    sensor._node_state = lambda: NodeState(status={"charge_level": "bad"})
    assert sensor.native_value is None


def test_htr_mod_power_uses_the_configured_rated_power():
    """HaverlandHtrModController.getPower() returns setup.power, not status."""
    sensor = object.__new__(PowerSensor)
    sensor._node = MagicMock()
    sensor._node.node_type = SmartboxNodeType.HTR_MOD
    sensor._pmo_power = None
    sensor._node_state = lambda: NodeState(
        status={"active": True, "power": "1"},
        setup={"power": "510.0"},
    )
    assert sensor.native_value == 510.0


async def test_set_charging_schedule_service_writes_setup(
    hass, mock_smartbox, config_entry
):
    """The storage charging service merges days and valley slots."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    _runtime, fake = await seed_stack(hass, mock_smartbox)
    device = (await mock_smartbox.session.get_devices())[0]
    node = (await mock_smartbox.session.get_nodes(device["dev_id"]))[1]
    entity_id = get_sensor_entity_id(node, "target_charge")
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_CHARGING_SCHEDULE,
        {
            ATTR_ENTITY_ID: [entity_id],
            "active_days": [1, 0, 1, 0, 1, 0, 1],
            "slot_1": {"start": 60, "end": 120},
        },
        blocking=True,
    )
    setup = await fake.get_node_setup(
        device["dev_id"], NodeRef(node["type"], str(node["addr"]))
    )
    assert setup["charging_conf"]["active_days"] == [1, 0, 1, 0, 1, 0, 1]
    assert setup["charging_conf"]["slot_1"] == {"start": 60, "end": 120}


async def test_water_storage_target_charge_is_not_a_charging_service_target(
    hass, mock_smartbox, config_entry
):
    """The water controller omits charge days/valley slots from its settings."""
    mock_smartbox._node_info["device_1"][1]["product_id"] = "082A0000"
    mock_smartbox._node_info["device_1"][1]["fw_version"] = "1.9"
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)
    device = (await mock_smartbox.session.get_devices())[0]
    node = (await mock_smartbox.session.get_nodes(device["dev_id"]))[1]
    entity_id = get_sensor_entity_id(node, "target_charge")
    assert hass.states.get(entity_id) is not None
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_CHARGING_SCHEDULE,
            {ATTR_ENTITY_ID: [entity_id], "active_days": [1] * 7},
            blocking=True,
        )
    assert err.value.translation_key == "charging_schedule_wrong_target"
