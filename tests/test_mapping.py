"""Pure mapping-function tables.

Plain pytest, no HA harness: the functions under test are HA-free except
the climate enums. Raise-path rows assert exception TYPES, never
messages.
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging

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
import pytest

from custom_components.smartbox.const import (
    PRESET_FROST,
    PRESET_SCHEDULE,
    PRESET_SELF_LEARN,
    SmartboxNodeType,
)
from custom_components.smartbox.mapping import (
    AutoOverrideWrite,
    HtrModWrite,
    ModeWrite,
    SetpointWrite,
    _warn_unknown,
    get_boost_end_datetime,
    get_current_prog_profile,
    get_hvac_mode,
    get_next_prog_change,
    get_temperature_unit,
    htr_mod_policy,
    htr_mod_setpoint_limits,
    hvac_mode_write,
    preset_mode_write,
    rtc_time_to_datetime,
    temperature_write,
)

# --- temperature_write ---


def test_temperature_write_is_a_plain_setpoint_by_default():
    """The setpoint VALUE stays out of the intent: the write path formats it.

    The intent names the operation and the scale; ``values.format_setpoint``
    owns the grid (pinned in tests/test_values.py, the wire bodies in
    tests/test_writes.py).
    """
    assert temperature_write(SmartboxNodeType.HTR, {"units": "C"}, 21.3) == (
        SetpointWrite(units="C")
    )
    assert temperature_write(SmartboxNodeType.ACM, {"units": "F"}, 78) == (
        SetpointWrite(units="F")
    )


@pytest.mark.parametrize(
    ("node_type", "status"),
    [
        (SmartboxNodeType.HTR, {}),
        (SmartboxNodeType.ACM, {}),
        (SmartboxNodeType.HTR_MOD, {"selected_temp": "comfort"}),
    ],
)
def test_temperature_write_missing_units_raises(node_type, status):
    with pytest.raises(KeyError):
        temperature_write(node_type, status, 24.7)


def test_temperature_write_htr_mod_whole_degree_comfort():
    """The htr_mod grid is whole degrees: 20.5 lands on 21 (app parity)."""
    assert temperature_write(
        SmartboxNodeType.HTR_MOD,
        {
            "mode": "manual",
            "selected_temp": "comfort",
            "comfort_temp": "22.0",
            "eco_offset": "4.0",
            "units": "C",
        },
        20.5,
    ) == HtrModWrite(
        on=True,
        mode="manual",
        selected_temp="comfort",
        comfort_temp=21.0,
        eco_offset="4.0",
        units="C",
    )


def test_temperature_write_htr_mod_recomposes_the_eco_offset():
    """A target below 18 C stays on the comfort profile, eco offset widened."""
    assert temperature_write(
        SmartboxNodeType.HTR_MOD,
        {
            "mode": "manual",
            "selected_temp": "comfort",
            "comfort_temp": "22.0",
            "eco_offset": "4.0",
            "units": "C",
        },
        17.0,
    ) == HtrModWrite(
        on=True,
        mode="manual",
        selected_temp="eco",
        comfort_temp=22.0,
        eco_offset="5.0",
        units="C",
    )


def test_temperature_write_htr_mod_walks_the_comfort_down():
    """A 10 C gap exceeds the 8 C ceiling, so comfort drops to 20."""
    assert temperature_write(
        SmartboxNodeType.HTR_MOD,
        {
            "mode": "manual",
            "selected_temp": "comfort",
            "comfort_temp": "22.0",
            "eco_offset": "4.0",
            "units": "C",
        },
        12.0,
    ) == HtrModWrite(
        on=True,
        mode="manual",
        selected_temp="eco",
        comfort_temp=20.0,
        eco_offset="8.0",
        units="C",
    )


def test_temperature_write_htr_mod_walks_the_comfort_up():
    """Comfort 18 cannot sit 4 C above 17, so it rises to 21."""
    assert temperature_write(
        SmartboxNodeType.HTR_MOD,
        {
            "mode": "manual",
            "selected_temp": "comfort",
            "comfort_temp": "18.0",
            "eco_offset": "4.0",
            "units": "C",
        },
        17.0,
    ) == HtrModWrite(
        on=True,
        mode="manual",
        selected_temp="eco",
        comfort_temp=21.0,
        eco_offset="4.0",
        units="C",
    )


def test_temperature_write_htr_mod_18c_is_an_eco_setpoint():
    """The app's eco ceiling is 18 C, so 18 itself maps to eco."""
    assert temperature_write(
        SmartboxNodeType.HTR_MOD,
        {
            "mode": "manual",
            "selected_temp": "comfort",
            "comfort_temp": "22.0",
            "eco_offset": "4.0",
            "units": "C",
        },
        18.0,
    ) == HtrModWrite(
        on=True,
        mode="manual",
        selected_temp="eco",
        comfort_temp=22.0,
        eco_offset="4.0",
        units="C",
    )


def test_temperature_write_htr_mod_other_product_keeps_the_legacy_body():
    """Only the two Haverland product codes get the app's limits."""
    assert temperature_write(
        SmartboxNodeType.HTR_MOD,
        {
            "mode": "auto",
            "selected_temp": "eco",
            "comfort_temp": "17.2",
            "eco_offset": "4",
            "units": "C",
        },
        14.2,
        model_code="1C",
    ) == HtrModWrite(
        on=True,
        mode="manual",
        selected_temp="eco",
        comfort_temp=18.2,
        eco_offset="4",
        units="C",
    )


def test_temperature_write_htr_mod_fahrenheit_keeps_the_legacy_body():
    """The app constants are Celsius; F nodes keep the pass-through body."""
    assert temperature_write(
        SmartboxNodeType.HTR_MOD,
        {
            "mode": "auto",
            "selected_temp": "eco",
            "comfort_temp": "71.6",
            "eco_offset": "4",
            "units": "F",
        },
        60.0,
    ) == HtrModWrite(
        on=True,
        mode="manual",
        selected_temp="eco",
        comfort_temp=64.0,
        eco_offset="4",
        units="F",
    )


def test_temperature_write_htr_mod_setpoint_forces_manual():
    """The app's setpoint builder leaves the programme: mode is always manual."""
    assert temperature_write(
        SmartboxNodeType.HTR_MOD,
        {
            "mode": "auto",
            "selected_temp": "comfort",
            "comfort_temp": "21.0",
            "eco_offset": "4.0",
            "units": "C",
        },
        22.0,
    ) == HtrModWrite(
        on=True,
        mode="manual",
        selected_temp="comfort",
        comfort_temp=22.0,
        eco_offset="4.0",
        units="C",
    )


@pytest.mark.parametrize("model_code", ["0A", "ff", "FF", None])
def test_htr_mod_policy_is_the_haverland_one(model_code):
    """Both Haverland product codes, and an unread version, get the policy."""
    policy = htr_mod_policy(model_code)
    assert policy is not None
    assert policy.step_c == 1.0
    assert policy.comfort_c == (18.0, 28.0)
    assert policy.eco_max_c == 18.0
    assert policy.eco_min_c == 10.0  # comfort floor - largest offset
    assert policy.offset_c == (4, 8)


def test_htr_mod_policy_dispatches_on_the_product_code():
    # The app routes both Haverland codes to the same controller class.
    assert htr_mod_policy("0A") is htr_mod_policy("ff")
    assert htr_mod_policy("1C") is None
    assert htr_mod_policy("17") is None


def test_htr_mod_setpoint_limits():
    policy = htr_mod_policy(None)
    assert policy is not None
    # Comfort and eco share the union: the builder picks the profile from the
    # target, so the 18 C boundary is crossable from either one.
    assert htr_mod_setpoint_limits({"selected_temp": "comfort"}, policy) == (
        10.0,
        28.0,
    )
    assert htr_mod_setpoint_limits({"selected_temp": "eco"}, policy) == (10.0, 28.0)
    assert htr_mod_setpoint_limits(
        {"selected_temp": "ice", "ice_temp": "7"}, policy
    ) == (
        7.0,
        7.0,
    )
    assert htr_mod_setpoint_limits({"selected_temp": "off"}, policy) is None
    assert htr_mod_setpoint_limits({}, policy) is None


def test_htr_mod_setpoint_limits_convert_for_fahrenheit_nodes():
    """The bounds sit on the integer Fahrenheit grid the wire stores."""
    policy = htr_mod_policy(None)
    assert policy is not None
    limits = htr_mod_setpoint_limits({"selected_temp": "comfort", "units": "F"}, policy)
    assert limits is not None
    assert limits == (50.0, 82.0)


def test_temperature_write_htr_mod_ice_mode_raises():
    with pytest.raises(ValueError):
        temperature_write(
            SmartboxNodeType.HTR_MOD,
            {"mode": "auto", "selected_temp": "ice", "ice_temp": "7", "units": "C"},
            7,
        )


@pytest.mark.parametrize("temp", [29.0, 9.0])
def test_temperature_write_htr_mod_rejects_targets_outside_the_policy(temp):
    """The policy's range is the only range the app can place."""
    with pytest.raises(ValueError, match="accepts 10-28"):
        temperature_write(
            SmartboxNodeType.HTR_MOD,
            {
                "mode": "manual",
                "selected_temp": "comfort",
                "comfort_temp": "22.0",
                "eco_offset": "4.0",
                "units": "C",
            },
            temp,
        )


def test_temperature_write_htr_mod_snaps_an_off_grid_stored_comfort():
    """The app compares the raw comfort and fails; the walk uses whole degrees."""
    assert temperature_write(
        SmartboxNodeType.HTR_MOD,
        {
            "mode": "manual",
            "selected_temp": "eco",
            "comfort_temp": "22.5",
            "eco_offset": "4.0",
            "units": "C",
        },
        17.0,
    ) == HtrModWrite(
        on=True,
        mode="manual",
        selected_temp="eco",
        comfort_temp=23.0,
        eco_offset="6.0",
        units="C",
    )


def test_temperature_write_htr_mod_comfort_needs_the_stored_offset():
    with pytest.raises(KeyError, match="eco_offset"):
        temperature_write(
            SmartboxNodeType.HTR_MOD,
            {
                "mode": "manual",
                "selected_temp": "comfort",
                "comfort_temp": "22.0",
                "units": "C",
            },
            21.0,
        )


@pytest.mark.parametrize(
    ("status", "key", "model_code"),
    [
        (
            {
                "mode": "auto",
                "selected_temp": "eco",
                "comfort_temp": "17.2",
                "units": "C",
            },
            "eco_offset",
            "1C",
        ),
        (
            {
                "mode": "auto",
                "selected_temp": "blah",
                "comfort_temp": "17.2",
                "units": "C",
            },
            None,
            None,
        ),
    ],
)
def test_temperature_write_htr_mod_missing_keys_raise(status, key, model_code):
    # None pins only the exception type - the message wording is not contract
    with pytest.raises(KeyError, match=key):
        temperature_write(SmartboxNodeType.HTR_MOD, status, 17.2, model_code=model_code)


def test_temperature_write_modified_auto_engagement():
    # E5: setpoint inside the running programme engages modified_auto when
    # the setup advertises the feature and the mode is plain auto
    assert temperature_write(
        SmartboxNodeType.HTR,
        {"units": "C", "mode": "auto"},
        21.5,
        {"modified_auto_span": 6},
    ) == AutoOverrideWrite(units="C")
    for mode in ("manual", "off", "modified_auto"):
        assert temperature_write(
            SmartboxNodeType.HTR,
            {"units": "C", "mode": mode},
            21.5,
            {"modified_auto_span": 6},
        ) == SetpointWrite(units="C")
    assert temperature_write(
        SmartboxNodeType.HTR,
        {"units": "C", "mode": "auto"},
        21.5,
        {},
    ) == SetpointWrite(units="C")
    assert temperature_write(
        SmartboxNodeType.HTR,
        {"units": "C", "mode": "auto"},
        21.5,
    ) == SetpointWrite(units="C")


# --- hvac_mode_write ---


@pytest.mark.parametrize("node_type", [SmartboxNodeType.HTR, SmartboxNodeType.HTR_MOD])
def test_hvac_mode_write_invalid_hvac_mode_raises(node_type):
    with pytest.raises(ValueError):
        hvac_mode_write(node_type, {}, "blah")


def test_hvac_mode_write_tables():
    assert hvac_mode_write(SmartboxNodeType.HTR, {}, HVACMode.OFF) == ModeWrite(
        mode="off"
    )
    assert hvac_mode_write(SmartboxNodeType.ACM, {}, HVACMode.AUTO) == ModeWrite(
        mode="auto"
    )
    assert hvac_mode_write(SmartboxNodeType.HTR, {}, HVACMode.HEAT) == ModeWrite(
        mode="manual"
    )
    assert hvac_mode_write(SmartboxNodeType.HTR_MOD, {}, HVACMode.OFF) == HtrModWrite(
        on=False
    )
    assert hvac_mode_write(SmartboxNodeType.HTR_MOD, {}, HVACMode.AUTO) == HtrModWrite(
        on=True,
        mode="auto",
    )
    assert hvac_mode_write(
        SmartboxNodeType.HTR_MOD,
        {"selected_temp": "comfort"},
        HVACMode.HEAT,
    ) == HtrModWrite(on=True, mode="manual", selected_temp="comfort")
    # HEAT on htr_mod needs the selected_temp status key
    with pytest.raises(KeyError):
        hvac_mode_write(SmartboxNodeType.HTR_MOD, {}, HVACMode.HEAT)


# --- preset_mode_write ---


def test_preset_mode_write_htr_mod_table():
    assert preset_mode_write(
        SmartboxNodeType.HTR_MOD, {}, PRESET_SCHEDULE
    ) == HtrModWrite(on=True, mode="auto")
    assert preset_mode_write(
        SmartboxNodeType.HTR_MOD, {}, PRESET_SELF_LEARN
    ) == HtrModWrite(on=True, mode="self_learn")
    assert preset_mode_write(
        SmartboxNodeType.HTR_MOD, {}, PRESET_ACTIVITY
    ) == HtrModWrite(on=True, mode="presence")
    assert preset_mode_write(
        SmartboxNodeType.HTR_MOD, {}, PRESET_COMFORT
    ) == HtrModWrite(on=True, mode="manual", selected_temp="comfort")
    assert preset_mode_write(SmartboxNodeType.HTR_MOD, {}, PRESET_ECO) == HtrModWrite(
        on=True, mode="manual", selected_temp="eco"
    )
    assert preset_mode_write(SmartboxNodeType.HTR_MOD, {}, PRESET_FROST) == HtrModWrite(
        on=True, mode="manual", selected_temp="ice"
    )


@pytest.mark.parametrize(
    ("node_type", "preset"),
    [
        (SmartboxNodeType.HTR, PRESET_SCHEDULE),
        (SmartboxNodeType.ACM, PRESET_ACTIVITY),
        (SmartboxNodeType.PMO, PRESET_ECO),
        (SmartboxNodeType.HTR_MOD, "fake_preset"),
    ],
)
def test_preset_mode_write_unsupported_raises(node_type, preset):
    with pytest.raises(ValueError):
        preset_mode_write(node_type, {}, preset)


@pytest.mark.parametrize("preset", [PRESET_HOME, PRESET_AWAY, PRESET_NONE])
def test_preset_mode_write_registry_only_presets_raise(preset):
    # HOME/AWAY/NONE are handled outside the status-update path
    with pytest.raises(ValueError, match="registry-only"):
        preset_mode_write(SmartboxNodeType.HTR_MOD, {}, preset)


# --- get_hvac_mode / get_temperature_unit: literal tables ---


@pytest.mark.parametrize(
    ("node_type", "status", "expected"),
    [
        (SmartboxNodeType.HTR, {"mode": "off"}, HVACMode.OFF),
        (SmartboxNodeType.HTR, {"mode": "manual"}, HVACMode.HEAT),
        (SmartboxNodeType.HTR, {"mode": "auto"}, HVACMode.AUTO),
        (SmartboxNodeType.HTR, {"mode": "modified_auto"}, HVACMode.AUTO),
        (SmartboxNodeType.ACM, {"mode": "auto"}, HVACMode.AUTO),
        (SmartboxNodeType.ACM, {"mode": "manual"}, HVACMode.HEAT),
        # boost wins over the stored mode (the node is heating right now)
        (SmartboxNodeType.HTR, {"mode": "off", "boost": True}, HVACMode.HEAT),
        (SmartboxNodeType.HTR_MOD, {"on": True, "mode": "auto"}, HVACMode.AUTO),
        (SmartboxNodeType.HTR_MOD, {"on": True, "mode": "manual"}, HVACMode.HEAT),
        (SmartboxNodeType.HTR_MOD, {"on": True, "mode": "self_learn"}, HVACMode.AUTO),
        (SmartboxNodeType.HTR_MOD, {"on": True, "mode": "presence"}, HVACMode.AUTO),
        (SmartboxNodeType.HTR_MOD, {"on": False, "mode": "auto"}, HVACMode.OFF),
        (SmartboxNodeType.HTR_MOD, {"on": False, "mode": "manual"}, HVACMode.OFF),
    ],
)
def test_get_hvac_mode_literal_table(node_type, status, expected):
    """Literal per-branch expectations.

    ``tests/test_climate.py`` compares the entity state against this same
    function, so only a table like this one protects the wire-to-mode
    mapping itself.
    """
    assert get_hvac_mode(node_type, status) is expected


def test_get_hvac_mode_unknown_mode_degrades(caplog):
    """An unmodelled mode renders unknown and warns only once."""
    _warn_unknown.cache_clear()
    with caplog.at_level(logging.WARNING, logger="custom_components.smartbox.mapping"):
        assert get_hvac_mode(SmartboxNodeType.HTR, {"mode": "blah"}) is None
        assert get_hvac_mode(SmartboxNodeType.HTR, {"mode": "blah"}) is None
    assert caplog.text.count("Unknown smartbox node mode 'blah'") == 1


def test_get_hvac_mode_ill_typed_mode_degrades(caplog):
    """A non-string mode renders unknown instead of raising from the property."""
    _warn_unknown.cache_clear()
    with caplog.at_level(logging.WARNING, logger="custom_components.smartbox.mapping"):
        assert get_hvac_mode(SmartboxNodeType.HTR, {"mode": ["bogus"]}) is None
    assert "Unknown smartbox node mode ['bogus']" in caplog.text


@pytest.mark.parametrize("mode", ["easy", "setback"])
def test_get_hvac_mode_known_vendor_modes_degrade(mode):
    """Modes the app knows but HA does not model stay usable."""
    assert get_hvac_mode(SmartboxNodeType.HTR, {"mode": mode}) is HVACMode.HEAT


def test_get_temperature_unit_literal_table():
    assert get_temperature_unit({"units": "C"}) is UnitOfTemperature.CELSIUS
    assert get_temperature_unit({"units": "F"}) is UnitOfTemperature.FAHRENHEIT
    assert get_temperature_unit({}) is None
    # malformed server data reads as unknown; entity properties must not raise
    assert get_temperature_unit({"units": "K"}) is None
    assert get_temperature_unit({"units": 7}) is None


# --- get_current_prog_profile / get_next_prog_change boundaries ---


def _prog(day: int, slots: list[int]) -> dict[str, list[int]]:
    return {str(day): slots}


def test_current_prog_profile_boundaries():
    zone = dt_util.DEFAULT_TIME_ZONE
    now = datetime(2023, 10, 10, 1, 30, tzinfo=zone)  # Tuesday
    day = _prog(2, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])  # Tue = key 2
    # 01:30 falls in slot 0 -> its profile wins
    assert get_current_prog_profile(day, now) == 1
    # the slot's last second still applies
    now_prev = datetime(2023, 10, 10, 1, 59, 59, tzinfo=zone)
    assert get_current_prog_profile(day, now_prev) == 1
    # exactly on the boundary the next slot starts
    assert get_current_prog_profile(day, datetime(2023, 10, 10, 2, 0, tzinfo=zone)) == 2
    # the day's final slot runs to 24:00
    assert (
        get_current_prog_profile(day, datetime(2023, 10, 10, 23, 59, tzinfo=zone)) == 12
    )
    # hourly slots: hour 1 -> index 1
    assert get_current_prog_profile(_prog(2, list(range(24))), now) == 1
    # no schedule for today -> None
    assert get_current_prog_profile(_prog(0, [0]), now) is None
    assert get_current_prog_profile({}, now) is None
    # degenerate day arrays: longer than a day, or empty -> None
    assert get_current_prog_profile(_prog(2, list(range(1441))), now) is None
    assert get_current_prog_profile(_prog(2, []), now) is None


def test_current_prog_profile_uses_sunday_first_weekday_keys():
    """Day key 0 is Sunday, key 1 Monday, key 6 Saturday."""
    zone = dt_util.DEFAULT_TIME_ZONE
    prog = {"0": [0] * 24, "1": [1] * 24, "6": [2] * 24}
    assert (
        get_current_prog_profile(prog, datetime(2026, 2, 16, 12, 0, tzinfo=zone)) == 1
    )  # Monday
    assert (
        get_current_prog_profile(prog, datetime(2026, 2, 21, 12, 0, tzinfo=zone)) == 2
    )  # Saturday
    assert (
        get_current_prog_profile(prog, datetime(2026, 2, 22, 12, 0, tzinfo=zone)) == 0
    )  # Sunday


def test_next_prog_change_boundaries():
    zone = dt_util.DEFAULT_TIME_ZONE
    prog = _prog(2, [1] * 12 + [2] * 12)  # Tuesday, change at 12:00 local
    now = datetime(2023, 10, 10, 1, 30, tzinfo=zone)
    change = get_next_prog_change(prog, now)
    assert change == datetime(2023, 10, 10, 12, 0, tzinfo=zone)
    # Tuesday evening: no differing slot left today or until next Tuesday
    now_evening = datetime(2023, 10, 10, 13, 0, tzinfo=zone)
    assert get_next_prog_change(prog, now_evening) == datetime(
        2023,
        10,
        17,
        0,
        0,
        tzinfo=zone,
    )
    # a programme that never changes -> None
    uniform = {str(d): [1] * 24 for d in range(7)}
    assert get_next_prog_change(uniform, now) is None
    # empty programme -> None
    assert get_next_prog_change({}, now) is None


# --- rtc_time_to_datetime ---


def test_rtc_time_to_datetime_september_is_zero_indexed():
    # live-verified wire shape: September reports n=8
    rtc = {"d": 27, "h": 9, "m": 55, "n": 8, "s": 24, "w": 0, "y": 2026}
    assert rtc_time_to_datetime(rtc) == datetime(
        2026,
        9,
        27,
        9,
        55,
        24,
        tzinfo=dt_util.DEFAULT_TIME_ZONE,
    )


def test_rtc_time_to_datetime_december_unconfirmed_indexing():
    # n=11 -> December under the 0-indexed reading; a 1-indexed 12 fails
    # to parse and yields None (recorded Phase-4 open item)
    rtc = {"d": 31, "h": 23, "m": 59, "n": 11, "s": 59, "w": 0, "y": 2026}
    assert rtc_time_to_datetime(rtc).month == 12
    assert rtc_time_to_datetime({**rtc, "n": 12}) is None


@pytest.mark.parametrize(
    "rtc",
    [
        None,
        {},
        {"y": 2026},  # missing keys
        {"d": 27, "h": 9, "m": 55, "n": 13, "s": 24, "y": 2026},  # month 14
        {"d": 31, "h": 9, "m": 55, "n": 1, "s": 24, "y": 2026},  # Feb 31st
        {"d": "x", "h": 9, "m": 55, "n": 8, "s": 24, "y": 2026},
    ],
)
def test_rtc_time_to_datetime_malformed_returns_none(rtc):
    assert rtc_time_to_datetime(rtc) is None


# --- get_boost_end_datetime ---


@pytest.fixture
def fixed_utcnow(monkeypatch):
    fixed = datetime(2023, 10, 10, 1, 0, tzinfo=UTC)
    monkeypatch.setattr(dt_util, "utcnow", lambda: fixed)
    return fixed


def test_boost_end_datetime_later_today(fixed_utcnow):
    boost_end = get_boost_end_datetime(90)  # 01:30 UTC
    assert boost_end == datetime(2023, 10, 10, 1, 30, tzinfo=UTC)


def test_boost_end_datetime_already_passed_rolls_to_tomorrow(fixed_utcnow):
    boost_end = get_boost_end_datetime(30)  # 00:30 UTC, already past
    assert boost_end == datetime(2023, 10, 11, 0, 30, tzinfo=UTC)


def test_boost_end_datetime_exactly_now_rolls_to_tomorrow(fixed_utcnow):
    boost_end = get_boost_end_datetime(60)  # 01:00 UTC == now
    assert boost_end == datetime(2023, 10, 11, 1, 0, tzinfo=UTC)


def test_boost_end_datetime_is_local(fixed_utcnow):
    assert get_boost_end_datetime(90).tzinfo is dt_util.DEFAULT_TIME_ZONE
    # malformed / out-of-range minute values render unknown, never raise
    assert get_boost_end_datetime("garbage") is None
    assert get_boost_end_datetime(-1) is None
    assert get_boost_end_datetime(1440) is None
