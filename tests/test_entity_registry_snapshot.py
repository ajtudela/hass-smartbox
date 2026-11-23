"""Entity-registry golden snapshot for the mock account.

The surface matrix covers selected families through the builders directly.
This snapshot exercises the whole setup path and records the registry identity
and the static attributes HA exposes for every entity. It is the baseline for
the device-semantics extraction: a slice that changes an entity id, unique id,
translation key, option list or bound fails here even when the builders still
produce the same keys.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from homeassistant.helpers import entity_registry as er

from custom_components.smartbox.const import DOMAIN
from tests.stack import seed_stack

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _value(value: object) -> object:
    if hasattr(value, "value"):
        return value.value
    return value


def _snapshot(hass) -> dict[str, dict[str, object]]:
    registry = er.async_get(hass)
    entries = sorted(
        (entry for entry in registry.entities.values() if entry.platform == DOMAIN),
        key=lambda entry: entry.entity_id,
    )
    snapshot: dict[str, dict[str, object]] = {}
    for entry in entries:
        state = hass.states.get(entry.entity_id)
        attributes = dict(state.attributes) if state is not None else {}
        record: dict[str, object] = {
            "unique_id": entry.unique_id,
            "translation_key": entry.translation_key,
            "entity_category": _value(entry.entity_category),
            "disabled_by": _value(entry.disabled_by),
            "device_class": attributes.get("device_class"),
            "unit": attributes.get("unit_of_measurement"),
        }
        for key in ("options", "min", "max", "step", "hvac_modes", "preset_modes"):
            if key in attributes:
                record[key] = attributes[key]
        snapshot[entry.entity_id] = record
    return snapshot


async def test_entity_registry_snapshot(
    hass, mock_smartbox, config_entry, units
) -> None:
    """Every entity's registry identity and static attributes are unchanged."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)
    fixture = FIXTURE_DIR / f"entity_registry_{units}.json"
    snapshot = _snapshot(hass)

    if os.environ.get("UPDATE_SNAPSHOTS"):
        fixture.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    expected = json.loads(fixture.read_text(encoding="utf-8"))
    assert snapshot == expected


async def test_entity_registry_survives_a_reload(
    hass, mock_smartbox, config_entry, units
) -> None:
    """A reload rebuilds the same registry surface and attributes."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)
    before = _snapshot(hass)

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)
    assert _snapshot(hass) == before
