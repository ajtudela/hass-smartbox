"""Controller families: limits, profiles, and the remaining adapter gates.

The neutral limits and programme catalogue now live in
``smartbox.device.profile``. This module re-exports them for the adapter's
existing imports and keeps the gates that have not moved yet: lock kind, boost
kinds/activation policy, and the surface predicates.
"""

from __future__ import annotations

from collections.abc import Mapping

from smartbox.core.merging import boost_config
from smartbox.device.profile import (
    ELNUR_STORAGE_CODES,
    HAVERLAND_FIXED_TIME_CODES,
    HELKI_HEATER_CODES,
    HELKI_STORAGE_CODES,
    PROFILES_BASE,
    PROFILES_HTR_MOD,
    PROFILES_WITH_OFF,
    TEVOLVE_ATC_CODES,
    TEVOLVE_THERMOSTAT_CODES,
    TEVOLVE_TRIAC_CODES,
    TEVOLVE_WIFI_CODES,
    UI_MIN_TEMP_C,
    ControllerLimits,
    boost_temperature_bounds,
    climate_bounds,
    controller_limits,
    firmware_at_least,
    has_no_probes,
    is_helki_heater_family,
    is_htr_mod_controller,
    is_lhz_family,
    is_lhz_relay,
    is_tevolve_atc_family,
    is_tevolve_triac_family,
    is_tevolve_wifi_family,
    max_limit_bounds,
    product_code,
    profile_bounds,
    prog_bounds_c,
)
from smartbox.protocol.values import BoostConfig

from .const import SmartboxNodeType

_product_code = product_code

# Family sets still consumed by the lock/boost gates below.
_HELKI_HEATER_CODES = HELKI_HEATER_CODES
_HAVERLAND_FIXED_TIME_CODES = HAVERLAND_FIXED_TIME_CODES
_TEVOLVE_TRIAC_CODES = TEVOLVE_TRIAC_CODES
_TEVOLVE_WIFI_CODES = TEVOLVE_WIFI_CODES
_TEVOLVE_ATC_CODES = TEVOLVE_ATC_CODES
_TEVOLVE_THERMOSTAT_CODES = TEVOLVE_THERMOSTAT_CODES
_HELKI_STORAGE_CODES = HELKI_STORAGE_CODES
_ELNUR_STORAGE_CODES = ELNUR_STORAGE_CODES


def lock_kind(
    node_type: str,
    pid: str | None,
    fw_version: str | None,
    setup: Mapping[str, object] | None = None,
) -> str:
    """Return the lock payload kind the controller family uses.

    ``int`` means the status field is 0/1/2 (UNBLOCKED / LOCALLY_BLOCKED /
    REMOTELY_BLOCKED) and the write must carry an integer. ``bool`` is the
    base ElectricHeater/Haverland settings path. Storage, the WiFi/ATC chain,
    code 47 and Haverland 1D at fw 1.7+ always write integers; the Helki/Lhz
    heater chain follows the app's ``getKeyboardLockPayload``, which writes
    integers only when ``factory_options.super_lock_available`` is set. The
    family decides; the live status type only decodes values, so a lost node
    at discovery does not switch a known integer-lock unit to a boolean write.
    """
    code = _product_code(pid)
    if (
        node_type == SmartboxNodeType.ACM
        or code in _HELKI_STORAGE_CODES
        or code in _ELNUR_STORAGE_CODES
        or code in _TEVOLVE_WIFI_CODES
        or code in _TEVOLVE_ATC_CODES
        or code == "47"
    ):
        return "int"
    if code == "1D":
        return "int" if firmware_at_least(fw_version, "1.7") else "bool"
    if code in _HELKI_HEATER_CODES:
        factory = (setup or {}).get("factory_options")
        super_lock = isinstance(factory, Mapping) and bool(
            factory.get("super_lock_available")
        )
        return "int" if super_lock else "bool"
    return "bool"


_EASY_CODES = _TEVOLVE_TRIAC_CODES | _TEVOLVE_WIFI_CODES
_RUNBACK_TRIAC_CODES = _TEVOLVE_TRIAC_CODES - {"27"}


def supports_easy(pid: str | None, setup: Mapping[str, object] | None = None) -> bool:
    """Return True for the controllers that expose the EASY flag.

    Tevolve triac/towel-rail/PIR and WiFi keep EASY; the ATC subclasses remove
    it, and the factory-default controllers have no such setting. The Tevolve
    thermostat (28) also drops its EASY/RUNBACK settings when the controller
    reports NO_PROBES (timer mode).
    """
    code = _product_code(pid)
    if code in _TEVOLVE_THERMOSTAT_CODES and has_no_probes(setup):
        return False
    return code in _EASY_CODES


def boost_uses_legacy_path(
    pid: str | None, setup: Mapping[str, object] | None = None
) -> bool:
    """Whether the controller's boost activation is the setup+status path.

    TevolveTriacHeaterController overrides activateBoost/deactivateBoost and
    always uses the legacy path, including when boost_config is FULL (3). The
    Haverland fixed-time families inherit ``setBasicBoost``: at boost_config
    0-2 their activation is the status flag with no temperature/duration. At
    FULL (3) the generic controller enters ``/boost_mode`` instead.
    """
    code = _product_code(pid)
    if code in _TEVOLVE_TRIAC_CODES and code != "27":
        return True
    if code in _HAVERLAND_FIXED_TIME_CODES:
        return boost_config(setup or {}) != BoostConfig.FULL
    return False


def boost_params_editable(  # noqa: PLR0911 - one return per family/config
    node_type: str, pid: str | None, setup: Mapping[str, object] | None
) -> bool:
    """Whether the app exposes editable boost temperature/duration.

    ``boost_config`` absent is not fixed-time: the base controller falls back
    to SET_TEMP_AND_TIME when its BOOST mode is available. Storage (Helki),
    LhzRelay 36 and the Tevolve triac/towel-rail family add BOOST
    unconditionally. The triac family and storage also treat explicit FULL (3)
    as temp-and-time editable. The Haverland fixed-time families (17/1D/47)
    never expose parameters: their app activation calls ``setBasicBoost``.
    The WiFi families expose set-temp-and-time only with config 2, and the
    Tevolve thermostat's NO_PROBES timer mode has no BOOST at all.
    """
    code = _product_code(pid)
    if node_type == SmartboxNodeType.ACM:
        # Storage BOOST is the Helki /boost endpoint, independent of
        # boost_config; the Elnur families have no BOOST surface.
        return code not in _ELNUR_STORAGE_CODES
    if code in _HAVERLAND_FIXED_TIME_CODES:
        return False
    if code in _TEVOLVE_THERMOSTAT_CODES and has_no_probes(setup):
        return False
    factory = (setup or {}).get("factory_options")
    explicit = isinstance(factory, Mapping) and "boost_config" in factory
    config = boost_config(setup or {})
    if explicit:
        if config == BoostConfig.SET_TEMP_AND_TIME:
            return True
        if config == BoostConfig.FULL:
            return node_type == SmartboxNodeType.ACM or (
                code in _TEVOLVE_TRIAC_CODES and code != "27"
            )
        return False
    if code == "36":
        return True
    return code in _TEVOLVE_TRIAC_CODES and code != "27"


def boost_is_fixed_time(pid: str | None) -> bool:
    """Whether the app activates boost in its fixed-time mode."""
    return _product_code(pid) in _HAVERLAND_FIXED_TIME_CODES


def boost_modes_available(
    status: Mapping[str, object] | None,
) -> tuple[str, ...]:
    """Return the FULL endpoint's advertised modes from a status snapshot."""
    value = (status or {}).get("boost_modes_available")
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(mode for mode in value if isinstance(mode, str))


def boost_activation_mode(
    pid: str | None,
    setup: Mapping[str, object] | None,
    status: Mapping[str, object] | None,
) -> str | None:
    """Return the ``/boost_mode`` style a FULL controller should use.

    ``None`` means the controller takes the legacy setup+status path, or that
    the endpoint advertises no basic boost style. Only WiFi/ATC read the
    status list; the Haverland fixed-time families always use the fixed style
    at FULL, and the triac family keeps its legacy override at every config.
    """
    if boost_config(setup or {}) != BoostConfig.FULL:
        return None
    code = _product_code(pid)
    if code in _HAVERLAND_FIXED_TIME_CODES:
        return "max_temp_fixed_time"
    if code in _TEVOLVE_WIFI_CODES or code in _TEVOLVE_ATC_CODES:
        modes = boost_modes_available(status)
        if "temp_and_time" in modes:
            return "temp_and_time"
        if "max_temp_fixed_time" in modes:
            return "max_temp_fixed_time"
    return None


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
    code = _product_code(pid)
    if code not in _TEVOLVE_WIFI_CODES and code not in _TEVOLVE_ATC_CODES:
        return False
    return "runback" in boost_modes_available(status)


def boost_activation_takes_parameters(
    pid: str | None, setup: Mapping[str, object] | None
) -> bool:
    """Whether activation should carry the stored boost temperature/duration."""
    code = _product_code(pid)
    if code in _HAVERLAND_FIXED_TIME_CODES:
        return False
    return boost_config(setup or {}) != BoostConfig.MAX_TEMP_FIXED_TIME


def supports_runback(
    pid: str | None, setup: Mapping[str, object] | None = None
) -> bool:
    """Return True for controllers that expose the RUNBACK flag.

    Tevolve triac/towel-rail keep it; PIR removes it. WiFi/ATC keep it when the
    boost config is not FULL. On FULL the app routes activation through
    ``/boost_mode`` with a temperature and duration the switch does not carry,
    so the switch is withheld rather than posting the wrong status-only write.
    The Tevolve thermostat hides EASY/RUNBACK when it reports NO_PROBES.
    """
    code = _product_code(pid)
    if code in _TEVOLVE_THERMOSTAT_CODES and has_no_probes(setup):
        return False
    if code in _RUNBACK_TRIAC_CODES:
        return True
    if code in _TEVOLVE_WIFI_CODES or code in _TEVOLVE_ATC_CODES:
        return boost_config(setup or {}) in {
            BoostConfig.SET_TEMP_AND_TIME,
            BoostConfig.MAX_TEMP_FIXED_TIME,
        }
    return False


# The factory dispatches these THM products to an htr-family controller.
# Code 28 is TevolveThermostatController, which extends the triac heater and
# uses the heater int-slot programme instead of the thermostat {m,t} shape.
_HEATER_SURFACE_THM_CODES = frozenset({"28"})


def is_heater_surface(node_type: str, pid: str | None) -> bool:
    """Whether the node gets heater entities, including the THM 28 floor unit."""
    if node_type in (
        SmartboxNodeType.HTR,
        SmartboxNodeType.HTR_MOD,
        SmartboxNodeType.ACM,
    ):
        return True
    return node_type == SmartboxNodeType.THM and _product_code(pid) in (
        _HEATER_SURFACE_THM_CODES
    )


def is_thermostat_surface(node_type: str, pid: str | None) -> bool:
    """Whether the node gets the thermostat climate/schedule surfaces."""
    return node_type == SmartboxNodeType.THM and not is_heater_surface(node_type, pid)


__all__ = [
    "PROFILES_BASE",
    "PROFILES_HTR_MOD",
    "PROFILES_WITH_OFF",
    "UI_MIN_TEMP_C",
    "ControllerLimits",
    "boost_activation_mode",
    "boost_activation_takes_parameters",
    "boost_is_fixed_time",
    "boost_modes_available",
    "boost_params_configurable",
    "boost_params_editable",
    "boost_temperature_bounds",
    "boost_uses_legacy_path",
    "climate_bounds",
    "controller_limits",
    "firmware_at_least",
    "has_no_probes",
    "is_heater_surface",
    "is_helki_heater_family",
    "is_htr_mod_controller",
    "is_lhz_family",
    "is_lhz_relay",
    "is_tevolve_atc_family",
    "is_tevolve_triac_family",
    "is_tevolve_wifi_family",
    "is_thermostat_surface",
    "lock_kind",
    "max_limit_bounds",
    "profile_bounds",
    "prog_bounds_c",
    "runback_params_configurable",
    "supports_easy",
    "supports_runback",
]
