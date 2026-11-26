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

from smartbox.device.codes import PRODUCT_CODE_LABELS, product_code

from .const import SmartboxNodeType

NODE_TYPE_LABELS: dict[str, str] = {
    SmartboxNodeType.HTR: "Heater",
    SmartboxNodeType.HTR_MOD: "Heater",
    SmartboxNodeType.ACM: "Storage heater",
    SmartboxNodeType.THM: "Thermostat",
    SmartboxNodeType.PMO: "Power monitor",
}


def product_label(node_type: str, pid: str | None) -> str:
    """Return a human product label for a node, falling back to its wire type."""
    code = product_code(pid)
    if code is not None and code in PRODUCT_CODE_LABELS:
        return PRODUCT_CODE_LABELS[code]
    return NODE_TYPE_LABELS.get(node_type, node_type)


__all__ = [
    "NODE_TYPE_LABELS",
    "PRODUCT_CODE_LABELS",
    "product_code",
    "product_label",
]
