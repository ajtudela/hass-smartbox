"""Per-controller temperature ranges and programme profile catalogues.

The vendor app resolves a node's controller from ``version.pid[2:4]`` and the
wire node type, then exposes that controller's own setpoint and programme
limits (``docs/api-contract.md`` §1.9). This module encodes the decoded table
once so the climate entity, the number entities and the write sanitizer agree.

The controller constants are client-side app values. The app can additionally
raise the minimum with a runtime ``controllerOpts.minTempCelsius``; the decoded
2.54.2 builds never populate that field, and the mobile UI stops at 1.0 °C on a
0-30 family (observed on HJM pid 0132). The climate floor therefore applies a
1.0 °C UI floor on top of the controller minimum. Programme bounds keep the
controller minimum, because the app derives them from it (comfort starts at
``min + 2 * profile_offset``, which is 1.0 on the base family).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from smartbox.core.merging import boost_config
from smartbox.protocol.values import BoostConfig

from .const import SmartboxNodeType

Profile = Literal["frost", "eco", "comfort", "off"]

PROFILES_BASE: tuple[Profile, ...] = ("frost", "eco", "comfort")
PROFILES_WITH_OFF: tuple[Profile, ...] = ("frost", "eco", "comfort", "off")
PROFILES_HTR_MOD: tuple[Profile, ...] = ("off", "frost", "eco", "comfort")

# The mobile UI's absolute floor. A controller with a higher minimum keeps it.
UI_MIN_TEMP_C = 1.0

# Family code sets from EProductCodeValue (docs/api-contract.md §3.1).
# HelkiHeater subclasses: 32 HelkiWifi, 35 Helki7S, 33 LhzFourButton,
# 38 LhzWifi, 36 LhzRelay. Codes 0D/18/43 fall to the factory default
# ElectricHeaterController.
_HELKI_HEATER_CODES = frozenset({"32", "35", "33", "38", "36"})
_LHZ_HEATER_CODES = frozenset({"35", "33", "38", "36"})
_HAVERLAND_CODES = frozenset({"17", "1D", "47"})
_HAVERLAND_HTR_MOD_CODES = frozenset({"0A", "FF"})
_TEVOLVE_TRIAC_CODES = frozenset({"1A", "21", "23", "27", "28"})
_TEVOLVE_WIFI_CODES = frozenset({"31", "34", "39"})
_TEVOLVE_ATC_CODES = frozenset({"4C", "4D"})
_TEVOLVE_THERMOSTAT_CODES = frozenset({"28"})
# Haverland's app-side boost is fixed max-temp, with no editable params.
_HAVERLAND_FIXED_TIME_CODES = frozenset({"17", "1D", "47"})
_TEVOLVE_RELAY_CODES = frozenset({"1E"})
_HELKI_STORAGE_CODES = frozenset({"1C", "2E"})
_ELNUR_STORAGE_CODES = frozenset({"14", "20", "25", "2A"})
_THERMOSTAT_CODES = frozenset({"04", "09", "0F"})


@dataclass(frozen=True)
class ControllerLimits:
    """Setpoint and programme limits for one controller family, in Celsius."""

    min_c: float
    max_c: float
    ice_min_c: float | None = None
    ice_max_c: float | None = None
    profile_offset_c: float = 0.5
    # TevolvePIRHeaterController raises the comfort/eco gap to 3.5 °C
    # (PROG_TEMP_COMFORT_ECO_OFFSET_IN_CELSIUS); the default keeps the app's
    # symmetric offset rule.
    comfort_eco_offset_c: float | None = None
    # PIR exposes the ICE slot non-editable; the wire still carries ice_temp.
    ice_editable: bool = True
    profiles: tuple[Profile, ...] = PROFILES_BASE
    htr_mod: bool = False
    max_limit_max_c: float | None = None
    boost_max_c: float | None = None
    boost_max_minutes: int = 1440
    prog_min_c: float | None = None
    prog_max_c: float | None = None


_BASE_LIMITS = ControllerLimits(min_c=0.0, max_c=30.0)
_HAVERLAND_LIMITS = ControllerLimits(
    min_c=5.0,
    max_c=30.0,
    ice_min_c=5.0,
    ice_max_c=7.0,
    profile_offset_c=2.0,
    profiles=PROFILES_WITH_OFF,
)
_HTR_MOD_LIMITS = ControllerLimits(
    min_c=5.0,
    max_c=28.0,
    profile_offset_c=1.0,
    profiles=PROFILES_HTR_MOD,
    htr_mod=True,
)
_TEVOLVE_TRIAC_LIMITS = ControllerLimits(min_c=7.0, max_c=35.0)
# TevolvePIRHeaterController raises the comfort/eco gap to 3.5 °C and makes
# the ICE slot non-editable.
_TEVOLVE_PIR_LIMITS = ControllerLimits(
    min_c=7.0,
    max_c=35.0,
    comfort_eco_offset_c=3.5,
    ice_editable=False,
)
# TevolveWifiHeaterController extends the triac controller without new bounds.
_TEVOLVE_WIFI_LIMITS = _TEVOLVE_TRIAC_LIMITS
_TEVOLVE_ATC_LIMITS = ControllerLimits(
    min_c=5.0, max_c=30.0, ice_min_c=5.0, ice_max_c=10.0, boost_max_minutes=120
)
_TEVOLVE_RELAY_LIMITS = ControllerLimits(min_c=0.0, max_c=35.0)
_ELNUR_STORAGE_LIMITS = ControllerLimits(min_c=3.0, max_c=30.0)
_THERMOSTAT_LIMITS = ControllerLimits(min_c=7.0, max_c=35.0)
_HELKI_LIMITS = ControllerLimits(min_c=0.0, max_c=30.0, profiles=PROFILES_WITH_OFF)
_CLIMASTAR_TRIAC_LIMITS = ControllerLimits(min_c=0.0, max_c=30.0, boost_max_minutes=300)

# Code -> fixed controller limits. Dynamic families (htr_mod, Helki storage)
# are resolved before this table.
_CODE_LIMITS: dict[str, ControllerLimits] = {
    **dict.fromkeys(_HELKI_HEATER_CODES, _HELKI_LIMITS),
    **dict.fromkeys(_HAVERLAND_CODES, _HAVERLAND_LIMITS),
    **dict.fromkeys(_TEVOLVE_TRIAC_CODES, _TEVOLVE_TRIAC_LIMITS),
    "27": _TEVOLVE_PIR_LIMITS,
    **dict.fromkeys(_TEVOLVE_WIFI_CODES, _TEVOLVE_WIFI_LIMITS),
    **dict.fromkeys(_TEVOLVE_ATC_CODES, _TEVOLVE_ATC_LIMITS),
    "3D": _CLIMASTAR_TRIAC_LIMITS,
    **dict.fromkeys(_TEVOLVE_RELAY_CODES, _TEVOLVE_RELAY_LIMITS),
    **dict.fromkeys(_ELNUR_STORAGE_CODES, _ELNUR_STORAGE_LIMITS),
    **dict.fromkeys(_THERMOSTAT_CODES, _THERMOSTAT_LIMITS),
    **dict.fromkeys(
        (
            "01",
            "02",
            "07",
            "08",
            "0B",
            "0C",
            "0D",
            "10",
            "11",
            "12",
            "13",
            "16",
            "18",
            "1B",
            "1F",
            "24",
            "43",
            "FF",
        ),
        _BASE_LIMITS,
    ),
}


_PID_CODE_START = 2
_PID_CODE_END = 4


def _product_code(pid: str | None) -> str | None:
    """Return ``pid[2:4]`` uppercased, matching the app's dispatch."""
    if not isinstance(pid, str):
        return None
    value = pid.strip().upper()
    return value[_PID_CODE_START:_PID_CODE_END] if len(value) >= _PID_CODE_END else None


def _setup_number(setup: Mapping[str, object], key: str) -> float | None:
    """Read one numeric setup field, tolerating wire strings."""
    value = setup.get(key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value))
    except TypeError, ValueError:
        return None


def firmware_at_least(version: str | None, target: str) -> bool:
    """Compare dotted firmware versions numerically (missing parts are 0)."""
    if not isinstance(version, str):
        return False

    def parts(value: str) -> tuple[int, ...]:
        try:
            return tuple(int(part) for part in value.split(".") if part != "")
        except ValueError:
            return ()

    left = parts(version)
    right = parts(target)
    if not left:
        return False
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) >= right + (0,) * (width - len(right))


def _helki_storage_limits(
    fw_version: str | None, setup: Mapping[str, object]
) -> ControllerLimits:
    """Return the Helki storage profile: setup bounds and fw-gated profiles."""
    min_c = _setup_number(setup, "min_stemp")
    max_c = _setup_number(setup, "max_stemp")
    profiles = (
        PROFILES_WITH_OFF if firmware_at_least(fw_version, "1.8") else PROFILES_BASE
    )
    return ControllerLimits(
        min_c=min_c if min_c is not None else 5.0,
        max_c=max_c if max_c is not None else 30.0,
        profiles=profiles,
    )


def controller_limits(
    node_type: str,
    pid: str | None,
    fw_version: str | None,
    setup: Mapping[str, object] | None = None,
) -> ControllerLimits:
    """Resolve the decoded controller limits for one node.

    ``node_type`` is the wire type. The app dispatches ``htr_mod`` nodes and
    the Haverland htr_mod product codes to the off-first htr_mod catalogue;
    a ``ff``-pid plain htr keeps the ElectricHeater catalogue.
    """
    setup = setup or {}
    code = _product_code(pid)
    if is_htr_mod_controller(pid, node_type):
        return _HTR_MOD_LIMITS

    if code in _HELKI_STORAGE_CODES:
        return _helki_storage_limits(fw_version, setup)

    limits = _CODE_LIMITS.get(code or "")
    if limits is not None:
        return limits
    if node_type == SmartboxNodeType.ACM:
        # The factory's default branch for an acm node is
        # HelkiStorageHeaterController, including an unknown/00 PID.
        return _helki_storage_limits(fw_version, setup)
    # Every unlisted htr PID falls to the factory's default branch,
    # ElectricHeaterController (0-30). The wire type decides only the acm case.
    return _BASE_LIMITS


def climate_bounds(
    limits: ControllerLimits, max_limit_c: float | None
) -> tuple[float, float]:
    """Return the climate setpoint ``(min, max)`` in Celsius.

    The maximum is clamped by the node's ``max_stemp_limit`` when it is
    non-zero; the minimum applies the mobile UI's 1.0 °C floor.
    """
    if limits.htr_mod:
        # htr_mod's per-mode bounds are handled by the climate entity; this is
        # only the global fallback.
        return (limits.min_c, limits.max_c)
    maximum = limits.max_c
    if max_limit_c is not None:
        maximum = min(maximum, max_limit_c)
    return (max(limits.min_c, UI_MIN_TEMP_C), maximum)


def max_limit_bounds(limits: ControllerLimits) -> tuple[float, float]:
    """Return the ``max_stemp_limit`` setting bounds in Celsius."""
    maximum = (
        limits.max_limit_max_c if limits.max_limit_max_c is not None else limits.max_c
    )
    return (max(limits.min_c + 1.0, 16.0), maximum)


def boost_temperature_bounds(limits: ControllerLimits) -> tuple[float, float]:
    """Return the boost-temperature setting bounds in Celsius."""
    maximum = limits.boost_max_c if limits.boost_max_c is not None else limits.max_c
    return (5.0, maximum)


def _profile_ranges(limits: ControllerLimits) -> dict[str, tuple[float, float]]:
    """Return the programme ranges in Celsius, keyed by profile.

    The PIR family replaces the symmetric ``offset`` gap between frost, eco
    and comfort with its wider ``comfort_eco_offset_c`` on the eco ceiling and
    the comfort floor.
    """
    offset = limits.profile_offset_c
    prog_min = limits.prog_min_c if limits.prog_min_c is not None else limits.min_c
    prog_max = limits.prog_max_c if limits.prog_max_c is not None else limits.max_c
    ice_min = limits.ice_min_c if limits.ice_min_c is not None else prog_min
    ice_max = (
        limits.ice_max_c if limits.ice_max_c is not None else prog_max - 2.0 * offset
    )
    gap = limits.comfort_eco_offset_c
    eco_max = prog_max - (gap if gap is not None else offset)
    comfort_min = prog_min + (gap + offset if gap is not None else 2.0 * offset)
    return {
        "frost": (ice_min, ice_max),
        "eco": (prog_min + offset, eco_max),
        "comfort": (comfort_min, prog_max),
    }


def profile_bounds(
    limits: ControllerLimits, profile: str
) -> tuple[float, float] | None:
    """Return ``(min, max)`` Celsius for one editable programme profile.

    ``None`` means the profile has no editable range (htr_mod's ICE slot, the
    PIR ICE slot and the OFF profile).
    """
    if profile == "off":
        return None
    if limits.htr_mod:
        return {"eco": (10.0, 18.0), "comfort": (18.0, 28.0)}.get(profile)
    if profile == "frost" and not limits.ice_editable:
        return None
    return _profile_ranges(limits).get(profile)


def prog_bounds_c(limits: ControllerLimits) -> dict[str, tuple[float, float]] | None:
    """Return the write-sanitizer bounds in Celsius, or None for htr_mod.

    The ICE clamp is included even when the slot is read-only: the wire body
    carries ``ice_temp`` back unchanged, and the engine needs all three keys
    when bounds are supplied.
    """
    if limits.htr_mod:
        return None
    names = {"ice_temp": "frost", "eco_temp": "eco", "comf_temp": "comfort"}
    ranges = _profile_ranges(limits)
    return {key: ranges[profile] for key, profile in names.items()}


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


def is_lhz_family(pid: str | None) -> bool:
    """Return True for the controllers that hard-code 0/80/90 duty presets."""
    return _product_code(pid) in _LHZ_HEATER_CODES


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
    """Whether the app activates boost in its fixed-time mode.

    Haverland 17/1D/47 override ``getSupportedBoostMode`` to
    MAX_TEMP_FIXED_TIME for every configuration.
    """
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
    """Whether HA should expose boost temperature/duration controls.

    Legacy controllers keep the values on the device. FULL WiFi/ATC expose
    them only when the endpoint advertises ``temp_and_time``; a fixed-only or
    unknown list has nothing to configure.
    """
    if boost_params_editable(node_type, pid, setup):
        return True
    return boost_activation_mode(pid, setup, status) == "temp_and_time"


def runback_params_configurable(
    pid: str | None,
    setup: Mapping[str, object] | None,
    status: Mapping[str, object] | None,
) -> bool:
    """Whether HA should expose runback temperature/duration controls.

    Only the FULL endpoint carries runback parameters. Legacy runback is the
    status flag, and its device defaults are not part of the activation.
    """
    if boost_config(setup or {}) != BoostConfig.FULL:
        return False
    code = _product_code(pid)
    if code not in _TEVOLVE_WIFI_CODES and code not in _TEVOLVE_ATC_CODES:
        return False
    return "runback" in boost_modes_available(status)


def boost_activation_takes_parameters(
    pid: str | None, setup: Mapping[str, object] | None
) -> bool:
    """Whether activation should carry the stored boost temperature/duration.

    Fixed-time controllers ignore the pair (Haverland 17/1D/47 and any
    controller whose ``boost_config`` is MAX_TEMP_FIXED_TIME). The other BOOST
    controllers accept it; the full WiFi/ATC path needs it even when the
    parameter numbers are withheld until its ``boost_modes_available``
    parameters are modelled.
    """
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


def is_htr_mod_controller(pid: str | None, node_type: str | None = None) -> bool:
    """Return True for the product codes that use htr_mod control.

    Code ``0A`` always dispatches to the htr_mod controller; code ``FF`` does
    so only for the ``htr_mod`` wire type (a plain ``htr`` FF stays the base
    ElectricHeater).
    """
    code = _product_code(pid)
    if code == "0A":
        return True
    return code == "FF" and node_type == SmartboxNodeType.HTR_MOD


# The factory dispatches these THM products to an htr-family controller.
# Code 28 is TevolveThermostatController, which extends the triac heater and
# uses the heater int-slot programme instead of the thermostat {m,t} shape.
_HEATER_SURFACE_THM_CODES = frozenset({"28"})
SENSOR_MODE_NO_PROBES = 3


def has_no_probes(setup: Mapping[str, object] | None) -> bool:
    """Return True when the node's extra options select NO_PROBES."""
    if not isinstance(setup, Mapping):
        return False
    extra = setup.get("extra_options")
    if not isinstance(extra, Mapping):
        return False
    value = extra.get("sensor_mode")
    return not isinstance(value, bool) and value == SENSOR_MODE_NO_PROBES


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


def is_helki_heater_family(pid: str | None) -> bool:
    """Return True for the HelkiHeater/Lhz controller chain."""
    return _product_code(pid) in _HELKI_HEATER_CODES


def is_lhz_relay(pid: str | None) -> bool:
    """Return True for the LhzRelayHeaterController (code 36)."""
    return _product_code(pid) == "36"


def is_tevolve_triac_family(pid: str | None) -> bool:
    """Return True for Tevolve triac/towel-rail/PIR controllers."""
    return _product_code(pid) in _TEVOLVE_TRIAC_CODES


def is_tevolve_wifi_family(pid: str | None) -> bool:
    """Return True for the Tevolve WiFi controller chain."""
    return _product_code(pid) in _TEVOLVE_WIFI_CODES


def is_tevolve_atc_family(pid: str | None) -> bool:
    """Return True for the Tevolve/Ferroli ATC controllers."""
    return _product_code(pid) in _TEVOLVE_ATC_CODES
