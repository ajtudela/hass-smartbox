"""Per-node boost activation preferences."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.smartbox.boost_prefs import BoostPrefs, boost_prefs_for


async def test_preferences_round_trip_and_clear(hass):
    """A stored preference is restored by a new store instance."""
    prefs = boost_prefs_for(hass, "entry_1")
    await prefs.async_update(
        "dev_htr_0",
        boost_temp_c=21.5,
        boost_time=120,
        runback_temp_c=18.0,
        runback_time=60,
    )
    assert prefs.get("dev_htr_0").boost_temp_c == 21.5
    await prefs.async_flush()

    restored = boost_prefs_for(hass, "entry_1")
    await restored.async_load()
    node = restored.get("dev_htr_0")
    assert node.boost_temp_c == 21.5
    assert node.boost_time == 120
    assert node.runback_temp_c == 18.0
    assert node.runback_time == 60

    await restored.async_clear()
    empty = boost_prefs_for(hass, "entry_1")
    await empty.async_load()
    assert empty.get("dev_htr_0").boost_time is None


async def test_unknown_fields_are_refused(hass):
    """Only the four known fields are accepted."""
    prefs = BoostPrefs(hass, "entry_2")
    with pytest.raises(ValueError, match="unknown boost preference fields"):
        await prefs.async_update("node", boost_tempc=20)


async def test_malformed_records_degrade_to_no_preference(hass):
    """Unreadable storage never blocks setup."""
    prefs = BoostPrefs(hass, "entry_3")
    await prefs._store.async_save({"nodes": {"node": {"boost_time": "bad"}}})
    await prefs.async_load()
    assert prefs.get("node").boost_time is None
    assert prefs.get("node").boost_temp_c is None


async def test_failed_load_does_not_clobber_the_stored_record(hass, monkeypatch):
    """An unreadable record is not overwritten by a later slider write."""
    prefs = BoostPrefs(hass, "entry_4")
    monkeypatch.setattr(
        prefs._store, "async_load", AsyncMock(side_effect=OSError("unreadable"))
    )
    await prefs.async_load()
    saved = AsyncMock()
    monkeypatch.setattr(prefs._store, "async_save", saved)
    await prefs.async_update("node", boost_time=60)
    assert prefs.get("node").boost_time == 60
    saved.assert_not_awaited()


async def test_load_skips_non_string_keys_and_non_mapping_records(hass):
    """A hand-edited record never leaks a bad key into the store."""
    prefs = BoostPrefs(hass, "entry_4")
    prefs._store.async_load = AsyncMock(
        return_value={
            "nodes": {
                1: {"boost_time": 30},
                "bad": "not-a-record",
                "ok": {"boost_time": 45},
            }
        }
    )
    await prefs.async_load()
    assert prefs.get("ok").boost_time == 45


async def test_clearing_every_field_drops_the_node(hass):
    """A node with no stored fields is removed instead of kept empty."""
    prefs = BoostPrefs(hass, "entry_5")
    await prefs.async_update("node", boost_time=30)
    await prefs.async_update("node", boost_time=None)
    assert "node" not in prefs._nodes


async def test_unreadable_record_is_not_flushed_and_save_errors_warn(hass, caplog):
    """The record is never rewritten after a failed read, and save errors warn."""
    prefs = BoostPrefs(hass, "entry_6")
    prefs._store.async_load = AsyncMock(side_effect=OSError)
    await prefs.async_load()
    assert prefs._load_failed is True
    await prefs.async_flush()
    prefs._load_failed = False
    with patch.object(prefs._store, "async_save", AsyncMock(side_effect=OSError)):
        await prefs.async_flush()
    assert "Could not persist the boost preferences" in caplog.text
