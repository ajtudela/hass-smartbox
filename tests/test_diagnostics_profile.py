"""Direct coverage for the diagnostics profile resolver."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.smartbox.diagnostics import _node_profile


def _node(pid: str | None, setup: dict, fw: str = "1.9") -> SimpleNamespace:
    return SimpleNamespace(
        version={"pid": pid, "fw_version": fw} if pid else {},
        setup=setup,
    )


def test_storage_profile_is_firmware_gated() -> None:
    old = _node_profile("acm/1", _node("081C0000", {}, "1.7.9"))
    new = _node_profile("acm/1", _node("081C0000", {}, "1.8.0"))
    assert old is not None
    assert new is not None
    assert old["limits"]["profiles"] == ("frost", "eco", "comfort")
    assert new["limits"]["profiles"] == ("frost", "eco", "comfort", "off")


def test_htr_mod_profile_has_no_prog_bounds() -> None:
    profile = _node_profile("htr_mod/1", _node("010A0000", {}))
    assert profile is not None
    assert profile["prog_bounds"] is None
    assert profile["limits"]["htr_mod"] is True


def test_missing_pid_returns_no_profile() -> None:
    assert _node_profile("htr/0", _node(None, {})) is None


def test_stored_max_limit_clamps_the_reported_climate_bounds() -> None:
    profile = _node_profile("htr/0", _node("04020000", {"max_stemp_limit": "23.0"}))
    assert profile is not None
    assert profile["climate_bounds"] == (1.0, 23.0)


def test_non_heater_programme_kinds() -> None:
    """Thermostat and water storage are not reported as heater profiles."""
    thermostat = _node_profile("thm/0", _node("01040000", {}))
    water = _node_profile("acm/1", _node("082A0000", {}))
    assert thermostat is not None
    assert water is not None
    assert thermostat["programme_kind"] == "thresholds"
    assert thermostat["prog_bounds"] is None
    assert water["programme_kind"] == "on_off_auto"
    assert water["prog_bounds"] is None


def test_disabled_max_limit_keeps_the_controller_profile() -> None:
    """The documented `0.0` sentinel must not produce inverted bounds."""
    profile = _node_profile("htr/0", _node("04020000", {"max_stemp_limit": "0.0"}))
    assert profile is not None
    assert profile["climate_bounds"] == (1.0, 30.0)
    assert profile["prog_bounds"]["comf_temp"] == (1.0, 30.0)
