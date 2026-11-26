"""Domain service contract snapshot.

The field names and selector shapes are read from ``services.yaml`` (the
user-facing schema) and checked against the runtime registration and a golden.
A rename, dropped field or selector change fails here. The registered
``vol`` schema is also compared field by field, so a YAML selector cannot
drift from the validator that actually accepts the call.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from homeassistant.helpers import config_validation as cv
import pytest
import voluptuous as vol
import yaml

from custom_components.smartbox.const import DOMAIN

FIXTURE = Path(__file__).parent / "fixtures" / "services.json"
SERVICES_YAML = (
    Path(__file__).parents[1] / "custom_components" / "smartbox" / "services.yaml"
)

# Added by ``cv.ENTITY_SERVICE_FIELDS``; the YAML target block covers them.
ENTITY_FIELDS = {marker.schema for marker in cv.ENTITY_SERVICE_FIELDS}
# The temperature selector spans both node units (5-30 °C / 41-86 °F), so its
# bounds are a UI hint; the entity validates the value in the node's unit.
ADVISORY_NUMBER_RANGES = {
    ("set_boost_mode", "temperature"),
    ("set_boost_params", "temperature"),
}


def _fields(body: dict) -> dict[str, object]:
    fields = {}
    for name, field in sorted((body.get("fields") or {}).items()):
        selector = field.get("selector")
        fields[name] = {
            "required": bool(field.get("required", False)),
            "selector": selector,
        }
    return fields


def _contract() -> dict[str, object]:
    services = yaml.safe_load(SERVICES_YAML.read_text(encoding="utf-8")) or {}
    return {
        name: {"fields": _fields(body)}
        for name, body in sorted(services.items())
        if isinstance(body, dict)
    }


def _schema_fields(schema: object) -> dict[str, tuple[object, object]]:
    """Return ``{field: (marker, validator)}`` for the inner ``vol.Schema``."""
    node = schema
    while not isinstance(node, vol.Schema):
        node = node.validators[0]
    return {marker.schema: (marker, node.schema[marker]) for marker in node.schema}


def _unwrap(value: object) -> tuple[object, ...]:
    return value.validators if isinstance(value, vol.All) else (value,)


async def test_service_contract_snapshot(hass, mock_smartbox, config_entry) -> None:
    """Service names, YAML fields and selector shapes are unchanged."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    contract = _contract()
    runtime = set(hass.services.async_services().get(DOMAIN, {}))
    assert set(contract) == runtime

    if os.environ.get("UPDATE_SNAPSHOTS"):
        FIXTURE.write_text(
            json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        pytest.skip("snapshot updated")
    assert contract == json.loads(FIXTURE.read_text(encoding="utf-8"))


async def test_yaml_selectors_match_the_registered_schemas(
    hass, mock_smartbox, config_entry
) -> None:
    """Every YAML field is in the runtime schema with the same accept rule."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    contract = _contract()
    services = hass.services.async_services()[DOMAIN]
    for name, body in contract.items():
        fields = _schema_fields(services[name].schema)
        assert set(body["fields"]) == set(fields) - ENTITY_FIELDS, name
        for field, spec in body["fields"].items():
            marker, validator = fields[field]
            assert isinstance(marker, vol.Required) is spec["required"], (name, field)
            selector = spec["selector"] or {}
            kind = next(iter(selector))
            parts = _unwrap(validator)
            if kind == "boolean":
                assert any(part is cv.boolean for part in parts), (name, field)
            elif kind == "select":
                options = next(
                    part.container for part in parts if isinstance(part, vol.In)
                )
                assert list(options) == selector["select"]["options"], (name, field)
            elif kind == "number":
                ranges = [part for part in parts if isinstance(part, vol.Range)]
                if (name, field) in ADVISORY_NUMBER_RANGES:
                    assert not ranges, f"{name}.{field} has a validator range"
                else:
                    assert ranges, f"{name}.{field} has no validator range"
                    assert selector["number"]["min"] == ranges[0].min
                    assert selector["number"]["max"] == ranges[0].max
            elif kind == "object":
                assert any(
                    isinstance(part, (dict, list, vol.Schema)) for part in parts
                ), (name, field)
            else:
                pytest.fail(f"unexpected selector {kind} on {name}.{field}")
