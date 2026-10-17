import asyncio
from unittest.mock import AsyncMock, patch

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.smartbox import (
    APIUnavailableError,
    InvalidAuthError,
    SmartboxError,
    async_remove_entry,
    async_setup_entry,
    async_unload_entry,
    create_smartbox_session_from_entry,
    update_listener,
)
from custom_components.smartbox.const import DOMAIN
from custom_components.smartbox.device import SmartboxDevice

# Key -> entity domain of the legacy per-node registry entries to re-home.
_LEGACY_KEYS = {
    "away_status": SWITCH_DOMAIN,
    "connected": BINARY_SENSOR_DOMAIN,
    "power_limit": NUMBER_DOMAIN,
}


@pytest.mark.asyncio
async def test_async_setup_entry_auth_failed(hass, config_entry):
    with (
        patch(
            "custom_components.smartbox.create_smartbox_session_from_entry",
            side_effect=InvalidAuthError,
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(hass, config_entry)
    with (
        patch(
            "custom_components.smartbox.create_smartbox_session_from_entry",
            side_effect=SmartboxError,
        ),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, config_entry)


@pytest.mark.asyncio
async def test_create_smartbox_session_from_entry_success(
    hass, config_entry, mock_session
):
    with (
        patch(
            "custom_components.smartbox.async_get_clientsession",
            return_value=AsyncMock(),
        ),
        patch(
            "custom_components.smartbox.AsyncSmartboxSession",
            return_value=mock_session,
        ),
    ):
        session = await create_smartbox_session_from_entry(hass, config_entry)
        assert session is not None
        assert session.health_check.called
        assert session.check_refresh_auth.called


@pytest.mark.asyncio
async def test_create_smartbox_session_from_entry_api_unavailable(hass, config_entry):
    with (
        patch(
            "custom_components.smartbox.async_get_clientsession",
            return_value=AsyncMock(),
        ),
        patch(
            "custom_components.smartbox.AsyncSmartboxSession",
            side_effect=APIUnavailableError,
        ),
        pytest.raises(APIUnavailableError),
    ):
        await create_smartbox_session_from_entry(hass, config_entry)


@pytest.mark.asyncio
async def test_create_smartbox_session_from_entry_invalid_auth(hass, config_entry):
    with (
        patch(
            "custom_components.smartbox.async_get_clientsession",
            return_value=AsyncMock(),
        ),
        patch(
            "custom_components.smartbox.AsyncSmartboxSession",
            side_effect=InvalidAuthError,
        ),
        pytest.raises(InvalidAuthError),
    ):
        await create_smartbox_session_from_entry(hass, config_entry)


@pytest.mark.asyncio
async def test_create_smartbox_session_from_entry_smartbox_error(hass, config_entry):
    with (
        patch(
            "custom_components.smartbox.async_get_clientsession",
            return_value=AsyncMock(),
        ),
        patch(
            "custom_components.smartbox.AsyncSmartboxSession",
            side_effect=SmartboxError,
        ),
        pytest.raises(SmartboxError),
    ):
        await create_smartbox_session_from_entry(hass, config_entry)


@pytest.mark.asyncio
async def test_update_listener(hass, config_entry):
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, config_entry)
        mock_reload.assert_called_once_with(config_entry.entry_id)


async def test_device_cancel_bounded_when_socket_teardown_hangs(
    hass, mock_smartbox, config_entry
):
    """Regression: wedged socket teardown must neither hang nor raise.

    On live hardware run() parks in the websocket loop, so at cancel time the
    watchdog task is still alive. cancel() used to await the cancelled
    watchdog task bare, raising CancelledError out of async_unload_entry
    (UI reload died silently), and awaited update_manager.cancel() unbounded.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    device = config_entry.runtime_data.devices[0]

    never = asyncio.Event()
    hang_cancel = AsyncMock(side_effect=never.wait)
    # The mock socket's run() parks forever (like live hardware), so the
    # watchdog task created by setup is already the parked task we need.

    with (
        patch.object(device.update_manager, "cancel", hang_cancel),
        patch.object(device._supervisor, "_teardown_timeout", 0.05),
        patch("custom_components.smartbox.supervisor._EXIT_GRACE_SECONDS", 0.01),
    ):
        cancel_task = asyncio.create_task(device.cancel())
        done, _pending = await asyncio.wait({cancel_task}, timeout=5)
        assert done, "device.cancel() hung (regression)"
        # Must complete without raising (CancelledError used to escape).
        await cancel_task

    assert device._supervisor.task.cancelled()
    assert hang_cancel.await_count == 1


async def test_unload_entry_completes_with_parked_watchdog(
    hass, mock_smartbox, config_entry
):
    """Regression: UI reload unloads the entry first; unload must finish."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    devices = list(config_entry.runtime_data.devices)
    # The mock socket's run() parks forever (like live hardware), so the
    # setup-created watchdog task is already parked as this test needs.
    never = asyncio.Event()
    for device in devices:
        device.update_manager.cancel = AsyncMock(side_effect=never.wait)

    for device in devices:
        device._supervisor._teardown_timeout = 0.05
    with patch("custom_components.smartbox.supervisor._EXIT_GRACE_SECONDS", 0.01):
        unload_task = asyncio.create_task(
            hass.config_entries.async_unload(config_entry.entry_id)
        )
        done, _pending = await asyncio.wait({unload_task}, timeout=5)
        assert done, "async_unload_entry hung (regression)"
        assert unload_task.result() is True

    for device in devices:
        assert device._supervisor.task.cancelled()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_hass_stop_cancels_device_websocket_tasks(
    hass, mock_smartbox, config_entry
):
    """Regression: HA shutdown used to leave the watchdog tasks running."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    devices = config_entry.runtime_data.devices

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    for device in devices:
        # A faithful socket returns from run() once cancelled (it is not
        # force-cancelled) and the watchdog must not treat that as a crash.
        assert device._supervisor.task.done()
        assert device._supervisor.restarts == 0


async def test_box_device_created_and_legacy_entities_rehomed(
    hass, mock_smartbox, config_entry
):
    """The box is its own device; legacy per-node registry entries re-home.

    Before the box device existed, the away switch, connectivity sensor and
    power limit were created on the per-node devices with unique_ids
    ``{dev_id}_{addr}_{key}`` — including duplicates per node. On setup they
    must be re-keyed onto the box device (first/lowest addr per key) and the
    duplicates removed.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    # Pre-box-device registry state: per-node devices carrying the
    # device-level entries the old integration versions created.
    for dev_id in ("device_1", "device_2"):
        node_device = device_registry.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={(DOMAIN, f"{dev_id}_node0")},
        )
        for addr in (0, 1):
            for key in ("away_status", "connected"):
                entity_registry.async_get_or_create(
                    _LEGACY_KEYS[key],
                    DOMAIN,
                    f"{dev_id}_{addr}_{key}",
                    config_entry=config_entry,
                    device_id=node_device.id,
                    suggested_object_id=f"{dev_id}_{addr}_{key}",
                )
        entity_registry.async_get_or_create(
            NUMBER_DOMAIN,
            DOMAIN,
            f"{dev_id}_0_power_limit",
            config_entry=config_entry,
            device_id=node_device.id,
            suggested_object_id=f"{dev_id}_0_power_limit",
        )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    device_dicts = {
        device_dict["dev_id"]: device_dict
        for device_dict in await mock_smartbox.session.get_devices()
    }
    for dev_id in ("device_1", "device_2"):
        box_device = device_registry.async_get_device(identifiers={(DOMAIN, dev_id)})
        assert box_device is not None
        assert box_device.name == device_dicts[dev_id]["name"]
        assert box_device.sw_version == device_dicts[dev_id]["fw_version"]
        assert box_device.serial_number == device_dicts[dev_id]["serial_id"]
        for key, domain in _LEGACY_KEYS.items():
            entity_id = entity_registry.async_get_entity_id(
                domain, DOMAIN, f"{dev_id}_{key}"
            )
            assert entity_id is not None, f"{dev_id}_{key} not re-keyed"
            entity_entry = entity_registry.async_get(entity_id)
            assert entity_entry.device_id == box_device.id
        # Duplicates beyond the first addr were removed.
        assert (
            entity_registry.async_get_entity_id(
                SWITCH_DOMAIN, DOMAIN, f"{dev_id}_1_away_status"
            )
            is None
        )
        assert (
            entity_registry.async_get_entity_id(
                BINARY_SENSOR_DOMAIN, DOMAIN, f"{dev_id}_1_connected"
            )
            is None
        )

    # Re-running setup must be a no-op (idempotent re-home). The mock harness
    # refuses to create a second socket per device, so clear it to emulate
    # the fresh session a real reload would open.
    mock_smartbox._sockets.clear()
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert (
        entity_registry.async_get_entity_id(
            SWITCH_DOMAIN, DOMAIN, "device_1_away_status"
        )
        is not None
    )
    assert (
        entity_registry.async_get_entity_id(
            SWITCH_DOMAIN, DOMAIN, "device_1_0_away_status"
        )
        is None
    )


async def test_unload_keeps_transport_when_platforms_refuse(
    hass, mock_smartbox, config_entry
):
    """A refused platform unload must not tear the transport down."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    devices = list(config_entry.runtime_data.devices)

    with (
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=False),
        ),
        patch.object(SmartboxDevice, "cancel", AsyncMock()) as mock_cancel,
    ):
        assert await async_unload_entry(hass, config_entry) is False

    assert devices
    mock_cancel.assert_not_awaited()


async def test_remove_entry_drops_ws_user_reload_marks(hass, config_entry):
    """Removing the entry forgets its ws_user reload history."""
    key = f"{config_entry.entry_id}_ws_user_reload_marks"
    hass.data.setdefault(DOMAIN, {})[key] = [1.0]

    await async_remove_entry(hass, config_entry)

    assert key not in hass.data[DOMAIN]
