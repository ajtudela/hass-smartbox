"""Exhaustive decoded-product-code parity table.

One row per `EProductCodeValue` entry from the vendor 2.54.2 bundle. The rows
are written out here rather than derived from `controller.py`, so a missing or
misgrouped production set fails this test instead of re-asserting itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from custom_components.smartbox.controller import (
    boost_params_editable,
    boost_temperature_bounds,
    boost_uses_legacy_path,
    climate_bounds,
    controller_limits,
    has_no_probes,
    is_heater_surface,
    is_thermostat_surface,
    lock_kind,
    max_limit_bounds,
    profile_bounds,
    prog_bounds_c,
    supports_easy,
    supports_runback,
)
from custom_components.smartbox.identity import (
    boost_available,
    duty_limit_options,
    max_temp_limit_available,
)
from custom_components.smartbox.storage import is_elnur_storage, is_water_storage

BASE = ("frost", "eco", "comfort")
WITH_OFF = ("frost", "eco", "comfort", "off")
HTR_MOD = ("off", "frost", "eco", "comfort")
ALL_CONFIGS = frozenset({0, 1, 2, 3})

# Programme bounds in Celsius, keyed the way the write layer names the slots.
BASE_PROG = {"ice_temp": (0.0, 29.0), "eco_temp": (0.5, 29.5), "comf_temp": (1.0, 30.0)}
ELNUR_PROG = {
    "ice_temp": (3.0, 29.0),
    "eco_temp": (3.5, 29.5),
    "comf_temp": (4.0, 30.0),
}
STORAGE_PROG = {
    "ice_temp": (5.0, 29.0),
    "eco_temp": (5.5, 29.5),
    "comf_temp": (6.0, 30.0),
}
HAVERLAND_PROG = {
    "ice_temp": (5.0, 7.0),
    "eco_temp": (7.0, 28.0),
    "comf_temp": (9.0, 30.0),
}
TRIAC_PROG = {
    "ice_temp": (7.0, 34.0),
    "eco_temp": (7.5, 34.5),
    "comf_temp": (8.0, 35.0),
}
# TevolvePIRHeaterController widens the comfort/eco gap to 3.5 °C and keeps
# the ICE slot read-only; the ICE clamp is only for the write sanitizer.
PIR_PROG = {
    "ice_temp": (7.0, 34.0),
    "eco_temp": (7.5, 31.5),
    "comf_temp": (11.0, 35.0),
}
ATC_PROG = {"ice_temp": (5.0, 10.0), "eco_temp": (5.5, 29.5), "comf_temp": (6.0, 30.0)}
RELAY_PROG = {
    "ice_temp": (0.0, 34.0),
    "eco_temp": (0.5, 34.5),
    "comf_temp": (1.0, 35.0),
}

HELKI_DUTY = (("Off", 0), ("LST", 25), ("Baby Care", 50))
LHZ_DUTY = (
    ("Off", 0),
    ("LST", 25),
    ("Baby Care", 50),
    ("70 %", 70),
    ("80 %", 80),
    ("90 %", 90),
)


@dataclass(frozen=True)
class Expectation:
    """The decoded behavior one product code must resolve to."""

    node_type: str
    min_c: float | None
    max_c: float | None
    profiles: tuple[str, ...] | None
    boost_configs: frozenset[int]
    easy: bool
    runback_configs: frozenset[int]
    lock: str
    prog_bounds: dict[str, tuple[float, float]] | None
    climate_bounds: tuple[float, float] | None
    max_limit_bounds: tuple[float, float] | None
    boost_bounds: tuple[float, float] | None
    boost_cap: int | None
    boost_params: frozenset[int]
    duty_options: tuple[tuple[str, int], ...]
    max_limit: bool
    storage_ui: bool
    surface: str  # "heater" | "thermostat" | "power"
    ice_editable: bool = True
    water: bool = False
    extra: dict[str, object] = field(default_factory=dict)


BASE_ROW = Expectation(
    node_type="htr",
    min_c=0.0,
    max_c=30.0,
    profiles=BASE,
    boost_configs=frozenset(),
    easy=False,
    runback_configs=frozenset(),
    lock="bool",
    prog_bounds=BASE_PROG,
    climate_bounds=(1.0, 30.0),
    max_limit_bounds=(16.0, 30.0),
    boost_bounds=(5.0, 30.0),
    boost_cap=1440,
    boost_params=frozenset({2}),
    duty_options=(),
    max_limit=True,
    storage_ui=False,
    surface="heater",
)
HELKI_HEATER = Expectation(
    node_type="htr",
    min_c=0.0,
    max_c=30.0,
    profiles=WITH_OFF,
    boost_configs=frozenset({2}),
    easy=False,
    runback_configs=frozenset(),
    lock="int",
    prog_bounds=BASE_PROG,
    climate_bounds=(1.0, 30.0),
    max_limit_bounds=(16.0, 30.0),
    boost_bounds=(5.0, 30.0),
    boost_cap=1440,
    boost_params=frozenset({2}),
    duty_options=HELKI_DUTY,
    max_limit=True,
    storage_ui=False,
    surface="heater",
)
LHZ_WIFI = Expectation(
    node_type="htr",
    min_c=0.0,
    max_c=30.0,
    profiles=WITH_OFF,
    boost_configs=frozenset({2}),
    easy=False,
    runback_configs=frozenset(),
    lock="int",
    prog_bounds=BASE_PROG,
    climate_bounds=(1.0, 30.0),
    max_limit_bounds=(16.0, 30.0),
    boost_bounds=(5.0, 30.0),
    boost_cap=1440,
    boost_params=frozenset({2}),
    duty_options=LHZ_DUTY,
    max_limit=True,
    storage_ui=False,
    surface="heater",
)
LHZ_RELAY = Expectation(
    node_type="htr",
    min_c=0.0,
    max_c=30.0,
    profiles=WITH_OFF,
    boost_configs=ALL_CONFIGS,
    easy=False,
    runback_configs=frozenset(),
    lock="int",
    prog_bounds=BASE_PROG,
    climate_bounds=(1.0, 30.0),
    max_limit_bounds=(16.0, 30.0),
    boost_bounds=(5.0, 30.0),
    boost_cap=1440,
    boost_params=frozenset({2}),
    duty_options=LHZ_DUTY,
    max_limit=True,
    storage_ui=False,
    surface="heater",
)
TEVOLVE_TRIAC = Expectation(
    node_type="htr",
    min_c=7.0,
    max_c=35.0,
    profiles=BASE,
    boost_configs=ALL_CONFIGS,
    easy=True,
    runback_configs=ALL_CONFIGS,
    lock="bool",
    prog_bounds=TRIAC_PROG,
    climate_bounds=(7.0, 35.0),
    max_limit_bounds=(16.0, 35.0),
    boost_bounds=(5.0, 35.0),
    boost_cap=1440,
    boost_params=frozenset({2, 3}),
    duty_options=(),
    max_limit=False,
    storage_ui=False,
    surface="heater",
)
TEVOLVE_WIFI = Expectation(
    node_type="htr",
    min_c=7.0,
    max_c=35.0,
    profiles=BASE,
    boost_configs=frozenset({1, 2, 3}),
    easy=True,
    runback_configs=frozenset({1, 2}),
    lock="int",
    prog_bounds=TRIAC_PROG,
    climate_bounds=(7.0, 35.0),
    max_limit_bounds=(16.0, 35.0),
    boost_bounds=(5.0, 35.0),
    boost_cap=1440,
    boost_params=frozenset({2}),
    duty_options=(),
    max_limit=False,
    storage_ui=False,
    surface="heater",
)
TEVOLVE_ATC = Expectation(
    node_type="htr",
    min_c=5.0,
    max_c=30.0,
    profiles=BASE,
    boost_configs=frozenset({1, 2, 3}),
    easy=False,
    runback_configs=frozenset({1, 2}),
    lock="int",
    prog_bounds=ATC_PROG,
    climate_bounds=(5.0, 30.0),
    max_limit_bounds=(16.0, 30.0),
    boost_bounds=(5.0, 30.0),
    boost_cap=120,
    boost_params=frozenset({2}),
    duty_options=(),
    max_limit=True,
    storage_ui=False,
    surface="heater",
)
HAVERLAND = Expectation(
    node_type="htr",
    min_c=5.0,
    max_c=30.0,
    profiles=WITH_OFF,
    boost_configs=ALL_CONFIGS,
    easy=False,
    runback_configs=frozenset(),
    lock="bool",
    prog_bounds=HAVERLAND_PROG,
    climate_bounds=(5.0, 30.0),
    max_limit_bounds=(16.0, 30.0),
    boost_bounds=(5.0, 30.0),
    boost_cap=1440,
    boost_params=frozenset(),
    duty_options=(),
    max_limit=True,
    storage_ui=False,
    surface="heater",
)
HAVERLAND_WIFI = Expectation(
    node_type="htr",
    min_c=5.0,
    max_c=30.0,
    profiles=WITH_OFF,
    boost_configs=frozenset({0, 2, 3}),
    easy=False,
    runback_configs=frozenset(),
    lock="int",
    prog_bounds=HAVERLAND_PROG,
    climate_bounds=(5.0, 30.0),
    max_limit_bounds=(16.0, 30.0),
    boost_bounds=(5.0, 30.0),
    boost_cap=1440,
    boost_params=frozenset(),
    duty_options=(),
    max_limit=False,
    storage_ui=False,
    surface="heater",
)
HAVERLAND_WIFI_B = Expectation(
    node_type="htr",
    min_c=5.0,
    max_c=30.0,
    profiles=WITH_OFF,
    boost_configs=frozenset({0, 2, 3}),
    easy=False,
    runback_configs=frozenset(),
    lock="int",
    prog_bounds=HAVERLAND_PROG,
    climate_bounds=(5.0, 30.0),
    max_limit_bounds=(16.0, 30.0),
    boost_bounds=(5.0, 30.0),
    boost_cap=1440,
    boost_params=frozenset(),
    duty_options=(),
    max_limit=True,
    storage_ui=False,
    surface="heater",
)
HTR_MOD_ROW = Expectation(
    node_type="htr_mod",
    min_c=5.0,
    max_c=28.0,
    profiles=HTR_MOD,
    boost_configs=frozenset(),
    easy=False,
    runback_configs=frozenset(),
    lock="bool",
    prog_bounds=None,
    climate_bounds=(5.0, 28.0),
    max_limit_bounds=(16.0, 28.0),
    boost_bounds=(5.0, 28.0),
    boost_cap=1440,
    boost_params=frozenset({2}),
    duty_options=(),
    max_limit=False,
    storage_ui=False,
    surface="heater",
    ice_editable=False,
)
STORAGE_HELKI = Expectation(
    node_type="acm",
    min_c=5.0,
    max_c=30.0,
    profiles=WITH_OFF,
    boost_configs=ALL_CONFIGS,
    easy=False,
    runback_configs=frozenset(),
    lock="int",
    prog_bounds=STORAGE_PROG,
    climate_bounds=(5.0, 30.0),
    max_limit_bounds=(16.0, 30.0),
    boost_bounds=(5.0, 30.0),
    boost_cap=1440,
    boost_params=ALL_CONFIGS,
    duty_options=(),
    max_limit=False,
    storage_ui=False,
    surface="heater",
)
STORAGE_ELNUR = Expectation(
    node_type="acm",
    min_c=3.0,
    max_c=30.0,
    profiles=BASE,
    boost_configs=frozenset(),
    easy=False,
    runback_configs=frozenset(),
    lock="int",
    prog_bounds=ELNUR_PROG,
    climate_bounds=(3.0, 30.0),
    max_limit_bounds=(16.0, 30.0),
    boost_bounds=(5.0, 30.0),
    boost_cap=1440,
    boost_params=frozenset(),
    duty_options=(),
    max_limit=False,
    storage_ui=True,
    surface="heater",
)
# Water storage's app catalogue is ON/OFF_AUTO; the integration does not model
# those profiles and withholds its programme entities, so BASE is a placeholder.
WATER = Expectation(
    node_type="acm",
    min_c=3.0,
    max_c=30.0,
    profiles=BASE,
    boost_configs=frozenset(),
    easy=False,
    runback_configs=frozenset(),
    lock="int",
    prog_bounds=ELNUR_PROG,
    climate_bounds=(3.0, 30.0),
    max_limit_bounds=(16.0, 30.0),
    boost_bounds=(5.0, 30.0),
    boost_cap=1440,
    boost_params=frozenset(),
    duty_options=(),
    max_limit=False,
    storage_ui=True,
    surface="heater",
    water=True,
)
THERMOSTAT = Expectation(
    node_type="thm",
    min_c=7.0,
    max_c=35.0,
    profiles=BASE,
    boost_configs=frozenset(),
    easy=False,
    runback_configs=frozenset(),
    lock="bool",
    prog_bounds=TRIAC_PROG,
    climate_bounds=(7.0, 35.0),
    max_limit_bounds=(16.0, 35.0),
    boost_bounds=(5.0, 35.0),
    boost_cap=1440,
    boost_params=frozenset({2}),
    duty_options=(),
    max_limit=False,
    storage_ui=False,
    surface="thermostat",
)
THM_28 = Expectation(
    node_type="thm",
    min_c=7.0,
    max_c=35.0,
    profiles=BASE,
    boost_configs=ALL_CONFIGS,
    easy=True,
    runback_configs=ALL_CONFIGS,
    lock="bool",
    prog_bounds=TRIAC_PROG,
    climate_bounds=(7.0, 35.0),
    max_limit_bounds=(16.0, 35.0),
    boost_bounds=(5.0, 35.0),
    boost_cap=1440,
    boost_params=frozenset({2, 3}),
    duty_options=(),
    max_limit=False,
    storage_ui=False,
    surface="heater",
)
POWER = Expectation(
    node_type="pmo",
    min_c=None,
    max_c=None,
    profiles=None,
    boost_configs=frozenset(),
    easy=False,
    runback_configs=frozenset(),
    lock="bool",
    prog_bounds=None,
    climate_bounds=None,
    max_limit_bounds=None,
    boost_bounds=None,
    boost_cap=None,
    boost_params=frozenset({2}),
    duty_options=(),
    max_limit=False,
    storage_ui=False,
    surface="power",
)
CLIMASTAR_WIFI = Expectation(
    node_type="htr",
    min_c=0.0,
    max_c=30.0,
    profiles=BASE,
    boost_configs=frozenset({2}),
    easy=False,
    runback_configs=frozenset(),
    lock="bool",
    prog_bounds=BASE_PROG,
    climate_bounds=(1.0, 30.0),
    max_limit_bounds=(16.0, 30.0),
    boost_bounds=(5.0, 30.0),
    boost_cap=300,
    boost_params=frozenset({2}),
    duty_options=(),
    max_limit=True,
    storage_ui=False,
    surface="heater",
)
TEVOLVE_RELAY = Expectation(
    node_type="htr",
    min_c=0.0,
    max_c=35.0,
    profiles=BASE,
    boost_configs=frozenset(),
    easy=False,
    runback_configs=frozenset(),
    lock="bool",
    prog_bounds=RELAY_PROG,
    climate_bounds=(1.0, 35.0),
    max_limit_bounds=(16.0, 35.0),
    boost_bounds=(5.0, 35.0),
    boost_cap=1440,
    boost_params=frozenset({2}),
    duty_options=(),
    max_limit=True,
    storage_ui=False,
    surface="heater",
)

# EProductCodeValue: 51 entries, all explicit.
CODES: dict[str, Expectation] = {
    "00": BASE_ROW,
    "01": BASE_ROW,
    "02": BASE_ROW,
    "03": POWER,
    "04": THERMOSTAT,
    "07": BASE_ROW,
    "08": BASE_ROW,
    "09": THERMOSTAT,
    "0A": HTR_MOD_ROW,
    "0B": BASE_ROW,
    "0C": BASE_ROW,
    "0D": BASE_ROW,
    "0F": THERMOSTAT,
    "10": BASE_ROW,
    "11": BASE_ROW,
    "12": BASE_ROW,
    "13": BASE_ROW,
    "14": STORAGE_ELNUR,
    "16": BASE_ROW,
    "17": HAVERLAND,
    "18": BASE_ROW,
    "1A": TEVOLVE_TRIAC,
    "1B": BASE_ROW,
    "1C": STORAGE_HELKI,
    "1D": HAVERLAND_WIFI,
    "1E": TEVOLVE_RELAY,
    "1F": BASE_ROW,
    "20": STORAGE_ELNUR,
    "21": TEVOLVE_TRIAC,
    "22": POWER,
    "23": TEVOLVE_TRIAC,
    "24": BASE_ROW,
    "25": STORAGE_ELNUR,
    "27": Expectation(
        node_type="htr",
        min_c=7.0,
        max_c=35.0,
        profiles=BASE,
        boost_configs=frozenset(),
        easy=True,
        runback_configs=frozenset(),
        lock="bool",
        prog_bounds=PIR_PROG,
        climate_bounds=(7.0, 35.0),
        max_limit_bounds=(16.0, 35.0),
        boost_bounds=(5.0, 35.0),
        boost_cap=1440,
        boost_params=frozenset({2}),
        duty_options=(),
        max_limit=False,
        storage_ui=False,
        surface="heater",
        ice_editable=False,
    ),
    "28": THM_28,
    "2A": WATER,
    "2E": STORAGE_HELKI,
    "31": TEVOLVE_WIFI,
    "32": HELKI_HEATER,
    "33": LHZ_WIFI,
    "34": TEVOLVE_WIFI,
    "35": LHZ_WIFI,
    "36": LHZ_RELAY,
    "38": LHZ_WIFI,
    "39": TEVOLVE_WIFI,
    "3D": CLIMASTAR_WIFI,
    "43": BASE_ROW,
    "47": HAVERLAND_WIFI_B,
    "4C": TEVOLVE_ATC,
    "4D": TEVOLVE_ATC,
    "FF": HTR_MOD_ROW,
}


def _pid(code: str) -> str:
    return f"08{code}0000"


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


def _node(row: Expectation, pid: str):
    return SimpleNamespace(
        node_type=row.node_type,
        pid=pid,
        setup=_setup(),
        version={"fw_version": "1.9"},
    )


def test_the_table_covers_every_decoded_product_code() -> None:
    """The 2.54.2 `EProductCodeValue` enum has exactly 51 entries."""
    assert len(CODES) == 51
    assert set(CODES) == {
        "00",
        "01",
        "02",
        "03",
        "04",
        "07",
        "08",
        "09",
        "0A",
        "0B",
        "0C",
        "0D",
        "0F",
        "10",
        "11",
        "12",
        "13",
        "14",
        "16",
        "17",
        "18",
        "1A",
        "1B",
        "1C",
        "1D",
        "1E",
        "1F",
        "20",
        "21",
        "22",
        "23",
        "24",
        "25",
        "27",
        "28",
        "2A",
        "2E",
        "31",
        "32",
        "33",
        "34",
        "35",
        "36",
        "38",
        "39",
        "3D",
        "43",
        "47",
        "4C",
        "4D",
        "FF",
    }


@pytest.mark.parametrize("code", sorted(CODES))
def test_product_code_parity_row(code: str) -> None:
    """One code's family, bounds, profiles, capabilities and setting gates."""
    row = CODES[code]
    pid = _pid(code)

    assert is_heater_surface(row.node_type, pid) is (row.surface == "heater")
    assert is_thermostat_surface(row.node_type, pid) is (row.surface == "thermostat")
    assert is_water_storage(pid) is row.water
    assert is_elnur_storage(pid) is row.storage_ui

    if row.profiles is not None:
        # Helki storage reads its bounds from the node's setup; an empty setup
        # exercises the factory fallback the row records.
        limits_setup = {} if code in {"1C", "2E"} else _setup()
        limits = controller_limits(row.node_type, pid, "1.9", limits_setup)
        assert limits.min_c == row.min_c
        assert limits.max_c == row.max_c
        assert limits.profiles == row.profiles
        assert prog_bounds_c(limits) == row.prog_bounds
        if row.ice_editable:
            assert profile_bounds(limits, "frost") is not None
        else:
            assert profile_bounds(limits, "frost") is None
        assert climate_bounds(limits, None) == row.climate_bounds
        assert max_limit_bounds(limits) == row.max_limit_bounds
        assert boost_temperature_bounds(limits) == row.boost_bounds
        assert limits.boost_max_minutes == row.boost_cap

    for config in (0, 1, 2, 3):
        setup = _setup()
        setup["factory_options"] = {
            **setup["factory_options"],
            "boost_config": config,
        }
        assert boost_available(
            setup, node_type=row.node_type, pid=pid, fw_version="1.9"
        ) is (config in row.boost_configs)
        assert supports_runback(pid, setup) is (config in row.runback_configs)
        assert boost_params_editable(row.node_type, pid, setup) is (
            config in row.boost_params
        )

    assert supports_easy(pid) is row.easy
    assert lock_kind(row.node_type, pid, "1.9", _setup()) == row.lock
    assert duty_limit_options(_setup(), pid) == row.duty_options
    assert max_temp_limit_available(_node(row, pid)) is row.max_limit


def test_thm_28_no_probes_removes_boost_and_probe_settings() -> None:
    """TevolveThermostat's timer mode drops BOOST, EASY and RUNBACK."""
    setup = _setup()
    setup["extra_options"] = {"sensor_mode": 3}
    setup["factory_options"] = {
        **setup["factory_options"],
        "boost_config": 2,
    }
    pid = _pid("28")
    assert boost_available(setup, node_type="thm", pid=pid, fw_version="1.9") is False
    assert boost_params_editable("thm", pid, setup) is False
    assert supports_easy(pid, setup) is False
    assert supports_runback(pid, setup) is False

    setup["extra_options"] = {"sensor_mode": 0}
    assert boost_available(setup, node_type="thm", pid=pid, fw_version="1.9") is True
    assert supports_easy(pid, setup) is True
    assert supports_runback(pid, setup) is True


def test_max_stemp_limit_clamps_the_climate_maximum() -> None:
    """A stored limit below the controller maximum caps the climate setpoint."""
    limits = controller_limits("htr", _pid("1A"), "1.9", _setup())
    assert climate_bounds(limits, 30.0) == (7.0, 30.0)
    assert climate_bounds(limits, None) == (7.0, 35.0)


def test_helki_storage_bounds_come_from_the_setup() -> None:
    """Storage reads min_stemp/max_stemp; unusable values keep the factory pair."""
    limits = controller_limits(
        "acm", _pid("1C"), "1.9", {"min_stemp": "4", "max_stemp": "26"}
    )
    assert (limits.min_c, limits.max_c) == (4.0, 26.0)
    fallback = controller_limits(
        "acm", _pid("1C"), "1.9", {"min_stemp": "x", "max_stemp": None}
    )
    assert (fallback.min_c, fallback.max_c) == (5.0, 30.0)


def test_the_off_slot_has_no_editable_range() -> None:
    """The OFF programme slot is a switch, not a temperature to edit."""
    limits = controller_limits("htr", _pid("32"), "1.9", _setup())
    assert profile_bounds(limits, "off") is None


def test_legacy_boost_path_is_the_triac_and_haverland_fixed_time_families() -> None:
    """Triac always uses it; Haverland fixed-time uses it below config 3."""
    legacy = {code for code in CODES if boost_uses_legacy_path(_pid(code))}
    assert legacy == {"1A", "21", "23", "28", "17", "1D", "47"}
    full = {"factory_options": {"boost_config": 3}}
    for code in ("17", "1D", "47"):
        assert boost_uses_legacy_path(_pid(code), full) is False


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("1A", True),
        ("27", False),
        ("28", True),
        ("36", True),
        ("1C", True),
        ("14", False),
        ("2A", False),
        ("32", False),
    ],
)
def test_boost_parameters_default_to_set_temp_and_time_when_absent(
    code: str, expected: bool
) -> None:
    """A setup without boost_config falls back per controller family."""
    assert (
        boost_params_editable(CODES[code].node_type, _pid(code), {"units": "C"})
        is expected
    )


def test_firmware_only_changes_the_decoded_lock_and_storage_profile_gates() -> None:
    """The fw-1.6 baseline differs from 1.9 on the integer-lock and storage rows."""
    changed = {
        code
        for code in CODES
        if lock_kind(CODES[code].node_type, _pid(code), "1.6.0")
        != lock_kind(CODES[code].node_type, _pid(code), "1.9")
    }
    assert changed == {"1D"}
    assert lock_kind("htr", _pid("1D"), "1.6.0") == "bool"
    super_lock = {"factory_options": {"super_lock_available": True}}
    for code in ("32", "33", "35", "36", "38"):
        assert lock_kind("htr", _pid(code), "1.6.0", super_lock) == "int"
        assert lock_kind("htr", _pid(code), "1.9", super_lock) == "int"

    for code in ("1C", "2E"):
        old = controller_limits("acm", _pid(code), "1.7.9", {})
        new = controller_limits("acm", _pid(code), "1.8.0", {})
        assert old.profiles == BASE
        assert new.profiles == WITH_OFF
        assert old.min_c == new.min_c == 5.0
        assert old.max_c == new.max_c == 30.0


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        (None, False),
        ({}, False),
        ({"extra_options": "x"}, False),
        ({"extra_options": {"sensor_mode": 0}}, False),
        ({"extra_options": {"sensor_mode": 2}}, False),
        ({"extra_options": {"sensor_mode": 3}}, True),
        ({"extra_options": {"sensor_mode": "3"}}, False),
        ({"extra_options": {"sensor_mode": True}}, False),
    ],
)
def test_no_probes_gate_reads_only_the_sensor_mode_enum(
    setup: dict[str, object] | None, expected: bool
) -> None:
    """NO_PROBES hides temperature control; strings and booleans are not the enum."""
    assert has_no_probes(setup) is expected
