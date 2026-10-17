"""Pure Smartbox protocol helpers: types, status and programme translation."""

from datetime import datetime, timedelta
import logging
from typing import Any, Final

from homeassistant.components.climate import (
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
from smartbox import SmartboxNodeType

from custom_components.smartbox.const import (
    GITHUB_ISSUES_URL,
    PRESET_FROST,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
)

_LOGGER = logging.getLogger(__name__)

# A programme day always covers 24 h: slot length = 1440 / len(day array).
_PROG_DAY_MINUTES: Final = 1440

FactoryOptionsDict = dict[str, bool]
SetupDict = dict[str, Any]
StatusDict = dict[str, Any]
SamplesDict = list[dict[str, Any]]
ProgDict = dict[str, list[int]]
Node = dict[str, Any]
Device = dict[str, Any]


def get_temperature_unit(status: StatusDict) -> UnitOfTemperature | None:
    """Get the unit of temperature."""
    if "units" not in status:
        return None
    unit = status["units"]
    if unit == "C":
        return UnitOfTemperature.CELSIUS
    if unit == "F":
        return UnitOfTemperature.FAHRENHEIT
    msg = f"Unknown temp unit {unit}"
    raise ValueError(msg)


def rtc_time_to_datetime(rtc: dict[str, Any] | None) -> datetime | None:
    """Convert a device RTC payload into a local datetime, or None.

    Wire shape (live-verified 2026-09-27): ``{"d": 27, "h": 9, "m": 55,
    "n": 8, "s": 24, "w": 0, "y": 2026}`` — September reported as ``n=8``,
    so ``n`` is treated as a 0-indexed month (unconfirmed for December,
    where a 0-indexed value would be 11; a 1-indexed 12 fails to parse
    and yields None). The RTC is interpreted in the Home Assistant
    timezone: the box timezones the cloud reports have not been captured
    live yet.
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


def get_boost_end_datetime(boost_end_min: int) -> datetime:
    """Return the local datetime at which the node boost ends.

    boost_end_min is the minute of the day (UTC) at which the boost ends, as
    reported by the API status. Returns a future datetime (the next day if the
    boost already ended earlier in the current UTC day).
    """
    now = dt_util.utcnow()
    hour, minute = divmod(int(boost_end_min), 60)
    boost_end = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if boost_end <= now:
        # Boost already ended for the current UTC day: it ends tomorrow.
        boost_end += timedelta(days=1)
    return dt_util.as_local(boost_end)


def check_status_key(key: str, node_type: str, status: dict[str, Any]) -> None:
    """Raise a KeyError asking for a bug report when a status key is missing."""
    if key not in status:
        msg = (
            f"'{key}' not found in {node_type} - please report to {GITHUB_ISSUES_URL}. "
            f"status: {status}"
        )
        raise KeyError(msg)


def get_target_temperature(node_type: str, status: dict[str, Any]) -> float | None:
    """Get the target temperature.

    None when the node is off: there is no set point then (matching the
    official app). The wire still carries the stale previous-mode value
    (stemp / selected_temp "off"), so the off check mirrors get_hvac_mode
    instead of trusting those keys.
    """
    if node_type == SmartboxNodeType.HTR_MOD:
        if (
            status.get("mode") == "off"
            or status.get("on", True) is False
            or status.get("selected_temp") == "off"
        ):
            return None
        check_status_key("selected_temp", node_type, status)
        if status["selected_temp"] == "comfort":
            check_status_key("comfort_temp", node_type, status)
            return float(status["comfort_temp"])
        if status["selected_temp"] == "eco":
            check_status_key("comfort_temp", node_type, status)
            check_status_key("eco_offset", node_type, status)
            return float(status["comfort_temp"]) - float(status["eco_offset"])
        if status["selected_temp"] == "ice":
            check_status_key("ice_temp", node_type, status)
            return float(status["ice_temp"])
        msg = (
            f"Unexpected 'selected_temp' value {status['selected_temp']}"
            f" found for {node_type} - please report to"
            f" {GITHUB_ISSUES_URL}. status: {status}"
        )
        raise KeyError(msg)
    if status.get("mode") == "off":
        return None
    check_status_key("stemp", node_type, status)
    return float(status["stemp"])


def set_temperature_args(
    node_type: str,
    status: dict[str, Any],
    temp: float,
    setup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Set targeted temperature."""
    check_status_key("units", node_type, status)
    if node_type == SmartboxNodeType.HTR_MOD:
        if status["selected_temp"] == "comfort":
            target_temp = temp
        elif status["selected_temp"] == "eco":
            check_status_key("eco_offset", node_type, status)
            target_temp = temp + float(status["eco_offset"])
        elif status["selected_temp"] == "ice":
            msg = "Can't set temperature for htr_mod devices when ice mode is selected"
            raise ValueError(msg)
        else:
            msg = (
                f"Unexpected 'selected_temp' value {status['selected_temp']}"
                f" found for {node_type} - please report to "
                f"{GITHUB_ISSUES_URL}. status: {status}"
            )
            raise KeyError(msg)
        return {
            "on": True,
            "mode": status["mode"],
            "selected_temp": status["selected_temp"],
            "comfort_temp": str(target_temp),
            "eco_offset": status["eco_offset"],
            "units": status["units"],
        }
    status_args = {
        "stemp": round_setpoint(temp, status["units"]),
        "units": status["units"],
    }
    # Setpoint inside the running programme: the official app engages
    # "modified_auto" with the same body so the override survives programme
    # transitions (webapi-spec.md §4.2; live-probed 2026-09-28: the body
    # flips the mode on the wire, the heater follows, and it reverts with
    # {"mode": "auto"}). The app's INDEPENDENT_TEMP_AND_MODE_ON_UPDATE
    # capability is not wire-visible; modified_auto_span in setup is the
    # available proxy for "this unit supports the feature".
    if (
        setup is not None
        and "modified_auto_span" in setup
        and status.get("mode") == "auto"
    ):
        status_args["mode"] = "modified_auto"
    return status_args


def round_setpoint(temp: float, units: str) -> str:
    """Round a setpoint onto the device grid and format it for the wire.

    The device silently quantizes off-grid values to the 0.5 °C grid
    (live-probed 2026-09-28, ../smartbox api-notes.md); the app only ever
    sends on-grid values. Rounding here keeps the optimistic UI value equal
    to what the device actually stores. Fahrenheit setpoints are passed
    through: their wire grid is unverified.
    """
    if units == "C":
        return f"{round(temp * 2) / 2:.1f}"
    return str(temp)


_WARNED_UNKNOWN: set[str] = set()


def warn_unknown_once(kind: str, value: object) -> None:
    """Log a single warning per unknown protocol value.

    These values are evaluated on every state write, so logging each time
    would flood the log for as long as the firmware reports them.
    """
    key = f"{kind}:{value}"
    if key in _WARNED_UNKNOWN:
        return
    _WARNED_UNKNOWN.add(key)
    _LOGGER.warning(
        "Unknown smartbox %s %r - please report to %s", kind, value, GITHUB_ISSUES_URL
    )


def get_hvac_mode(  # noqa: PLR0911
    node_type: str, status: dict[str, Any]
) -> HVACMode | None:
    """Get the mode of HVAC."""
    if status.get("boost", False):
        return HVACMode.HEAT
    check_status_key("mode", node_type, status)
    if status["mode"] == "off" or (
        node_type == SmartboxNodeType.HTR_MOD and not status["on"]
    ):
        return HVACMode.OFF
    if status["mode"] == "manual":
        return HVACMode.HEAT
    if status["mode"] == "auto":
        return HVACMode.AUTO
    if status["mode"] == "modified_auto":
        # This occurs when the temperature is modified while in auto mode.
        # Mapping it to auto seems to make this most sense
        return HVACMode.AUTO
    if status["mode"] == "self_learn" or status["mode"] == "presence":
        return HVACMode.AUTO
    warn_unknown_once("node mode", status["mode"])
    return None


def set_hvac_mode_args(
    node_type: str, status: dict[str, Any], hvac_mode: str
) -> dict[str, Any]:
    """Set the mode of HVAC."""
    error_msg = f"Unsupported hvac mode {hvac_mode}"
    if node_type == SmartboxNodeType.HTR_MOD:
        if hvac_mode == HVACMode.OFF:
            return {"on": False}
        if hvac_mode == HVACMode.HEAT:
            # We need to pass these status keys on when setting the mode
            required_status_keys = ["selected_temp"]
            for key in required_status_keys:
                check_status_key(key, node_type, status)
            hvac_mode_args = {k: status[k] for k in required_status_keys}
            hvac_mode_args["on"] = True
            hvac_mode_args["mode"] = "manual"
            return hvac_mode_args
        if hvac_mode == HVACMode.AUTO:
            return {"on": True, "mode": "auto"}
        raise ValueError(error_msg)
    if hvac_mode == HVACMode.OFF:
        return {"mode": "off"}
    if hvac_mode == HVACMode.HEAT:
        return {"mode": "manual"}
    if hvac_mode == HVACMode.AUTO:
        return {"mode": "auto"}
    raise ValueError(error_msg)


def set_preset_mode_status_update(
    node_type: str, status: dict[str, Any], preset_mode: str
) -> dict[str, Any]:
    """Set preset mode status update."""
    if node_type != SmartboxNodeType.HTR_MOD:
        msg = f"{node_type} nodes do not support preset {preset_mode}"
        raise ValueError(msg)
    # PRESET_HOME and PRESET_AWAY are not handled via status updates
    if preset_mode in (PRESET_HOME, PRESET_AWAY, PRESET_NONE):
        msg = f"Preset {preset_mode} is not applied through a status update"
        raise ValueError(msg)

    if preset_mode == PRESET_SCHEDULE:
        return set_hvac_mode_args(node_type, status, HVACMode.AUTO)
    if preset_mode == PRESET_SELF_LEARN:
        return {"on": True, "mode": "self_learn"}
    if preset_mode == PRESET_ACTIVITY:
        return {"on": True, "mode": "presence"}
    if preset_mode == PRESET_COMFORT:
        return {"on": True, "mode": "manual", "selected_temp": "comfort"}
    if preset_mode == PRESET_ECO:
        return {"on": True, "mode": "manual", "selected_temp": "eco"}
    if preset_mode == PRESET_FROST:
        return {"on": True, "mode": "manual", "selected_temp": "ice"}
    msg = f"Unsupported preset {preset_mode} for node type {node_type}"
    raise ValueError(msg)


def normalize_prog(payload: Any) -> ProgDict | None:  # noqa: ANN401
    """Validate and normalize a schedule payload into day-keyed slot lists.

    Accepts both the REST shape ``{"prog": {...}, "sync_status": ...}`` and
    a bare day-keyed object; returns None when the shape is unexpected. Day
    keys must be strings and each value a list of ints.
    """
    if isinstance(payload, dict) and isinstance(payload.get("prog"), dict):
        payload = payload["prog"]
    if not isinstance(payload, dict):
        return None
    prog: ProgDict = {}
    for day, slots in payload.items():
        if not (
            isinstance(day, str)
            and isinstance(slots, list)
            and all(isinstance(slot, int) for slot in slots)
        ):
            return None
        prog[day] = list(slots)
    return prog


def get_current_prog_profile(prog: ProgDict, now: datetime) -> int | None:
    """Return the profile index the schedule asks for at local time ``now``.

    Day keys "0".."6" run Monday..Sunday. Slot length is 24 h divided by the
    day-array length (24 hourly slots, or 48 half-hourly where supported).
    """
    day = prog.get(str(now.weekday()))
    if not day:
        return None
    slot_minutes = _PROG_DAY_MINUTES // len(day)
    if slot_minutes < 1:
        return None
    slot = (now.hour * 60 + now.minute) // slot_minutes
    return day[min(slot, len(day) - 1)]


def get_next_prog_change(prog: ProgDict, now: datetime) -> datetime | None:
    """Return the local datetime at which the schedule next changes profile.

    Walks slot boundaries forward from ``now``, wrapping after Sunday; None
    when the programme never changes. ``now`` must be an aware datetime (its
    timezone defines "local"); DST shifts are handled best-effort.
    """
    current = get_current_prog_profile(prog, now)
    local_now = dt_util.as_local(now)
    for day_offset in range(8):
        day = prog.get(str((local_now.weekday() + day_offset) % 7))
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
