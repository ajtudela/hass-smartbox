from typing import Any, Dict, List

from custom_components.smartbox.const import (
    CONF_API_NAME,
    CONF_PASSWORD,
    CONF_SESSION_BACKOFF_FACTOR,
    CONF_SESSION_RETRY_ATTEMPTS,
    CONF_SOCKET_BACKOFF_FACTOR,
    CONF_SOCKET_RECONNECT_ATTEMPTS,
    CONF_USERNAME,
    DOMAIN,
    SmartboxNodeType,
)

CONF_DEVICE_IDS = "device_ids"

MOCK_SMARTBOX_CONFIG = {
    DOMAIN: {
        CONF_API_NAME: "test_api_name_1",
        CONF_USERNAME: "test_username_1",
        CONF_PASSWORD: "test_password_1",
        CONF_DEVICE_IDS: ["device_1", "device_2"],
    }
}

MOCK_SESSION_CONFIG = {
    DOMAIN: {
        CONF_SESSION_RETRY_ATTEMPTS: 7,
        CONF_SESSION_BACKOFF_FACTOR: 0.4,
        CONF_SOCKET_RECONNECT_ATTEMPTS: 6,
        CONF_SOCKET_BACKOFF_FACTOR: 0.5,
    }
}


MOCK_SMARTBOX_DEVICE_INFO = {
    "device_1": {
        "dev_id": "device_1",
        "name": "Device 1",
        "product_id": "product_id_1",
        "fw_version": "fw_version_1",
        "serial_id": "serial_id_1",
    },
    "device_2": {
        "dev_id": "device_2",
        "name": "Device 2",
        "product_id": "product_id_2",
        "fw_version": "fw_version_2",
        "serial_id": "serial_id_2",
    },
}

MOCK_SMARTBOX_HOME_INFO = [
    {
        "id": "home_1",
        "name": "Home 1",
        "owner": True,
        "devs": [item for item in MOCK_SMARTBOX_DEVICE_INFO.values()],
    },
]

MOCK_SMARTBOX_NODE_INFO = {
    "device_1": [
        {
            "addr": 0,
            "name": "Device 1 0",
            "type": SmartboxNodeType.HTR,
            "product_id": "product_id_1_0",
            "fw_version": "fw_version_1_0",
            "serial_id": "serial_id_1_0",
        },
        {
            "addr": 1,
            "name": "Device 1 1",
            "type": SmartboxNodeType.ACM,
            "product_id": "product_id_1_1",
            "fw_version": "fw_version_1_1",
            "serial_id": "serial_id_1_1",
        },
    ],
    "device_2": [
        {
            "addr": 0,
            "name": "Device 2 0",
            "type": SmartboxNodeType.PMO,
            "product_id": "product_id_2_0",
            "fw_version": "fw_version_2_0",
            "serial_id": "serial_id_2_0",
        },
        {
            "addr": 1,
            "name": "Device 2 1",
            "type": SmartboxNodeType.HTR_MOD,
            "product_id": "product_id_2_1",
            "fw_version": "fw_version_2_1",
            "serial_id": "serial_id_2_1",
        },
        {
            "addr": 2,
            "name": "Device 2 2",
            "type": SmartboxNodeType.HTR_MOD,
            "product_id": "product_id_2_2",
            "fw_version": "fw_version_2_2",
            "serial_id": "serial_id_2_2",
        },
        {
            "addr": 3,
            "name": "Device 2 3",
            "type": SmartboxNodeType.HTR_MOD,
            "product_id": "product_id_2_3",
            "fw_version": "fw_version_2_3",
            "serial_id": "serial_id_2_3",
        },
        {
            "addr": 4,
            "name": "Device 2 4",
            "type": SmartboxNodeType.HTR_MOD,
            "product_id": "product_id_2_4",
            "fw_version": "fw_version_2_4",
            "serial_id": "serial_id_2_4",
        },
        {
            "addr": 5,
            "name": "Device 2 5",
            "type": SmartboxNodeType.HTR_MOD,
            "product_id": "product_id_2_5",
            "fw_version": "fw_version_2_5",
            "serial_id": "serial_id_2_5",
        },
    ],
}

MOCK_SMARTBOX_NODE_SETUP: Dict[str, List[Dict[str, Any]]] = {
    "device_1": [
        {
            "factory_options": {
                "true_radiant_available": True,
                "window_mode_available": True,
            },
            "true_radiant_enabled": False,
            "window_mode_enabled": False,
        },
        {
            "factory_options": {
                "true_radiant_available": False,
                "window_mode_available": False,
            },
        },
    ],
    "device_2": [
        {"circuit_type": 0, "power_limit": 1500, "reverse": False, "power": 1500},
        {
            "factory_options": {
                "true_radiant_available": True,
                "window_mode_available": True,
            },
            "true_radiant_enabled": False,
            "window_mode_enabled": False,
        },
        {
            "factory_options": {
                "true_radiant_available": True,
                "window_mode_available": True,
            },
            "true_radiant_enabled": True,
            "window_mode_enabled": True,
        },
        {
            "factory_options": {
                "true_radiant_available": True,
                "window_mode_available": True,
            },
            "true_radiant_enabled": False,
            "window_mode_enabled": False,
        },
        {
            "factory_options": {
                "true_radiant_available": False,
                "window_mode_available": False,
            },
            "true_radiant_enabled": True,
            "window_mode_enabled": True,
        },
        {
            # Test factory_options missing
        },
    ],
}

MOCK_SMARTBOX_NODE_AWAY: Dict[str, List[Dict[str, Any]]] = {
    "device_1": {"enabled": True, "away": True, "forced": True},
    "device_2": {"enabled": False, "away": False, "forced": False},
}

MOCK_SMARTBOX_DEVICE_POWER: Dict[str, List[Dict[str, Any]]] = {
    "device_1": 1500,
    "device_2": 1000,
}

MOCK_SMARTBOX_NODE_STATUS: Dict[str, List[Dict[str, Any]]] = {
    "device_1": [
        {
            "ice_temp": "5.0",
            "eco_temp": "18.0",
            "comf_temp": "22.0",
            "act_duty": 45,
            "pcb_temp": "30.0",
            "power_pcb_temp": "35.0",
            "presence": True,
            "window_open": False,
            "true_radiant_active": True,
            "boost": False,
            "boost_end_min": 0,
            "boost_end_day": 0,
            "error_code": "none",
            "mtemp": "25.7",
            "stemp": "20.3",
            "units": "C",
            "sync_status": "ok",
            "locked": 0,
            "active": True,
            "power": "510",
            "duty": 50,
            "mode": "auto",
        },
        {
            "mtemp": "19.2",
            "stemp": "21",
            "units": "C",
            "sync_status": "ok",
            "locked": False,
            # acm nodes have a 'charging' state rather than 'active'
            "charging": True,
            "charge_level": 2,
            "power": "620",
            "mode": "auto",
        },
    ],
    "device_2": [
        {
            "sync_status": "ok",
            "locked": False,
            "power": "2500",
            "active": True,
            "mtemp": "19.2",
        },
        {
            "on": True,
            "mtemp": "18.2",
            "selected_temp": "comfort",
            "comfort_temp": "20.3",
            "eco_offset": "4",
            "ice_temp": "7",
            "units": "C",
            "sync_status": "ok",
            "locked": False,
            "active": True,
            "mode": "manual",
            "power": "510",
        },
        {
            "on": True,
            "mtemp": "19.2",
            "selected_temp": "eco",
            "comfort_temp": "24.3",
            "eco_offset": "4",
            "ice_temp": "7",
            "units": "C",
            "sync_status": "ok",
            "locked": False,
            "active": True,
            "mode": "manual",
            "power": "510",
        },
        {
            "on": True,
            "mtemp": "17.2",
            "selected_temp": "ice",
            "comfort_temp": "23.3",
            "eco_offset": "4",
            "ice_temp": "7",
            "units": "C",
            "sync_status": "ok",
            "locked": False,
            "active": True,
            "mode": "manual",
            "power": "510",
        },
        {
            "on": True,
            "mtemp": "16.2",
            "selected_temp": "ice",
            "comfort_temp": "23.3",
            "eco_offset": "4",
            "ice_temp": "7",
            "units": "C",
            "sync_status": "ok",
            "locked": False,
            "active": True,
            "mode": "self_learn",
            "power": "510",
        },
        {
            "on": True,
            "mtemp": "15.2",
            "selected_temp": "ice",
            "comfort_temp": "23.3",
            "eco_offset": "4",
            "ice_temp": "7",
            "units": "C",
            "sync_status": "ok",
            "locked": False,
            "active": True,
            "mode": "presence",
            "power": "510",
        },
    ],
}
