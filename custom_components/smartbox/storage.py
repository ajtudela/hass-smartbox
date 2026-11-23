"""Storage-heater mode semantics from the 2.54.2 controller classes.

Two controller families share the ``acm`` wire type:

* HelkiStorage (product codes 1C/2E): standard AUTO/MANUAL/OFF/ON modes,
  filtered by ``setup.operational_mode``, plus the legacy ``/boost`` endpoint.
* ElnurStorage (14/20/25/2A): ``factory_options.ui_mode`` selects ECOMBI,
  CLASSIC_COMBINED or CLASSIC_ACCUMULATOR. In CLASSIC_ACCUMULATOR the app's
  ON/OFF write ``target_charge_per`` 100/0 instead of a mode, and temperature
  control is not available.

The integration uses the status path for the non-classic modes; the decoded
``/mode`` endpoint remains available for the app's assistant path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from homeassistant.components.climate import HVACMode  # type: ignore[attr-defined]

from .const import SmartboxNodeType
from .product import product_code

UI_MODE_ECOMBI = 0
UI_MODE_CLASSIC_COMBINED = 1
UI_MODE_CLASSIC_ACCUMULATOR = 2

OPERATIONAL_MANUAL_OFF = 0
OPERATIONAL_AUTO_OFF = 1

_HELKI_STORAGE_CODES = frozenset({"1C", "2E"})
_ELNUR_STORAGE_CODES = frozenset({"14", "20", "25", "2A"})


@dataclass(frozen=True)
class StorageMode:
    """One resolved storage-heater mode write."""

    kind: str  # "charge" | "status"
    hvac_mode: HVACMode
    target_charge: int | None = None
    mode: str | None = None


def _product_code(pid: str | None) -> str | None:
    return product_code(pid)


def is_helki_storage(pid: str | None) -> bool:
    """Return True for the 1C/2E storage controller."""
    return _product_code(pid) in _HELKI_STORAGE_CODES


def is_elnur_storage(pid: str | None) -> bool:
    """Return True for the Elnur 14/20/25/2A storage controllers."""
    return _product_code(pid) in _ELNUR_STORAGE_CODES


def is_water_storage(pid: str | None) -> bool:
    """Return True for the Elnur water-storage controller (2A)."""
    return _product_code(pid) == "2A"


def _extra_nrg_conf(setup: Mapping[str, object]) -> Mapping[str, object]:
    value = setup.get("extra_nrg_conf")
    return value if isinstance(value, Mapping) else {}


def water_extra_energy_enabled(setup: Mapping[str, object]) -> bool:
    """Whether the 2A extra-energy configuration is enabled."""
    return bool(_extra_nrg_conf(setup).get("enabled"))


def water_extra_energy_mode(setup: Mapping[str, object]) -> int | None:
    """Return the 2A extra-energy mode (0 EXTRA_ENERGY_USAGE) when numeric."""
    value = _extra_nrg_conf(setup).get("mode")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def ui_mode(setup: Mapping[str, object]) -> int | None:
    """Return ``factory_options.ui_mode`` (0/1/2) when present."""
    factory = setup.get("factory_options")
    if not isinstance(factory, Mapping):
        return None
    value = factory.get("ui_mode")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


# The app's STORAGE_HEATER_UI_MODE setting is per product:
# 14 accepts CLASSIC_COMBINED/CLASSIC_ACCUMULATOR; the E2 products 20/25
# accept ECOMBI/CLASSIC_COMBINED; water storage 2A omits the setting.
_UI_MODE_OPTIONS: dict[str, tuple[tuple[str, int], ...]] = {
    "14": (
        ("Classic combined", UI_MODE_CLASSIC_COMBINED),
        ("Classic accumulator", UI_MODE_CLASSIC_ACCUMULATOR),
    ),
    "20": (
        ("Ecombi", UI_MODE_ECOMBI),
        ("Classic combined", UI_MODE_CLASSIC_COMBINED),
    ),
    "25": (
        ("Ecombi", UI_MODE_ECOMBI),
        ("Classic combined", UI_MODE_CLASSIC_COMBINED),
    ),
}


def ui_mode_options(pid: str | None) -> tuple[tuple[str, int], ...]:
    """Return the app's accepted storage UI-mode choices for this product."""
    return _UI_MODE_OPTIONS.get(_product_code(pid) or "", ())


def ui_mode_available(pid: str | None, setup: Mapping[str, object]) -> bool:
    """Whether the app shows the storage UI-mode setting.

    ``ElnurStorageHeaterController.isSettingAvailable`` only offers it while the
    unit is already in a classic mode; ECOMBI hides the setting, and water
    storage does not list it at all.
    """
    return bool(ui_mode_options(pid)) and ui_mode(setup) in (
        UI_MODE_CLASSIC_COMBINED,
        UI_MODE_CLASSIC_ACCUMULATOR,
    )


def operational_mode(setup: Mapping[str, object]) -> int | None:
    """Return ``setup.operational_mode`` (0 manual, 1 auto) when present."""
    value = setup.get("operational_mode")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def classic_accumulator(pid: str | None, setup: Mapping[str, object]) -> bool:
    """Whether this Elnur unit uses the target-charge ON/OFF UI."""
    return is_elnur_storage(pid) and ui_mode(setup) == UI_MODE_CLASSIC_ACCUMULATOR


def available_modes(
    node_type: str, pid: str | None, setup: Mapping[str, object]
) -> list[HVACMode]:
    """Return the HA HVAC modes the app exposes for this storage controller.

    Helki op=1 (``AUTO_OFF_AVAILABLE``) is a deliberate superset of the app's
    picker: the app's boost deactivation posts wire mode ``manual`` on that
    configuration even though its mode list omits MANUAL, so HEAT must be
    offered to represent the state the app itself produces.
    """
    if node_type != SmartboxNodeType.ACM:
        return [HVACMode.HEAT, HVACMode.AUTO, HVACMode.OFF]
    if classic_accumulator(pid, setup):
        # ON/OFF only; the app's ON is HEAT here.
        return [HVACMode.HEAT, HVACMode.OFF]
    modes: list[HVACMode] = []
    if is_elnur_storage(pid):
        if is_water_storage(pid):
            # ON resolves to the app's default mode: MANUAL when AUTO is
            # filtered out (EXTRA_ENERGY_USAGE), AUTO in the combined modes.
            if not water_extra_energy_enabled(setup):
                modes.extend((HVACMode.AUTO, HVACMode.HEAT))
            elif water_extra_energy_mode(setup) == 0:
                modes.append(HVACMode.HEAT)
            else:
                modes.append(HVACMode.AUTO)
        else:
            mode = ui_mode(setup)
            if mode == UI_MODE_ECOMBI:
                modes.append(HVACMode.AUTO)
            if mode != UI_MODE_CLASSIC_ACCUMULATOR:
                modes.append(HVACMode.HEAT)
    else:
        # HelkiStorage, and the app factory's default for an unknown/00 acm.
        op = operational_mode(setup)
        if op == OPERATIONAL_AUTO_OFF:
            modes.append(HVACMode.AUTO)
        modes.append(HVACMode.HEAT)
    modes.append(HVACMode.OFF)
    # Keep a usable mode list even if every capability filter dropped out.
    return modes or [HVACMode.OFF]


def current_mode(  # noqa: PLR0911 - one return per mode
    node_type: str,
    pid: str | None,
    setup: Mapping[str, object],
    status: Mapping[str, object],
) -> HVACMode | None:
    """Return the app's current storage mode."""
    if node_type != SmartboxNodeType.ACM:
        return None
    if status.get("boost", False):
        # The app's base heating controller resolves BOOST before status.mode;
        # a storage unit in boost is heating even while mode still says "off".
        return HVACMode.HEAT
    if classic_accumulator(pid, setup):
        if status.get("mode") == "off":
            return HVACMode.OFF
        target = status.get("target_charge_per")
        if target in (0, "0", 0.0):
            return HVACMode.OFF
        return HVACMode.HEAT
    mode = status.get("mode")
    if mode == "off" or mode is None:
        return HVACMode.OFF
    if mode in ("auto", "modified_auto"):
        return HVACMode.AUTO
    if mode in ("manual", "on"):
        return HVACMode.HEAT
    return None


def write_for(  # noqa: C901, PLR0911 - one return per mode
    node_type: str,
    pid: str | None,
    setup: Mapping[str, object],
    hvac_mode: HVACMode,
) -> StorageMode | None:
    """Translate one HA mode into the storage operation the app sends."""
    if node_type != SmartboxNodeType.ACM:
        return None
    if classic_accumulator(pid, setup):
        if hvac_mode == HVACMode.HEAT:
            return StorageMode("charge", HVACMode.HEAT, target_charge=100)
        if hvac_mode == HVACMode.OFF:
            return StorageMode("charge", HVACMode.OFF, target_charge=0)
        return None
    if is_water_storage(pid) and water_extra_energy_enabled(setup):
        # The app removes MANUAL in the combined extra-energy modes and AUTO
        # in EXTRA_ENERGY_USAGE. Reject the mode it does not offer.
        if hvac_mode == HVACMode.HEAT and water_extra_energy_mode(setup) != 0:
            return None
        if hvac_mode == HVACMode.AUTO and water_extra_energy_mode(setup) == 0:
            return None
    if hvac_mode == HVACMode.HEAT:
        return StorageMode("status", HVACMode.HEAT, mode="manual")
    if hvac_mode == HVACMode.AUTO:
        return StorageMode("status", HVACMode.AUTO, mode="auto")
    if hvac_mode == HVACMode.OFF:
        return StorageMode("status", HVACMode.OFF, mode="off")
    return None


def boost_supported(node_type: str, pid: str | None) -> bool:
    """Storage boost is the Helki 1C/2E ``/boost`` path.

    The app's unlisted-acm factory default is HelkiStorage, so an unknown ACM
    PID keeps the legacy boost path; the Elnur families do not.
    """
    return node_type == SmartboxNodeType.ACM and not is_elnur_storage(pid)


def lock_endpoint(node_type: str) -> bool:
    """Storage lock writes use the dedicated ``/lock`` endpoint."""
    return node_type == SmartboxNodeType.ACM
