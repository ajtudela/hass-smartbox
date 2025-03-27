"""Provide info to system health."""

from typing import TYPE_CHECKING, Any

from homeassistant.components import system_health
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

if TYPE_CHECKING:
    from . import SmartboxConfigEntry


async def system_health_info(hass: HomeAssistant) -> dict[str, Any]:
    """Get info for the info page."""
    config_entry: SmartboxConfigEntry = hass.config_entries.async_entries(DOMAIN)[0]
    api_version = await config_entry.runtime_data.client.api_version()
    return {
        "api_version": f"{api_version['major']}.{api_version['minor']}.{api_version['subminor']}.{api_version['commit']}",
        "api_health_check": (await config_entry.runtime_data.client.health_check())[
            "message"
        ],
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
