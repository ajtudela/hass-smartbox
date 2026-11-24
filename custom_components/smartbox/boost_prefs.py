"""Per-node boost activation preferences that Home Assistant owns.

FULL-boost controllers (``/boost_mode``) take a temperature and duration with
each activation; the vendor app does not persist them. Home Assistant keeps the
last values the user chose so the Boost/Runback switches stay one-click and the
sliders survive a restart. Legacy controllers store the same values on the
device in ``extra_options``, so they never reach this store.

Record layout::

    {"nodes": {<node_id>: {"boost_temp_c": 21.0, "boost_time": 60,
                           "runback_temp_c": 18.0, "runback_time": 30}}}

Temperatures are Celsius regardless of the node's display unit. A missing or
malformed record degrades to "no preference", never to a write failure.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
FIELDS = ("boost_temp_c", "boost_time", "runback_temp_c", "runback_time")


@dataclass
class NodeBoostPrefs:
    """The four activation preferences one node can hold."""

    boost_temp_c: float | None = None
    boost_time: int | None = None
    runback_temp_c: float | None = None
    runback_time: int | None = None


class BoostPrefs:
    """One config entry's per-node boost preferences in HA storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Bind the store to one config entry."""
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.boost_prefs.{entry_id}",
            private=True,
            atomic_writes=True,
        )
        self._lock = asyncio.Lock()
        self._nodes: dict[str, dict[str, Any]] = {}
        self._load_failed = False

    async def async_load(self) -> None:
        """Load the record; malformed entries are ignored, not fatal."""
        self._load_failed = False
        try:
            data = await self._store.async_load()
        except OSError, ValueError, TypeError, KeyError:
            # Keep the on-disk record untouched: saving the empty in-memory
            # map would wipe every other node's preferences.
            self._load_failed = True
            _LOGGER.warning("Could not read the boost-preference record")
            return
        nodes = data.get("nodes") if isinstance(data, dict) else None
        if not isinstance(nodes, dict):
            return
        clean: dict[str, dict[str, Any]] = {}
        for node_id, record in nodes.items():
            if not isinstance(node_id, str) or not isinstance(record, dict):
                continue
            clean[node_id] = {
                key: record.get(key) for key in FIELDS if record.get(key) is not None
            }
        self._nodes = clean

    def get(self, node_id: str) -> NodeBoostPrefs:
        """Return the stored preferences for one node (empty when absent)."""
        record = self._nodes.get(node_id, {})
        values: dict[str, Any] = {}
        for key in FIELDS:
            value = record.get(key)
            numeric: float | None = None
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric = float(value)
            if key.endswith("_temp_c"):
                values[key] = numeric
            else:
                values[key] = int(numeric) if numeric is not None else None
        return NodeBoostPrefs(**values)

    async def async_update(self, node_id: str, **fields: float | None) -> None:
        """Store the given fields for one node and persist the record."""
        unknown = set(fields) - set(FIELDS)
        if unknown:
            msg = f"unknown boost preference fields: {sorted(unknown)}"
            raise ValueError(msg)
        if self._load_failed:
            await self.async_load()
        async with self._lock:
            record = dict(self._nodes.get(node_id, {}))
            for key, value in fields.items():
                if value is None:
                    record.pop(key, None)
                else:
                    record[key] = value
            if record:
                self._nodes[node_id] = record
            else:
                self._nodes.pop(node_id, None)
            if self._load_failed:
                _LOGGER.warning(
                    "Not persisting boost preferences: the stored record is unreadable"
                )
                return
            try:
                await self._store.async_save({"nodes": self._nodes})
            except OSError:
                _LOGGER.warning("Could not persist the boost preferences")

    async def async_clear(self) -> None:
        """Remove the record (entry removal or a re-pair)."""
        async with self._lock:
            self._nodes = {}
            await self._store.async_remove()


def boost_prefs_for(hass: HomeAssistant, entry_id: str) -> BoostPrefs:
    """Build the store for one entry (the only construction path)."""
    return BoostPrefs(hass, entry_id)


__all__ = ["BoostPrefs", "NodeBoostPrefs", "boost_prefs_for"]
