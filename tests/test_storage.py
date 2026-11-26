"""Storage mode semantics from the decoded Elnur/Helki controllers."""

from __future__ import annotations

from homeassistant.components.climate import HVACMode
from smartbox.device.storage import (
    boost_supported,
    classic_accumulator,
    ui_mode_available,
    ui_mode_options,
)

from custom_components.smartbox.storage import available_modes, current_mode, write_for

ELNUR_CLASSIC = {
    "factory_options": {"ui_mode": 2},
}
ELNUR_ECOMBI = {
    "factory_options": {"ui_mode": 0},
}
HELKI_MANUAL = {"operational_mode": 0}
HELKI_AUTO = {"operational_mode": 1}


def test_elnur_classic_accumulator_uses_target_charge_for_on_off() -> None:
    """The app's CLASSIC_ACCUMULATOR ON/OFF is 100/0, not a mode write."""
    assert classic_accumulator("0814", ELNUR_CLASSIC)
    assert available_modes("acm", "0814", ELNUR_CLASSIC) == [
        HVACMode.HEAT,
        HVACMode.OFF,
    ]
    assert (
        current_mode("acm", "0814", ELNUR_CLASSIC, {"target_charge_per": 0})
        is HVACMode.OFF
    )
    assert (
        current_mode("acm", "0814", ELNUR_CLASSIC, {"target_charge_per": 100})
        is HVACMode.HEAT
    )
    heat = write_for("acm", "0814", ELNUR_CLASSIC, HVACMode.HEAT)
    off = write_for("acm", "0814", ELNUR_CLASSIC, HVACMode.OFF)
    assert heat is not None
    assert heat.kind == "charge"
    assert heat.target_charge == 100
    assert off is not None
    assert off.kind == "charge"
    assert off.target_charge == 0
    assert write_for("acm", "0814", ELNUR_CLASSIC, HVACMode.AUTO) is None


def test_elnur_ecombi_keeps_auto_and_manual() -> None:
    """ECOMBI offers AUTO/MANUAL; the classic target-charge path is off."""
    assert not classic_accumulator("0814", ELNUR_ECOMBI)
    assert available_modes("acm", "0814", ELNUR_ECOMBI) == [
        HVACMode.AUTO,
        HVACMode.HEAT,
        HVACMode.OFF,
    ]
    auto = write_for("acm", "0814", ELNUR_ECOMBI, HVACMode.AUTO)
    assert auto is not None
    assert auto.kind == "status"
    assert auto.mode == "auto"


def test_helki_operational_mode_filters_auto_or_manual() -> None:
    """MANUAL_OFF_AVAILABLE offers manual; AUTO_OFF_AVAILABLE adds auto.

    HEAT stays available in AUTO_OFF_AVAILABLE because the app's boost
    deactivation writes wire mode ``manual`` on that configuration, even
    though the picker itself omits MANUAL.
    """
    assert available_modes("acm", "081C", HELKI_MANUAL) == [
        HVACMode.HEAT,
        HVACMode.OFF,
    ]
    assert available_modes("acm", "081C", HELKI_AUTO) == [
        HVACMode.AUTO,
        HVACMode.HEAT,
        HVACMode.OFF,
    ]
    assert available_modes("acm", "081C", {}) == [
        HVACMode.HEAT,
        HVACMode.OFF,
    ]
    manual = current_mode("acm", "081C", HELKI_AUTO, {"mode": "manual"})
    assert manual is HVACMode.HEAT
    assert manual in available_modes("acm", "081C", HELKI_AUTO)


def test_elnur_absent_ui_mode_does_not_infer_auto() -> None:
    """An absent ui_mode offers MANUAL/OFF/ON, matching the app's filter."""
    assert available_modes("acm", "0814", {}) == [
        HVACMode.HEAT,
        HVACMode.OFF,
    ]


def test_storage_boost_reports_heat_while_the_wire_mode_is_off() -> None:
    """The app resolves BOOST before status.mode on the storage controller."""
    assert (
        current_mode("acm", "081C", HELKI_AUTO, {"mode": "off", "boost": True})
        is HVACMode.HEAT
    )


def test_unknown_acm_uses_the_helki_storage_fallback() -> None:
    """An unknown/00 acm gets HelkiStorage modes, not the generic list."""
    for pid in (None, "0000", "0899"):
        assert available_modes("acm", pid, {}) == [
            HVACMode.HEAT,
            HVACMode.OFF,
        ]
    assert available_modes("acm", "0899", HELKI_AUTO) == [
        HVACMode.AUTO,
        HVACMode.HEAT,
        HVACMode.OFF,
    ]


def test_water_storage_filters_modes_by_extra_energy_config() -> None:
    """2A removes AUTO in EXTRA_ENERGY_USAGE and keeps it otherwise."""
    enabled_pure = {"extra_nrg_conf": {"enabled": True, "mode": 0}}
    enabled_combined = {"extra_nrg_conf": {"enabled": True, "mode": 1}}
    disabled = {"extra_nrg_conf": {"enabled": False, "mode": 0}}
    assert available_modes("acm", "082A", enabled_pure) == [
        HVACMode.HEAT,
        HVACMode.OFF,
    ]
    assert available_modes("acm", "082A", enabled_combined) == [
        HVACMode.AUTO,
        HVACMode.OFF,
    ]
    assert available_modes("acm", "082A", disabled) == [
        HVACMode.AUTO,
        HVACMode.HEAT,
        HVACMode.OFF,
    ]
    assert write_for("acm", "082A", enabled_combined, HVACMode.HEAT) is None
    auto = write_for("acm", "082A", enabled_combined, HVACMode.AUTO)
    assert auto is not None
    assert auto.mode == "auto"


def test_storage_boost_is_the_helki_1c_2e_path() -> None:
    """The Elnur families do not get the legacy /boost switch."""
    assert boost_supported("acm", "081C") is True
    assert boost_supported("acm", "082E") is True
    assert boost_supported("acm", "0814") is False
    assert boost_supported("htr", "081C") is False


def test_storage_ui_mode_options_follow_the_product() -> None:
    """14 accepts the two classic modes; 20/25 accept ECOMBI/combined."""
    assert ui_mode_options("08140000") == (
        ("Classic combined", 1),
        ("Classic accumulator", 2),
    )
    for pid in ("08200000", "08250000"):
        assert ui_mode_options(pid) == (
            ("Ecombi", 0),
            ("Classic combined", 1),
        )
    assert ui_mode_options("082A0000") == ()
    assert ui_mode_options("081C0000") == ()


def test_storage_ui_mode_setting_only_in_a_classic_mode() -> None:
    """The app hides the UI-mode setting while the unit is in ECOMBI."""
    assert ui_mode_available("08140000", ELNUR_CLASSIC) is True
    assert ui_mode_available("08140000", ELNUR_ECOMBI) is False
    assert ui_mode_available("08200000", ELNUR_ECOMBI) is False
    assert ui_mode_available("08200000", {"factory_options": {"ui_mode": 1}}) is True
    assert ui_mode_available("082A0000", {"factory_options": {"ui_mode": 1}}) is False


def test_unsupported_hvac_mode_returns_none() -> None:
    """A direct caller's unknown mode keeps the translated user-error path."""
    assert write_for("acm", "081C", HELKI_MANUAL, HVACMode.COOL) is None
