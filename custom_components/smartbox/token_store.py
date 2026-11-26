"""Persist one config entry's token bundle across Home Assistant restarts.

The vendor rotates the refresh token on every grant, so the stored copy is
rewritten from the ``on_grant`` seam of the protocol layer. Entry data is
deliberately untouched: the integration's update listener reloads the entry
whenever that data changes, and a rotation there would reload it every few
hours.

The record is stamped with the entry's account (api name, serial, case-folded
username and effective API host). A reconfigure or host override that points
the entry at another account/endpoint therefore leaves a record the new
runtime refuses to restore, even if the old runtime completed a grant after
the flow cleared the file. The stamp is record ownership; the library
additionally checks the access token's `email`/`serialId` claims against the
configured account, which covers a record whose stamp matches but whose tokens
belong elsewhere. Keeping both matters for opaque tokens, where the claim
check cannot run.

The file lives in ``.storage`` with the same trust level as the config entry
(which already carries the password), written owner-only and atomically.
Diagnostics never read it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_USERNAME
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from smartbox.protocol.auth import TokenBundle
from smartbox.protocol.errors import ResellerNotExistError
from smartbox.token_cache import account_stamp

from .const import CONF_API_NAME, DOMAIN
from .reseller import api_base_url, reseller_from_entry_data

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant
    from smartbox.protocol.reseller import SmartboxReseller

__all__ = [
    "TokenStore",
    "account_from_entry_data",
    "storage_key",
    "token_store_for",
]

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
_STORAGE_KEY = f"{DOMAIN}.tokens"
_LOCKS_KEY = "_token_store_locks"


def _lock_for(hass: HomeAssistant, key: str) -> asyncio.Lock:
    """Return the per-storage-key lock shared by every store instance.

    The lock lives in ``hass.data`` so it is scoped to one home-assistant
    instance (and one event loop) while still serialising a clear issued by a
    fresh instance against an in-flight save from the retired runtime.
    """
    locks: dict[str, asyncio.Lock] = hass.data.setdefault(DOMAIN, {}).setdefault(
        _LOCKS_KEY, {}
    )
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


def storage_key(entry_id: str) -> str:
    """Return the ``.storage`` key holding one entry's bundle."""
    return f"{_STORAGE_KEY}.{entry_id}"


def account_from_entry_data(
    data: Mapping[str, Any], *, reseller: SmartboxReseller | None = None
) -> str:
    """Return the record's account stamp (reseller, serial, user, host).

    The same format the library's CLI cache uses, so a record's owner is
    expressed one way everywhere. An unknown reseller (a malformed entry, or
    a test fixture) falls back to no serial/host; the key and the claim check
    still identify the owner.
    """
    try:
        resolved = reseller if reseller is not None else reseller_from_entry_data(data)
        serial = str(resolved.serial_id)
        host = api_base_url(resolved)
    except ResellerNotExistError:
        serial = ""
        host = ""
    return account_stamp(
        str(data[CONF_API_NAME]), serial, str(data[CONF_USERNAME]), host
    )


def token_store_for(
    hass: HomeAssistant,
    entry_id: str,
    data: Mapping[str, Any],
    *,
    reseller: SmartboxReseller | None = None,
) -> TokenStore:
    """Build the store for one entry (the only construction path)."""
    return TokenStore(hass, entry_id, account_from_entry_data(data, reseller=reseller))


class TokenStore:
    """One entry's token bundle in Home Assistant's storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str, account: str) -> None:
        """Bind the store to one config entry and its account stamp."""
        self._account = account
        self._key = storage_key(entry_id)
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            self._key,
            private=True,
            atomic_writes=True,
        )
        self._lock = _lock_for(hass, self._key)
        self._closed = False

    def close(self) -> None:
        """Stop accepting new writes from this store instance.

        An in-flight save still completes; :meth:`async_clear` on the shared
        key lock waits for it before removing the record.
        """
        self._closed = True

    async def async_load(self) -> TokenBundle | None:
        """Return the stored bundle, or ``None`` when absent or unusable.

        Unreadable or malformed storage degrades to a password grant: the
        record is only a cache, so it must never block setup.
        """
        # Share the write lock so a reload loads the rotation the retiring
        # runtime may still be saving, rather than the record it replaces.
        async with self._lock:
            try:
                data = await self._store.async_load()
            except (
                HomeAssistantError,
                OSError,
                KeyError,
                NotImplementedError,
                TypeError,
                ValueError,
            ) as err:
                _LOGGER.warning(
                    "Could not read stored smartbox tokens (%s: %s); "
                    "starting with a password grant",
                    self._key,
                    type(err).__name__,
                )
                return None
            if not isinstance(data, dict):
                return None
            return self._decode_record(data)

    def _decode_record(self, data: dict[str, Any]) -> TokenBundle | None:
        """Validate one stored record; ``None`` when it is unusable."""
        if data.get("account") != self._account:
            _LOGGER.debug("Ignoring stored smartbox tokens for a different account")
            return None
        try:
            return TokenBundle.from_record(data)
        except ValueError:
            _LOGGER.debug("Ignoring malformed stored smartbox tokens (%s)", self._key)
            return None

    async def async_save(self, tokens: TokenBundle) -> None:
        """Write a newly granted bundle (called for every rotation)."""
        async with self._lock:
            if self._closed:
                return
            try:
                await self._store.async_save(
                    {"account": self._account, **tokens.to_record()}
                )
            except (HomeAssistantError, OSError) as err:
                _LOGGER.warning(
                    "Could not persist smartbox tokens (%s: %s); "
                    "the next restart will run a password grant",
                    self._key,
                    type(err).__name__,
                )

    async def async_clear(self) -> None:
        """Drop the stored bundle (reauth, reconfigure, entry removal).

        Waits for an in-flight save first, so a rotation from a retired
        runtime cannot resurrect the record after this returns. The instance
        is retired as well: later grants are not written.
        """
        async with self._lock:
            self._closed = True
            try:
                await self._store.async_remove()
            except (HomeAssistantError, OSError) as err:
                _LOGGER.warning(
                    "Could not remove stored smartbox tokens (%s: %s)",
                    self._key,
                    type(err).__name__,
                )
