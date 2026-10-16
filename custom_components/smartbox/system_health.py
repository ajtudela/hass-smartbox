"""Provide info to system health."""

from typing import TYPE_CHECKING, Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

if TYPE_CHECKING:
    from . import SmartboxConfigEntry


async def _api_version(config_entry: SmartboxConfigEntry) -> str:
    """Return the cloud API version as a dotted string."""
    version = await config_entry.runtime_data.client.api_version()
    return f"{version['major']}.{version['minor']}.{version['subminor']}.{version['commit']}"


async def _api_health_check(config_entry: SmartboxConfigEntry) -> str:
    """Return the message of the cloud API health check."""
    return (await config_entry.runtime_data.client.health_check())["message"]


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Get info for the info page.

    Values are returned as awaitables so HA resolves each one on its own and
    shows a per-value error instead of failing the whole block.
    """
    # Only loaded entries have runtime_data (a retrying entry has none).
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        return {}
    config_entry: SmartboxConfigEntry = entries[0]
    return {
        "api_version": _api_version(config_entry),
        "api_health_check": _api_health_check(config_entry),
        "can_reach_web_reseller": system_health.async_check_can_reach_url(
            hass, config_entry.runtime_data.client.reseller.web_url
        ),
    }


@callback
def async_register(
    _: HomeAssistant, register: system_health.SystemHealthRegistration
) -> None:
    """Register system health callbacks."""
    register.async_register_info(system_health_info)
