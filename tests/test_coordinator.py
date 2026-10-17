"""Tests for the consumption coordinator."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from smartbox.error import APIUnavailableError

from custom_components.smartbox.const import (
    CONF_HISTORY_CONSUMPTION,
    HistoryConsumptionStatus,
)
from custom_components.smartbox.core.coordinator import (
    CONSUMPTION_UPDATE_INTERVAL,
    ConsumptionCoordinator,
    polling_coordinator,
)


def _coordinator(hass, config_entry, history):
    hass.config_entries.async_update_entry(
        config_entry,
        options={**config_entry.options, CONF_HISTORY_CONSUMPTION: history},
    )
    node = MagicMock(node_id="device_1_1")
    node.get_samples = AsyncMock(
        return_value=[{"t": 20, "counter": 2}, {"t": 10, "counter": 1}]
    )
    return ConsumptionCoordinator(hass, config_entry, node), node


async def test_start_fetches_three_years_once(hass, config_entry):
    """START asks one query per year and feeds the node counter."""
    coordinator, node = _coordinator(hass, config_entry, HistoryConsumptionStatus.START)

    data = await coordinator._async_update_data()

    assert node.get_samples.await_count == 3
    assert data.history == HistoryConsumptionStatus.START
    assert [sample["t"] for sample in data.samples] == sorted(
        sample["t"] for sample in data.samples
    )
    node.apply_samples.assert_called_once_with(data.samples)


@pytest.mark.parametrize(
    "history", [HistoryConsumptionStatus.AUTO, HistoryConsumptionStatus.OFF]
)
async def test_auto_and_off_use_a_single_query(hass, config_entry, history):
    """Every other mode needs only one query per cycle."""
    coordinator, node = _coordinator(hass, config_entry, history)

    data = await coordinator._async_update_data()

    node.get_samples.assert_awaited_once()
    assert data.history == history
    node.apply_samples.assert_called_once()


async def test_api_error_becomes_update_failed(hass, config_entry):
    """A library error fails the cycle instead of escaping."""
    coordinator, node = _coordinator(hass, config_entry, HistoryConsumptionStatus.AUTO)
    node.get_samples.side_effect = APIUnavailableError("down")

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    node.apply_samples.assert_not_called()


def test_update_interval(hass, config_entry):
    """The cycle keeps the 15 minute cadence."""
    coordinator, _node = _coordinator(hass, config_entry, HistoryConsumptionStatus.AUTO)
    assert coordinator.update_interval == CONSUMPTION_UPDATE_INTERVAL


async def test_polling_coordinator_returns_the_method_result(hass, config_entry):
    """The polled resource becomes the coordinator data."""
    coordinator = polling_coordinator(
        hass,
        config_entry,
        name="test",
        interval=timedelta(minutes=1),
        method=AsyncMock(return_value={"ok": True}),
    )

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data == {"ok": True}


async def test_polling_coordinator_maps_library_errors(hass, config_entry):
    """A library error fails the cycle so entities go unavailable."""
    coordinator = polling_coordinator(
        hass,
        config_entry,
        name="test",
        interval=timedelta(minutes=1),
        method=AsyncMock(side_effect=APIUnavailableError("down")),
    )

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
