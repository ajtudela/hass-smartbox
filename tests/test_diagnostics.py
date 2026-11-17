"""Tests for the integration's diagnostics."""

import json

from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from smartbox.protocol import TransportUnsupportedError

import custom_components.smartbox as smartbox_init
from custom_components.smartbox.const import DOMAIN
from custom_components.smartbox.diagnostics import (
    _scrub_identifiers,
    async_get_config_entry_diagnostics,
)
from custom_components.smartbox.reseller import RESELLER_OVERRIDES

_ENTRY_DATA = {
    "api_name": "test_api_name_1",
    "username": "test_username_1",
    "password": "test_password_1",
}


@pytest.fixture
def units():
    """Pin the mock world to Celsius: diagnostics dump raw wire values."""
    return "C"


def _make_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=_ENTRY_DATA, title="user")
    entry.add_to_hass(hass)
    return entry


async def test_diagnostics_redacts_credentials(hass, mock_smartbox, config_entry):
    """Credentials/titles are redacted from the entry dump."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, config_entry)
    assert result["entry"]["data"]["password"] == "**REDACTED**"
    assert result["entry"]["data"]["username"] == "**REDACTED**"
    assert result["entry"]["data"]["device_ids"] == "**REDACTED**"
    assert "test_password_1" not in str(result)
    assert "test_username_1" not in str(result)
    # Device identifiers, serials, home ids and registry deep links are
    # account data too: a diagnostics download is pasted into public issues.
    assert "serial_id_1" not in str(result)
    assert "home_1" not in str(result)
    assert "web_url" not in str(result)
    assert all(
        device["identifiers"] == "**REDACTED**" for device in result["hass_devices"]
    )
    assert all(
        entity["unique_id"] == "**REDACTED**" for entity in result["hass_entities"]
    )


async def test_diagnostics_new_stack_facts(hass, mock_smartbox, config_entry):
    """Transport/supervision/token facts come from the runtime."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, config_entry)
    runtime_section = result["runtime"]
    # transport as a facts dict: type + live transport facts
    assert runtime_section["transport"]["type"] == "ws_user"
    assert runtime_section["transport"]["running"] is True
    assert runtime_section["transport"]["short_cycle_streak"] == 0
    assert runtime_section["transport"]["auth_rejection_streak"] == 0
    assert runtime_section["transport"]["last_close_code"] is None
    assert runtime_section["transport"]["last_end_reason"] is None
    assert runtime_section["supervision"] == {
        "unsupported": False,
        "auth_failed": False,
        "terminal_error": None,
    }
    # The token expiry key tracks the TokenManager's cached bundle (None
    # in these tests: the stubbed grant never populates the cache).
    assert "token_expiry" in runtime_section
    assert runtime_section["token_origin"] == "granted"
    # Feed (websocket) facts: whether values are flowing and the outage
    # history.
    assert runtime_section["feed"] == {
        "available": True,
        "reason": None,
        "down_s": None,
        "outages": 0,
        "last_outage_s": None,
        "last_close_expected": None,
        "grace_s": 10.0,
    }
    assert runtime_section["membership"] == {
        "new_nodes": [],
        "repair_issue_raised": False,
        "pending_removals": [],
    }


async def test_diagnostics_engine_store(hass, mock_smartbox, config_entry):
    """The engine section mirrors the store (device slots + node slots)."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    runtime = config_entry.runtime_data
    await runtime.transport.deliver_snapshot(
        [
            {
                "id": "home_1",
                "devs": [
                    {
                        "dev_id": "device_1",
                        "devData": {
                            "connected": True,
                            "away_status": {"away": False},
                            "nodes": [
                                {
                                    "type": "htr",
                                    "addr": "0",
                                    "status": {"stemp": "20.0", "units": "C"},
                                    "setup": {"priority": "low"},
                                    "version": {"pid": "081c", "hw_version": "2.3"},
                                }
                            ],
                        },
                    }
                ],
            }
        ]
    )
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, config_entry)
    engine = result["runtime"]["engine"]
    device = engine["devices"]["device_1"]
    assert device["connected"] is True
    assert device["away"] == {"away": False}
    node = device["nodes"]["htr/0"]
    assert node["status"] == {"stemp": "20.0", "units": "C"}
    assert node["setup"] == {"priority": "low"}
    assert node["version"] == {"pid": "081c", "hw_version": "2.3"}
    assert node["available"] is True  # snapshot ok status marks available
    # nothing is waiting to flip a node: the machinery's armed view is empty
    assert engine["armed_deadlines"] == []


def test_scrub_identifiers_leaves_coincidental_numbers():
    """A numeric dev_id in a date is not treated as an identifier."""
    labels = {"2026": "device_1", "dev_1": "device_2"}
    assert _scrub_identifiers(
        {"when": "2026-11-02", "year": "2026", "ids": ("dev_1_0",)}, labels
    ) == {
        "when": "2026-11-02",
        "year": "device_1",
        "ids": ("device_2_0",),
    }


async def test_diagnostics_redacts_device_identifiers(
    hass, mock_smartbox, config_entry
):
    """A dev_id/home id used as a key or value becomes a stable label."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    runtime = config_entry.runtime_data
    await runtime.transport.deliver_snapshot(
        [
            {
                "id": "secret_home_9",
                "devs": [
                    {
                        "dev_id": "secret_device_9",
                        "devData": {
                            "connected": True,
                            "away_status": {"away": False},
                            "nodes": [
                                {
                                    "type": "htr",
                                    "addr": "0",
                                    "status": {"stemp": "20.0", "units": "C"},
                                    "setup": {"priority": "low"},
                                    "version": {"pid": "081c"},
                                }
                            ],
                        },
                    }
                ],
            }
        ]
    )
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, config_entry)
    dump = json.dumps(result, default=str)
    assert "secret_device_9" not in dump
    assert "secret_home_9" not in dump

    engine = result["runtime"]["engine"]
    # The label is stable and correlates with the other sections.
    assert "device_3" in engine["devices"]
    assert engine["devices"]["device_3"]["home_id"] == "**REDACTED**"


async def test_diagnostics_reports_statistics_import_state(
    hass, mock_smartbox, config_entry
):
    """The history-import ledger is visible: pending, completed and failures."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    runtime = config_entry.runtime_data
    node_ids = sorted(node.node_id for node in runtime.identity.nodes)
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    section = result["runtime"]["statistics_import"]
    assert section["total_nodes"] == len(node_ids)
    assert section["pending_nodes"] == node_ids
    assert section["completed_nodes"] == []
    # No recorder in this test: every pending node carries the deferred
    # "recorder unavailable" attempt, which is exactly the state a user
    # would need to see in a diagnostics download.
    assert set(section["failures"]) == set(node_ids)
    first = node_ids[0]
    failure = section["failures"][first]
    assert failure["count"] == 1
    assert failure["last_error"] == "recorder unavailable"
    assert failure["retry_in_s"] > 0

    runtime.history.complete(first)
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    section = result["runtime"]["statistics_import"]
    assert first in section["completed_nodes"]
    assert first not in section["pending_nodes"]
    assert first not in section["failures"]


async def test_diagnostics_registry_sections(hass, mock_smartbox, config_entry):
    """HASS devices/entities are listed as before."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await async_get_config_entry_diagnostics(hass, config_entry)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    assert len(result["hass_devices"]) == len(
        dr.async_entries_for_config_entry(device_registry, config_entry.entry_id)
    )
    assert len(result["hass_entities"]) == len(
        er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    )


async def test_diagnostics_reports_reseller_override(
    hass, config_entry, reseller, monkeypatch
):
    """A manual override is visible, with its field names and effective host."""
    monkeypatch.setitem(
        RESELLER_OVERRIDES,
        "test_api_name_1",
        {
            "api_host": "https://api-test.helki.com/",
            "serial_id": 99,
            "basic_auth": "secret_override",
        },
    )
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    assert result["reseller_override"] == {
        "active": True,
        "fields": ["api_host", "basic_auth", "serial_id"],
        "api_host": "https://api-test.helki.com",
    }
    assert "secret_override" not in str(result)


async def test_diagnostics_absent_runtime(hass, mock_smartbox, config_entry):
    """A failing/retrying entry reports unknown runtime facts."""
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    assert result["runtime"] == {
        "transport": None,
        "supervision": None,
        "token_expiry": None,
        "token_origin": None,
        "feed": None,
        "membership": None,
        "statistics_import": None,
    }


async def test_diagnostics_reports_the_ws_user_repair_issue(hass, config_entry):
    """The in-place recovery signal is visible: the repair issue's presence."""
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    assert result["ws_user_recovery"] == {"repair_issue_raised": False}

    smartbox_init._create_flapping_issue(
        hass, config_entry, TransportUnsupportedError("404")
    )
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    assert result["ws_user_recovery"] == {"repair_issue_raised": True}
