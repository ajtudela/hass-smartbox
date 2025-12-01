"""The Smartbox integration."""

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from smartbox import AsyncSmartboxSession
from smartbox.error import APIUnavailableError, InvalidAuthError, SmartboxError

from .const import CONF_API_NAME
from .models import SmartboxDevice, SmartboxNode, get_devices

__version__ = "2.1.2"

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

type SmartboxConfigEntry = ConfigEntry[SmartboxData]


@dataclass
class SmartboxData:
    """Runtime data for the Smartbox class."""

    client: AsyncSmartboxSession
    devices: list[SmartboxDevice]
    nodes: list[SmartboxNode]


async def create_smartbox_session_from_entry(
    hass: HomeAssistant,
    entry: SmartboxConfigEntry | dict[str, Any] | None = None,
) -> AsyncSmartboxSession:
    """Create a Session class from smartbox."""
    data = {}
    if isinstance(entry, dict):
        data = entry
    elif isinstance(entry, ConfigEntry):
        data = dict(entry.data)
    try:
        websession = async_get_clientsession(hass)
        session = AsyncSmartboxSession(
            api_name=data[CONF_API_NAME],
            username=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
            websession=websession,
        )
        print(data[CONF_API_NAME])
        await session.health_check()
        await session.check_refresh_auth()
    except APIUnavailableError as ex:
        raise APIUnavailableError(ex) from ex
    except InvalidAuthError as ex:
        print(data[CONF_API_NAME])
        print(ex)
        raise InvalidAuthError(ex) from ex
    except SmartboxError as ex:
        raise SmartboxError(ex) from ex
    else:
        return session


async def async_setup_entry(hass: HomeAssistant, entry: SmartboxConfigEntry) -> bool:
    """Set up Smartbox from a config entry."""
    try:
        entry.runtime_data = SmartboxData(
            client=(await create_smartbox_session_from_entry(hass, entry)),
            devices=[],
            nodes=[],
        )
    except InvalidAuthError as ex:
        raise ConfigEntryAuthFailed from ex
    except (SmartboxError, APIUnavailableError) as ex:
        raise ConfigEntryNotReady from ex

    devices = await get_devices(session=entry.runtime_data.client, hass=hass)
    for device in devices:
        _LOGGER.info("Setting up configured device %s", device.dev_id)
        entry.runtime_data.devices.append(device)
    for device in entry.runtime_data.devices:
        nodes = device.get_nodes()
        _LOGGER.debug("Configuring nodes for device %s %s", device.dev_id, nodes)
        entry.runtime_data.nodes.extend(nodes)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SmartboxConfigEntry) -> bool:
    """Unload a config entry."""
    for device in entry.runtime_data.devices:
        await device.update_manager.cancel()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def update_listener(hass: HomeAssistant, entry: SmartboxConfigEntry) -> None:
    """Reload entity from config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
