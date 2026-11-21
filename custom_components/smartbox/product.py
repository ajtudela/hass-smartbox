"""Product labels from the node version PID.

The vendor app's ``EProductCodeValue`` enum maps the product code (the
third and fourth PID characters, ``pid[2:4]`` uppercased) to a control
product category. The app itself uses the same slice for controller
dispatch; this module turns it into a human label for the Home Assistant
device page. It is a device-level fact, not an entity.

A towel rail is an add-device/UI category, not a product code: the enum
has no towel-rail value, so a towel rail reports the category of its
controller (usually a triac or relay heater) and cannot be distinguished
from an ordinary heater here. The model picker is driven by the per-tenant
``client-data/<serial>/capabilities.json`` ``supportedNodeTypes`` /
``supportedDevices`` data, not by a node's PID.
"""

from __future__ import annotations

from .const import SmartboxNodeType

# The app's EProductCodeValue enum, grouped by control category. Codes not
# present here fall back to the wire node type.
PRODUCT_CODE_LABELS: dict[str, str] = {
    # Helki
    "01": "Triac heater",
    "02": "Relay heater",
    "07": "Triac heater",
    "32": "WiFi heater",
    "35": "WiFi heater",
    "1C": "Storage heater",
    "04": "Thermostat",
    "0F": "Thermostatic valve",
    "03": "Power monitor",
    "22": "Power monitor",
    # Elnur
    "11": "Triac heater",
    "0B": "Triac heater",
    "13": "Relay heater",
    "1B": "Triac heater",
    "0C": "Triac heater",
    "14": "Storage heater",
    "20": "Storage heater",
    "25": "Storage heater",
    "2A": "Water storage heater",
    "2E": "Storage heater",
    # Haverland
    "0A": "Heater module",
    "17": "Relay heater",
    "1D": "WiFi heater",
    "47": "WiFi heater",
    "FF": "Heater",
    # Climastar
    "08": "Triac heater",
    "10": "Relay heater",
    "16": "Relay heater",
    "12": "Heater",
    "3D": "WiFi heater",
    # Lucht LHZ
    "33": "WiFi heater",
    "0D": "Relay heater",
    "18": "Relay heater",
    "36": "Relay heater",
    # Perry
    "09": "Thermostat",
    # Tevolve
    "1A": "Triac heater",
    "21": "Triac heater",
    "1E": "Relay heater",
    "23": "Relay heater",
    "24": "Heater",
    "27": "Heater",
    "28": "Thermostat",
    "31": "WiFi heater",
    "34": "WiFi heater",
    "39": "WiFi heater",
    "4C": "WiFi heater",
    # EV Confort / Ferroli
    "1F": "Triac heater",
    "38": "WiFi heater",
    "43": "WiFi heater",
    "4D": "WiFi heater",
}

NODE_TYPE_LABELS: dict[str, str] = {
    SmartboxNodeType.HTR: "Heater",
    SmartboxNodeType.HTR_MOD: "Heater",
    SmartboxNodeType.ACM: "Storage heater",
    SmartboxNodeType.THM: "Thermostat",
    SmartboxNodeType.PMO: "Power monitor",
}

# The vendor app reads the product code as pid.substring(2, 4).
_PID_PRODUCT_CODE_START = 2
_PID_PRODUCT_CODE_END = 4


def product_code(pid: str | None) -> str | None:
    """Return the vendor product code (``pid[2:4]``, uppercased), or None."""
    if not isinstance(pid, str):
        return None
    value = pid.strip().upper()
    if len(value) < _PID_PRODUCT_CODE_END:
        return None
    return value[_PID_PRODUCT_CODE_START:_PID_PRODUCT_CODE_END]


def product_label(node_type: str, pid: str | None) -> str:
    """Return a human product label for a node, falling back to its wire type."""
    code = product_code(pid)
    if code is not None and code in PRODUCT_CODE_LABELS:
        return PRODUCT_CODE_LABELS[code]
    return NODE_TYPE_LABELS.get(node_type, node_type)
