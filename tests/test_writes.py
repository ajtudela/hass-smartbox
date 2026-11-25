"""Tests for the named write operations (``smartbox.protocol.writes``).

The HTTP layer is a scripted FakeSession (shared in ``fakes.py``); each
op test asserts the exact POST body, the path, the single-attempt
plumbing (no retry on 5xx) and the 401 -> one-resend behaviour.
"""

from __future__ import annotations

import logging

import aiohttp
import pytest
from smartbox.protocol.auth import AuthConfig
from smartbox.protocol.client import NodeRef, SmartboxClient
from smartbox.protocol.errors import AuthError, SmartboxError, TransportUnavailableError
from smartbox.protocol.writes import WriteApi

from tests.fakes import FakeResponse, FakeSession, SleepController, make_jwt

API_HOST = "https://api.example.test"

CONFIG = AuthConfig(
    api_host=API_HOST,
    basic_auth_creds="cmVzZWxsZXJfc2VjcmV0",
    serial_id="1234",
    username="user@example.test",
    password="hunter2",
)

JWT_U1 = make_jwt({"userId": "u1", "exp": 9999999999})
JWT_U2 = make_jwt({"userId": "u2", "exp": 9999999999})

GRANT_U1 = {"access_token": JWT_U1, "refresh_token": "rt1", "expires_in": 14400}
GRANT_U2 = {"access_token": JWT_U2, "refresh_token": "rt2", "expires_in": 14400}

NODE = NodeRef(node_type="htr", addr="2")
STATUS_PATH = "devs/dev1/htr/2/status"
SETUP_PATH = "devs/dev1/htr/2/setup"
PROG_PATH = "devs/dev1/htr/2/prog"

SLOTS = list(range(24))


@pytest.fixture
def sleeps(monkeypatch):
    return SleepController()  # writes never sleep; wired in for parity


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def client(session) -> SmartboxClient:
    return SmartboxClient(CONFIG, session=session)


@pytest.fixture
def writes(client) -> WriteApi:
    """Build the named ops over the scripted client (as the engine does)."""
    return WriteApi(client)


def grant(session) -> None:
    session.enqueue(FakeResponse(payload=GRANT_U1))


def grant_reauth(session) -> None:
    session.enqueue(FakeResponse(payload=GRANT_U1))
    session.enqueue(FakeResponse(status=401))
    session.enqueue(FakeResponse(payload=GRANT_U2))
    session.enqueue(FakeResponse())  # the resent write


def posts(session) -> list[dict]:
    return [
        c for c in session.calls if c["method"] == "POST" and c.get("json") is not None
    ]


def gets(session) -> list[dict]:
    return [c for c in session.calls if c["method"] == "GET"]


# --- status ops: exact body + path ---


@pytest.mark.parametrize(
    "mode", ["off", "manual", "auto", "modified_auto", "self_learn", "presence"]
)
async def test_set_mode_body_and_path(sleeps, session, client, writes, mode):
    grant(session)
    session.enqueue(FakeResponse())
    await writes.set_mode("dev1", NODE, mode)
    call = posts(session)[0]
    assert call["url"] == f"{API_HOST}/api/v2/{STATUS_PATH}"
    assert call["json"] == {"mode": mode}


@pytest.mark.parametrize("mode", ["on", "AUTO", "", "modified-auto"])
async def test_set_mode_rejects_unknown_before_network(
    sleeps, session, client, writes, mode
):
    with pytest.raises(ValueError, match="mode"):
        await writes.set_mode("dev1", NODE, mode)
    assert session.calls == []


@pytest.mark.parametrize(
    ("temp", "units", "expected"),
    [
        (21.3, "C", "21.0"),
        (22.4, "C", "22.0"),
        (21.0, "C", "21.0"),
        (72.3, "F", "72.0"),
    ],
)
async def test_set_setpoint_body(
    sleeps, session, client, writes, temp, units, expected
):
    grant(session)
    session.enqueue(FakeResponse())
    await writes.set_setpoint("dev1", NODE, temp, units)
    call = posts(session)[0]
    assert call["url"] == f"{API_HOST}/api/v2/{STATUS_PATH}"
    assert call["json"] == {"stemp": expected, "units": units}


async def test_engage_modified_auto_app_shaped_body(sleeps, session, client, writes):
    grant(session)
    session.enqueue(FakeResponse())
    await writes.engage_modified_auto("dev1", NODE, 22.4, "C")
    assert posts(session)[0]["json"] == {
        "stemp": "22.0",
        "units": "C",
        "mode": "modified_auto",
    }


@pytest.mark.parametrize("locked", [True, False])
async def test_set_lock_body(sleeps, session, client, writes, locked):
    grant(session)
    session.enqueue(FakeResponse())
    await writes.set_lock("dev1", NODE, locked)
    assert posts(session)[0]["json"] == {"locked": locked}


async def test_set_lock_accepts_bool_or_wire_enum(sleeps, session, client, writes):
    grant(session)
    for value in (True, False, 0, 1, 2):
        session.enqueue(FakeResponse())
        await writes.set_lock("dev1", NODE, value)
        assert posts(session)[-1]["json"] == {"locked": value}
    with pytest.raises(ValueError, match="locked"):
        await writes.set_lock("dev1", NODE, 3)


@pytest.mark.parametrize("on", [True, False])
async def test_set_boost_body(sleeps, session, client, writes, on):
    grant(session)
    session.enqueue(FakeResponse())
    await writes.set_boost("dev1", NODE, on)
    assert posts(session)[0]["json"] == {"boost": on}


async def test_set_boost_rejects_non_bool(sleeps, session, client, writes):
    with pytest.raises(ValueError, match="on"):
        await writes.set_boost("dev1", NODE, "yes")
    assert session.calls == []


async def test_set_boost_mode_bodies(sleeps, session, client, writes):
    """The full-boost endpoint body follows the current app exactly."""
    grant(session)
    session.enqueue(FakeResponse())
    await writes.set_boost_mode(
        "dev1",
        NODE,
        active=True,
        mode="temp_and_time",
        temperature=21.5,
        time_minutes=120,
    )
    call = posts(session)[0]
    assert call["url"].endswith("/htr/2/boost_mode")
    assert call["json"] == {
        "active": True,
        "mode": "temp_and_time",
        "temp": "21.5",
        "time": 120,
    }

    session.enqueue(FakeResponse())
    await writes.set_boost_mode("dev1", NODE, active=False)
    call = posts(session)[1]
    assert call["url"].endswith("/htr/2/boost_mode")
    assert call["json"] == {"active": False}


async def test_set_boost_mode_rejects_invalid_inputs(sleeps, session, client, writes):
    with pytest.raises(ValueError, match="boost mode must be one of"):
        await writes.set_boost_mode("dev1", NODE, active=True, mode="nope")
    with pytest.raises(ValueError, match="only active=false"):
        await writes.set_boost_mode("dev1", NODE, active=False, mode="runback")
    assert session.calls == []


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"on": False}, {"on": False}),
        (
            {"on": True, "mode": "auto"},
            {"on": True, "mode": "auto"},
        ),
        (
            {"on": True, "mode": "manual", "selected_temp": "comfort"},
            {"on": True, "mode": "manual", "selected_temp": "comfort"},
        ),
        # comfort_temp is a setpoint like any other: Celsius is quantized onto
        # the 0.5 grid, Fahrenheit to whole degrees (the vendor app's F grid).
        # The scale formats the value; the current app does not post `units`.
        (
            {"on": True, "mode": "manual", "comfort_temp": 21.3, "units": "C"},
            {"on": True, "mode": "manual", "comfort_temp": "21.0"},
        ),
        (
            {"on": True, "comfort_temp": 70.7, "eco_offset": "4", "units": "F"},
            {"on": True, "comfort_temp": "71.0", "eco_offset": "4"},
        ),
        # `units` is a formatting input, not a body key.
        ({"on": True, "units": "C"}, {"on": True}),
    ],
)
async def test_set_htr_mod_status_body(
    sleeps, session, client, writes, kwargs, expected
):
    grant(session)
    session.enqueue(FakeResponse())
    await writes.set_htr_mod_status("dev1", NODE, **kwargs)
    call = posts(session)[0]
    assert call["url"] == f"{API_HOST}/api/v2/{STATUS_PATH}"
    assert call["json"] == expected


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"on": "yes"},
        {"mode": "AUTO"},
        {"selected_temp": 4},
        {"eco_offset": 4},
        {"comfort_temp": 21.0},  # units required with comfort_temp
        {"comfort_temp": "21.0", "units": "C"},
        {"units": "C"},  # units is not a body key
        {"units": "X"},
        {"on": True, "units": "X"},  # invalid scale even without a temperature
    ],
)
async def test_set_htr_mod_status_rejects_before_network(
    sleeps, session, client, writes, kwargs
):
    with pytest.raises(ValueError):
        await writes.set_htr_mod_status("dev1", NODE, **kwargs)
    assert session.calls == []


@pytest.mark.parametrize(
    ("temps", "expected"),
    [
        (
            (10.4, 17.3, 21.6, "C"),
            {"ice_temp": "10.0", "eco_temp": "17.0", "comf_temp": "21.5", "units": "C"},
        ),
        ((None, None, 21.0, "C"), {"comf_temp": "21.0", "units": "C"}),
        (
            (70.0, 60.5, 80.25, "F"),
            {"ice_temp": "70.0", "eco_temp": "61.0", "comf_temp": "80.0", "units": "F"},
        ),
    ],
)
async def test_set_prog_temps_app_body(
    sleeps, session, client, writes, temps, expected
):
    grant(session)
    session.enqueue(FakeResponse())
    ice, eco, comf, units = temps
    await writes.set_prog_temps(
        "dev1",
        NODE,
        ice_temp=ice,
        eco_temp=eco,
        comf_temp=comf,
        units=units,
    )
    call = posts(session)[0]
    assert call["url"] == f"{API_HOST}/api/v2/{STATUS_PATH}"
    assert call["json"] == expected


@pytest.mark.parametrize(
    ("temp", "units"),
    [("abc", "F"), (float("nan"), "F"), (float("inf"), "C"), (True, "C")],
)
async def test_set_setpoint_validates_temp_before_network(
    sleeps, session, client, writes, temp, units
):
    with pytest.raises(ValueError, match="temp"):
        await writes.set_setpoint("dev1", NODE, temp, units)
    assert session.calls == []


async def test_set_extra_options_requires_mapping(sleeps, session, client, writes):
    with pytest.raises(ValueError, match="mapping"):
        await writes.set_extra_options("dev1", NODE, [("a", 1)])
    assert session.calls == []


async def test_set_prog_temps_requires_units(sleeps, session, client, writes):
    """A unitless profile-temp body is one the app never sends (api-contract §5.2)."""
    with pytest.raises(ValueError, match="units"):
        await writes.set_prog_temps(
            "dev1",
            NODE,
            ice_temp=5.0,
            eco_temp=None,
            comf_temp=None,
            units=None,
        )
    assert session.calls == []


async def test_set_prog_temps_quantizes_celsius_on_the_setpoint_grid(
    sleeps, session, client, writes
):
    """Profile temps are setpoints: Celsius is floored onto the 0.5 grid."""
    grant(session)
    session.enqueue(FakeResponse())
    await writes.set_prog_temps(
        "dev1",
        NODE,
        ice_temp=5.3,
        eco_temp=None,
        comf_temp=21.9,
        units="C",
    )
    assert posts(session)[0]["json"] == {
        "ice_temp": "5.0",
        "comf_temp": "21.5",
        "units": "C",
    }


async def test_set_prog_temps_rejects_unknown_units(sleeps, session, client, writes):
    with pytest.raises(ValueError, match="units"):
        await writes.set_prog_temps(
            "dev1",
            NODE,
            ice_temp=5.0,
            eco_temp=None,
            comf_temp=None,
            units="K",
        )
    assert session.calls == []


async def test_set_prog_temps_requires_at_least_one_temp(
    sleeps, session, client, writes
):
    with pytest.raises(ValueError, match="at least one"):
        await writes.set_prog_temps(
            "dev1",
            NODE,
            ice_temp=None,
            eco_temp=None,
            comf_temp=None,
            units="C",
        )
    assert session.calls == []


async def test_set_prog_temps_rejects_non_numbers(sleeps, session, client, writes):
    with pytest.raises(ValueError, match="eco_temp"):
        await writes.set_prog_temps(
            "dev1",
            NODE,
            ice_temp=10.0,
            eco_temp="17",
            comf_temp=None,
            units="C",
        )
    assert session.calls == []


# --- setup ops: GET-merge-POST the COMPLETE object ---


async def test_set_window_mode_get_merges_posts_full_setup(
    sleeps, session, client, writes
):
    grant(session)
    session.enqueue(
        FakeResponse(
            payload={
                "window_mode_enabled": False,
                "priority": "low",
                "htr_brand": "acme",
            }
        )
    )
    session.enqueue(FakeResponse())
    enabled = True
    await writes.set_window_mode("dev1", NODE, enabled)
    assert len(gets(session)) == 1
    assert gets(session)[0]["url"] == f"{API_HOST}/api/v2/{SETUP_PATH}"
    call = posts(session)[0]
    assert call["url"] == f"{API_HOST}/api/v2/{SETUP_PATH}"
    assert call["json"] == {
        "window_mode_enabled": True,
        "priority": "low",
        "htr_brand": "acme",
    }


async def test_set_true_radiant_get_merges_posts_full_setup(
    sleeps, session, client, writes
):
    grant(session)
    session.enqueue(
        FakeResponse(
            payload={"true_radiant_enabled": True, "window_mode_enabled": False}
        )
    )
    session.enqueue(FakeResponse())
    disabled = False
    await writes.set_true_radiant("dev1", NODE, disabled)
    assert posts(session)[0]["json"] == {
        "true_radiant_enabled": False,
        "window_mode_enabled": False,
    }


@pytest.mark.parametrize(
    ("offset", "expected"),
    [(0, "0.0"), (5, "5.0"), (2.5, "2.5")],
)
async def test_set_away_offset_stringified(
    sleeps, session, client, writes, offset, expected
):
    grant(session)
    session.enqueue(FakeResponse(payload={"away_offset": "0.0"}))
    session.enqueue(FakeResponse())
    await writes.set_away_offset("dev1", NODE, offset)
    assert posts(session)[0]["json"] == {"away_offset": expected}


async def test_set_away_offset_rejects_negative(sleeps, session, client, writes):
    with pytest.raises(ValueError, match=">= 0"):
        await writes.set_away_offset("dev1", NODE, -1.0)
    assert session.calls == []


@pytest.mark.parametrize(
    ("limit", "units", "expected"),
    [
        (None, "C", "0.0"),
        (0, "F", "0.0"),
        (0.0, "C", "0.0"),
        (22.4, "C", "22.0"),
        (35, "C", "35.0"),
        (75.0, "F", "24.0"),  # the app's own F value -> the wire's Celsius
        (72.95, "F", "23.0"),  # converted to the wire's Celsius (72.95 F = 22.75 C)
    ],
)
async def test_set_max_stemp_limit_sentinel_and_grid(
    sleeps, session, client, writes, limit, units, expected
):
    grant(session)
    session.enqueue(FakeResponse(payload={"max_stemp_limit": "0.0"}))
    session.enqueue(FakeResponse())
    await writes.set_max_stemp_limit("dev1", NODE, limit, units)
    assert posts(session)[0]["json"] == {"max_stemp_limit": expected}


async def test_set_max_stemp_limit_rejects_negative(sleeps, session, client, writes):
    with pytest.raises(ValueError, match=">= 0"):
        await writes.set_max_stemp_limit("dev1", NODE, -1.0)
    assert session.calls == []


async def test_set_max_stemp_limit_validates_units(sleeps, session, client, writes):
    with pytest.raises(ValueError, match="units"):
        await writes.set_max_stemp_limit("dev1", NODE, 75.0, "K")
    assert session.calls == []


async def test_set_max_stemp_limit_refuses_the_disabled_sentinel(
    sleeps, session, client, writes
):
    """A positive limit that rounds to 0.0 would silently disable the limit."""
    with pytest.raises(ValueError, match="disabled sentinel"):
        await writes.set_max_stemp_limit("dev1", NODE, 0.1, "C")
    assert session.calls == []


@pytest.mark.parametrize("limit", [False, True])
async def test_set_max_stemp_limit_rejects_bool(sleeps, session, client, writes, limit):
    """Bools must not ride the ``limit == 0`` sentinel branch (False == 0)."""
    with pytest.raises(ValueError, match="bool"):
        await writes.set_max_stemp_limit("dev1", NODE, limit)
    assert session.calls == []  # no network call


@pytest.mark.parametrize("priority", ["low", "medium", "high"])
async def test_set_priority(sleeps, session, client, writes, priority):
    grant(session)
    session.enqueue(FakeResponse(payload={"priority": "low"}))
    session.enqueue(FakeResponse())
    await writes.set_priority("dev1", NODE, priority)
    assert posts(session)[0]["json"] == {"priority": priority}


async def test_set_priority_rejects_unknown(sleeps, session, client, writes):
    with pytest.raises(ValueError, match="priority"):
        await writes.set_priority("dev1", NODE, "urgent")
    assert session.calls == []


async def test_set_extra_options_merges_full_object(sleeps, session, client, writes):
    grant(session)
    session.enqueue(
        FakeResponse(
            payload={
                "extra_options": {"existing": "keep", "stale": "gone"},
                "htr_brand": "acme",
            }
        ),
    )
    session.enqueue(FakeResponse())
    await writes.set_extra_options("dev1", NODE, {"changed": 1, "stale": None})
    assert posts(session)[0]["json"] == {
        "extra_options": {"existing": "keep", "stale": None, "changed": 1},
        "htr_brand": "acme",
    }


async def test_set_extra_options_refuses_an_empty_setup_read(
    sleeps, session, client, writes
):
    """A 204/empty/non-JSON setup read must not become a one-key POST."""
    grant(session)
    session.enqueue(FakeResponse(payload={}))
    with pytest.raises(SmartboxError, match="no body"):
        await writes.set_extra_options("dev1", NODE, {"eco_mode": True})
    assert posts(session) == []


# --- device ops ---


@pytest.mark.parametrize("away", [True, False])
async def test_set_away_body_is_the_app_three_key_shape(
    sleeps, session, client, writes, away
):
    """The app sends enabled/forced hardcoded; a bare {away} is tolerated."""
    grant(session)
    session.enqueue(FakeResponse())
    await writes.set_away("dev1", away)
    call = posts(session)[0]
    assert call["url"] == f"{API_HOST}/api/v2/devs/dev1/mgr/away_status"
    assert call["json"] == {"away": away, "enabled": True, "forced": False}


async def test_set_power_limit_body_and_path(sleeps, session, client, writes):
    grant(session)
    session.enqueue(FakeResponse())
    await writes.set_power_limit("dev1", 2500)
    call = posts(session)[0]
    assert call["url"] == f"{API_HOST}/api/v2/devs/dev1/htr_system/power_limit"
    assert call["json"] == {"power_limit": "2500"}  # stringified (E18)


@pytest.mark.parametrize("value", [-1, "2500", 2500.0, True])
async def test_set_power_limit_validates(sleeps, session, client, writes, value):
    with pytest.raises(ValueError, match="power_limit"):
        await writes.set_power_limit("dev1", value)
    assert session.calls == []


# --- prog op: validation, GET-merge-POST ---


def _prog_payload(*days: str) -> dict:
    return {"prog": dict.fromkeys(days, SLOTS)}


async def test_set_prog_merges_day_level_over_current(sleeps, session, client, writes):
    new_slots = [7] * 24
    grant(session)
    session.enqueue(FakeResponse(payload=_prog_payload("0", "1", "6")))
    session.enqueue(FakeResponse())
    await writes.set_prog("dev1", NODE, {"1": new_slots})
    assert gets(session)[0]["url"] == f"{API_HOST}/api/v2/{PROG_PATH}"
    call = posts(session)[0]
    assert call["url"] == f"{API_HOST}/api/v2/{PROG_PATH}"
    assert call["json"] == {"prog": {"0": SLOTS, "1": new_slots, "6": SLOTS}}


async def test_set_prog_merges_several_partial_days(sleeps, session, client, writes):
    grant(session)
    session.enqueue(FakeResponse(payload=_prog_payload("0", "6")))
    session.enqueue(FakeResponse())
    await writes.set_prog("dev1", NODE, {"0": [9] * 24, "6": [3] * 24})
    assert posts(session)[0]["json"] == {"prog": {"0": [9] * 24, "6": [3] * 24}}


@pytest.mark.parametrize(
    "days",
    [
        {"7": SLOTS},  # unknown day key
        {1: SLOTS},  # non-string day key
        {"0": []},  # empty day
        {"0": ["1"] * 24},  # non-int slots
        {"0": [True] + [0] * 23},  # bool is not an int slot
        {"0": "0123..."},  # not a list
        [("0", SLOTS)],  # not a mapping
    ],
)
async def test_set_prog_validates_before_any_network_call(
    sleeps, session, client, writes, days
):
    with pytest.raises(ValueError, match="prog"):
        await writes.set_prog("dev1", NODE, days)
    assert session.calls == []


@pytest.mark.parametrize(
    "days",
    [
        {"7": ["x" * 5000 + "\nCRASH"]},  # giant day value
        [("0", "x" * 5000 + "\nCRASH")],  # giant non-mapping value
    ],
)
async def test_set_prog_validation_reprs_bounded(sleeps, session, client, writes, days):
    """Giant prog inputs render bounded, single-line messages."""
    with pytest.raises(ValueError) as err:
        await writes.set_prog("dev1", NODE, days)
    message = str(err.value)
    assert "\n" not in message
    assert len(message) < 200
    assert "CRASH" not in message
    assert session.calls == []


async def test_set_prog_accepts_48_half_hourly_slots(sleeps, session, client, writes):
    """mapping.py documents 48-half-hourly schedules; no exact-24 check."""
    grant(session)
    session.enqueue(FakeResponse(payload=_prog_payload("0")))
    session.enqueue(FakeResponse())
    half_hourly = [7, 8] * 24  # 48 slots
    await writes.set_prog("dev1", NODE, {"0": half_hourly})
    assert posts(session)[0]["json"] == {"prog": {"0": half_hourly}}


async def test_set_prog_accepts_an_empty_schedule_base(sleeps, session, client, writes):
    """A genuinely empty schedule is a valid merge base, not a malformed read."""
    grant(session)
    session.enqueue(FakeResponse(payload={"prog": {}, "sync_status": "ok"}))
    session.enqueue(FakeResponse())
    await writes.set_prog("dev1", NODE, {"1": SLOTS})
    assert posts(session)[0]["json"] == {"prog": {"1": SLOTS}}


async def test_set_prog_refuses_a_lost_read(sleeps, session, client, writes):
    """A lost schedule read must not become {sync_status: lost, day: ...}."""
    grant(session)
    session.enqueue(FakeResponse(payload={"sync_status": "lost"}))
    with pytest.raises(SmartboxError, match="no usable day map"):
        await writes.set_prog("dev1", NODE, {"1": SLOTS})
    assert posts(session) == []


async def test_set_prog_value_equal_is_not_posted(sleeps, session, client, writes):
    """No-op schedules push no frame, so posting one would arm a false timeout."""
    grant(session)
    session.enqueue(FakeResponse(payload=_prog_payload("0")))
    path, body = await writes.set_prog("dev1", NODE, {"0": SLOTS})
    assert path == PROG_PATH
    assert body == {"prog": {"0": SLOTS}}
    assert posts(session) == []


# --- plumbing: single attempt, 401 resend, error contract, secrets ---


async def test_post_5xx_single_attempt_never_retried(sleeps, session, client, writes):
    grant(session)
    session.enqueue(FakeResponse(status=503))
    with pytest.raises(TransportUnavailableError, match="503"):
        await writes.set_mode("dev1", NODE, "auto")
    assert len(posts(session)) == 1
    assert sleeps.delays == []


async def test_post_network_failure_single_attempt(sleeps, session, client, writes):
    grant(session)
    session.enqueue(aiohttp.ClientError("reset"))
    with pytest.raises(TransportUnavailableError):
        await writes.set_setpoint("dev1", NODE, 21.0, "C")
    assert len(posts(session)) == 1


async def test_post_401_invalidates_and_resends_exactly_once(
    sleeps, session, client, writes
):
    grant_reauth(session)
    await writes.set_mode("dev1", NODE, "auto")
    assert len(posts(session)) == 2
    # the resend carried the same body
    assert posts(session)[1]["json"] == {"mode": "auto"}


async def test_post_second_401_raises_auth_error(sleeps, session, client, writes):
    grant(session)
    session.enqueue(FakeResponse(status=401))
    session.enqueue(FakeResponse(payload=GRANT_U2))
    session.enqueue(FakeResponse(status=401))
    with pytest.raises(AuthError):
        await writes.set_mode("dev1", NODE, "auto")


async def test_post_4xx_raises_smartbox_error_with_status(
    sleeps, session, client, writes
):
    grant(session)
    session.enqueue(FakeResponse(status=400))
    with pytest.raises(SmartboxError, match="400"):
        await writes.set_mode("dev1", NODE, "auto")
    assert len(posts(session)) == 1


async def test_post_body_never_reaches_the_log(sleeps, session, client, writes, caplog):
    caplog.set_level(logging.DEBUG, logger="smartbox.protocol.client")
    grant(session)
    session.enqueue(FakeResponse())
    await writes.set_setpoint("sécret dev", NodeRef("htr", "2"), 21.0, "C")
    log_text = caplog.text
    assert "sécret" not in log_text  # percent-encoded in the path line
    assert JWT_U1 not in log_text
    assert "Bearer" not in log_text
    assert "POST /api/v2/devs/s%C3%A9cret%20dev/htr/2/status -> 200" in log_text


async def test_url_segments_percent_encoded(sleeps, session, client, writes):
    grant(session)
    session.enqueue(FakeResponse())
    away = True
    await writes.set_away("dev/1 x", away)
    assert (
        posts(session)[0]["url"]
        == f"{API_HOST}/api/v2/devs/dev%2F1%20x/mgr/away_status"
    )


async def test_set_max_stemp_limit_rejects_an_f_value_below_freezing(
    sleeps, session, client, writes
):
    """An F limit that converts to a negative Celsius is refused, not posted."""
    with pytest.raises(ValueError, match="negative Celsius"):
        await writes.set_max_stemp_limit("dev1", NODE, 20.0, "F")
    assert session.calls == []


async def test_set_node_power_limit_body_and_path(sleeps, session, client, writes):
    """The node's own limit: its own endpoint, the app's stringified body."""
    grant(session)
    session.enqueue(FakeResponse())
    await writes.set_node_power_limit("dev1", NODE, 2000)
    call = posts(session)[0]
    assert call["url"] == f"{API_HOST}/api/v2/devs/dev1/htr/2/power_limit"
    assert call["json"] == {"power_limit": "2000"}  # stringified (app toFixed(0))


@pytest.mark.parametrize("value", [-1, "2000", 2000.0, True])
async def test_set_node_power_limit_validates(sleeps, session, client, writes, value):
    with pytest.raises(ValueError, match="power_limit"):
        await writes.set_node_power_limit("dev1", NODE, value)
    assert session.calls == []
