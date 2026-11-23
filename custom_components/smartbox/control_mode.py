"""Per-product control-mode catalogue (re-exported from the library).

The decoded maps and resolution live in ``smartbox.device.control_modes`` so
the CLI and the integration share one neutral implementation.
"""

from __future__ import annotations

from smartbox.device.control_modes import (
    ACTUATION,
    EXPLICIT_FAMILY,
    HTR_RELAY_UI,
    HTR_TRIAC_UI,
    HTR_WIRE,
    HYSTERESIS_ONLY_UI,
    MODE_LABELS_C,
    MODE_LABELS_F,
    STORAGE_WIRE,
    _choices,
    control_mode_available,
    control_mode_choices,
    control_mode_label,
)

__all__ = [
    "ACTUATION",
    "EXPLICIT_FAMILY",
    "HTR_RELAY_UI",
    "HTR_TRIAC_UI",
    "HTR_WIRE",
    "HYSTERESIS_ONLY_UI",
    "MODE_LABELS_C",
    "MODE_LABELS_F",
    "STORAGE_WIRE",
    "_choices",
    "control_mode_available",
    "control_mode_choices",
    "control_mode_label",
]
