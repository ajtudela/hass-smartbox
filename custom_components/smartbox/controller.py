"""Compatibility re-exports for the neutral device profile.

Limits/profiles/surface predicates live in ``smartbox.device.profile``; lock
and boost policy in ``smartbox.device.modes``. Entity code keeps importing
from this module while the extraction lands.
"""

from __future__ import annotations

from smartbox.device.modes import (
    boost_activation_mode,
    boost_activation_takes_parameters,
    boost_is_fixed_time,
    boost_modes_available,
    boost_params_configurable,
    boost_params_editable,
    boost_uses_legacy_path,
    lock_kind,
    runback_params_configurable,
    supports_easy,
    supports_runback,
)
from smartbox.device.profile import (
    PROFILES_BASE,
    PROFILES_HTR_MOD,
    PROFILES_WITH_OFF,
    UI_MIN_TEMP_C,
    ControllerLimits,
    boost_temperature_bounds,
    climate_bounds,
    controller_limits,
    firmware_at_least,
    has_no_probes,
    is_heater_surface,
    is_helki_heater_family,
    is_htr_mod_controller,
    is_lhz_family,
    is_lhz_relay,
    is_tevolve_atc_family,
    is_tevolve_triac_family,
    is_tevolve_wifi_family,
    is_thermostat_surface,
    max_limit_bounds,
    profile_bounds,
    prog_bounds_c,
)

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
