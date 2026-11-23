"""Domain service contract snapshot.

Service names, field names and selector shapes are the adapter's public
contract. The device-semantics extraction must not rename a service, drop a
field or change a selector while moving profile data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from custom_components.smartbox.const import DOMAIN

FIXTURE = Path(__file__).parent / "fixtures" / "services.json"


def _contract(hass) -> dict[str, object]:
    services = hass.services.async_services().get(DOMAIN, {})
    contract: dict[str, object] = {}
    for name in sorted(services):
        service = services[name]
        contract[name] = {
            "supports_response": str(service.supports_response),
        }
    return contract


async def test_service_contract_snapshot(hass, mock_smartbox, config_entry) -> None:
    """The domain service names, fields and selectors are unchanged."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    contract = _contract(hass)
    if os.environ.get("UPDATE_SNAPSHOTS"):
        FIXTURE.write_text(
            json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    assert contract == json.loads(FIXTURE.read_text(encoding="utf-8"))
