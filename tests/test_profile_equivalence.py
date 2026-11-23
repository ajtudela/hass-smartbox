"""Temporary old-vs-new profile equivalence harness.

Runs the integration's current controller helpers and the library's neutral
``resolve_profile`` over every code, firmware branch and setup variant. Delete
the integration-side assertions once ``controller.py`` re-exports the library
resolver; the library golden test then owns the facts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from smartbox.device.profile import resolve_profile

from custom_components.smartbox.controller import (
    boost_temperature_bounds,
    climate_bounds,
    controller_limits,
    max_limit_bounds,
    prog_bounds_c,
)

FIXTURE = Path(__file__).parent / "fixtures" / "product_codes.json"
CODES = json.loads(FIXTURE.read_text(encoding="utf-8"))["codes"]


def _setup() -> dict[str, object]:
    return {
        "units": "C",
        "power": "2000.0",
        "max_stemp_limit": "30.0",
        "extra_options": {"user_duty_limit": 0, "sensor_mode": 0},
        "factory_options": {
            "lst_available": True,
            "lst_value": 25,
            "bbc_available": True,
            "bbc_value": 50,
            "ui_mode": 0,
            "super_lock_available": True,
        },
    }


@pytest.mark.parametrize("code", sorted(CODES))
@pytest.mark.parametrize("fw", ["1.6.0", "1.7.0", "1.8.0", "1.9"])
@pytest.mark.parametrize("setup_kind", ["canonical", "empty"])
def test_old_and_new_profile_resolution_agree(code, fw, setup_kind) -> None:
    """Every code/fw/setup point resolves to the same neutral facts."""
    row = CODES[code]
    setup = _setup() if setup_kind == "canonical" else {}
    node_type = row["node_type"]
    pid = f"08{code}0000"

    old_limits = controller_limits(node_type, pid, fw, setup)
    old = {
        "min_c": old_limits.min_c,
        "max_c": old_limits.max_c,
        "ice_min_c": old_limits.ice_min_c,
        "ice_max_c": old_limits.ice_max_c,
        "profile_offset_c": old_limits.profile_offset_c,
        "comfort_eco_offset_c": old_limits.comfort_eco_offset_c,
        "ice_editable": old_limits.ice_editable,
        "profiles": old_limits.profiles,
        "htr_mod": old_limits.htr_mod,
        "max_limit_max_c": old_limits.max_limit_max_c,
        "boost_max_c": old_limits.boost_max_c,
        "boost_max_minutes": old_limits.boost_max_minutes,
        "prog_min_c": old_limits.prog_min_c,
        "prog_max_c": old_limits.prog_max_c,
        "climate_bounds": climate_bounds(old_limits, None),
        "max_limit_bounds": max_limit_bounds(old_limits),
        "boost_bounds": boost_temperature_bounds(old_limits),
        "prog_bounds": prog_bounds_c(old_limits),
    }
    new = resolve_profile(node_type, pid, fw, setup)
    new_limits = new.limits
    assert old == {
        "min_c": new_limits.min_c,
        "max_c": new_limits.max_c,
        "ice_min_c": new_limits.ice_min_c,
        "ice_max_c": new_limits.ice_max_c,
        "profile_offset_c": new_limits.profile_offset_c,
        "comfort_eco_offset_c": new_limits.comfort_eco_offset_c,
        "ice_editable": new_limits.ice_editable,
        "profiles": new_limits.profiles,
        "htr_mod": new_limits.htr_mod,
        "max_limit_max_c": new_limits.max_limit_max_c,
        "boost_max_c": new_limits.boost_max_c,
        "boost_max_minutes": new_limits.boost_max_minutes,
        "prog_min_c": new_limits.prog_min_c,
        "prog_max_c": new_limits.prog_max_c,
        "climate_bounds": new.climate_bounds,
        "max_limit_bounds": new.max_limit_bounds,
        "boost_bounds": new.boost_bounds,
        "prog_bounds": new.prog_bounds,
    }
