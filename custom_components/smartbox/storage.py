"""Adapter mapping for the neutral storage semantics.

The decoded storage rules live in ``smartbox.device.storage``; this module
maps its neutral intents to Home Assistant's ``HVACMode`` and keeps the
integration's existing return shapes for the climate entity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.climate import HVACMode  # type: ignore[attr-defined]
from smartbox.device.storage import (
    OPERATIONAL_AUTO_OFF,
    OPERATIONAL_MANUAL_OFF,
    UI_MODE_CLASSIC_ACCUMULATOR,
    UI_MODE_CLASSIC_COMBINED,
    UI_MODE_ECOMBI,
    StorageIntent,
    available_modes as _available_modes,
    boost_supported,
    classic_accumulator,
    current_mode as _current_mode,
    is_elnur_storage,
    is_helki_storage,
    is_water_storage,
    lock_endpoint,
    operational_mode,
    ui_mode,
    ui_mode_available,
    ui_mode_options,
    water_extra_energy_enabled,
    water_extra_energy_mode,
    write_for as _write_for,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class StorageMode:
    """One resolved storage-heater mode write in HA terms."""

    kind: str  # "charge" | "status"
    hvac_mode: HVACMode
    target_charge: int | None = None
    mode: str | None = None


def available_modes(
    node_type: str, pid: str | None, setup: Mapping[str, object]
) -> list[HVACMode]:
    """Return the HA modes the app exposes for this storage controller."""
    return [
        HVACMode(intent.value) for intent in _available_modes(node_type, pid, setup)
    ]


def current_mode(
    node_type: str,
    pid: str | None,
    setup: Mapping[str, object],
    status: Mapping[str, object],
) -> HVACMode | None:
    """Return the app's current storage mode as an HA mode."""
    intent = _current_mode(node_type, pid, setup, status)
    return HVACMode(intent.value) if intent is not None else None


def write_for(
    node_type: str,
    pid: str | None,
    setup: Mapping[str, object],
    hvac_mode: HVACMode,
) -> StorageMode | None:
    """Translate one HA mode into the storage operation the app sends."""
    write = _write_for(node_type, pid, setup, StorageIntent(hvac_mode.value))
    if write is None:
        return None
    return StorageMode(
        kind=write.kind,
        hvac_mode=HVACMode(write.intent.value),
        target_charge=write.target_charge,
        mode=write.mode,
    )


__all__ = [
    "OPERATIONAL_AUTO_OFF",
    "OPERATIONAL_MANUAL_OFF",
    "UI_MODE_CLASSIC_ACCUMULATOR",
    "UI_MODE_CLASSIC_COMBINED",
    "UI_MODE_ECOMBI",
    "StorageMode",
    "available_modes",
    "boost_supported",
    "classic_accumulator",
    "current_mode",
    "is_elnur_storage",
    "is_helki_storage",
    "is_water_storage",
    "lock_endpoint",
    "operational_mode",
    "ui_mode",
    "ui_mode_available",
    "ui_mode_options",
    "water_extra_energy_enabled",
    "water_extra_energy_mode",
    "write_for",
]
