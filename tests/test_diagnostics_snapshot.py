"""Diagnostics golden snapshot for the profile-relevant runtime state.

The diagnostics payload carries the engine store (status/setup/version per
node), membership and supervision facts. Those are exactly the inputs the
device-semantics extraction moves; the volatile counters and deadlines are
excluded. The snapshot is the extraction's proof that the adapter still feeds
the library the same profile inputs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from custom_components.smartbox.diagnostics import async_get_config_entry_diagnostics
from tests.stack import seed_stack

FIXTURE_DIR = Path(__file__).parent / "fixtures"


async def test_diagnostics_snapshot(hass, mock_smartbox, config_entry, units) -> None:
    """The engine store and membership facts are unchanged."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await seed_stack(hass, mock_smartbox)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    runtime = diagnostics["runtime"]
    snapshot = json.loads(
        json.dumps(
            {
                "engine": runtime["engine"],
                "membership": {
                    "new_nodes": runtime["membership"]["new_nodes"],
                    "pending_removals": runtime["membership"]["pending_removals"],
                },
                "statistics_total_nodes": runtime["statistics_import"]["total_nodes"],
                "transport_type": runtime["transport"]["type"],
                "supervision": runtime["supervision"],
            },
            default=str,
        )
    )
    fixture = FIXTURE_DIR / f"diagnostics_engine_{units}.json"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        fixture.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    assert snapshot == json.loads(fixture.read_text(encoding="utf-8"))
