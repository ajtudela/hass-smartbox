"""Wire mapping helpers: the status/setup-shaped rules the entities share.

Pure functions the platforms call at runtime: the setpoint/mode/preset decision,
schedule normalization and slot math, RTC and boost time decoding, unit
resolution. Single-source, so the same conversion is
not re-implemented per platform, with the wire rules cited from
``docs/api-contract.md``. The one side effect is
:func:`warn_unknown_once`, the bounded warning entity properties use for an
unmodelled wire value.

The write decisions return an INTENT (which named operation, with domain
values), not a wire body: the body belongs to
:mod:`smartbox.protocol.writes`, which owns every wire shape the API accepts.

Entity availability gates live in :mod:`custom_components.smartbox.identity`:
they run on the discovery identity objects, not on wire dicts.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
import logging
import math
from typing import TYPE_CHECKING, Any

# HA's component root re-exports these public names without __all__;
# mypy strict's no_implicit_reexport cannot see that re-export.
from homeassistant.components.climate import (  # type: ignore[attr-defined]
    PRESET_ACTIVITY,
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_HOME,
    PRESET_NONE,
    HVACMode,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter
from smartbox.device.profile import HtrModPolicy, htr_mod_policy, is_htr_mod_node
from smartbox.device.storage import is_water_storage
from smartbox.protocol.values import bounded_repr, parse_number_or_none

from .const import (
    GITHUB_ISSUES_URL,
    PRESET_FROST,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
    WATER_READY_LEVEL,
    SmartboxNodeType,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)

# A programme day always covers 24 h: slot length = 1440 / len(day array).
_PROG_DAY_MINUTES = 1440


ProgDict = dict[str, list[int]]


@lru_cache(maxsize=32)
def _warn_unknown(kind: str, value: str) -> None:
    """Log one unknown wire value, bounded so a firmware loop cannot flood."""
    _LOGGER.warning(
        "Unknown smartbox %s %s - please report to %s",
        kind,
        value,
        GITHUB_ISSUES_URL,
    )


def warn_unknown_once(kind: str, value: object) -> None:
    """Log an unmodelled wire value once per distinct value.

    Modes and ``selected_temp`` are evaluated on every state write, so logging
    each time would flood the log while the firmware keeps reporting the same
    unmodelled value. The cache is bounded: a new value still warns, and an old
    one warns again after eviction.
    """
    _warn_unknown(kind, bounded_repr(value))


@dataclass(frozen=True)
class SetpointWrite:
    """A plain setpoint: the ``set_setpoint`` operation, in this scale.

    Applies immediately; inside ``auto`` it does not persist the override
    across programme transitions (see :class:`AutoOverrideWrite`).
    """

    units: str


@dataclass(frozen=True)
class AutoOverrideWrite:
    """The app-shaped override: ``engage_modified_auto``, in this scale."""

    units: str


@dataclass(frozen=True)
class HtrModWrite:
    """An htr_mod status change: the keys it sets (``None`` = not set).

    The key set is the app's htr_mod one (docs/api-contract.md §7 item 7), and
    the operation sends only the keys that are not ``None``. ``comfort_temp`` is
    a temperature in the given ``units``: the protocol layer formats it onto the
    setpoint grid.
    """

    on: bool | None = None
    mode: str | None = None
    selected_temp: str | None = None
    comfort_temp: float | None = None
    eco_offset: str | None = None
    units: str | None = None


@dataclass(frozen=True)
class ModeWrite:
    """A plain mode change: the ``set_mode`` operation and its mode."""

    mode: str


TemperatureWrite = SetpointWrite | AutoOverrideWrite | HtrModWrite
StatusModeWrite = ModeWrite | HtrModWrite


def get_temperature_unit(status: dict[str, Any]) -> UnitOfTemperature | None:
    """Get the unit of temperature; an unknown/ill-typed value reads as None.

    Entity properties call this while rendering state, so malformed
    server data must not raise there; the caller falls back to Celsius.
    """
    if "units" not in status:
        return None
    unit = status["units"]
    if unit == "C":
        return UnitOfTemperature.CELSIUS
    if unit == "F":
        return UnitOfTemperature.FAHRENHEIT
    return None


def rtc_time_to_datetime(rtc: dict[str, Any] | None) -> datetime | None:
    """Convert a device RTC payload into a local datetime, or None.

    Wire shape: ``{"d": 27, "h": 9, "m": 55, "n": 8, "s": 24, "w": 0, "y":
    2026}``. September arrives as ``n=8``, so ``n`` is a 0-indexed month
    (unconfirmed for December, where a 1-indexed 12 fails to parse and yields
    None). The RTC is read in the Home Assistant timezone; the box timezones
    the cloud reports are not captured.
    """
    if rtc is None:
        return None
    try:
        return datetime(
            year=int(rtc["y"]),
            month=int(rtc["n"]) + 1,
            day=int(rtc["d"]),
            hour=int(rtc["h"]),
            minute=int(rtc["m"]),
            second=int(rtc["s"]),
            tzinfo=dt_util.DEFAULT_TIME_ZONE,
        )
    except KeyError, TypeError, ValueError:
        return None


def get_boost_end_datetime(
    boost_end_min: int,
    boost_end_day: int | None = None,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Return the local datetime at which the node boost ends.

    ``boost_end_min`` is the HOME-LOCAL minute of day, not UTC: a 60-minute
    boost started at 13:49 CET reported ``889`` (14:49) `[live 2026-11-22]`.
    ``boost_end_day`` is the weekday of the end (0 = Sunday), not a day offset;
    the vendor apps ignore it. It stays in the signature for compatibility.
    Returns a future datetime, or None for a malformed/out-of-range minute
    value (entity properties must not raise).
    """
    del boost_end_day
    try:
        minute_of_day = int(boost_end_min)
    except TypeError, ValueError:
        return None
    if not 0 <= minute_of_day < _PROG_DAY_MINUTES:
        return None
    current = now if now is not None else dt_util.now()
    hour, minute = divmod(minute_of_day, 60)
    boost_end = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if boost_end < current:
        # The end minute has passed: the next occurrence is tomorrow.
        boost_end += timedelta(days=1)
    return boost_end


def check_status_key(key: str, node_type: str, status: dict[str, Any]) -> None:
    """Raise KeyError for a required status key (report-link message).

    Cross-module contract: climate.py imports this for its preset gates.
    """
    if key not in status:
        # keep the key + node identity; never embed the status dict
        # (server-controlled, can be huge)
        msg = (
            f"'{key}' not found in {node_type} - please report to {GITHUB_ISSUES_URL}."
        )
        raise KeyError(msg)


def _htr_mod_profile_temperature(status: dict[str, Any], profile: str) -> float | None:
    """Return the htr_mod target for one programme profile."""
    if profile == "off":
        return None
    if profile == "ice":
        return parse_number_or_none(status.get("ice_temp"))
    comfort = parse_number_or_none(status.get("comfort_temp"))
    if profile == "eco":
        offset = parse_number_or_none(status.get("eco_offset"))
        if comfort is None or offset is None:
            return None
        return comfort - offset
    return comfort


def _get_htr_mod_target(status: dict[str, Any], prog: ProgDict | None) -> float | None:
    """Return the htr_mod target without the generic off check."""
    if status.get("mode") == "off" or status.get("on", True) is False:
        return None
    if status.get("mode") == "auto" and prog is not None:
        index = get_current_prog_profile(prog, dt_util.now())
        if index is not None:
            names = ("off", "ice", "eco", "comfort")
            if 0 <= index < len(names):
                return _htr_mod_profile_temperature(status, names[index])
    check_status_key("selected_temp", SmartboxNodeType.HTR_MOD, status)
    selected = status["selected_temp"]
    if selected == "off":
        return None
    if selected in ("comfort", "eco", "ice"):
        return _htr_mod_profile_temperature(status, selected)
    msg = (
        f"Unexpected 'selected_temp' value {bounded_repr(selected)}"
        f" found for {SmartboxNodeType.HTR_MOD} - please report to"
        f" {GITHUB_ISSUES_URL}."
    )
    raise KeyError(msg)


def _is_htr_mod(node_type: str, model_code: str | None) -> bool:
    """Whether the node uses htr_mod control.

    The htr_mod wire keeps its historical surface before the PID arrives;
    ``0A`` is htr_mod on any wire, while ``FF`` is htr_mod only on that wire.
    """
    return is_htr_mod_node(model_code, node_type)


def get_target_temperature(
    node_type: str,
    status: dict[str, Any],
    prog: ProgDict | None = None,
    model_code: str | None = None,
) -> float | None:
    """Get the target temperature.

    None when the node is off, matching the official app. The wire keeps the
    stale previous-mode value, so the off check mirrors ``get_hvac_mode``
    instead of trusting ``stemp`` / ``selected_temp``. htr_mod in AUTO derives
    the target from the current programme slot, as the app's
    ``getAutoModeTemp`` does.
    """
    if _is_htr_mod(node_type, model_code):
        return _get_htr_mod_target(status, prog)
    if status.get("boost", False):
        # The app's boost mode is a UI heating mode of its own; the wire keeps
        # the pre-boost ``mode`` (often "off") while ``stemp`` carries the
        # boost target. Read the target before the mode gate.
        return parse_number_or_none(status.get("stemp"))
    if node_type == SmartboxNodeType.ACM:
        # Storage telemetry has no stemp on the classic/accumulator families;
        # a storage target is the charge level, exposed separately.
        if status.get("mode") == "off":
            return None
        return parse_number_or_none(status.get("stemp"))
    if status.get("mode") == "off":
        return None
    check_status_key("stemp", node_type, status)
    return parse_number_or_none(status["stemp"])


# --- htr_mod setpoint policy (vendor-app parity) ---
# The app's factory dispatches on the product code (pid[2:4]) and each
# controller carries its own constants; this table is that dispatch in data
# form (docs/api-contract.md §1.9, §7 item 7).


def htr_mod_setpoint_limits(
    status: dict[str, Any], policy: HtrModPolicy
) -> tuple[float, float] | None:
    """Return the editable setpoint range for the selected profile, in the node's unit.

    Comfort and eco share the union of both ranges: the setpoint builder picks
    the profile from the target (above 18 C comfort, at or below 18 C eco), so
    the 18 C boundary has to be crossable from either one. Ice is the slot's
    own fixed temperature.
    """
    selected = status.get("selected_temp")
    unit = get_temperature_unit(status) or UnitOfTemperature.CELSIUS

    def as_node(celsius: float) -> float:
        value = TemperatureConverter.convert(celsius, UnitOfTemperature.CELSIUS, unit)
        if unit == UnitOfTemperature.FAHRENHEIT:
            # The wire stores Fahrenheit setpoints on whole degrees
            # (values.format_setpoint), so the advertised bounds sit on that
            # grid rather than at 64.4.
            return float(math.floor(value + 0.5))
        return value

    if status.get("mode") == "auto":
        return as_node(policy.global_min_c), as_node(policy.comfort_c[1])
    if selected in ("comfort", "eco"):
        return as_node(policy.eco_min_c), as_node(policy.comfort_c[1])
    if selected == "ice":
        try:
            ice = float(status["ice_temp"])
        except KeyError, TypeError, ValueError:
            return None
        return ice, ice
    if selected == "off":
        # The app enters eco/comfort on a setpoint from OFF; advertise that
        # write range rather than the global 5 C floor it cannot accept.
        return as_node(policy.eco_min_c), as_node(policy.comfort_c[1])
    return None


def _htr_mod_snap(temp: float, step: float) -> float:
    """Snap a setpoint to the policy's grid (half up)."""
    return math.floor(temp / step + 0.5) * step


def _htr_mod_eco_split(
    target: int, comfort: float, policy: HtrModPolicy
) -> tuple[int, int]:
    """Return the comfort temperature and eco offset that place eco at ``target``.

    The app walks ``comfort_temp`` in whole-degree steps until the gap fits;
    for a target inside the eco range the first fit is the stored comfort
    itself, or the offset range's nearest bound.
    """
    low, high = policy.offset_c
    # The offset ladder is integral, so the stored comfort snaps to a whole
    # degree (half up) before the walk; the app compares the raw value and
    # fails to fit when it is off-grid.
    start = min(
        max(math.floor(comfort + 0.5), int(policy.comfort_c[0])),
        int(policy.comfort_c[1]),
    )
    offset = start - target
    if offset < low:
        return target + low, low
    if offset > high:
        return target + high, high
    return start, offset


def _htr_mod_temperature_write(
    status: dict[str, Any], temp: float, policy: HtrModPolicy
) -> HtrModWrite:
    """Compose an htr_mod setpoint the way the app's builder does.

    The builder always sends ``mode: "manual"``: a setpoint is a manual
    request, and the app leaves the programme to get one.
    """
    check_status_key("comfort_temp", SmartboxNodeType.HTR_MOD, status)
    target = _htr_mod_snap(temp, policy.step_c)
    low, high = policy.eco_min_c, policy.comfort_c[1]
    if not low <= target <= high:
        msg = (
            f"Can't place an htr_mod setpoint of {target:g} C: the hardware"
            f" policy accepts {low:g}-{high:g} C"
        )
        raise ValueError(msg)
    if target > policy.eco_max_c:
        check_status_key("eco_offset", SmartboxNodeType.HTR_MOD, status)
        return HtrModWrite(
            on=True,
            mode="manual",
            selected_temp="comfort",
            comfort_temp=target,
            eco_offset=status["eco_offset"],
            units=status["units"],
        )
    # The app's offset search walks whole degrees, on the policy's grid.
    comfort, offset = _htr_mod_eco_split(
        int(target), float(status["comfort_temp"]), policy
    )
    return HtrModWrite(
        on=True,
        mode="manual",
        selected_temp="eco",
        comfort_temp=float(comfort),
        eco_offset=f"{offset:.1f}",
        units=status["units"],
    )


def temperature_write(
    node_type: str,
    status: dict[str, Any],
    temp: float,
    setup: dict[str, Any] | None = None,
    model_code: str | None = None,
) -> TemperatureWrite:
    """Map a target-temperature change onto the operation that performs it."""
    check_status_key("units", node_type, status)
    if _is_htr_mod(node_type, model_code):
        policy = htr_mod_policy(model_code)
        if (
            policy is not None
            and status.get("units") == "C"
            and status.get("selected_temp") in ("comfort", "eco", "off")
        ):
            return _htr_mod_temperature_write(status, temp, policy)
        if status["selected_temp"] in ("comfort", "off"):
            target_temp = temp
        elif status["selected_temp"] == "eco":
            check_status_key("eco_offset", node_type, status)
            target_temp = temp + float(status["eco_offset"])
        elif status["selected_temp"] == "ice":
            msg = "Can't set temperature for htr_mod devices when ice mode is selected"
            raise ValueError(msg)
        else:
            msg = (
                f"Unexpected 'selected_temp' value {bounded_repr(status['selected_temp'])}"
                f" found for {node_type} - please report to "
                f"{GITHUB_ISSUES_URL}."
            )
            raise KeyError(msg)
        return HtrModWrite(
            on=True,
            # The app's setpoint builder sends manual on every setpoint, on any
            # node scale; this pass-through keeps that part of the body.
            mode="manual",
            selected_temp=(
                "comfort"
                if status["selected_temp"] == "off"
                else status["selected_temp"]
            ),
            comfort_temp=target_temp,
            eco_offset=status["eco_offset"],
            units=status["units"],
        )
    # Setpoint inside the running programme: the official app engages
    # "modified_auto" with the same body so the override survives programme
    # transitions (docs/api-contract.md §5.1; live-probed 2026-09-28: the body
    # flips the mode on the wire, the heater follows, and it reverts with
    # {"mode": "auto"}). The app's INDEPENDENT_TEMP_AND_MODE_ON_UPDATE
    # capability is not wire-visible; modified_auto_span in setup is the
    # available proxy for "this unit supports the feature".
    if (
        setup is not None
        and "modified_auto_span" in setup
        and status.get("mode") == "auto"
    ):
        return AutoOverrideWrite(units=status["units"])
    return SetpointWrite(units=status["units"])


def acm_active(status: Mapping[str, Any], pid: str | None) -> bool:
    """Whether a storage heater is actively heating.

    Water storage (product code ``2A``) replaces the CHARGING indicator with
    ACTIVE = ``charging && charge_level != 50``; 50 is the water-ready level
    and 100 is fully ready (docs/api-contract.md §3.4).
    """
    if status.get("charging") is not True:
        return False
    if is_water_storage(pid):
        return parse_number_or_none(status.get("charge_level")) != WATER_READY_LEVEL
    return True


def get_hvac_mode(node_type: str, status: dict[str, Any]) -> HVACMode | None:
    """Get the mode of HVAC."""
    if status.get("boost", False):
        return HVACMode.HEAT
    check_status_key("mode", node_type, status)
    if status["mode"] == "off" or (
        node_type == SmartboxNodeType.HTR_MOD and not status.get("on", True)
    ):
        return HVACMode.OFF
    if (
        node_type == SmartboxNodeType.HTR_MOD
        and status["mode"] == "manual"
        and status.get("selected_temp") == "off"
    ):
        return HVACMode.OFF
    mode = status["mode"]
    mapped = (
        {
            "manual": HVACMode.HEAT,
            "auto": HVACMode.AUTO,
            "modified_auto": HVACMode.AUTO,
            "self_learn": HVACMode.AUTO,
            "presence": HVACMode.AUTO,
            # Vendor-app modes this integration does not model: keep the
            # entity usable rather than raising while a vendor-app change
            # lands.
            "easy": HVACMode.HEAT,
            "setback": HVACMode.HEAT,
        }.get(mode)
        if isinstance(mode, str)
        else None
    )
    if mapped is not None:
        return mapped
    # A firmware mode this integration does not model: keep the entity usable
    # and report it once, instead of raising from every state write. The
    # isinstance guard above also keeps an ill-typed mode from raising out of
    # the property.
    warn_unknown_once("node mode", mode)
    return None


def hvac_mode_write(
    node_type: str,
    status: dict[str, Any],
    hvac_mode: str,
    model_code: str | None = None,
) -> StatusModeWrite:
    """Map an HVAC-mode change onto the operation that performs it."""
    error_msg = f"Unsupported hvac mode {hvac_mode}"
    if _is_htr_mod(node_type, model_code):
        if hvac_mode == HVACMode.OFF:
            return HtrModWrite(on=False)
        if hvac_mode == HVACMode.HEAT:
            # The status key has to travel with the mode on these nodes
            check_status_key("selected_temp", node_type, status)
            selected = status["selected_temp"]
            if selected == "off":
                selected = "comfort"
            return HtrModWrite(on=True, mode="manual", selected_temp=selected)
        if hvac_mode == HVACMode.AUTO:
            return HtrModWrite(on=True, mode="auto")
        raise ValueError(error_msg)
    if hvac_mode == HVACMode.OFF:
        return ModeWrite(mode="off")
    if hvac_mode == HVACMode.HEAT:
        return ModeWrite(mode="manual")
    if hvac_mode == HVACMode.AUTO:
        return ModeWrite(mode="auto")
    raise ValueError(error_msg)


def preset_mode_write(
    node_type: str,
    status: dict[str, Any],
    preset_mode: str,
    model_code: str | None = None,
) -> StatusModeWrite:
    """Map a preset change onto the operation that performs it."""
    if not _is_htr_mod(node_type, model_code):
        msg = f"{node_type} nodes do not support preset {preset_mode}"
        raise ValueError(msg)
    # PRESET_HOME and PRESET_AWAY are not handled via status updates
    if preset_mode in (PRESET_HOME, PRESET_AWAY, PRESET_NONE):
        msg = f"Preset {preset_mode} is registry-only, not a status update"
        raise ValueError(msg)

    if preset_mode == PRESET_SCHEDULE:
        return hvac_mode_write(node_type, status, HVACMode.AUTO, model_code=model_code)
    if preset_mode == PRESET_SELF_LEARN:
        return HtrModWrite(on=True, mode="self_learn")
    if preset_mode == PRESET_ACTIVITY:
        return HtrModWrite(on=True, mode="presence")
    if preset_mode == PRESET_COMFORT:
        return HtrModWrite(on=True, mode="manual", selected_temp="comfort")
    if preset_mode == PRESET_ECO:
        return HtrModWrite(on=True, mode="manual", selected_temp="eco")
    if preset_mode == PRESET_FROST:
        return HtrModWrite(on=True, mode="manual", selected_temp="ice")
    msg = f"Unsupported preset {preset_mode} for node type {node_type}"
    raise ValueError(msg)


def get_current_prog_profile(prog: ProgDict, now: datetime) -> int | None:
    """Return the profile index the schedule asks for at local time ``now``.

    Day keys "0".."6" run Sunday..Saturday (HA's weekday index). Slot length
    is 24 h divided by the day-array length (24 hourly slots, or 48
    half-hourly where supported).
    """
    day = prog.get(str((now.weekday() + 1) % 7))
    if not day:
        return None
    slot_minutes = _PROG_DAY_MINUTES // len(day)
    if slot_minutes < 1:
        return None
    slot = (now.hour * 60 + now.minute) // slot_minutes
    return day[min(slot, len(day) - 1)]


def get_next_prog_change(prog: ProgDict, now: datetime) -> datetime | None:
    """Return the local datetime at which the schedule next changes profile.

    Walks slot boundaries forward from ``now``, wrapping after Saturday; None
    when the programme never changes. ``now`` must be an aware datetime (its
    timezone defines "local"); DST shifts are handled best-effort.
    """
    current = get_current_prog_profile(prog, now)
    local_now = dt_util.as_local(now)
    for day_offset in range(8):
        day = prog.get(str((local_now.weekday() + 1 + day_offset) % 7))
        if not day:
            continue
        slot_minutes = _PROG_DAY_MINUTES // len(day)
        if slot_minutes < 1:
            continue
        day_start = (local_now + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        for slot, profile in enumerate(day):
            start = dt_util.as_local(day_start + timedelta(minutes=slot * slot_minutes))
            if start <= local_now:
                continue
            if profile != current:
                return start
    return None
