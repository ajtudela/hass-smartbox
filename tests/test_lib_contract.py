"""Contract tests pinning the shipped protocol values.

The protocol layer owns the reseller table, the ``SmartboxNodeType``
enum (``const.py``) and the shared backoff helper. The pinned VALUES
below originated in the ``smartbox`` library's v2.7.0 table and were
extended with the 3.0.2 per-reseller API host; they must not drift.
"""

import pytest
from smartbox.protocol.errors import ResellerNotExistError
from smartbox.protocol.reseller import AvailableResellers, SmartboxReseller
from smartbox.protocol.retry import backoff_delay

from custom_components.smartbox.const import SmartboxNodeType


def test_package_exports() -> None:
    """Names imported by the integration resolve from the vendored layer."""
    assert AvailableResellers is not None
    assert SmartboxNodeType is not None
    assert backoff_delay is not None


def test_smartbox_node_type_members() -> None:
    """Node types keep their lowercase wire values, as a closed set."""
    assert SmartboxNodeType.HTR.value == "htr"
    assert SmartboxNodeType.THM.value == "thm"
    assert SmartboxNodeType.ACM.value == "acm"
    assert SmartboxNodeType.HTR_MOD.value == "htr_mod"
    assert SmartboxNodeType.PMO.value == "pmo"
    assert {member.value for member in SmartboxNodeType} == {
        "htr",
        "thm",
        "htr_mod",
        "acm",
        "pmo",
    }


def test_shipped_availability_windows_are_pinned() -> None:
    """The windows the vendor's behaviour was measured against (contract §6.3).

    Every test shrinks these; nothing else would notice a field-behaviour
    change to the shipped values.
    """
    from smartbox.core.feed import FEED_GRACE_SECONDS  # noqa: PLC0415

    from custom_components.smartbox.const import (  # noqa: PLC0415 - locality
        SMARTBOX_UNAVAILABLE_DELAY,
        SMARTBOX_WRITE_CONFIRM_TIMEOUT,
    )

    assert SMARTBOX_UNAVAILABLE_DELAY == 6.0
    assert SMARTBOX_WRITE_CONFIRM_TIMEOUT == 6.0
    assert FEED_GRACE_SECONDS == 10.0


def test_backoff_delay_saturates() -> None:
    """The supervision watchdog's backoff helper keeps its contract."""
    assert backoff_delay(0, 5.0, 300.0) == 5.0
    assert backoff_delay(1, 5.0, 300.0) == 10.0
    assert backoff_delay(50, 5.0, 300.0) == 300.0  # saturated


def test_reseller_table_values_pinned() -> None:
    """Table values pinned against the shipped ``smartbox`` library."""
    resellers = AvailableResellers.resellers
    assert resellers["api"] == SmartboxReseller(
        name="Helki",
        web_url="https://app.helki.com/",
        api_url="api",
        basic_auth="NTRiY2NiZmI0MWE5YTUxMTNmMDQ4OGQwOnZkaXZkaQ==",
        serial_id=1,
    )
    assert resellers["api-lhz"].name == "Lucht LHZ"
    assert resellers["api-lhz"].serial_id == 2
    assert resellers["api-ehc"].name == "Electric Heating Company"
    assert resellers["api-ehc"].serial_id == 3
    assert resellers["api-ehc"].api_host == "api-ehc.helki.com"
    assert resellers["api-climastar"].name == "Climastar"
    assert resellers["api-climastar"].serial_id == 5
    assert resellers["api-elnur"].name == "Elnur"
    assert resellers["api-elnur"].serial_id == 7
    assert resellers["api-elnur"].api_host == "api-elnur.helki.com"
    assert resellers["api"].api_host == "users-api.helki.com"
    assert resellers["api-valderoma"].name == "Valderoma"
    assert resellers["api-valderoma"].api_url == "api-climastar"
    assert resellers["api-valderoma"].serial_id == 8
    assert resellers["api-iheatcontrol"].name == "iHeat Control"
    assert resellers["api-iheatcontrol"].api_url == "api-evconfort"
    assert resellers["api-iheatcontrol"].serial_id == 9
    assert resellers["api-hjm"].name == "HJM"
    assert resellers["api-hjm"].serial_id == 10
    assert resellers["api-evconfort"].name == "Electrorad"
    assert resellers["api-evconfort"].serial_id == 12
    assert resellers["api-haverland"].name == "Haverland"
    assert (
        resellers["api-haverland"].basic_auth
        == "NTU2ZDc0MWI3OGUzYmU5YjU2NjA3NTQ4OnZkaXZkaQ=="
    )
    assert resellers["api-haverland"].serial_id == 14
    assert resellers["api-technoterm"].name == "Technotherm"
    assert resellers["api-technoterm"].api_url == "api-lhz"
    assert resellers["api-technoterm"].serial_id == 16
    assert resellers["api-smartcontrol"].name == "SmartControl"
    assert resellers["api-smartcontrol"].api_url == "api-lhz"
    assert resellers["api-smartcontrol"].serial_id == 17


def test_tevolve_reseller_row() -> None:
    """Tevolve tenant constants decoded from its web bundle (2026-10-20).

    The web app at ``https://tevolve.termoweb.net/`` embeds the Helki API
    endpoint, serial id 15 and its own client credential. The row is
    static-evidenced; a live login has not been exercised.
    """
    reseller = AvailableResellers.resellers["api-tevolve"]
    assert reseller == SmartboxReseller(
        name="Tevolve",
        web_url="https://tevolve.termoweb.net/",
        api_url="api-tevolve",
        basic_auth="NWM0OWRjZTk3NzUxMDM1MTUwNmM0MmRiOnRldm9sdmU=",
        serial_id=15,
    )


def test_reseller_lookup_known_and_unknown() -> None:
    """Lookup resolves a table row and rejects an unknown bare api_name."""
    reseller = AvailableResellers(api_url="api").reseller
    assert reseller.name == "Helki"
    assert AvailableResellers(api_url="api").api_url == "api"
    assert AvailableResellers(api_url="api").name == "Helki"
    assert AvailableResellers(api_url="api").web_url == "https://app.helki.com/"


def test_unknown_reseller_without_args_raises() -> None:
    """An off-table api_name with no fallback args fails closed."""
    with pytest.raises(ResellerNotExistError, match="not yet available"):
        AvailableResellers(api_url="api-unknown").reseller


def test_unknown_reseller_with_args_is_constructed() -> None:
    """An off-table api_name with full args becomes a custom row."""
    reseller = AvailableResellers(
        api_url="api-unknown",
        basic_auth="creds",
        web_url="https://custom.example/",
        serial_id=99,
        name="Custom",
    ).reseller
    assert reseller == SmartboxReseller(
        name="Custom",
        web_url="https://custom.example/",
        api_url="api-unknown",
        basic_auth="creds",
        serial_id=99,
    )
