"""Per-controller temperature ranges and profile catalogues."""

from __future__ import annotations

from custom_components.smartbox.controller import (
    boost_activation_mode,
    boost_activation_takes_parameters,
    boost_params_configurable,
    boost_params_editable,
    boost_temperature_bounds,
    boost_uses_legacy_path,
    climate_bounds,
    controller_limits,
    is_heater_surface,
    is_thermostat_surface,
    lock_kind,
    max_limit_bounds,
    profile_bounds,
    prog_bounds_c,
    runback_params_configurable,
    supports_easy,
    supports_runback,
)


def _limits(node_type: str, pid: str, fw: str, setup: dict[str, str] | None = None):
    return controller_limits(node_type, pid, fw, setup or {})


def test_hjm_helki_wifi_family() -> None:
    """PID 0132 is HelkiWifi -> HelkiHeater; 4 profiles, base 0-30 bounds."""
    limits = _limits("htr", "0132", "1.8.0")
    assert limits.profiles == ("frost", "eco", "comfort", "off")
    assert climate_bounds(limits, None) == (1.0, 30.0)
    assert profile_bounds(limits, "frost") == (0.0, 29.0)
    assert profile_bounds(limits, "eco") == (0.5, 29.5)
    assert profile_bounds(limits, "comfort") == (1.0, 30.0)
    assert profile_bounds(limits, "off") is None
    assert (
        lock_kind(
            "htr", "0132", "1.8.0", {"factory_options": {"super_lock_available": True}}
        )
        == "int"
    )
    assert lock_kind("htr", "0132", "1.8.0") == "bool"
    assert limits.boost_max_minutes == 1440


def test_base_0402_and_max_stemp_limit() -> None:
    """PID 0402 is the base ElectricHeater -> 3 profiles and boolean lock."""
    limits = _limits("htr", "0402", "1.9", {"max_stemp_limit": "23.0"})
    assert limits.profiles == ("frost", "eco", "comfort")
    assert climate_bounds(limits, 23.0) == (1.0, 23.0)
    assert max_limit_bounds(limits) == (16.0, 30.0)
    assert boost_temperature_bounds(limits) == (5.0, 30.0)
    assert lock_kind("htr", "0402", "1.9") == "bool"


def test_haverland_lock_kinds_follow_the_controller():
    """1D gates integer lock at fw 1.7; 47 is always integer; 43/0D/18 bool."""
    assert lock_kind("htr", "011D", "1.6.0") == "bool"
    assert lock_kind("htr", "011D", "1.7.0") == "int"
    assert lock_kind("htr", "0147", "1.6.0") == "int"
    for pid in ("0143", "010D", "0118"):
        assert lock_kind("htr", pid, "1.9") == "bool"
    # The ATC subclasses inherit TevolveWifiHeaterController's integer payload.
    for pid in ("014C", "014D"):
        assert lock_kind("htr", pid, "1.9") == "int"


def test_helki_lock_follows_super_lock_available() -> None:
    """The Helki/Lhz heater payload is integer only with super_lock_available."""
    flag = {"factory_options": {"super_lock_available": True}}
    for code in ("0132", "0133", "0135", "0136", "0138"):
        assert lock_kind("htr", code, "1.6.0", flag) == "int"
        assert lock_kind("htr", code, "1.9", flag) == "int"
        assert lock_kind("htr", code, "1.9") == "bool"
        assert lock_kind("htr", code, "1.9", {"factory_options": {}}) == "bool"


def test_helki_storage_profiles_follow_firmware() -> None:
    """Helki storage gains the OFF profile at firmware 1.8."""
    old = _limits("acm", "081C0000", "1.7.9", {"min_stemp": "4", "max_stemp": "26"})
    new = _limits("acm", "081C0000", "1.8.0", {"min_stemp": "4", "max_stemp": "26"})
    assert old.profiles == ("frost", "eco", "comfort")
    assert new.profiles == ("frost", "eco", "comfort", "off")
    assert climate_bounds(new, None) == (4.0, 26.0)


def test_htr_mod_is_product_code_scoped() -> None:
    """Only Haverland 0A/FF dispatch to the off-first htr_mod limits."""
    htr_mod = _limits("htr_mod", "010A", "1.8.0")
    generic = _limits("htr_mod", "0101", "1.8.0")
    assert htr_mod.profiles == ("off", "frost", "eco", "comfort")
    assert profile_bounds(htr_mod, "eco") == (10.0, 18.0)
    assert profile_bounds(htr_mod, "comfort") == (18.0, 28.0)
    assert generic.profiles == ("frost", "eco", "comfort")
    assert climate_bounds(generic, None) == (1.0, 30.0)


def test_tevolve_families() -> None:
    """Tevolve triac/WiFi/towel-rail share 7-35; ATC keeps 5-30/ICE 5-10."""
    triac = _limits("htr", "011A", "1.9")
    wifi = _limits("htr", "0134", "1.9")
    towel = _limits("htr", "0123", "1.9")
    assert climate_bounds(triac, None) == (7.0, 35.0)
    assert climate_bounds(wifi, None) == (7.0, 35.0)
    assert climate_bounds(towel, None) == (7.0, 35.0)
    assert profile_bounds(wifi, "frost") == (7.0, 34.0)
    assert profile_bounds(wifi, "eco") == (7.5, 34.5)

    pir = _limits("htr", "0127", "1.9")
    assert profile_bounds(pir, "frost") is None
    assert profile_bounds(pir, "eco") == (7.5, 31.5)
    assert profile_bounds(pir, "comfort") == (11.0, 35.0)
    assert prog_bounds_c(pir) == {
        "ice_temp": (7.0, 34.0),
        "eco_temp": (7.5, 31.5),
        "comf_temp": (11.0, 35.0),
    }
    atc = _limits("htr", "014C", "1.9")
    ferroli = _limits("htr", "014D", "1.9")
    for limits in (atc, ferroli):
        assert climate_bounds(limits, None) == (5.0, 30.0)
        assert profile_bounds(limits, "frost") == (5.0, 10.0)
        assert limits.boost_max_minutes == 120
    assert _limits("htr", "013D", "1.9").boost_max_minutes == 300
    assert supports_easy("0134")
    assert supports_easy("011A")
    assert supports_easy("0123")
    assert supports_easy("0127")
    assert not supports_easy("0132")
    assert not supports_easy("0402")
    assert supports_runback("011A")
    assert supports_runback("0123")
    assert not supports_runback("0127")
    assert not supports_runback("0124")
    assert not supports_easy("014C")
    assert supports_runback("014C", {"factory_options": {"boost_config": 2}})
    assert not supports_runback("014C", {"factory_options": {"boost_config": 0}})
    assert not supports_runback("014C", {"factory_options": {"boost_config": 3}})
    assert not supports_runback("0134", {})


def test_factory_default_rows_use_the_electric_heater_profile() -> None:
    """0D/18/24/43 have no controller case and use the base catalogue."""
    for pid in ("010D", "0118", "0124", "0143"):
        limits = _limits("htr", pid, "1.9")
        assert limits.profiles == ("frost", "eco", "comfort")
        assert climate_bounds(limits, None) == (1.0, 30.0)


def test_unknown_acm_uses_the_helki_storage_profile() -> None:
    """The factory's default acm branch is HelkiStorage, including 00/unknown."""
    setup = {"min_stemp": "4", "max_stemp": "26"}
    unknown = _limits("acm", "0899", "1.8.0", setup)
    zero = _limits("acm", "0000", "1.8.0", setup)
    for limits in (unknown, zero):
        assert climate_bounds(limits, None) == (4.0, 26.0)
        assert limits.profiles == ("frost", "eco", "comfort", "off")


def test_boost_parameters_default_to_set_temp_and_time_when_absent():
    """Absence is not fixed-time for the unconditional-BOOST controllers."""
    assert boost_params_editable("acm", "081C0000", {}) is True
    assert boost_params_editable("htr", "01360000", {}) is True
    for pid in ("011A0000", "01210000", "01230000", "01280000"):
        assert boost_params_editable("htr", pid, {}) is True
    assert boost_params_editable("htr", "01270000", {}) is False
    assert boost_params_editable(
        "htr", "01320000", {"factory_options": {"boost_config": 2}}
    )
    assert boost_params_editable("htr", "01320000", {}) is False
    assert boost_params_editable("htr", "01340000", {}) is False
    assert boost_params_editable(
        "htr", "01340000", {"factory_options": {"boost_config": 2}}
    )
    assert boost_params_editable("acm", "08140000", {}) is False
    assert (
        boost_params_editable(
            "htr", "01170000", {"factory_options": {"boost_config": 2}}
        )
        is False
    )
    # FULL is temp-and-time editable on storage and the triac family.
    assert boost_params_editable(
        "acm", "081C0000", {"factory_options": {"boost_config": 3}}
    )
    assert boost_params_editable(
        "htr", "011A0000", {"factory_options": {"boost_config": 3}}
    )
    assert not boost_params_editable(
        "htr", "01340000", {"factory_options": {"boost_config": 3}}
    )


def test_full_boost_attribute_visibility_follows_the_advertised_modes() -> None:
    """Sliders exist only where the endpoint advertises the parameter style."""
    setup = {"factory_options": {"boost_config": 3}}
    temp = {"boost_modes_available": ["temp_and_time"]}
    fixed = {"boost_modes_available": ["max_temp_fixed_time"]}
    both = {"boost_modes_available": ["temp_and_time", "runback"]}
    none: dict[str, object] = {"boost_modes_available": []}

    assert boost_activation_mode("0134", setup, temp) == "temp_and_time"
    assert boost_activation_mode("0134", setup, fixed) == "max_temp_fixed_time"
    assert boost_activation_mode("0134", setup, none) is None
    assert boost_activation_mode("011D", setup, none) == "max_temp_fixed_time"

    assert boost_params_configurable("htr", "0134", setup, temp) is True
    assert boost_params_configurable("htr", "0134", setup, fixed) is False
    assert runback_params_configurable("0134", setup, both) is True
    assert runback_params_configurable("0134", setup, temp) is False
    # Legacy config 2 keeps its device-backed sliders.
    legacy = {"factory_options": {"boost_config": 2}}
    assert boost_params_configurable("htr", "0132", legacy, {}) is True


def test_thm_surface_follows_the_controller_family() -> None:
    """THM 28 is a heater-family controller; 04/09/0F are thermostats."""
    assert is_heater_surface("thm", "0128")
    assert not is_thermostat_surface("thm", "0128")
    for pid in ("0104", "0109", "010F"):
        assert not is_heater_surface("thm", pid)
        assert is_thermostat_surface("thm", pid)
    assert is_heater_surface("htr", "0402")
    assert not is_thermostat_surface("htr", "0402")


def test_unknown_pid_uses_the_electric_heater_default() -> None:
    """The factory's default branch is ElectricHeaterController (0-30)."""
    limits = _limits("htr", "0899", "1.9")
    assert climate_bounds(limits, None) == (1.0, 30.0)
    assert profile_bounds(limits, "comfort") == (1.0, 30.0)
    assert max_limit_bounds(limits) == (16.0, 30.0)
    assert boost_temperature_bounds(limits) == (5.0, 30.0)


def test_haverland_fixed_time_boost_never_uses_parameters() -> None:
    """17/1D/47 inherit setBasicBoost; config 2/3 cannot add parameters."""
    for code in ("01170000", "011D0000", "01470000"):
        for config in (0, 1, 2, 3):
            setup = {"factory_options": {"boost_config": config}}
            assert boost_params_editable("htr", code, setup) is False
            assert boost_activation_takes_parameters(code, setup) is False
            # The status-flag path covers 0-2; FULL enters /boost_mode.
            assert boost_uses_legacy_path(code, setup) is (config != 3)
        assert boost_uses_legacy_path(code) is True

    # Full WiFi/ATC activation needs the stored pair even though the parameter
    # number entities are withheld until boost_modes_available is modelled.
    assert boost_activation_takes_parameters(
        "01340000", {"factory_options": {"boost_config": 3}}
    )
    assert not boost_activation_takes_parameters(
        "01340000", {"factory_options": {"boost_config": 1}}
    )


def test_storage_boost_parameters_ignore_the_boost_config() -> None:
    """Helki storage's /boost path is independent of boost_config."""
    for config in (0, 1, 2, 3):
        setup = {"factory_options": {"boost_config": config}}
        assert boost_params_editable("acm", "081C0000", setup) is True
        assert boost_params_editable("acm", "082E0000", setup) is True
        assert boost_params_editable("acm", "08140000", setup) is False
