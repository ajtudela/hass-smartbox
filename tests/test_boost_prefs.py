"""Per-node boost activation preferences."""

from __future__ import annotations

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
