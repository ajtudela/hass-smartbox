import asyncio
from datetime import datetime
import logging
from unittest.mock import AsyncMock, MagicMock, NonCallableMock, patch

from homeassistant.components.climate import (
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_HOME,
    HVACMode,
    UnitOfTemperature,
)
from homeassistant.util import dt as dt_util
import pytest
from smartbox.error import APIUnavailableError

from custom_components.smartbox.const import (
    PRESET_FROST,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
    SmartboxNodeType,
)
from custom_components.smartbox.models import (
    SmartboxDevice,
    SmartboxNode,
    away_offset_available,
    get_devices,
    get_hvac_mode,
    get_target_temperature,
    get_temperature_unit,
    max_temp_limit_available,
    priority_available,
    prog_temps_available,
    set_hvac_mode_args,
    set_preset_mode_status_update,
    set_temperature_args,
)

from .const import MOCK_SMARTBOX_DEVICE_INFO
from .test_utils import assert_log_message

_LOGGER = logging.getLogger(__name__)


async def test_smartbox_device_dev_data_updates(hass):
    """Independently test device data updates usually done by UpdateManager."""
    dev_id = "device_1"
    mock_session = MagicMock()
    mock_node_1 = MagicMock()
    mock_node_2 = MagicMock()
    # Simulate initialise_nodes with mock data, make sure nobody calls the real one
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO[dev_id], mock_session, hass)
        device._nodes = {
            (SmartboxNodeType.HTR, 1): mock_node_1,
            (SmartboxNodeType.ACM, 2): mock_node_2,
        }

        mock_dev_data = {"away": True}
        device._away_status_update(mock_dev_data)
        assert device.away

        mock_dev_data = {"away": False}
        device._away_status_update(mock_dev_data)
        assert not device.away

        device._power_limit_update(1045)
        assert device.power_limit == 1045


async def test_smartbox_device_connected_updates(hass):
    """Independently test device data updates usually done by UpdateManager."""
    dev_id = "device_1"
    mock_session = MagicMock()
    mock_node_1 = MagicMock()
    mock_node_2 = MagicMock()
    mock_node_1.node_id = "device_1_1"
    mock_node_2.node_id = "device_1_2"
    # Simulate initialise_nodes with mock data, make sure nobody calls the real one
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO[dev_id], mock_session, hass)
        device._nodes = {
            (SmartboxNodeType.HTR, 1): mock_node_1,
            (SmartboxNodeType.ACM, 2): mock_node_2,
        }

        with patch(
            "custom_components.smartbox.models.async_dispatcher_send"
        ) as mock_send:
            device._connected(connected=True)
            assert device.connected

            device._connected(connected=False)
            assert not device.connected

        # Connectivity updates must be dispatched on the device-level signal:
        # the box Connectivity entity listens on f"{DOMAIN}_{dev_id}_connected".
        assert [call.args[1] for call in mock_send.call_args_list] == [
            "smartbox_device_1_connected",
            "smartbox_device_1_connected",
        ]
        assert [call.args[2] for call in mock_send.call_args_list] == [
            True,
            False,
        ]


async def test_get_devices_cancels_devices_on_failure(hass, mock_smartbox):
    """Devices initialised before a failure must be cancelled.

    Each initialised device already runs websocket/update tasks; without this
    cleanup a failed (or automatically retried) setup leaks duplicate sessions.
    """
    initialised_device = AsyncMock()
    with (
        patch(
            "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
            AsyncMock(side_effect=[initialised_device, APIUnavailableError("fail")]),
        ),
        pytest.raises(APIUnavailableError),
    ):
        await get_devices(session=mock_smartbox.session, hass=hass)

    assert initialised_device.cancel.await_count == 1


async def test_cancel(hass, caplog):
    dev_id = "device_1"
    mock_session = MagicMock()

    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        # No watchdog task: only update_manager.cancel() is called
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO[dev_id], mock_session, hass)
        device.update_manager = AsyncMock()
        device._watchdog_task = None
        await device.cancel()
        device.update_manager.cancel.assert_awaited_once()

        # Watchdog task already done: no forced cancellation, no warning
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO[dev_id], mock_session, hass)
        device.update_manager = AsyncMock()
        finished = asyncio.create_task(asyncio.sleep(0))
        await finished
        device._watchdog_task = finished
        with caplog.at_level(logging.WARNING, logger="custom_components.smartbox.models"):
            await device.cancel()
        device.update_manager.cancel.assert_awaited_once()
        assert not caplog.records

        # Watchdog task still running: forced cancellation, warning logged,
        # and cancel() returns promptly with the watchdog task cancelled
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO[dev_id], mock_session, hass)
        device.update_manager = AsyncMock()
        running = asyncio.create_task(asyncio.sleep(3600))
        device._watchdog_task = running
        with caplog.at_level(logging.WARNING, logger="custom_components.smartbox.models"):
            await device.cancel()
        device.update_manager.cancel.assert_awaited_once()
        assert running.cancelled()
        assert_log_message(
            caplog,
            "custom_components.smartbox.models",
            logging.WARNING,
            f"Force-cancelling watchdog task for device {dev_id}",
        )


async def test_smartbox_device_node_status_update(hass, caplog):
    """Independently test node status updates usually called by UpdateManager."""
    dev_id = "device_1"
    mock_session = MagicMock()
    mock_node_1 = MagicMock()
    mock_node_2 = MagicMock()
    mock_node_3 = MagicMock()
    # Simulate initialise_nodes with mock data, make sure nobody calls the real one
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO[dev_id], mock_session, hass)
        device._nodes = {
            (SmartboxNodeType.HTR, 1): mock_node_1,
            (SmartboxNodeType.ACM, 2): mock_node_2,
            (SmartboxNodeType.PMO, 3): mock_node_3,
        }

        mock_status = {"foo": "bar"}
        device._node_status_update(SmartboxNodeType.HTR, 1, mock_status)
        mock_node_1.update_status.assert_called_with(mock_status)
        mock_node_2.update_status.assert_not_called()

        mock_node_1.reset_mock()
        mock_node_2.reset_mock()
        device._node_status_update(SmartboxNodeType.ACM, 2, mock_status)
        mock_node_2.update_status.assert_called_with(mock_status)
        mock_node_1.update_status.assert_not_called()

        mock_node_1.reset_mock()
        mock_node_2.reset_mock()
        device._node_status_update(SmartboxNodeType.PMO, 3, mock_status)
        mock_node_3.update_status.assert_not_called()
        mock_node_1.update_status.assert_not_called()
        mock_node_2.update_status.assert_not_called()

        # test unknown node
        mock_node_1.reset_mock()
        mock_node_2.reset_mock()
        device._node_status_update(SmartboxNodeType.HTR, 3, mock_status)
        mock_node_1.update_status.assert_not_called()
        mock_node_2.update_status.assert_not_called()
        assert_log_message(
            caplog,
            "custom_components.smartbox.models",
            logging.ERROR,
            "Received status update for unknown node htr 3",
        )


async def test_smartbox_device_node_setup_update(hass, caplog):
    """Independently test node setup updates usually called by UpdateManager."""
    dev_id = "device_1"
    mock_session = MagicMock()
    mock_node_1 = MagicMock()
    mock_node_2 = MagicMock()
    # Simulate initialise_nodes with mock data, make sure nobody calls the real one
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO[dev_id], mock_session, hass)
        device._nodes = {
            (SmartboxNodeType.HTR, 1): mock_node_1,
            (SmartboxNodeType.ACM, 2): mock_node_2,
        }

        mock_setup = {"foo": "bar"}
        device._node_setup_update(SmartboxNodeType.HTR, 1, mock_setup)
        mock_node_1.update_setup.assert_called_with(mock_setup)
        mock_node_2.update_setup.assert_not_called()

        mock_node_1.reset_mock()
        mock_node_2.reset_mock()
        device._node_setup_update(SmartboxNodeType.ACM, 2, mock_setup)
        mock_node_2.update_setup.assert_called_with(mock_setup)
        mock_node_1.update_setup.assert_not_called()

        # test unknown node
        mock_node_1.reset_mock()
        mock_node_2.reset_mock()
        device._node_setup_update(SmartboxNodeType.HTR, 3, mock_setup)
        mock_node_1.update_setup.assert_not_called()
        mock_node_2.update_setup.assert_not_called()
        assert_log_message(
            caplog,
            "custom_components.smartbox.models",
            logging.ERROR,
            "Received setup update for unknown node htr 3",
        )


async def test_smartbox_node(hass):
    dev_id = "test_device_id_1"
    mock_device = AsyncMock()
    mock_device.dev_id = dev_id
    mock_device.away = False
    # Real SmartboxNode.set_status forwards the optimistic merge through
    # this method; make it a sync mock so no unawaited coroutine is created
    mock_device.dispatch_node_status = MagicMock()
    node_addr = 3
    node_type = SmartboxNodeType.HTR
    node_name = "Bathroom Heater"
    node_info = {"addr": node_addr, "name": node_name, "type": node_type}
    node_sample = {"t": 1735686000, "temp": "11.3", "counter": 247426}
    mock_session = AsyncMock()
    initial_status = {"mtemp": "21.4", "stemp": "22.5"}
    initial_setup = {
        "true_radiant_enabled": False,
        "window_mode_enabled": False,
    }

    node = SmartboxNode(
        mock_device,
        node_info,
        mock_session,
        initial_status,
        initial_setup,
        node_sample,
        {},
    )
    assert node.node_id == f"{dev_id}_{node_addr}"
    assert node.name == node_name
    assert node.node_type == node_type
    assert node.addr == node_addr
    assert node.node_info == node_info

    assert node.status == initial_status
    new_status = {"mtemp": "21.6", "stemp": "22.5"}
    node.update_status(new_status)
    assert node.status == new_status

    await node.set_status(stemp=23.5)
    mock_session.set_node_status.assert_called_with(dev_id, node_info, {"stemp": 23.5})
    # The optimistic merge is forwarded to entities immediately: they never
    # poll, so this is what keeps them in sync with the node cache until the
    # websocket confirms (which may be equal to the cache and dedup-immune
    # now that every ok frame is forwarded).
    mock_device.dispatch_node_status.assert_called_once_with(
        node, {"mtemp": "21.6", "stemp": 23.5}
    )

    assert not node.away
    mock_device.away = True
    assert node.away

    status_update = await node.async_update(hass)
    assert status_update == node.status

    # setup fields
    assert not node.window_mode
    node.update_setup({"window_mode_enabled": True})
    assert node.window_mode
    node.update_setup({})
    with pytest.raises(KeyError):
        node.window_mode

    node.update_setup(initial_setup)
    assert not node.true_radiant
    node.update_setup({"true_radiant_enabled": True})
    assert node.true_radiant
    node.update_setup({})
    with pytest.raises(KeyError):
        node.true_radiant


def test_get_target_temperature():
    assert get_target_temperature(SmartboxNodeType.HTR, {"stemp": "22.5"}) == 22.5
    assert get_target_temperature(SmartboxNodeType.ACM, {"stemp": "12.6"}) == 12.6
    with pytest.raises(KeyError):
        get_target_temperature(SmartboxNodeType.HTR, {"xxx": "22.5"})

    assert (
        get_target_temperature(
            SmartboxNodeType.HTR_MOD,
            {
                "selected_temp": "comfort",
                "comfort_temp": "17.2",
            },
        )
        == 17.2
    )
    assert (
        get_target_temperature(
            SmartboxNodeType.HTR_MOD,
            {
                "selected_temp": "eco",
                "comfort_temp": "17.2",
                "eco_offset": "4",
            },
        )
        == 13.2
    )
    assert (
        get_target_temperature(
            SmartboxNodeType.HTR_MOD,
            {
                "selected_temp": "ice",
                "ice_temp": "7",
            },
        )
        == 7
    )
    assert (
        get_target_temperature(
            SmartboxNodeType.HTR_MOD,
            {
                "selected_temp": "off",
            },
        )
        is None
    )
    # off = no set point: None even though the wire still carries a stale
    # stemp (htr) or on=False (htr_mod); checks mirror get_hvac_mode
    assert (
        get_target_temperature(
            SmartboxNodeType.HTR,
            {"stemp": "20.3", "mode": "off"},
        )
        is None
    )
    assert (
        get_target_temperature(
            SmartboxNodeType.ACM,
            {"stemp": "20.3", "mode": "manual"},
        )
        == 20.3
    )
    assert (
        get_target_temperature(
            SmartboxNodeType.HTR_MOD,
            {"on": False, "mode": "manual", "selected_temp": "comfort", "comfort_temp": "22.0"},
        )
        is None
    )

    with pytest.raises(KeyError) as exc_info:
        get_target_temperature(
            SmartboxNodeType.HTR_MOD,
            {
                "selected_temp": "comfort",
            },
        )
    assert "comfort_temp" in exc_info.exconly()
    with pytest.raises(KeyError) as exc_info:
        get_target_temperature(
            SmartboxNodeType.HTR_MOD,
            {
                "selected_temp": "eco",
                "comfort_temp": "17.2",
            },
        )
    assert "eco_offset" in exc_info.exconly()
    with pytest.raises(KeyError) as exc_info:
        get_target_temperature(
            SmartboxNodeType.HTR_MOD,
            {
                "selected_temp": "ice",
            },
        )
    assert "ice_temp" in exc_info.exconly()
    with pytest.raises(KeyError) as exc_info:
        get_target_temperature(
            SmartboxNodeType.HTR_MOD,
            {
                "selected_temp": "blah",
            },
        )
    assert "Unexpected 'selected_temp' value blah" in exc_info.exconly()


def test_set_temperature_args():
    # Off-grid setpoints are rounded onto the device's 0.5 °C grid before
    # POSTing (the device quantizes silently; live-probed 2026-09-28)
    assert set_temperature_args(SmartboxNodeType.HTR, {"units": "C"}, 21.7) == {
        "stemp": "21.5",
        "units": "C",
    }
    assert set_temperature_args(SmartboxNodeType.HTR, {"units": "C"}, 21.3) == {
        "stemp": "21.5",
        "units": "C",
    }
    assert set_temperature_args(SmartboxNodeType.ACM, {"units": "F"}, 78) == {
        "stemp": "78",
        "units": "F",
    }
    with pytest.raises(KeyError) as exc_info:
        set_temperature_args(SmartboxNodeType.HTR, {}, 24.7)
    assert "units" in exc_info.exconly()

    assert set_temperature_args(
        SmartboxNodeType.HTR_MOD,
        {
            "mode": "auto",
            "selected_temp": "comfort",
            "comfort_temp": "18.2",
            "eco_offset": "4",
            "units": "C",
        },
        17.2,
    ) == {
        "on": True,
        "mode": "auto",
        "selected_temp": "comfort",
        "comfort_temp": "17.2",
        "eco_offset": "4",
        "units": "C",
    }
    assert set_temperature_args(
        SmartboxNodeType.HTR_MOD,
        {
            "mode": "auto",
            "selected_temp": "eco",
            "comfort_temp": "17.2",
            "eco_offset": "4",
            "units": "C",
        },
        14.2,
    ) == {
        "on": True,
        "mode": "auto",
        "selected_temp": "eco",
        "comfort_temp": "18.2",
        "eco_offset": "4",
        "units": "C",
    }
    with pytest.raises(ValueError) as exc_info:
        set_temperature_args(
            SmartboxNodeType.HTR_MOD,
            {
                "mode": "auto",
                "selected_temp": "ice",
                "ice_temp": "7",
                "units": "C",
            },
            7,
        )
    assert "ice mode" in exc_info.exconly()

    with pytest.raises(KeyError) as exc_info:
        set_temperature_args(
            SmartboxNodeType.HTR_MOD,
            {
                "mode": "auto",
                "selected_temp": "eco",
                "comfort_temp": "17.2",
                "units": "C",
            },
            17.2,
        )
    assert "eco_offset" in exc_info.exconly()
    with pytest.raises(KeyError) as exc_info:
        set_temperature_args(
            SmartboxNodeType.HTR_MOD,
            {
                "mode": "auto",
                "selected_temp": "blah",
                "comfort_temp": "17.2",
                "units": "C",
            },
            17.2,
        )
    assert "Unexpected 'selected_temp' value blah" in exc_info.exconly()

    # E5: setpoint inside the running programme engages modified_auto (the
    # app's body) when the unit advertises the feature via
    # modified_auto_span in setup — so the override survives programme
    # transitions (live-probed 2026-09-28, see ../smartbox api-notes.md)
    assert set_temperature_args(
        SmartboxNodeType.HTR,
        {"units": "C", "mode": "auto"},
        21.5,
        {"modified_auto_span": 6},
    ) == {
        "stemp": "21.5",
        "units": "C",
        "mode": "modified_auto",
    }
    # ... but only in plain auto: manual/off keep the 2-key body, and in
    # modified_auto the override is already active (mode stays)
    for mode in ("manual", "off", "modified_auto"):
        assert set_temperature_args(
            SmartboxNodeType.HTR,
            {"units": "C", "mode": mode},
            21.5,
            {"modified_auto_span": 6},
        ) == {"stemp": "21.5", "units": "C"}
    # ... and only when the setup advertises the feature
    assert set_temperature_args(
        SmartboxNodeType.HTR,
        {"units": "C", "mode": "auto"},
        21.5,
        {},
    ) == {"stemp": "21.5", "units": "C"}
    assert set_temperature_args(
        SmartboxNodeType.HTR,
        {"units": "C", "mode": "auto"},
        21.5,
    ) == {"stemp": "21.5", "units": "C"}


def test_get_hvac_mode():
    assert get_hvac_mode(SmartboxNodeType.HTR, {"mode": "off"}) == HVACMode.OFF
    assert get_hvac_mode(SmartboxNodeType.ACM, {"mode": "auto"}) == HVACMode.AUTO
    assert (
        get_hvac_mode(SmartboxNodeType.HTR, {"mode": "modified_auto"}) == HVACMode.AUTO
    )
    assert get_hvac_mode(SmartboxNodeType.ACM, {"mode": "manual"}) == HVACMode.HEAT
    with pytest.raises(ValueError):
        get_hvac_mode(SmartboxNodeType.HTR, {"mode": "blah"})
    assert (
        get_hvac_mode(SmartboxNodeType.HTR_MOD, {"on": True, "mode": "auto"})
        == HVACMode.AUTO
    )
    assert (
        get_hvac_mode(SmartboxNodeType.HTR_MOD, {"on": True, "mode": "self_learn"})
        == HVACMode.AUTO
    )
    assert (
        get_hvac_mode(SmartboxNodeType.HTR_MOD, {"on": True, "mode": "presence"})
        == HVACMode.AUTO
    )
    assert (
        get_hvac_mode(SmartboxNodeType.HTR_MOD, {"on": True, "mode": "manual"})
        == HVACMode.HEAT
    )
    assert (
        get_hvac_mode(SmartboxNodeType.HTR_MOD, {"on": False, "mode": "auto"})
        == HVACMode.OFF
    )
    assert (
        get_hvac_mode(SmartboxNodeType.HTR_MOD, {"on": False, "mode": "self_learn"})
        == HVACMode.OFF
    )
    assert (
        get_hvac_mode(SmartboxNodeType.HTR_MOD, {"on": False, "mode": "presence"})
        == HVACMode.OFF
    )
    assert (
        get_hvac_mode(SmartboxNodeType.HTR_MOD, {"on": False, "mode": "manual"})
        == HVACMode.OFF
    )
    with pytest.raises(ValueError):
        get_hvac_mode(SmartboxNodeType.HTR_MOD, {"on": True, "mode": "blah"})
    with pytest.raises(KeyError) as exc_info:
        get_hvac_mode(SmartboxNodeType.HTR_MOD, {"mode": "manual"})
    assert "on" in exc_info.exconly()


def test_set_hvac_mode_args():
    assert set_hvac_mode_args(SmartboxNodeType.HTR, {}, HVACMode.OFF) == {"mode": "off"}
    assert set_hvac_mode_args(SmartboxNodeType.ACM, {}, HVACMode.AUTO) == {
        "mode": "auto"
    }
    assert set_hvac_mode_args(SmartboxNodeType.HTR, {}, HVACMode.HEAT) == {
        "mode": "manual"
    }
    with pytest.raises(ValueError):
        set_hvac_mode_args(SmartboxNodeType.HTR, {}, "blah")
    assert set_hvac_mode_args(
        SmartboxNodeType.HTR_MOD,
        {},
        HVACMode.OFF,
    ) == {
        "on": False,
    }
    assert set_hvac_mode_args(
        SmartboxNodeType.HTR_MOD,
        {},
        HVACMode.AUTO,
    ) == {
        "on": True,
        "mode": "auto",
    }
    assert set_hvac_mode_args(
        SmartboxNodeType.HTR_MOD,
        {
            "selected_temp": "comfort",
        },
        HVACMode.HEAT,
    ) == {
        "on": True,
        "mode": "manual",
        "selected_temp": "comfort",
    }
    with pytest.raises(ValueError):
        set_hvac_mode_args(
            SmartboxNodeType.HTR_MOD,
            {},
            "blah",
        )
    with pytest.raises(KeyError) as exc_info:
        set_hvac_mode_args(
            SmartboxNodeType.HTR_MOD,
            {},
            HVACMode.HEAT,
        )
    assert "selected_temp" in exc_info.exconly()


def test_set_preset_mode_status_update():
    assert set_preset_mode_status_update(
        SmartboxNodeType.HTR_MOD, {}, PRESET_SCHEDULE
    ) == {"on": True, "mode": "auto"}
    assert set_preset_mode_status_update(
        SmartboxNodeType.HTR_MOD, {}, PRESET_SELF_LEARN
    ) == {"on": True, "mode": "self_learn"}
    assert set_preset_mode_status_update(
        SmartboxNodeType.HTR_MOD, {}, PRESET_ACTIVITY
    ) == {"on": True, "mode": "presence"}
    assert set_preset_mode_status_update(
        SmartboxNodeType.HTR_MOD, {}, PRESET_COMFORT
    ) == {"on": True, "mode": "manual", "selected_temp": "comfort"}
    assert set_preset_mode_status_update(SmartboxNodeType.HTR_MOD, {}, PRESET_ECO) == {
        "on": True,
        "mode": "manual",
        "selected_temp": "eco",
    }
    assert set_preset_mode_status_update(
        SmartboxNodeType.HTR_MOD, {}, PRESET_FROST
    ) == {
        "on": True,
        "mode": "manual",
        "selected_temp": "ice",
    }

    with pytest.raises(ValueError):
        set_preset_mode_status_update(SmartboxNodeType.HTR, {}, PRESET_SCHEDULE)
    with pytest.raises(ValueError):
        set_preset_mode_status_update(SmartboxNodeType.ACM, {}, PRESET_ACTIVITY)

    with pytest.raises(ValueError):
        set_preset_mode_status_update(SmartboxNodeType.HTR_MOD, {}, "fake_preset")
    with pytest.raises(AssertionError):
        set_preset_mode_status_update(SmartboxNodeType.HTR_MOD, {}, PRESET_HOME)
    with pytest.raises(AssertionError):
        set_preset_mode_status_update(SmartboxNodeType.HTR_MOD, {}, PRESET_AWAY)


def test_get_temperature_unit():
    assert get_temperature_unit({"units": "C"}) == UnitOfTemperature.CELSIUS
    assert get_temperature_unit({"units": "F"}) == UnitOfTemperature.FAHRENHEIT
    assert get_temperature_unit({}) is None
    with pytest.raises(ValueError) as exc_info:
        get_temperature_unit({"units": "K"})
    assert "Unknown temp unit K" in exc_info.exconly()


def test_version_and_get_model_code():
    node = object.__new__(SmartboxNode)

    node._version = {"pid": "081c", "hw_version": "2.3"}
    assert node.version == {"pid": "081c", "hw_version": "2.3"}
    assert node.pid == "081c"
    assert node.hw_version == "2.3"
    assert node.get_model_code() == "1C"

    node._version = {"pid": "ab"}
    assert node.get_model_code() == "AB"

    node._version = {"pid": "x"}
    assert node.get_model_code() is None

    node._version = {"pid": ""}
    assert node.get_model_code() is None

    node._version = {}
    assert node.pid is None
    assert node.hw_version is None
    assert node.get_model_code() is None


async def test_update_samples(hass):
    dev_id = "test_device_id_1"
    mock_device = AsyncMock()
    mock_device.dev_id = dev_id
    mock_device.away = False
    node_addr = 3
    node_type = SmartboxNodeType.HTR
    node_name = "Bathroom Heater"
    node_info = {"addr": node_addr, "name": node_name, "type": node_type}
    mock_session = AsyncMock()
    initial_status = {"mtemp": "21.4", "stemp": "22.5"}
    initial_setup = {
        "true_radiant_enabled": False,
        "window_mode_enabled": False,
    }
    node_sample = [
        {"t": 1735685000, "temp": "11.3", "counter": 0},
        {"t": 1735686000, "temp": "11.3", "counter": 247426},
    ]

    node = SmartboxNode(
        mock_device,
        node_info,
        mock_session,
        initial_status,
        initial_setup,
        node_sample,
        {},
    )
    assert node.total_energy == 247426
    # Test case where get_samples returns less than 2 samples
    mock_session.get_node_samples.return_value = {"samples": [{"counter": 100}]}
    await node.update_samples()
    assert node._samples == node_sample

    # Test case where get_samples returns 2 or more samples
    mock_session.get_node_samples.return_value = {
        "samples": [
            {"counter": 100},
            {"counter": 200},
        ]
    }
    await node.update_samples()
    assert node._samples == [{"counter": 100}, {"counter": 200}]

    # Test case where get_samples returns more than 2 samples
    mock_session.get_node_samples.return_value = {
        "samples": [
            {"counter": 100},
            {"counter": 200},
            {"counter": 300},
        ]
    }
    await node.update_samples()
    assert node._samples == [{"counter": 200}, {"counter": 300}]
    node = SmartboxNode(
        mock_device,
        node_info,
        mock_session,
        initial_status,
        initial_setup,
        [],
        {},
    )
    assert node.total_energy is None


async def test_update_power(hass):
    dev_id = "test_device_id_1"
    mock_device = AsyncMock()
    mock_device.dev_id = dev_id
    mock_device.away = False
    node_addr = 3
    node_type = SmartboxNodeType.HTR
    node_name = "Bathroom Heater"
    node_info = {"addr": node_addr, "name": node_name, "type": node_type}
    mock_session = AsyncMock()
    initial_status = {"mtemp": "21.4", "stemp": "22.5", "power": 4500}
    initial_setup = {
        "true_radiant_enabled": False,
        "window_mode_enabled": False,
    }
    node_sample = {"samples": [{"t": 1735686000, "temp": "11.3", "counter": 247426}]}

    node = SmartboxNode(
        mock_device,
        node_info,
        mock_session,
        initial_status,
        initial_setup,
        node_sample,
        {},
    )
    assert node.status["power"] == 4500
    # Test case where get_samples returns less than 2 samples
    mock_session.get_device_power_limit.return_value = 100
    await node.update_power()
    assert node.status["power"] == 100


def _make_node(status: dict, setup: dict) -> SmartboxNode:
    """Build a SmartboxNode over mocks for setup-field tests."""
    mock_device = AsyncMock()
    mock_device.dev_id = "test_device_id_1"
    node_info = {"addr": 3, "name": "Bathroom Heater", "type": SmartboxNodeType.HTR}
    return SmartboxNode(
        mock_device,
        node_info,
        AsyncMock(),
        status,
        setup,
        [],
        {},
    )


async def test_away_offset():
    """away_offset reads/writes the setup field; absent means None."""
    node = _make_node({}, {"away_offset": "2.0"})
    assert node.away_offset == 2.0

    await node.set_away_offset(4.5)
    node._session.set_node_setup.assert_awaited_once_with(
        "test_device_id_1",
        node.node_info,
        {"away_offset": "4.5"},
    )
    assert node.setup["away_offset"] == "4.5"
    assert node.away_offset == 4.5

    node = _make_node({}, {})
    assert node.away_offset is None
    assert away_offset_available(node) is False
    assert away_offset_available(_make_node({}, {"away_offset": "0"})) is True


async def test_max_stemp_limit():
    """max_stemp_limit reads/writes the setup field; 0.0 counts as unset."""
    node = _make_node({}, {"max_stemp_limit": "30.0"})
    assert node.max_stemp_limit == 30.0
    assert max_temp_limit_available(node) is True

    await node.set_max_stemp_limit(27.0)
    node._session.set_node_setup.assert_awaited_once_with(
        "test_device_id_1",
        node.node_info,
        {"max_stemp_limit": "27.0"},
    )
    assert node.setup["max_stemp_limit"] == "27.0"
    assert node.max_stemp_limit == 27.0

    # "0.0" on the wire means the limit is disabled, not absent.
    node = _make_node({}, {"max_stemp_limit": "0.0"})
    assert node.max_stemp_limit is None
    assert max_temp_limit_available(node) is True

    node = _make_node({}, {})
    assert node.max_stemp_limit is None
    assert max_temp_limit_available(node) is False


async def test_max_temp_limit_enabled():
    """Toggling writes the wire's "0.0" sentinel; on restores the last limit."""
    node = _make_node({}, {"max_stemp_limit": "30.0"})
    assert node.max_temp_limit_enabled is True

    # The last non-zero limit is remembered from setup and writes.
    await node.set_max_stemp_limit(27.0)
    assert node._last_max_stemp_limit == "27.0"
    node.update_setup({"max_stemp_limit": "25.0"})
    assert node._last_max_stemp_limit == "25.0"
    # "0.0" frames do not erase the memory.
    node.update_setup({"max_stemp_limit": "0.0"})
    assert node._last_max_stemp_limit == "25.0"
    assert node.max_temp_limit_enabled is False
    assert node.max_stemp_limit is None

    # Disable: writes "0.0" (already disabled; idempotent wire write).
    node._session.set_node_setup.reset_mock()
    await node.set_max_temp_limit_enabled(False)
    node._session.set_node_setup.assert_awaited_once_with(
        "test_device_id_1",
        node.node_info,
        {"max_stemp_limit": "0.0"},
    )
    assert node.setup["max_stemp_limit"] == "0.0"
    assert node.max_temp_limit_enabled is False

    # Enable: restores the last non-zero limit seen.
    node._session.set_node_setup.reset_mock()
    await node.set_max_temp_limit_enabled(True)
    node._session.set_node_setup.assert_awaited_once_with(
        "test_device_id_1",
        node.node_info,
        {"max_stemp_limit": "25.0"},
    )
    assert node.setup["max_stemp_limit"] == "25.0"
    assert node.max_temp_limit_enabled is True

    # A node that never saw a non-zero limit enables at 30.0 (slider cap).
    node = _make_node({}, {"max_stemp_limit": "0.0"})
    await node.set_max_temp_limit_enabled(True)
    node._session.set_node_setup.assert_awaited_once_with(
        "test_device_id_1",
        node.node_info,
        {"max_stemp_limit": "30.0"},
    )


async def test_priority():
    """Priority reads/writes the setup field; absent means None."""
    node = _make_node({}, {"priority": "low"})
    assert node.priority == "low"
    assert priority_available(node) is True

    await node.set_priority("high")
    node._session.set_node_setup.assert_awaited_once_with(
        "test_device_id_1",
        node.node_info,
        {"priority": "high"},
    )
    assert node.setup["priority"] == "high"
    assert node.priority == "high"

    node = _make_node({}, {})
    assert node.priority is None
    assert priority_available(node) is False


async def test_set_prog_temps():
    """set_prog_temps sends the app-shaped 4-key status body."""
    status = {
        "ice_temp": "5.0",
        "eco_temp": "18.0",
        "comf_temp": "22.0",
        "units": "C",
    }
    node = _make_node(status, {})
    node._device.dispatch_node_status = AsyncMock()

    await node.set_prog_temps(eco_temp=19.5)
    node._session.set_node_status.assert_awaited_once_with(
        "test_device_id_1",
        node.node_info,
        {
            "ice_temp": "5.0",
            "eco_temp": "19.5",
            "comf_temp": "22.0",
            "units": "C",
        },
    )
    # Optimistic merge into the local status cache.
    assert node.status["eco_temp"] == "19.5"

    # Off-grid values land on the device's 0.5 C grid.
    await node.set_prog_temps(comf_temp=21.3)
    body = node._session.set_node_status.await_args.args[2]
    assert body["comf_temp"] == "21.5"


def test_prog_temps_available():
    """Profile temps are offered on plain htr/acm nodes only."""
    full_status = {"ice_temp": "5.0", "eco_temp": "18.0", "comf_temp": "22.0"}
    node = _make_node(full_status, {})
    node._node_info["type"] = SmartboxNodeType.HTR
    assert prog_temps_available(node) is True

    node._node_info["type"] = SmartboxNodeType.ACM
    assert prog_temps_available(node) is True

    # htr_mod resolves profiles via comfort_temp/eco_offset instead.
    node._node_info["type"] = SmartboxNodeType.HTR_MOD
    assert prog_temps_available(node) is False

    # Missing status keys (e.g. an unexpected family) → not offered.
    node._node_info["type"] = SmartboxNodeType.HTR
    node._status = {"ice_temp": "5.0"}
    assert prog_temps_available(node) is False


def test_smartbox_device_property():
    """Test the device property of SmartboxDevice."""
    dev_id = "device_1"
    mock_session = MagicMock()
    mock_device_info = MOCK_SMARTBOX_DEVICE_INFO[dev_id]
    # Simulate initialise_nodes with mock data, make sure nobody calls the real one
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(mock_device_info, mock_session, hass=None)
        assert device.device == mock_device_info
        assert device.name == MOCK_SMARTBOX_DEVICE_INFO[dev_id]["name"]


async def test_smartbox_device_away_status_extras(hass):
    """The away cache merges the live-verified {away, enabled, forced} flags."""
    dev_id = "device_1"
    mock_session = MagicMock()
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO[dev_id], mock_session, hass)
        device._away_status_update({"away": True, "enabled": True, "forced": True})
        assert device.away is True
        assert device.away_status == {"away": True, "enabled": True, "forced": True}

        # Partial frames merge over the cache instead of replacing it.
        device._away_status_update({"forced": False})
        assert device.away_status == {"away": True, "enabled": True, "forced": False}


async def test_smartbox_device_power_limit_no_limit(hass):
    """0 is the wire's "no limit" value; the last non-zero limit is tracked."""
    dev_id = "device_1"
    mock_session = MagicMock()
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO[dev_id], mock_session, hass)
        assert device.power_limit == 0
        assert device.no_power_limit is True
        assert device.last_nonzero_power_limit is None

        device._power_limit_update(1500)
        assert device.no_power_limit is False
        assert device.last_nonzero_power_limit == 1500

        # 0 never resets the restore history...
        device._power_limit_update(0)
        assert device.no_power_limit is True
        assert device.last_nonzero_power_limit == 1500

        # ...and a zero-watt limit write still lands optimistically.
        device._power_limit = 1500
        device._power_limit_update(0)
        assert device.power_limit == 0


async def test_smartbox_device_rtc_cache(hass):
    """async_refresh_rtc caches dict payloads and tolerates odd ones."""
    dev_id = "device_1"
    mock_session = AsyncMock()
    mock_session.get_device_rtc_time = AsyncMock(
        return_value={"d": 27, "h": 9, "m": 55, "n": 8, "s": 24, "w": 0, "y": 2026}
    )
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO[dev_id], mock_session, hass)
        assert await device.async_refresh_rtc() == mock_session.get_device_rtc_time.return_value

        # A non-dict payload keeps the cached snapshot.
        mock_session.get_device_rtc_time = AsyncMock(return_value=MagicMock())
        assert await device.async_refresh_rtc() == {
            "d": 27,
            "h": 9,
            "m": 55,
            "n": 8,
            "s": 24,
            "w": 0,
            "y": 2026,
        }


async def test_remaining_boost_time(hass):
    dev_id = "test_device_id_1"
    mock_device = AsyncMock()
    mock_device.dev_id = dev_id
    mock_device.away = False
    node_addr = 3
    node_type = SmartboxNodeType.HTR
    node_name = "Bathroom Heater"
    node_info = {"addr": node_addr, "name": node_name, "type": node_type}
    mock_session = AsyncMock()
    initial_status = {
        "mtemp": "21.4",
        "stemp": "22.5",
        "boost": True,
        "boost_end_min": 90,
    }
    initial_setup = {
        "true_radiant_enabled": False,
        "window_mode_enabled": False,
    }
    node_sample = {"samples": [{"t": 1735686000, "temp": "11.3", "counter": 247426}]}

    node = SmartboxNode(
        mock_device,
        node_info,
        mock_session,
        initial_status,
        initial_setup,
        node_sample,
        {},
    )

    assert node.boost_end_min == 90
    # Test case when boost is not active
    node._status["boost"] = False
    assert node.remaining_boost_time == 0

    # Test case when boost is active: boost_end_min is the minute of the day (UTC)
    fixed_now = datetime(2023, 10, 10, 1, 0, tzinfo=dt_util.UTC)
    with (
        patch(
            "custom_components.smartbox.models.dt_util.utcnow",
            return_value=fixed_now,
        ),
        patch(
            "custom_components.smartbox.models.dt_util.now",
            return_value=fixed_now,
        ),
    ):
        node._status["boost"] = True
        node._status["boost_end_min"] = 90  # 01:30 UTC, in the future
        assert node.remaining_boost_time == 30 * 60

        # Boost end time already passed in the UTC day: rolls over to tomorrow
        node._status["boost_end_min"] = 30  # 00:30 UTC
        assert node.remaining_boost_time == 23 * 3600 + 30 * 60


async def test_smartbox_device_node_prog_update(hass, caplog):
    """Node prog updates from the socket are validated and dispatched."""
    dev_id = "device_1"
    mock_session = MagicMock()
    mock_node_1 = MagicMock()
    mock_node_1.node_id = "device_1_1"
    mock_node_2 = MagicMock()
    mock_node_2.node_id = "device_1_2"
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO[dev_id], mock_session, hass)
        device._nodes = {
            (SmartboxNodeType.HTR, 1): mock_node_1,
            (SmartboxNodeType.ACM, 2): mock_node_2,
        }

        with patch(
            "custom_components.smartbox.models.async_dispatcher_send"
        ) as mock_send:
            # REST-shape payload (subscribe_to_node_prog update flow)
            device._node_prog_update(SmartboxNodeType.HTR, 1, {"prog": {"0": [1, 2]}})
            mock_node_1.update_prog.assert_called_with({"0": [1, 2]})
            mock_node_2.update_prog.assert_not_called()
            mock_send.assert_called_with(
                hass, "smartbox_device_1_1_prog", {"0": [1, 2]}
            )

            # An unchanged schedule is still applied and dispatched: after a
            # set_prog optimistic merge the *entity* copy can be the stale
            # one, and a frame equal to the node cache is then the only
            # confirmation it gets (mirrors the status desync fix,
            # 2026-09-28). update_prog is a mock, so simulate the cache
            # already holding the schedule first.
            mock_node_1.prog = {"0": [1, 2]}
            mock_node_1.update_prog.reset_mock()
            mock_send.reset_mock()
            device._node_prog_update(SmartboxNodeType.HTR, 1, {"prog": {"0": [1, 2]}})
            mock_node_1.update_prog.assert_called_with({"0": [1, 2]})
            mock_send.assert_called_with(
                hass, "smartbox_device_1_1_prog", {"0": [1, 2]}
            )

            # malformed payload: ignored, no error
            mock_node_1.update_prog.reset_mock()
            mock_send.reset_mock()
            device._node_prog_update(SmartboxNodeType.HTR, 1, {"prog": {"0": "nope"}})
            mock_node_1.update_prog.assert_not_called()
            mock_send.assert_not_called()

            # PMO nodes are skipped
            device._node_prog_update(SmartboxNodeType.PMO, 3, {"prog": {"0": [0]}})
            mock_node_1.update_prog.assert_not_called()
            mock_node_2.update_prog.assert_not_called()

        # unknown node
        device._node_prog_update(SmartboxNodeType.HTR, 3, {"prog": {"0": [0]}})
        assert_log_message(
            caplog,
            "custom_components.smartbox.models",
            logging.ERROR,
            "Received prog update for unknown node htr 3",
        )


async def test_smartbox_node_prog(hass):
    """Node schedule cache, REST refresh and partial-day write."""
    mock_session = AsyncMock()
    mock_device = MagicMock()
    mock_device.dev_id = "device_1"
    node_info = {"addr": 1, "name": "node_1", "type": "htr"}
    node = SmartboxNode(
        mock_device,
        node_info,
        mock_session,
        {"locked": False},
        {"factory_options": {}},
        [],
        {"hw_version": "1.0", "fw_version": "1.0"},
    )
    assert node.prog is None

    # REST refresh: valid response is normalized
    mock_session.get_node_prog.return_value = {
        "prog": {"0": [1, 2]},
        "sync_status": "ok",
    }
    assert await node.async_refresh_prog() == {"0": [1, 2]}
    # refresh does not itself update the cache (the entity does)
    assert node.prog is None

    # out-of-sync and malformed payloads keep the (empty) cache
    mock_session.get_node_prog.return_value = {
        "prog": {"0": [1]},
        "sync_status": "lost",
    }
    assert await node.async_refresh_prog() is None
    mock_session.get_node_prog.return_value = {
        "prog": "garbage",
        "sync_status": "ok",
    }
    assert await node.async_refresh_prog() is None

    # transient API failures propagate
    mock_session.get_node_prog.side_effect = APIUnavailableError("down")
    with pytest.raises(APIUnavailableError):
        await node.async_refresh_prog()

    # partial-day write merges the local cache optimistically
    node.update_prog({"0": [0] * 24, "1": [1] * 24})
    await node.set_prog({"1": [2] * 24})
    mock_session.set_node_prog.assert_awaited_once_with(
        "device_1", node_info, {"prog": {"1": [2] * 24}}
    )
    assert node.prog == {"0": [0] * 24, "1": [1] * 24} | {"1": [2] * 24}


async def test_watchdog_restarts_on_unexpected_exit(hass, caplog):
    """run() dying unexpectedly is logged and the task is restarted."""
    mock_session = MagicMock()
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO["device_1"], mock_session, hass)
        device.update_manager.run = AsyncMock()

        # Spy on the restart scheduler: record the call, then flip _stopping
        # so the churn loop cannot keep scheduling restart tasks forever.
        restarts: list[None] = []

        def _record_restart() -> None:
            restarts.append(None)
            device._stopping = True

        device._schedule_watchdog_restart = _record_restart

        task = asyncio.create_task(device.update_manager.run())
        device._watchdog_task = task
        task.add_done_callback(device._watchdog_done)
        await task
        # Flush the done callback (it runs via loop.call_soon).
        await asyncio.sleep(0)

        assert restarts, "unexpected run() exit must schedule a restart"
        assert_log_message(
            caplog,
            "custom_components.smartbox.models",
            logging.ERROR,
            "Update task for device device_1 exited unexpectedly; restarting",
        )


async def test_dispatch_node_status(hass):
    """Status snapshots go out on the node's status dispatcher event."""
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(
            MOCK_SMARTBOX_DEVICE_INFO["device_1"], MagicMock(), hass
        )
    mock_node = MagicMock()
    mock_node.node_id = "device_1_0"
    with patch(
        "custom_components.smartbox.models.async_dispatcher_send"
    ) as mock_send:
        device.dispatch_node_status(mock_node, {"mtemp": "21.4"})
    mock_send.assert_called_once_with(
        hass, "smartbox_device_1_0_status", {"mtemp": "21.4"}
    )


async def test_node_status_update_forwards_frame_equal_to_cache(hass):
    """A confirming frame equal to the node cache must still be dispatched.

    Regression (live finding, 2026-09-28): the frame used to be swallowed by
    the node-cache equality check, leaving entities -- whose own copy never
    saw the optimistic set_status merge -- on the old hvac mode until some
    unrelated change pushed a different frame.
    """
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(
            MOCK_SMARTBOX_DEVICE_INFO["device_1"], MagicMock(), hass
        )
        mock_node = MagicMock()
        mock_node.node_id = "device_1_0"
        mock_node.status = {
            "mode": "manual",
            "mtemp": "21.4",
            "stemp": "22.5",
            "sync_status": "ok",
        }
        device._nodes = {(SmartboxNodeType.HTR, 0): mock_node}

        frame = dict(mock_node.status)
        with patch(
            "custom_components.smartbox.models.async_dispatcher_send"
        ) as mock_send:
            device._node_status_update(SmartboxNodeType.HTR, 0, frame)
        mock_node.update_status.assert_called_once_with(frame)
        mock_send.assert_called_once_with(
            hass, "smartbox_device_1_0_status", frame
        )


async def test_node_status_update_skips_non_ok_frames(hass):
    """sync_status != ok frames are not dispatched to entities."""
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(
            MOCK_SMARTBOX_DEVICE_INFO["device_1"], MagicMock(), hass
        )
        mock_node = MagicMock()
        mock_node.node_id = "device_1_0"
        device._nodes = {(SmartboxNodeType.HTR, 0): mock_node}

        with patch(
            "custom_components.smartbox.models.async_dispatcher_send"
        ) as mock_send:
            device._node_status_update(
                SmartboxNodeType.HTR, 0, {"sync_status": "lost"}
            )
        # The sync marker still enters the node cache (the poll path uses it
        # to mark entities unavailable); entities are just not notified
        mock_node.update_status.assert_called_once_with({"sync_status": "lost"})
        mock_send.assert_not_called()


async def test_update_status_lost_frame_keeps_snapshot():
    """Transient {"sync_status": "lost"} frames only flip the sync marker."""
    node = SmartboxNode(
        MagicMock(),
        {"addr": 0, "name": "Heater", "type": SmartboxNodeType.HTR},
        AsyncMock(),
        {"mtemp": "21.4", "sync_status": "ok"},
        {},
        [],
        {},
    )
    node.update_status({"sync_status": "lost"})
    assert node.status == {"mtemp": "21.4", "sync_status": "lost"}


async def test_cancel_does_not_restart_watchdog_when_run_returns(hass, caplog):
    """run() returning after cancel() (reconnect backoff) must not restart it."""
    mock_session = MagicMock()
    with patch(
        "custom_components.smartbox.models.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO["device_1"], mock_session, hass)
        exit_requested = asyncio.Event()

        async def _run() -> None:
            await exit_requested.wait()

        async def _cancel() -> None:
            exit_requested.set()
            await asyncio.sleep(0)

        device.update_manager.run = _run
        device.update_manager.cancel = _cancel
        restarts: list[None] = []
        device._schedule_watchdog_restart = lambda: restarts.append(None)

        task = asyncio.create_task(device.update_manager.run())
        device._watchdog_task = task
        task.add_done_callback(device._watchdog_done)

        await device.cancel()
        await asyncio.sleep(0)

        assert not restarts
        assert "exited unexpectedly" not in caplog.text
