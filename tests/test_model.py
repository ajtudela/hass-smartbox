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
import pytest

from custom_components.smartbox.const import (
    PRESET_FROST,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
    SmartboxNodeType,
)
from custom_components.smartbox.model import (
    SmartboxDevice,
    SmartboxNode,
    _get_htr_mod_preset_mode,
    get_hvac_mode,
    get_target_temperature,
    get_temperature_unit,
    is_heater_node,
    is_supported_node,
    set_hvac_mode_args,
    set_preset_mode_status_update,
    set_temperature_args,
)

from .const import MOCK_SMARTBOX_DEVICE_INFO
from .mocks import mock_node
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
        "custom_components.smartbox.model.SmartboxDevice.initialise_nodes",
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
    # Simulate initialise_nodes with mock data, make sure nobody calls the real one
    with patch(
        "custom_components.smartbox.model.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(MOCK_SMARTBOX_DEVICE_INFO[dev_id], mock_session, hass)
        device._nodes = {
            (SmartboxNodeType.HTR, 1): mock_node_1,
            (SmartboxNodeType.ACM, 2): mock_node_2,
        }

        device._connected(connected=True)
        assert device.connected

        device._connected(connected=False)
        assert not device.connected


async def test_smartbox_device_node_status_update(hass, caplog):
    """Independently test node status updates usually called by UpdateManager."""
    dev_id = "device_1"
    mock_session = MagicMock()
    mock_node_1 = MagicMock()
    mock_node_2 = MagicMock()
    mock_node_3 = MagicMock()
    # Simulate initialise_nodes with mock data, make sure nobody calls the real one
    with patch(
        "custom_components.smartbox.model.SmartboxDevice.initialise_nodes",
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
            "custom_components.smartbox.model",
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
        "custom_components.smartbox.model.SmartboxDevice.initialise_nodes",
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
            "custom_components.smartbox.model",
            logging.ERROR,
            "Received setup update for unknown node htr 3",
        )


async def test_smartbox_node(hass):
    dev_id = "test_device_id_1"
    mock_device = AsyncMock()
    mock_device.dev_id = dev_id
    mock_device.away = False
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


def test_is_heater_node():
    dev_id = "device_1"
    addr = 1
    assert is_heater_node(mock_node(dev_id, addr, SmartboxNodeType.HTR))
    assert is_heater_node(mock_node(dev_id, addr, SmartboxNodeType.HTR_MOD))
    assert is_heater_node(mock_node(dev_id, addr, SmartboxNodeType.ACM))
    assert not is_heater_node(mock_node(dev_id, addr, "sldkfjsd"))


def test_is_supported_node():
    dev_id = "device_1"
    addr = 1
    assert is_supported_node(mock_node(dev_id, addr, SmartboxNodeType.HTR))
    assert is_supported_node(mock_node(dev_id, addr, SmartboxNodeType.HTR_MOD))
    assert is_supported_node(mock_node(dev_id, addr, SmartboxNodeType.ACM))
    assert not is_supported_node(mock_node(dev_id, addr, "oijijr"))


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
        == 0
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
    assert set_temperature_args(SmartboxNodeType.HTR, {"units": "C"}, 21.7) == {
        "stemp": "21.7",
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


def test_get_hvac_mode():
    assert get_hvac_mode(SmartboxNodeType.HTR, {"mode": "off"}) == HVACMode.OFF
    assert get_hvac_mode(SmartboxNodeType.ACM, {"mode": "auto"}) == HVACMode.AUTO
    assert get_hvac_mode(SmartboxNodeType.HTR, {"mode": "modified_auto"}) == HVACMode.AUTO
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
    assert set_hvac_mode_args(SmartboxNodeType.ACM, {}, HVACMode.AUTO) == {"mode": "auto"}
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
    assert set_preset_mode_status_update(SmartboxNodeType.HTR_MOD, {}, PRESET_FROST) == {
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


def test_get_htr_mod_preset_mode():
    assert (
        _get_htr_mod_preset_mode(SmartboxNodeType.HTR_MOD, "manual", "comfort")
        == PRESET_COMFORT
    )
    assert (
        _get_htr_mod_preset_mode(SmartboxNodeType.HTR_MOD, "manual", "eco") == PRESET_ECO
    )
    assert (
        _get_htr_mod_preset_mode(SmartboxNodeType.HTR_MOD, "manual", "ice")
        == PRESET_FROST
    )
    assert (
        _get_htr_mod_preset_mode(SmartboxNodeType.HTR_MOD, "auto", "") == PRESET_SCHEDULE
    )
    assert (
        _get_htr_mod_preset_mode(SmartboxNodeType.HTR_MOD, "presence", "")
        == PRESET_ACTIVITY
    )
    assert (
        _get_htr_mod_preset_mode(SmartboxNodeType.HTR_MOD, "self_learn", "")
        == PRESET_SELF_LEARN
    )

    with pytest.raises(ValueError) as exc_info:
        _get_htr_mod_preset_mode(SmartboxNodeType.HTR_MOD, "manual", "unknown")
    assert (
        "Unexpected 'selected_temp' value selected_temp found for htr_mod"
        in exc_info.exconly()
    )

    with pytest.raises(ValueError) as exc_info:
        _get_htr_mod_preset_mode(SmartboxNodeType.HTR_MOD, "unknown_mode", "")
    assert "Unknown smartbox node mode unknown_mode" in exc_info.exconly()


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
    )
    assert node.status["power"] == 4500
    # Test case where get_samples returns less than 2 samples
    mock_session.get_device_power_limit.return_value = 100
    await node.update_power()
    assert node.status["power"] == 100


def test_smartbox_device_property():
    """Test the device property of SmartboxDevice."""
    dev_id = "device_1"
    mock_session = MagicMock()
    mock_device_info = MOCK_SMARTBOX_DEVICE_INFO[dev_id]
    # Simulate initialise_nodes with mock data, make sure nobody calls the real one
    with patch(
        "custom_components.smartbox.model.SmartboxDevice.initialise_nodes",
        new_callable=NonCallableMock,
    ):
        device = SmartboxDevice(mock_device_info, mock_session, hass=None)
        assert device.device == mock_device_info
        assert device.name == MOCK_SMARTBOX_DEVICE_INFO[dev_id]["name"]
