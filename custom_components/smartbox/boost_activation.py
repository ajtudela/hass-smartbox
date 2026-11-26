"""Build and validate one boost/runback activation.

The vendor app sends a complete frame: temperature + duration for
``temp_and_time``/``runback``, and no parameters for the fixed-time and
advanced-slot styles. This module resolves the values the user configured (on
the device for legacy controllers, in :class:`BoostPrefs` for FULL ones),
validates them, and returns the request the engine should send. A missing or
invalid value is refused instead of posting a partial frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartbox.core.merging import boost_config
from smartbox.device.modes import (
    boost_activation_takes_parameters,
    boost_uses_legacy_path,
)
from smartbox.device.profile import boost_temperature_bounds, controller_limits
from smartbox.protocol.values import (
    BoostConfig,
    celsius_to_node_units,
    parse_number_or_none,
)

from .adapter import node_boost_params, node_runback_limits, node_runback_params
from .boost_policy import boost_activation_mode, runback_params_configurable
from .boost_prefs import BoostPrefs, NodeBoostPrefs
from .const import (
    BOOST_TIME_MIN,
    BOOST_TIME_STEP,
    RUNBACK_TIME_DEFAULT,
    RUNBACK_TIME_MAX,
    RUNBACK_TIME_STEP,
)
from .exceptions import validation_error

if TYPE_CHECKING:
    from smartbox.core.state import NodeState

    from .identity import SmartboxNodeInfo


class _NoPrefs:
    """Fallback when a caller has no entry store (unit-level construction)."""

    def get(self, node_id: str) -> NodeBoostPrefs:  # noqa: ARG002
        """Return an empty record, matching the real store's interface."""
        return NodeBoostPrefs()


_NO_PREFS = _NoPrefs()


@dataclass(frozen=True)
class ActivationPlan:
    """One activation request: an endpoint style or the legacy path."""

    mode: str | None
    temperature: float | None
    minutes: int | None
    legacy: bool


def plan_boost(
    node: SmartboxNodeInfo,
    state: NodeState | None,
    prefs: BoostPrefs | None,
) -> ActivationPlan:
    """Resolve the boost activation for one node."""
    setup = state.setup if state is not None else node.setup
    status = state.status if state is not None else node.status
    pid = _live_pid(node, state)
    fw = _live_fw(node, state)
    units = _units(state)
    legacy = boost_uses_legacy_path(pid, setup)
    mode = (
        None
        if legacy or node.node_type == "acm"
        else boost_activation_mode(pid, setup, status)
    )

    if (
        not boost_activation_takes_parameters(pid, setup)
        and mode is None
        and node.node_type != "acm"
    ):
        return ActivationPlan(None, None, None, legacy=legacy)
    if (
        not legacy
        and mode is None
        and boost_config(setup) == BoostConfig.FULL
        and node.node_type != "acm"
    ):
        msg = "boost_mode_unavailable"
        raise validation_error(msg)
    if mode == "max_temp_fixed_time":
        return ActivationPlan(mode, None, None, legacy=False)

    limits = controller_limits(node.node_type, pid, fw, setup)
    pref = (prefs or _NO_PREFS).get(node.node_id)
    device_temp, device_minutes = _device_boost_params(state)
    temperature: float | None
    if not legacy and pref.boost_temp_c is not None:
        temperature = celsius_to_node_units(pref.boost_temp_c, units)
    else:
        temperature = device_temp
    minutes = (
        pref.boost_time
        if not legacy and pref.boost_time is not None
        else device_minutes
    )
    low_c, high_c = boost_temperature_bounds(limits)
    temperature = _checked_temperature(
        "boost",
        temperature,
        low=celsius_to_node_units(low_c, units),
        high=celsius_to_node_units(high_c, units),
        units=units,
    )
    minutes = _checked_minutes(
        "boost",
        minutes,
        minimum=BOOST_TIME_MIN,
        maximum=limits.boost_max_minutes,
        step=BOOST_TIME_STEP,
    )
    return ActivationPlan(mode, temperature, minutes, legacy)


def plan_runback(
    node: SmartboxNodeInfo,
    state: NodeState | None,
    prefs: BoostPrefs | None,
) -> ActivationPlan:
    """Resolve a runback activation, which only the FULL endpoint carries."""
    setup = state.setup if state is not None else node.setup
    status = state.status if state is not None else node.status
    pid = _live_pid(node, state)
    fw = _live_fw(node, state)
    units = _units(state)
    if not runback_params_configurable(pid, setup, status):
        msg = "runback_requires_full_boost"
        raise validation_error(msg)

    limits = controller_limits(node.node_type, pid, fw, setup)
    pref = (prefs or _NO_PREFS).get(node.node_id)
    temperature: float | None
    if pref.runback_temp_c is not None:
        temperature = celsius_to_node_units(pref.runback_temp_c, units)
    else:
        temperature = _device_runback_temp(state)
    low, high = node_runback_limits(state)
    if low is None:
        low = celsius_to_node_units(limits.min_c, units)
    if high is None:
        high = celsius_to_node_units(limits.max_c, units)
    temperature = _checked_temperature(
        "runback", temperature, low=low, high=high, units=units
    )
    minutes = (
        pref.runback_time if pref.runback_time is not None else RUNBACK_TIME_DEFAULT
    )
    minutes = _checked_minutes(
        "runback",
        minutes,
        minimum=RUNBACK_TIME_DEFAULT,
        maximum=_runback_max_minutes(node, state),
        step=RUNBACK_TIME_STEP,
    )
    return ActivationPlan("runback", temperature, minutes, legacy=False)


def _live_pid(node: SmartboxNodeInfo, state: NodeState | None) -> str | None:
    version = state.version if state is not None else None
    if isinstance(version, dict):
        pid = version.get("pid")
        if isinstance(pid, str) and pid:
            return pid
    return node.pid


def _live_fw(node: SmartboxNodeInfo, state: NodeState | None) -> str | None:
    version = state.version if state is not None else None
    if isinstance(version, dict):
        fw = version.get("fw_version")
        if isinstance(fw, str) and fw:
            return fw
    return node.version.get("fw_version")


def _units(state: NodeState | None) -> str:
    if state is None:
        return "C"
    units = state.status.get("units")
    return units if isinstance(units, str) else "C"


def _device_boost_params(
    state: NodeState | None,
) -> tuple[float | None, int | None]:
    temperature, minutes = node_boost_params(state)
    return temperature, _as_int(minutes)


def _device_runback_temp(state: NodeState | None) -> float | None:
    temperature, _minutes = node_runback_params(state)
    if temperature is not None:
        return temperature
    raw = state.status.get("stemp") if state is not None else None
    return parse_number_or_none(raw)


def _runback_max_minutes(node: SmartboxNodeInfo, state: NodeState | None) -> int:
    setup = state.setup if state is not None else node.setup
    extra = setup.get("extra_options")
    raw = extra.get("max_runback_time") if isinstance(extra, dict) else None
    value = parse_number_or_none(raw)
    if value is None:
        return RUNBACK_TIME_MAX
    # The slider clamps the same field; keep the planner's ceiling above its
    # 30-minute floor so an unusually low stored cap cannot invert the range.
    return max(int(value), RUNBACK_TIME_DEFAULT)


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _checked_temperature(
    kind: str,
    value: float | None,
    *,
    low: float,
    high: float,
    units: str,
) -> float:
    if value is None:
        msg = f"{kind}_parameters_required"
        raise validation_error(msg)
    if not low <= value <= high:
        msg = f"{kind}_temperature_out_of_range"
        raise validation_error(
            msg,
            min=f"{low:g}",
            max=f"{high:g}",
            unit="°C" if units == "C" else "°F",
        )
    return value


def _checked_minutes(
    kind: str,
    value: int | None,
    *,
    minimum: int,
    maximum: int,
    step: int,
) -> int:
    if value is None:
        msg = f"{kind}_parameters_required"
        raise validation_error(msg)
    if value < minimum or value > maximum:
        msg = f"{kind}_duration_out_of_range"
        raise validation_error(msg, min=str(minimum), max=str(maximum), unit="min")
    if value % step:
        msg = f"{kind}_duration_step"
        raise validation_error(msg, step=str(step))
    return value
