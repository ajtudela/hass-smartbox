"""Identity/status boost-policy helpers shared by the adapter surfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING

from smartbox.core.merging import boost_config
from smartbox.device.codes import product_code
from smartbox.device.modes import (
    boost_activation_mode,
    boost_modes_available,
    boost_params_editable,
)
from smartbox.device.profile import TEVOLVE_ATC_CODES, TEVOLVE_WIFI_CODES
from smartbox.protocol.values import BoostConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "boost_activation_mode",
    "boost_modes_available",
    "boost_params_configurable",
    "runback_params_configurable",
]


def boost_params_configurable(
    node_type: str,
    pid: str | None,
    setup: Mapping[str, object] | None,
    status: Mapping[str, object] | None,
) -> bool:
    """Whether HA should expose boost temperature/duration controls."""
    if boost_params_editable(node_type, pid, setup):
        return True
    return boost_activation_mode(pid, setup, status) == "temp_and_time"


def runback_params_configurable(
    pid: str | None,
    setup: Mapping[str, object] | None,
    status: Mapping[str, object] | None,
) -> bool:
    """Whether HA should expose runback temperature/duration controls."""
    if boost_config(setup or {}) != BoostConfig.FULL:
        return False
    code = product_code(pid)
    if code not in TEVOLVE_WIFI_CODES and code not in TEVOLVE_ATC_CODES:
        return False
    return "runback" in boost_modes_available(status)
