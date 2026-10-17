"""Data update coordinators."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import time
from typing import TYPE_CHECKING, Final

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from smartbox.error import SmartboxError

from custom_components.smartbox.const import (
    CONF_HISTORY_CONSUMPTION,
    HistoryConsumptionStatus,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.smartbox import SmartboxConfigEntry

    from .node import SmartboxNode
    from .protocol import SamplesDict

_LOGGER = logging.getLogger(__name__)

CONSUMPTION_UPDATE_INTERVAL: Final = timedelta(minutes=15)
_DAY_SECONDS: Final = 24 * 60 * 60
_YEAR_SECONDS: Final = 365 * _DAY_SECONDS
_HOUR_SECONDS: Final = 60 * 60


@dataclass(frozen=True)
class ConsumptionData:
    """Samples fetched in one cycle and the history mode they were fetched for."""

    samples: SamplesDict
    history: HistoryConsumptionStatus


class ConsumptionCoordinator(DataUpdateCoordinator[ConsumptionData]):
    """Fetch the consumption samples of one node with a single query per cycle.

    The same samples feed the node's current counter and the recorder
    statistics import, which used to be two independent queries (a 3 hour one
    from the platform polling and a 1 day one from a separate timer).
    """

    config_entry: SmartboxConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: SmartboxConfigEntry, node: SmartboxNode
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"smartbox_consumption_{node.node_id}",
            update_interval=CONSUMPTION_UPDATE_INTERVAL,
        )
        self.node = node

    async def _async_update_data(self) -> ConsumptionData:
        history = HistoryConsumptionStatus(
            self.config_entry.options.get(
                CONF_HISTORY_CONSUMPTION, HistoryConsumptionStatus.START
            )
        )
        try:
            samples = await self._fetch(history)
        except SmartboxError as ex:
            msg = f"Error fetching the consumption of {self.node.name}: {ex}"
            raise UpdateFailed(msg) from ex
        samples = sorted(samples, key=lambda sample: sample["t"])
        self.node.apply_samples(samples)
        return ConsumptionData(samples=samples, history=history)

    async def _fetch(self, history: HistoryConsumptionStatus) -> SamplesDict:
        now = time.time()
        if history == HistoryConsumptionStatus.START:
            # last 3 years
            samples: SamplesDict = []
            for year in (3, 2, 1):
                samples.extend(
                    await self.node.get_samples(
                        int(now - year * _YEAR_SECONDS),
                        int(now - ((year - 1) * _YEAR_SECONDS - _HOUR_SECONDS)),
                    )
                )
            return samples
        if history == HistoryConsumptionStatus.AUTO:
            # last day
            return await self.node.get_samples(
                int(now - _DAY_SECONDS), int(now + _HOUR_SECONDS)
            )
        # OFF: no statistics, only what the counter needs.
        return await self.node.get_samples(int(now - 3 * _HOUR_SECONDS), int(now))
