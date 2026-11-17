"""Tests for the persisted token bundle (``token_store``)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
import pytest

from custom_components.smartbox.reseller import RESELLER_OVERRIDES
from custom_components.smartbox.token_store import (
    STORAGE_VERSION,
    TokenStore,
    account_from_entry_data,
    storage_key,
)
from tests.test_utils import stored_token_bundle

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

ENTRY_ID = "entry_1"
ACCOUNT = "test_api_name:user@example.com"


def _record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "account": ACCOUNT,
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "expires_at": "2030-01-01T00:00:00+00:00",
    }
    record.update(overrides)
    return record


def _raw_store(hass: HomeAssistant) -> Store[dict]:
    return Store(hass, STORAGE_VERSION, storage_key(ENTRY_ID))


async def test_roundtrip_save_load_clear(hass: HomeAssistant) -> None:
    store = TokenStore(hass, ENTRY_ID, ACCOUNT)
    assert await store.async_load() is None

    bundle = stored_token_bundle(access="access-token", refresh="refresh-token")
    await store.async_save(bundle)

    loaded = await store.async_load()
    assert loaded is not None
    assert loaded.access_token == bundle.access_token
    assert loaded.refresh_token == bundle.refresh_token
    assert loaded.expires_at == bundle.expires_at

    await store.async_clear()
    # A fresh instance: the Store object caches the record it last loaded.
    assert await TokenStore(hass, ENTRY_ID, ACCOUNT).async_load() is None


async def test_saved_record_carries_the_account(hass: HomeAssistant) -> None:
    raw = _raw_store(hass)
    bundle = stored_token_bundle(access="access-token", refresh="refresh-token")
    await TokenStore(hass, ENTRY_ID, ACCOUNT).async_save(bundle)

    record = await raw.async_load()
    assert record is not None
    assert record["account"] == ACCOUNT
    assert record["expires_at"] == bundle.expires_at.isoformat()


def test_store_is_private_and_written_atomically(
    hass: HomeAssistant, monkeypatch
) -> None:
    seen: dict[str, object] = {}
    real_init = Store.__init__

    def spy(self, *args, **kwargs):
        seen.update(kwargs)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(Store, "__init__", spy)
    TokenStore(hass, ENTRY_ID, ACCOUNT)
    assert seen["private"] is True
    assert seen["atomic_writes"] is True


async def test_two_entries_do_not_share_a_record(hass: HomeAssistant) -> None:
    await TokenStore(hass, ENTRY_ID, ACCOUNT).async_save(
        stored_token_bundle(access="access-token", refresh="refresh-token")
    )

    assert await TokenStore(hass, "entry_2", ACCOUNT).async_load() is None


async def test_record_for_another_account_is_refused(hass: HomeAssistant) -> None:
    await _raw_store(hass).async_save(_record(account="other_api:user@example.com"))

    assert await TokenStore(hass, ENTRY_ID, ACCOUNT).async_load() is None


async def test_a_valid_raw_record_loads(hass: HomeAssistant) -> None:
    """Positive control: the harness writes a record the parser accepts."""
    await _raw_store(hass).async_save(_record())

    loaded = await TokenStore(hass, ENTRY_ID, ACCOUNT).async_load()
    assert loaded is not None
    assert loaded.access_token == "access-token"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"account": ACCOUNT},
        _record(access_token=""),
        _record(refresh_token=""),
        _record(expires_at="not-a-date"),
        _record(access_token=1),
        # Naive timestamps are rejected: expiry comparisons run in UTC.
        _record(expires_at="2030-01-01T00:00:00"),
    ],
)
async def test_malformed_records_are_ignored(
    hass: HomeAssistant, payload: object
) -> None:
    raw = _raw_store(hass)
    await raw.async_save(payload)  # type: ignore[arg-type]

    assert await TokenStore(hass, ENTRY_ID, ACCOUNT).async_load() is None


@pytest.mark.parametrize(
    "error",
    [
        HomeAssistantError("permission denied"),
        OSError("disk"),
        KeyError("version"),
        NotImplementedError("no migration"),
        TypeError("bad envelope"),
        ValueError("bad envelope"),
    ],
)
async def test_unreadable_storage_falls_back_to_a_password_grant(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    store = TokenStore(hass, ENTRY_ID, ACCOUNT)
    store._store.async_load = AsyncMock(side_effect=error)
    with caplog.at_level("WARNING"):
        assert await store.async_load() is None
    assert "Could not read stored smartbox tokens" in caplog.text


async def test_a_closed_store_does_not_write(hass: HomeAssistant) -> None:
    store = TokenStore(hass, ENTRY_ID, ACCOUNT)
    store.close()
    await store.async_save(
        stored_token_bundle(access="access-token", refresh="refresh-token")
    )

    assert await _raw_store(hass).async_load() is None


async def test_clear_errors_are_logged_not_raised(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    store = TokenStore(hass, ENTRY_ID, ACCOUNT)
    store._store.async_remove = AsyncMock(side_effect=OSError("read-only"))
    with caplog.at_level("WARNING"):
        await store.async_clear()
    assert "Could not remove stored smartbox tokens" in caplog.text


def test_account_stamp_is_reseller_and_host_scoped_and_case_folded() -> None:
    assert (
        account_from_entry_data({"api_name": "api-lhz", "username": "User@Example.com"})
        == "api-lhz:2:user@example.com:https://users-api.helki.com"
    )
    assert (
        account_from_entry_data(
            {"api_name": "api-elnur", "username": "User@Example.com"}
        )
        == "api-elnur:7:user@example.com:https://api-elnur.helki.com"
    )


def test_account_stamp_tracks_a_host_override(monkeypatch) -> None:
    """A manual host override invalidates tokens from the previous endpoint."""
    data = {"api_name": "api-lhz", "username": "User@Example.com"}
    before = account_from_entry_data(data)
    monkeypatch.setitem(RESELLER_OVERRIDES, "api-lhz", {"api_host": "api-lhz.test"})
    assert account_from_entry_data(data) != before
    assert account_from_entry_data(data).endswith(":https://api-lhz.test")


async def test_save_errors_are_logged_not_raised(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    store = TokenStore(hass, ENTRY_ID, ACCOUNT)
    store._store.async_save = AsyncMock(side_effect=OSError("disk full"))
    with caplog.at_level("WARNING"):
        await store.async_save(
            stored_token_bundle(access="access-token", refresh="refresh-token")
        )
    assert "Could not persist smartbox tokens" in caplog.text


async def test_load_waits_for_an_inflight_save(hass: HomeAssistant) -> None:
    """A reload must not read the record a retiring runtime is still rotating."""
    store = TokenStore(hass, ENTRY_ID, ACCOUNT)
    bundle = stored_token_bundle(access="access-token", refresh="refresh-token")
    started = asyncio.Event()
    release = asyncio.Event()
    real_save = store._store.async_save

    async def slow_save(data: dict) -> None:
        started.set()
        await release.wait()
        await real_save(data)

    store._store.async_save = slow_save
    save_task = asyncio.create_task(store.async_save(bundle))
    await started.wait()

    load_task = asyncio.create_task(TokenStore(hass, ENTRY_ID, ACCOUNT).async_load())
    await asyncio.sleep(0)
    assert not load_task.done()

    release.set()
    loaded = await load_task
    await save_task
    assert loaded is not None
    assert loaded.refresh_token == "refresh-token"


async def test_clear_waits_for_an_inflight_save(hass: HomeAssistant) -> None:
    """A save already past the closed check cannot resurrect the record."""
    store = TokenStore(hass, ENTRY_ID, ACCOUNT)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_save(_data: object) -> None:
        started.set()
        await release.wait()

    store._store.async_save = slow_save
    save_task = asyncio.create_task(
        store.async_save(
            stored_token_bundle(access="access-token", refresh="refresh-token")
        )
    )
    await started.wait()

    clear_task = asyncio.create_task(TokenStore(hass, ENTRY_ID, ACCOUNT).async_clear())
    await asyncio.sleep(0)
    assert not clear_task.done()

    release.set()
    await save_task
    await clear_task
    assert await TokenStore(hass, ENTRY_ID, ACCOUNT).async_load() is None
