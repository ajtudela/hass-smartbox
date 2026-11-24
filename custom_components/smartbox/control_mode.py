"""Per-product control-mode catalogue (re-exported from the library).

The decoded maps and resolution live in ``smartbox.device.control_modes`` so
the CLI and the integration share one neutral implementation.
"""

from __future__ import annotations

from smartbox.device.control_modes import (
    control_mode_available,
    control_mode_choices,
    control_mode_label,
)

__all__ = [
    "control_mode_available",
    "control_mode_choices",
    "control_mode_label",
]
