"""Tests for the Smartbox system health info."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartbox.const import DOMAIN
from custom_components.smartbox.system_health import system_health_info


async def test_no_loaded_entry_reports_nothing(hass):
    """Without a loaded entry there is nothing to report."""
    assert await async_setup_component(hass, "system_health", {})
    assert await system_health_info(hass) == {}


async def test_retrying_entry_is_skipped(hass):
    """An entry that is not loaded (no runtime_data) must not break the info."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await system_health_info(hass) == {}


async def test_values_are_awaitables(hass, mock_smartbox, config_entry):
    """Each value is resolved on its own by HA."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client = MagicMock()
    client.api_version = AsyncMock(
        return_value={"major": 1, "minor": 2, "subminor": 3, "commit": "abc"}
    )
    client.health_check = AsyncMock(return_value={"message": "OK"})
    client.reseller.web_url = "https://web.example.com"
    config_entry.runtime_data.client = client

    with patch(
        "custom_components.smartbox.system_health.system_health.async_check_can_reach_url",
        new=MagicMock(return_value="reachable"),
    ):
        info = await system_health_info(hass)

    assert await info["api_version"] == "1.2.3.abc"
    assert await info["api_health_check"] == "OK"
    assert info["can_reach_web_reseller"] == "reachable"
