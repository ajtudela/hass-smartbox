"""Reseller table access (config-entry data -> reseller identity)."""

from dataclasses import replace
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from smartbox.protocol.reseller import (
    API_HOST,
    AvailableResellers,
    SmartboxReseller,
    api_base_url,
)

from .const import CONF_API_NAME

__all__ = [
    "API_HOST",
    "RESELLER_OVERRIDES",
    "api_base_url",
    "reseller_from_entry_data",
]

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)

# Manual testing only. Keyed by the entry's api_name (the selected brand key),
# these values replace the built-in row fields before AuthConfig is built.
# Uncomment/add an entry to test a host (hostname or https URL), serial id,
# Basic credential or referer; reapply after an integration update, or keep a
# copy outside the integration. Entry passwords do not belong here. Example:
#   "api-elnur": {"api_host": "https://api-elnur.helki.com"},
RESELLER_OVERRIDES: dict[str, dict[str, Any]] = {}

_ALLOWED_OVERRIDES = frozenset({"api_host", "basic_auth", "serial_id", "web_url"})
_LOGGED_OVERRIDES: set[str] = set()


def _normalize_api_host(value: object) -> str:
    """Accept a hostname or HTTPS URL and return the bare host (plus port)."""
    if not isinstance(value, str) or not value or value != value.strip():
        msg = "api_host override must be a non-empty hostname or https URL"
        raise ValueError(msg)
    candidate = value if "://" in value else f"//{value}"
    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as err:
        msg = "api_host override is not a valid hostname or https URL"
        raise ValueError(msg) from err
    if parsed.scheme not in ("", "https"):
        msg = "api_host override must use https (or omit the scheme)"
        raise ValueError(msg)
    if (
        parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or hostname is None
        or not parsed.netloc
    ):
        msg = (
            "api_host override must not contain a path, query, fragment or credentials"
        )
        raise ValueError(msg)
    return parsed.netloc


def reseller_from_entry_data(data: Mapping[str, str]) -> SmartboxReseller:
    """Return the reseller row for the entry's api_name, with overrides."""
    api_name = data[CONF_API_NAME]
    reseller = AvailableResellers(api_url=api_name).reseller
    overrides = RESELLER_OVERRIDES.get(api_name)
    if not overrides:
        return reseller
    unknown = set(overrides) - _ALLOWED_OVERRIDES
    if unknown:
        msg = f"Unsupported reseller override fields: {', '.join(sorted(unknown))}"
        raise ValueError(msg)
    overrides = dict(overrides)
    if "api_host" in overrides:
        overrides["api_host"] = _normalize_api_host(overrides["api_host"])
    if api_name not in _LOGGED_OVERRIDES:
        _LOGGED_OVERRIDES.add(api_name)
        _LOGGER.warning(
            "Manual reseller override active for %s: %s",
            api_name,
            ", ".join(sorted(overrides)),
        )
    return replace(reseller, **overrides)
