"""Diagnostics for Smartbox Integration.

Everything debuggable comes from the runtime: the supervised ws_user
transport, the engine's terminal supervision state, the token expiry, the
engine store, and the deadlines that will flip a node Unavailable. Credential
material never reaches the store, so the entry dict is the only place
credentials can appear.

Account and device identifiers are redacted too: a ``dev_id`` also opens the
device websocket, and the home id, serials and registry deep links identify the
installation in a file users paste into public issues. Device ids are replaced
with stable ``device_N`` labels, so the engine, membership and statistics
sections stay correlatable without exposing the ids themselves.
"""

from collections.abc import Mapping
from dataclasses import asdict
import re
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from smartbox.device.profile import resolve_profile
from smartbox.protocol.errors import ResellerNotExistError

from . import ws_user_flapping_issue_id
from .const import CONF_API_NAME, DOMAIN
from .reseller import RESELLER_OVERRIDES, api_base_url, reseller_from_entry_data

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import SmartboxConfigEntry, SmartboxRuntime

TO_REDACT = [CONF_PASSWORD, CONF_USERNAME, "device_ids", "title", "unique_id"]
# Identifiers users paste into public issues. A generic "id" key is NOT
# redacted (it would hide the registry's own ids); the device entries carry
# their identifiers under these names.
TO_REDACT_IDENTIFIERS = [
    "dev_id",
    "serial_id",
    "serial_number",
    "uid",
    "identifiers",
    "configuration_url",
    "home_id",
]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: SmartboxConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    # getattr: the attribute is ABSENT while setup never ran/retried.
    runtime: SmartboxRuntime | None = getattr(config_entry, "runtime_data", None)
    labels = _device_labels(runtime)
    diagnostics_data: dict[str, Any] = {
        "entry": async_redact_data(config_entry.as_dict(), TO_REDACT),
        "reseller_override": _reseller_override_diagnostics(config_entry),
        # A runtime is absent while the entry is failing/retrying setup.
        "runtime": _runtime_diagnostics(runtime, labels),
        "ws_user_recovery": _ws_user_recovery_diagnostics(hass, config_entry),
        "hass_devices": [
            async_redact_data(entry.dict_repr, TO_REDACT_IDENTIFIERS)
            for entry in dr.async_entries_for_config_entry(
                dr.async_get(hass), config_entry.entry_id
            )
        ],
        "hass_entities": [
            async_redact_data(entry.as_partial_dict, TO_REDACT)
            for entry in er.async_entries_for_config_entry(
                er.async_get(hass), config_entry.entry_id
            )
        ],
    }
    return {
        key: _scrub_identifiers(value, labels)
        for key, value in diagnostics_data.items()
    }


def _reseller_override_diagnostics(
    config_entry: SmartboxConfigEntry,
) -> dict[str, Any]:
    """Report whether a manual override is active and the effective host."""
    api_name = config_entry.data.get(CONF_API_NAME)
    overrides = (
        RESELLER_OVERRIDES.get(api_name, {}) if isinstance(api_name, str) else {}
    )
    fields = sorted(overrides)
    try:
        reseller = reseller_from_entry_data(config_entry.data)
        return {
            "active": bool(overrides),
            "fields": fields,
            "api_host": api_base_url(reseller),
        }
    except (KeyError, ValueError, ResellerNotExistError) as ex:
        return {"active": bool(overrides), "fields": fields, "error": str(ex)}


def _device_labels(runtime: SmartboxRuntime | None) -> dict[str, str]:
    """Map account device ids to stable diagnostic labels."""
    if runtime is None:
        return {}
    dev_ids = {device.dev_id for device in runtime.identity.devices if device.dev_id}
    dev_ids.update(dev_id for dev_id in runtime.engine.world.devices if dev_id)
    return {
        dev_id: f"device_{index}"
        for index, dev_id in enumerate(sorted(dev_ids), start=1)
    }


def _identifier_pattern(labels: Mapping[str, str]) -> re.Pattern[str] | None:
    """Match whole device ids, including inside node ids and free-text labels."""
    if not labels:
        return None
    # Longest first: device_10_0 must match device_10, not device_1. The
    # lookarounds keep a numeric dev_id from matching part of a larger number.
    return re.compile(
        r"(?<![A-Za-z0-9])("
        + "|".join(
            re.escape(dev_id) for dev_id in sorted(labels, key=len, reverse=True)
        )
        + r")(?![A-Za-z0-9])"
    )


def _label_identifier(
    value: str, labels: Mapping[str, str], pattern: re.Pattern[str] | None
) -> str:
    """Replace a device id where it is an identifier, not a coincidental number.

    Device ids appear as a whole value, as the prefix of a node/entity id, and
    inside the membership's ``"<node> (<dev_id>)"`` label. A numeric dev_id
    that happens to match a year or a reading is left alone.
    """
    if pattern is None:
        return value

    def replace(match: re.Match[str]) -> str:
        dev_id = match.group(0)
        start, end = match.span()
        is_whole_value = start == 0 and end == len(value)
        is_node_prefix = end < len(value) and value[end] == "_"
        is_label_suffix = (
            start > 0
            and value[start - 1] == "("
            and end < len(value)
            and value[end] == ")"
        )
        if is_whole_value or is_node_prefix or is_label_suffix:
            return labels[dev_id]
        return dev_id

    return pattern.sub(replace, value)


def _scrub_identifiers(value: object, labels: Mapping[str, str]) -> object:
    """Recursively replace raw device/node ids with their diagnostic labels."""
    pattern = _identifier_pattern(labels)

    def scrub(item: object) -> object:
        if isinstance(item, Mapping):
            return {
                _label_identifier(key, labels, pattern)
                if isinstance(key, str)
                else key: scrub(entry)
                for key, entry in item.items()
            }
        if isinstance(item, list):
            return [scrub(entry) for entry in item]
        if isinstance(item, tuple):
            return tuple(scrub(entry) for entry in item)
        if isinstance(item, str):
            return _label_identifier(item, labels, pattern)
        return item

    return scrub(value)


def _runtime_diagnostics(
    runtime: SmartboxRuntime | None, labels: Mapping[str, str]
) -> dict[str, Any]:
    """Transport, supervision, token and engine-store facts from the runtime.

    The transport comes first: a supervised ws_user transport restarting reads
    very differently from a dead socket.
    """
    if runtime is None:
        return {
            "transport": None,
            "supervision": None,
            "token_expiry": None,
            "token_origin": None,
            "feed": None,
            "membership": None,
            "statistics_import": None,
        }
    return {
        "transport": {"type": "ws_user", **runtime.transport.facts},
        # Terminal supervision state (ws_user disappeared / rejected
        # credentials): the monitor re-probes or starts reauth on these, so a
        # stuck entry shows up here.
        "supervision": {
            "unsupported": runtime.engine.unsupported,
            "auth_failed": runtime.engine.auth_failed,
            "terminal_error": (
                None
                if runtime.engine.terminal_error is None
                else str(runtime.engine.terminal_error)
            ),
        },
        "token_expiry": (
            None
            if (expiry := runtime.auth.token_expiry) is None
            else expiry.isoformat()
        ),
        # Whether setup resumed the stored bundle or minted a fresh session.
        "token_origin": runtime.token_origin,
        # Feed (websocket) state: whether values are still flowing, why the
        # feed went down, how long it has been down and the outage history.
        "feed": runtime.engine.feed.facts,
        # Nodes reported after setup: the reload hint and what it lists. The
        # removal identifier can name a registry device that is no longer in
        # the account, so it has no label to map to; the label field carries
        # the readable name.
        "membership": async_redact_data(runtime.membership.facts, ["identifier"]),
        # First-setup history import: which nodes are still pending, which
        # completed, and the backoff/error state of the ones that failed.
        "statistics_import": runtime.history.diagnostics(),
        "engine": _engine_diagnostics(runtime, labels),
    }


def _ws_user_recovery_diagnostics(
    hass: HomeAssistant, config_entry: SmartboxConfigEntry
) -> dict[str, Any]:
    """Report whether the ws_user repair issue is raised.

    The in-place re-probe streak lives on the monitor task, so the repair issue
    is the durable signal that the endpoint has been refusing.
    """
    issue_id = (DOMAIN, ws_user_flapping_issue_id(config_entry))
    return {"repair_issue_raised": issue_id in ir.async_get(hass).issues}


def _node_profile(
    node_key: str,
    node: Any,  # noqa: ANN401 - raw engine slot
) -> dict[str, Any] | None:
    """Return the neutral controller profile the adapter would resolve."""
    node_type = node_key.split("/", 1)[0]
    version = node.version if isinstance(node.version, Mapping) else {}
    pid = version.get("pid")
    if not isinstance(pid, str) or not pid:
        return None
    fw_version = version.get("fw_version")
    profile = resolve_profile(
        node_type,
        pid,
        fw_version if isinstance(fw_version, str) else None,
        node.setup,
    )
    prog_bounds = profile.prog_bounds
    return {
        "code": profile.code,
        "limits": asdict(profile.limits),
        "climate_bounds": profile.climate_bounds,
        "max_limit_bounds": profile.max_limit_bounds,
        "boost_bounds": profile.boost_bounds,
        "prog_bounds": None if prog_bounds is None else dict(prog_bounds),
    }


def _engine_diagnostics(
    runtime: SmartboxRuntime, labels: Mapping[str, str]
) -> dict[str, Any]:
    """Return the engine store: device slots + per-node slots (raw wire)."""
    devices: dict[str, Any] = {}
    for dev_id, device in runtime.engine.world.devices.items():
        devices[labels.get(dev_id, "device_unknown")] = async_redact_data(
            {
                "connected": device.connected,
                "away": device.away,
                "power_limit": device.power_limit,
                "home_id": device.home_id,
                "nodes": {
                    key: {
                        "status": node.status,
                        "setup": node.setup,
                        "prog": node.prog,
                        "version": node.version,
                        "available": node.available,
                        "profile": _node_profile(key, node),
                    }
                    for key, node in device.node_states.items()
                },
            },
            TO_REDACT_IDENTIFIERS,
        )
    return {
        "devices": devices,
        # Which node is about to flip Unavailable, and why - the direct answer
        # to "why is this entity unavailable?".
        "armed_deadlines": [
            {
                "dev_id": labels.get(deadline.dev_id, "device_unknown"),
                "node_key": deadline.node_key,
                "kind": deadline.kind,
                "slot": deadline.slot,
                "expires_in": round(deadline.expires_in, 3),
            }
            for deadline in runtime.engine.deadlines
        ],
    }
