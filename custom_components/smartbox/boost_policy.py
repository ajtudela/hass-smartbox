"""Identity/status boost-policy helpers shared by the adapter surfaces."""

from __future__ import annotations

from typing import TYPE_CHECKING

from smartbox.core.merging import boost_config
from smartbox.device.codes import code_info, product_code
from smartbox.device.modes import boost_params_editable
from smartbox.device.profile import (
    ELNUR_STORAGE_CODES,
    HAVERLAND_FIXED_TIME_CODES,
    HELKI_STORAGE_CODES,
    TEVOLVE_ATC_CODES,
    TEVOLVE_WIFI_CODES,
)
from smartbox.protocol.values import BoostConfig

if TYPE_CHECKING:
    from collections.abc import Mapping


def boost_modes_available(
    status: Mapping[str, object] | None,
) -> tuple[str, ...]:
    """Return the FULL endpoint's advertised modes from a status snapshot."""
    value = (status or {}).get("boost_modes_available")
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(mode for mode in value if isinstance(mode, str))


def boost_activation_mode(  # noqa: PLR0911 - one branch per family
    pid: str | None,
    setup: Mapping[str, object] | None,
    status: Mapping[str, object] | None,
) -> str | None:
    """Return the ``/boost_mode`` style a FULL controller should use."""
    if boost_config(setup or {}) != BoostConfig.FULL:
        return None
    code = product_code(pid)
    if code in HELKI_STORAGE_CODES or code in ELNUR_STORAGE_CODES:
        # Storage uses the legacy /boost path even at boost_config 3.
        return None
    if code in HAVERLAND_FIXED_TIME_CODES:
        return "max_temp_fixed_time"
    if code in TEVOLVE_WIFI_CODES or code in TEVOLVE_ATC_CODES:
        modes = boost_modes_available(status)
        if "temp_and_time" in modes:
            return "temp_and_time"
        if "max_temp_fixed_time" in modes:
            return "max_temp_fixed_time"
        return None
    # An acm PID outside the storage sets takes this generic tail; no reviewed
    # storage row carries a FULL boost config, so the storage list stays explicit.
    info = code_info(pid)
    if info is not None and info.fixed_time_boost:
        return "max_temp_fixed_time"
    return "temp_and_time"


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
