"""Tests for the integration's lifecycle.

Setup/probe outcome mapping, the full wiring smoke (real stack, offline
seams), the HA-stop teardown and the box-device creation plus the
per-node registry entry re-home and node links.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
)
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
    translation,
)
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from smartbox.protocol import (
    AuthError,
    TransportUnavailableError,
    TransportUnsupportedError,
)
from smartbox.protocol.auth import TokenBundle

import custom_components.smartbox as smartbox_init
from custom_components.smartbox import (
    async_setup_entry,
    async_unload_entry,
    update_listener,
)
from custom_components.smartbox.const import (
    CONF_DISPLAY_ENTITY_PICTURES,
    DOMAIN,
    FIELD_SCHEDULE,
    SERVICE_SET_SCHEDULE,
    SmartboxNodeType,
)
from custom_components.smartbox.history import HistoryImport
from custom_components.smartbox.services import ServiceTargets, remove_service_if_last
from custom_components.smartbox.token_store import token_store_for
from tests.const import CONF_PASSWORD, MOCK_SMARTBOX_CONFIG
from tests.fake_transport import FakeTransport
from tests.fakes import make_jwt
from tests.mocks import get_device_by_identifier
from tests.test_utils import stored_token_bundle

# Key -> entity domain of the per-node registry entries to re-home.
_REHOMED_TEST_KEYS = {
    "away_status": SWITCH_DOMAIN,
    "connected": BINARY_SENSOR_DOMAIN,
    "power_limit": NUMBER_DOMAIN,
}


@pytest.fixture
def units():
    """Pin the mock world to Celsius: setup ignores the node scale."""
    return "C"


@pytest.mark.asyncio
async def test_update_listener(hass, config_entry):
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, config_entry)
        mock_reload.assert_called_once_with(config_entry.entry_id)


@pytest.mark.asyncio
async def test_update_listener_ignores_a_history_only_change(hass, config_entry):
    """The first-setup history flag is written by the sensors, not the user.

    Reloading for it would tear down the websocket and cancel the imports of
    the nodes that have not finished, so the listener must ignore it - and still
    reload for any other option.
    """
    entry = SimpleNamespace(
        entry_id="entry_1",
        options={"history_consumption": "auto"},
        runtime_data=SimpleNamespace(
            history=HistoryImport(["node_1"], {"history_consumption": "start"})
        ),
    )
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, entry)
        mock_reload.assert_not_called()
        # any other option still reloads
        entry.options = {
            "history_consumption": "auto",
            CONF_DISPLAY_ENTITY_PICTURES: True,
        }
        await update_listener(hass, entry)
        mock_reload.assert_called_once_with("entry_1")


@pytest.mark.asyncio
async def test_update_listener_reloads_when_credentials_change(hass, config_entry):
    """A reauth data update rebuilds the client, so the listener reloads."""
    entry = SimpleNamespace(
        entry_id="entry_1",
        data={"api_name": "api", "username": "user@example.com", "password": "new"},
        options={},
        runtime_data=SimpleNamespace(
            history=HistoryImport([], {}),
            entry_data={
                "api_name": "api",
                "username": "user@example.com",
                "password": "old",
            },
        ),
    )
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, entry)
        mock_reload.assert_called_once_with("entry_1")


@pytest.mark.asyncio
async def test_update_listener_reloads_when_start_is_reselected(hass, config_entry):
    """Selecting ``start`` asks for the first-setup import again.

    The pending set is seeded when the runtime is built, so a reload is the
    only way the request can take effect.
    """
    entry = SimpleNamespace(
        entry_id="entry_1",
        options={"history_consumption": "start"},
        runtime_data=SimpleNamespace(
            history=HistoryImport(["node_1"], {"history_consumption": "auto"})
        ),
    )
    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as mock_reload:
        await update_listener(hass, entry)
        mock_reload.assert_called_once_with("entry_1")


# --- setup mapping (probe + identity discovery outcomes) ---


async def _setup_with_probe(hass, config_entry, probe):
    """Run async_setup_entry with the capability probe scripted."""
    with (
        patch(
            "custom_components.smartbox.check_ws_user_support",
            new=probe,
        ),
    ):
        return await async_setup_entry(hass, config_entry)


@pytest.mark.asyncio
async def test_setup_entry_probe_unsupported_raises_config_entry_error(
    hass, mock_smartbox, config_entry
):
    with pytest.raises(ConfigEntryError) as exc_info:
        await _setup_with_probe(
            hass,
            config_entry,
            AsyncMock(side_effect=TransportUnsupportedError("404")),
        )
    assert exc_info.value.translation_key == "ws_user_unsupported"
    # the rendered message carries the status text, no literal {error}
    hass.config.components.add(DOMAIN)  # the loader needs the component
    await translation.async_load_integrations(hass, {DOMAIN})
    message = translation.async_get_exception_message(
        DOMAIN,
        exc_info.value.translation_key,
        exc_info.value.translation_placeholders,
    )
    assert "404" in message
    assert "{error}" not in message
    assert config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.asyncio
async def test_setup_entry_probe_transient_raises_not_ready(
    hass, mock_smartbox, config_entry
):
    with pytest.raises(ConfigEntryNotReady):
        await _setup_with_probe(
            hass,
            config_entry,
            AsyncMock(side_effect=TransportUnavailableError("timeout")),
        )


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_setup_entry_token_auth_error_raises_auth_failed(
    hass, mock_smartbox, config_entry
):
    """A rejected password grant at setup starts the reauth flow."""
    with (
        patch(
            "smartbox.protocol.auth.TokenManager.get_token",
            new=AsyncMock(side_effect=AuthError("rejected")),
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(hass, config_entry)


@pytest.mark.asyncio
async def test_setup_entry_token_transient_raises_not_ready(
    hass, mock_smartbox, config_entry
):
    """A transient token-grant failure retries via ConfigEntryNotReady."""
    with (
        patch(
            "smartbox.protocol.auth.TokenManager.get_token",
            new=AsyncMock(side_effect=TransportUnavailableError("timeout")),
        ),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, config_entry)


# --- full wiring smoke (real stack, offline seams) ---


@pytest.mark.asyncio
async def test_setup_entry_wiring_full_stack(hass, mock_smartbox, config_entry):
    """Setup wires engine+client+transport+auth and supervises them."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    runtime = config_entry.runtime_data
    assert runtime is not None
    engine = runtime.engine
    assert engine is not None
    # The transport is supervised (FakeTransport.run parks like the real
    # socket).
    assert runtime.auth is runtime.client.auth
    assert engine.supervision_task is not None
    assert not engine.supervision_task.done()
    supervision_task = engine.supervision_task

    # Identity was discovered over the (mocked) client reads: two devices,
    # all nodes carrying names/versions for the registry surface.
    assert [device.dev_id for device in runtime.identity.devices] == [
        "device_1",
        "device_2",
    ]
    assert len(runtime.identity.nodes) == 8
    htr_node = runtime.identity.nodes[0]
    assert htr_node.name == "Device 1 0"
    assert htr_node.pid == "product_id_1_0"

    # Unload stops supervision, closes the transport and clears the runtime.
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    # stop() clears the engine's reference; the captured task is done.
    assert engine.supervision_task is None
    assert supervision_task.done()
    assert runtime.transport.closed
    # HA clears entry.runtime_data once the platforms unloaded.
    assert not hasattr(config_entry, "runtime_data")
    assert config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.asyncio
async def test_hass_stop_tears_down_transport(hass, mock_smartbox, config_entry):
    """The HA-stop listener tears the supervised transport down (bounded)."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    runtime = config_entry.runtime_data
    supervision_task = runtime.engine.supervision_task

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    assert runtime.transport.closed
    assert supervision_task.done()


@pytest.mark.asyncio
async def test_setup_failure_after_supervision_tears_the_transport_down(
    hass, mock_smartbox, config_entry
):
    """A setup that fails after the transport is live must stop it.

    HA runs the on-unload callbacks for a FAILED setup too, and the supervision
    task is not entry-tracked: without the teardown registered before the
    platform forward, the socket would stay open against a runtime nothing owns
    and the retry would open a second one.
    """
    boom_msg = "platform boom"
    boom = RuntimeError(boom_msg)
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        AsyncMock(side_effect=boom),
    ):
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    runtime = config_entry.runtime_data
    assert runtime.transport.closed
    assert runtime.engine.supervision_task is None


class _FlappingTransport(FakeTransport):
    """FakeTransport whose first ``run()`` raises ``TransportUnsupportedError``."""

    def __init__(self) -> None:
        super().__init__()
        self.runs = 0

    async def run(self) -> None:
        """Fail once (a backend rollback), then park like the real socket."""
        self.runs += 1
        if self.runs == 1:
            msg = "endpoint gone"
            raise TransportUnsupportedError(msg)
        await super().run()


@pytest.mark.asyncio
async def test_midrun_unsupported_recovery_keeps_the_entry_loaded(
    hass, mock_smartbox, config_entry, monkeypatch
):
    """A mid-run ws_user disappearance is recovered IN PLACE.

    The monitor re-supervises the restartable transport rather than reloading
    the entry: an entry-tracked reload task is cancelled by the reload's own
    unload, so the entry would be torn down and never set up again. The entry
    must therefore stay loaded, with its entities, and no reload may run.
    """
    monkeypatch.setattr(smartbox_init, "backoff_delay", lambda *_: 0.0)
    transport = _FlappingTransport()

    def _transport_stub(token_source, api_host, **kwargs) -> _FlappingTransport:
        """Stands in for WsUserTransport: the one scripted transport."""
        return transport

    with (
        patch("custom_components.smartbox.WsUserTransport", _transport_stub),
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_,
        patch.object(
            hass.config_entries, "async_schedule_reload", AsyncMock()
        ) as schedule_,
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        for _ in range(10):
            if transport.runs >= 2:
                break
            await hass.async_block_till_done()

    assert transport.runs >= 2  # the monitor re-probed in place
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data.engine.supervision_task is not None
    assert hass.states.async_entity_ids(CLIMATE_DOMAIN)
    reload_.assert_not_called()
    schedule_.assert_not_called()


@pytest.mark.asyncio
async def test_unload_completes_when_transport_close_hangs(
    hass, mock_smartbox, config_entry, monkeypatch
):
    """A transport whose close() never returns must not stall unload.

    The teardown bounds the transport close with
    ``_WS_USER_TEARDOWN_TIMEOUT_SECONDS``; a hung close times out
    (suppressed) and the entry still unloads cleanly.
    """
    monkeypatch.setattr(
        "custom_components.smartbox._WS_USER_TEARDOWN_TIMEOUT_SECONDS",
        0.05,
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    runtime = config_entry.runtime_data
    engine = runtime.engine
    supervision_task = engine.supervision_task

    hang = asyncio.Event()

    async def hanging_close():
        await hang.wait()

    with patch.object(runtime.transport, "close", side_effect=hanging_close):
        # the unload itself is bounded (this would hang forever otherwise)
        assert await asyncio.wait_for(
            hass.config_entries.async_unload(config_entry.entry_id),
            5.0,
        )
    await hass.async_block_till_done()

    # engine.stop() completed: the supervision task was cancelled + cleared
    assert engine.supervision_task is None
    assert supervision_task.done()
    # the transport was NEVER really closed - the timeout path was taken
    assert not runtime.transport.closed
    # HA clears entry.runtime_data once the platforms unloaded.
    assert not hasattr(config_entry, "runtime_data")
    assert config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.asyncio
async def test_teardown_closes_the_store_when_engine_stop_fails():
    """A failed stop must not leave the token store accepting writes."""
    runtime = SimpleNamespace(
        membership=SimpleNamespace(close=Mock()),
        engine=SimpleNamespace(stop=AsyncMock(side_effect=RuntimeError("boom"))),
        transport=SimpleNamespace(close=AsyncMock()),
        token_store=SimpleNamespace(close=Mock()),
    )

    with pytest.raises(RuntimeError):
        await smartbox_init._async_teardown_runtime(runtime)

    runtime.transport.close.assert_awaited_once()
    runtime.token_store.close.assert_called_once()


# --- box device creation + per-node registry re-home ---


async def test_box_device_created_and_per_node_entries_rehomed(
    hass, mock_smartbox, config_entry
):
    """The box is its own device; per-node registry entries re-home.

    Entries created on the per-node devices carry the away switch,
    connectivity sensor and
    power limit with unique_ids
    ``{dev_id}_{addr}_{key}`` — including duplicates per node. On setup they
    must be re-keyed onto the box device (first/lowest addr per key) and the
    duplicates removed. The live node devices are linked to the box as its
    connected devices.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    # Pre-box-device registry state: per-node devices carrying the
    # device-level entries earlier releases created.
    for dev_id in ("device_1", "device_2"):
        node_device = device_registry.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={(DOMAIN, f"{dev_id}_node0")},
        )
        for addr in (0, 1):
            for key in ("away_status", "connected"):
                entity_registry.async_get_or_create(
                    _REHOMED_TEST_KEYS[key],
                    DOMAIN,
                    f"{dev_id}_{addr}_{key}",
                    config_entry=config_entry,
                    device_id=node_device.id,
                    suggested_object_id=f"{dev_id}_{addr}_{key}",
                )
        entity_registry.async_get_or_create(
            NUMBER_DOMAIN,
            DOMAIN,
            f"{dev_id}_0_power_limit",
            config_entry=config_entry,
            device_id=node_device.id,
            suggested_object_id=f"{dev_id}_0_power_limit",
        )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    device_dicts = {
        device_dict["dev_id"]: device_dict
        for device_dict in await mock_smartbox.session.get_devices()
    }
    for dev_id in ("device_1", "device_2"):
        box_device = get_device_by_identifier(device_registry, (DOMAIN, dev_id))
        assert box_device is not None
        assert box_device.name == device_dicts[dev_id]["name"]
        assert box_device.sw_version == device_dicts[dev_id]["fw_version"]
        assert box_device.serial_number == device_dicts[dev_id]["serial_id"]
        # Every heater is a "connected device" of its box on the device page.
        for addr in (0, 1):
            node_device = get_device_by_identifier(
                device_registry, (DOMAIN, f"{dev_id}_{addr}")
            )
            assert node_device is not None
            assert node_device.via_device_id == box_device.id
        for key, domain in _REHOMED_TEST_KEYS.items():
            entity_id = entity_registry.async_get_entity_id(
                domain, DOMAIN, f"{dev_id}_{key}"
            )
            assert entity_id is not None, f"{dev_id}_{key} not re-keyed"
            entity_entry = entity_registry.async_get(entity_id)
            assert entity_entry.device_id == box_device.id
        # Duplicates beyond the first addr were removed.
        assert (
            entity_registry.async_get_entity_id(
                SWITCH_DOMAIN, DOMAIN, f"{dev_id}_1_away_status"
            )
            is None
        )
        assert (
            entity_registry.async_get_entity_id(
                BINARY_SENSOR_DOMAIN, DOMAIN, f"{dev_id}_1_connected"
            )
            is None
        )

    # Re-running setup must be a no-op (idempotent re-home).
    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert (
        entity_registry.async_get_entity_id(
            SWITCH_DOMAIN, DOMAIN, "device_1_away_status"
        )
        is not None
    )
    assert (
        entity_registry.async_get_entity_id(
            SWITCH_DOMAIN, DOMAIN, "device_1_0_away_status"
        )
        is None
    )


@pytest.mark.asyncio
async def test_node_device_model_shows_product_and_addr(
    hass, mock_smartbox, config_entry
):
    """The node device model carries the product label and slot address."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    node_device = get_device_by_identifier(dr.async_get(hass), (DOMAIN, "device_2_1"))

    assert node_device is not None
    assert node_device.model == "Heater (htr_mod, addr 1)"
    assert node_device.model_id == "product_id_2_1"
    assert node_device.sw_version == "fw_version_2_1"


@pytest.mark.asyncio
async def test_thermostat_energy_entities_are_pruned(hass, config_entry):
    """Old THM power and consumption registry entries are removed."""
    registry = er.async_get(hass)
    for unique_id in ("node_11_power", "node_11_total_consumption"):
        registry.async_get_or_create(
            "sensor", DOMAIN, unique_id, config_entry=config_entry
        )
    identity = SimpleNamespace(
        nodes=[SimpleNamespace(node_id="node_11", node_type=SmartboxNodeType.THM)]
    )

    smartbox_init._async_prune_thermostat_energy_entities(hass, config_entry, identity)

    assert registry.async_get_entity_id("sensor", DOMAIN, "node_11_power") is None
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, "node_11_total_consumption")
        is None
    )


@pytest.mark.asyncio
async def test_boxless_device_skips_box_power_limit_entities(
    hass, mock_smartbox, config_entry
):
    """No htr_system means no box power-limit entities and no radiator priority."""
    mock_smartbox._device_power = {
        **mock_smartbox._device_power,
        "device_1": None,
    }
    registry = er.async_get(hass)
    box_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "device_1")},
    )
    registry.async_get_or_create(
        NUMBER_DOMAIN,
        DOMAIN,
        "device_1_power_limit",
        suggested_object_id="device_1_power_limit",
        config_entry=config_entry,
        device_id=box_device.id,
    )
    registry.async_get_or_create(
        SWITCH_DOMAIN,
        DOMAIN,
        "device_1_no_power_limit",
        suggested_object_id="device_1_no_power_limit",
        config_entry=config_entry,
        device_id=box_device.id,
    )
    registry.async_get_or_create(
        SWITCH_DOMAIN,
        DOMAIN,
        "device_1_away_status",
        suggested_object_id="device_1_away_status",
        config_entry=config_entry,
        device_id=box_device.id,
    )
    registry.async_get_or_create(
        SELECT_DOMAIN,
        DOMAIN,
        "device_1_1_priority",
        suggested_object_id="device_1_1_priority",
        config_entry=config_entry,
        device_id=box_device.id,
    )
    boxed_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "device_2")},
    )
    registry.async_get_or_create(
        SELECT_DOMAIN,
        DOMAIN,
        "device_2_1_priority",
        suggested_object_id="device_2_1_priority",
        config_entry=config_entry,
        device_id=boxed_device.id,
    )

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert (
        registry.async_get_entity_id(NUMBER_DOMAIN, DOMAIN, "device_1_power_limit")
        is None
    )
    assert (
        registry.async_get_entity_id(SWITCH_DOMAIN, DOMAIN, "device_1_no_power_limit")
        is None
    )
    assert (
        registry.async_get_entity_id(SELECT_DOMAIN, DOMAIN, "device_1_1_priority")
        is None
    )
    assert (
        registry.async_get_entity_id(SELECT_DOMAIN, DOMAIN, "device_2_1_priority")
        is not None
    )
    assert (
        registry.async_get_entity_id(SWITCH_DOMAIN, DOMAIN, "device_1_away_status")
        is not None
    )
    assert (
        registry.async_get_entity_id(BINARY_SENSOR_DOMAIN, DOMAIN, "device_1_connected")
        is not None
    )
    assert (
        registry.async_get_entity_id(NUMBER_DOMAIN, DOMAIN, "device_2_power_limit")
        is not None
    )
    assert (
        registry.async_get_entity_id(SWITCH_DOMAIN, DOMAIN, "device_2_no_power_limit")
        is not None
    )


# --- ws_user repair-issue lifecycle ---


@pytest.mark.asyncio
async def test_flapping_issue_registered_and_pruned_on_remove(hass, config_entry):
    """The repair issue is created and pruned by entry removal."""
    smartbox_init._create_flapping_issue(
        hass, config_entry, TransportUnsupportedError("404")
    )
    issue_id = (DOMAIN, smartbox_init.ws_user_flapping_issue_id(config_entry))
    assert issue_id in ir.async_get(hass).issues

    await smartbox_init.async_remove_entry(hass, config_entry)

    assert issue_id not in ir.async_get(hass).issues


@pytest.mark.asyncio
async def test_successful_setup_clears_a_leftover_repair_issue(
    hass, mock_smartbox, config_entry
):
    """A successful load means the endpoint serves: the issue is cleared."""
    issue_id = (DOMAIN, smartbox_init.ws_user_flapping_issue_id(config_entry))
    smartbox_init._create_flapping_issue(
        hass, config_entry, TransportUnsupportedError("404")
    )
    assert issue_id in ir.async_get(hass).issues

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert issue_id not in ir.async_get(hass).issues

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()


async def test_set_schedule_reaches_every_loaded_entry(
    hass, mock_smartbox, config_entry
):
    """A domain-global service call fans out across loaded entries.

    The handler is registered once (by whichever entry loads first) and must
    reach the entities of every loaded entry, not only the registrant's.
    """
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    calls: list[dict] = []

    class _OtherSchedule:
        entity_id = "sensor.other_entry_schedule"

        async def async_set_schedule(self, prog) -> None:
            calls.append(prog)

    other = MockConfigEntry(domain=DOMAIN, data=dict(MOCK_SMARTBOX_CONFIG[DOMAIN]))
    other.add_to_hass(hass)
    other.mock_state(hass, ConfigEntryState.LOADED)
    other.runtime_data = SimpleNamespace(
        services=ServiceTargets(schedule=[_OtherSchedule()])
    )

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_SCHEDULE,
        {
            FIELD_SCHEDULE: {"0": [1] * 24},
            "entity_id": "sensor.other_entry_schedule",
        },
        blocking=True,
    )
    assert calls == [{"0": [1] * 24}]


async def test_unload_keeps_the_service_while_another_entry_is_loaded(
    hass, mock_smartbox, config_entry
):
    """Unloading one entry must not take the service away from the others."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    other = MockConfigEntry(domain=DOMAIN, data=dict(MOCK_SMARTBOX_CONFIG[DOMAIN]))
    other.add_to_hass(hass)
    other.mock_state(hass, ConfigEntryState.LOADED)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE)

    other.mock_state(hass, ConfigEntryState.NOT_LOADED)
    remove_service_if_last(hass, SERVICE_SET_SCHEDULE)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE)


async def test_unload_pauses_membership_and_resumes_on_refusal(
    hass, mock_smartbox, config_entry
):
    """A refused platform unload must re-enable in-place membership."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    runtime = config_entry.runtime_data
    with (
        patch.object(runtime.membership, "async_pause", new=AsyncMock()) as pause,
        patch.object(runtime.membership, "resume") as resume,
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=False),
        ),
    ):
        assert not await async_unload_entry(hass, config_entry)
    pause.assert_awaited_once()
    resume.assert_called_once()


async def test_remove_config_entry_device_refuses_live_and_allows_stale(
    hass, mock_smartbox, config_entry
):
    """The standard delete hook refuses live devices and frees stale ones."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    live = get_device_by_identifier(registry, (DOMAIN, "device_1_1"))
    assert live is not None
    assert (
        await smartbox_init.async_remove_config_entry_device(hass, config_entry, live)
        is False
    )

    stale = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "device_1_99")},
        name="Old Heater",
    )
    assert (
        await smartbox_init.async_remove_config_entry_device(hass, config_entry, stale)
        is True
    )

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    # No loaded runtime to compare against: the user-initiated cleanup wins.
    assert (
        await smartbox_init.async_remove_config_entry_device(hass, config_entry, live)
        is True
    )


# --- bundled frontend card ---


def test_bundled_programme_card_asset_is_shipped():
    """The repo ships the asset the registration looks for."""
    assert smartbox_init._CARD_JS.is_file()


async def test_async_setup_registers_the_bundled_programme_card(
    hass, tmp_path, monkeypatch
):
    """The card asset is served and auto-loaded when the frontend is up."""
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    card = frontend_dir / "smartbox-program-card.js"
    card.write_text("// card", encoding="utf-8")
    http = SimpleNamespace(async_register_static_paths=AsyncMock())
    hass.http = http
    hass.config.components.add("frontend")
    monkeypatch.setattr(smartbox_init, "_FRONTEND_DIR", frontend_dir)
    monkeypatch.setattr(smartbox_init, "_CARD_JS", card)

    with patch.object(smartbox_init, "add_extra_js_url") as add_url:
        assert await smartbox_init.async_setup(hass, {})

    registered = http.async_register_static_paths.await_args.args[0]
    assert [entry.url_path for entry in registered] == [smartbox_init._FRONTEND_URL]
    assert registered[0].path == str(frontend_dir)
    assert registered[0].cache_headers is True
    add_url.assert_called_once_with(
        hass,
        f"{smartbox_init._FRONTEND_URL}/{card.name}?v={smartbox_init.__version__}",
    )


async def test_async_setup_skips_the_card_without_a_frontend(hass):
    """A headless setup (tests, stripped installs) serves no card."""
    http = SimpleNamespace(async_register_static_paths=AsyncMock())
    hass.http = http
    assert "frontend" not in hass.config.components
    with patch.object(smartbox_init, "add_extra_js_url") as add_url:
        assert await smartbox_init.async_setup(hass, {})
    http.async_register_static_paths.assert_not_awaited()
    add_url.assert_not_called()


async def test_async_setup_skips_the_card_when_the_asset_is_missing(
    hass, tmp_path, monkeypatch
):
    """A build that omits the JS asset must not advertise a 404 module."""
    http = SimpleNamespace(async_register_static_paths=AsyncMock())
    hass.http = http
    hass.config.components.add("frontend")
    monkeypatch.setattr(smartbox_init, "_FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(smartbox_init, "_CARD_JS", tmp_path / "missing.js")
    with patch.object(smartbox_init, "add_extra_js_url") as add_url:
        assert await smartbox_init.async_setup(hass, {})
    http.async_register_static_paths.assert_not_awaited()
    add_url.assert_not_called()


# --- token persistence across restarts ---

# The mock_smartbox fixture stubs TokenManager.get_token; these tests
# need the real one, so they patch the class attribute back.
_REAL_GET_TOKEN = smartbox_init.TokenManager.get_token


def _token_store(hass, entry):
    return token_store_for(hass, entry.entry_id, entry.data)


@pytest.mark.asyncio
async def test_setup_restores_the_stored_token(hass, mock_smartbox, config_entry):
    """A bundle from the previous process is used without a password grant."""
    stored = stored_token_bundle()
    await _token_store(hass, config_entry).async_save(stored)
    probe = AsyncMock()

    with (
        patch.object(smartbox_init.TokenManager, "get_token", _REAL_GET_TOKEN),
        patch.object(
            smartbox_init.TokenManager,
            "_grant_with_retry",
            AsyncMock(side_effect=AssertionError("a grant ran")),
        ) as grant,
        patch("custom_components.smartbox.check_ws_user_support", probe),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)

    grant.assert_not_awaited()
    probe.assert_awaited_once()
    assert probe.await_args.args[0] == "stored-access"
    assert config_entry.runtime_data.token_origin == "restored"


@pytest.mark.asyncio
async def test_setup_persists_the_granted_bundle(hass, mock_smartbox, config_entry):
    """The first password grant is written through the manager's seam."""
    granted = TokenBundle(
        access_token="granted-access",
        refresh_token="granted-refresh",
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )

    with (
        patch.object(smartbox_init.TokenManager, "get_token", _REAL_GET_TOKEN),
        patch.object(
            smartbox_init.TokenManager,
            "_grant_with_retry",
            AsyncMock(return_value=granted),
        ),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)

    loaded = await _token_store(hass, config_entry).async_load()
    assert loaded is not None
    assert loaded.refresh_token == "granted-refresh"
    assert config_entry.runtime_data.token_origin == "granted"


@pytest.mark.asyncio
async def test_setup_persists_a_rotated_refresh_token(
    hass, mock_smartbox, config_entry
):
    """A refresh grant, not only the initial password grant, updates the record."""
    await _token_store(hass, config_entry).async_save(stored_token_bundle(lifetime_s=1))
    rotated = TokenBundle(
        access_token="rotated-access",
        refresh_token="rotated-refresh",
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )

    with (
        patch.object(smartbox_init.TokenManager, "get_token", _REAL_GET_TOKEN),
        patch.object(
            smartbox_init.TokenManager,
            "_grant_with_retry",
            AsyncMock(return_value=rotated),
        ) as grant,
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)

    grant.assert_awaited_once()
    loaded = await _token_store(hass, config_entry).async_load()
    assert loaded is not None
    assert loaded.refresh_token == "rotated-refresh"
    assert config_entry.runtime_data.token_origin == "restored"


@pytest.mark.asyncio
async def test_reload_keeps_the_stored_token(hass, mock_smartbox, config_entry):
    """The unload/reload path must not clear the bundle."""
    await _token_store(hass, config_entry).async_save(stored_token_bundle())

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert await hass.config_entries.async_reload(config_entry.entry_id)

    loaded = await _token_store(hass, config_entry).async_load()
    assert loaded is not None
    assert loaded.refresh_token == "stored-refresh"


@pytest.mark.asyncio
async def test_rejected_restored_token_is_replaced_before_judging_the_endpoint(
    hass, mock_smartbox, config_entry
):
    """A stale stored token must not be read as an unsupported ws endpoint."""
    await _token_store(hass, config_entry).async_save(stored_token_bundle())
    fresh = TokenBundle(
        access_token="fresh-access",
        refresh_token="fresh-refresh",
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )
    probe = AsyncMock(side_effect=[TransportUnsupportedError("401"), None])

    with (
        patch.object(smartbox_init.TokenManager, "get_token", _REAL_GET_TOKEN),
        patch.object(
            smartbox_init.TokenManager,
            "_grant_with_retry",
            AsyncMock(return_value=fresh),
        ),
        patch("custom_components.smartbox.check_ws_user_support", probe),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)

    assert probe.await_count == 2
    assert probe.await_args_list[0].args[0] == "stored-access"
    assert probe.await_args_list[1].args[0] == "fresh-access"
    assert config_entry.runtime_data.token_origin == "restored"


@pytest.mark.asyncio
async def test_remove_entry_clears_the_stored_token(hass, config_entry):
    store = _token_store(hass, config_entry)
    await store.async_save(stored_token_bundle())

    await smartbox_init.async_remove_entry(hass, config_entry)

    assert await _token_store(hass, config_entry).async_load() is None


@pytest.mark.asyncio
async def test_unload_clears_tokens_when_credentials_changed(
    hass, mock_smartbox, config_entry
):
    """The unload-time guard discards a rotation the flow's clear missed."""
    await _token_store(hass, config_entry).async_save(stored_token_bundle())
    assert await hass.config_entries.async_setup(config_entry.entry_id)

    with patch.object(hass.config_entries, "async_reload", AsyncMock()):
        hass.config_entries.async_update_entry(
            config_entry,
            data={**config_entry.data, CONF_PASSWORD: "new-password"},
        )
    assert await smartbox_init.async_unload_entry(hass, config_entry)

    assert (
        await token_store_for(
            hass, config_entry.entry_id, config_entry.data
        ).async_load()
        is None
    )


@pytest.mark.asyncio
async def test_unload_retires_the_runtime_store(hass, mock_smartbox, config_entry):
    """A grant completing after unload must not rewrite the record."""
    await _token_store(hass, config_entry).async_save(stored_token_bundle())
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    runtime = config_entry.runtime_data

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await runtime.token_store.async_save(stored_token_bundle(refresh="late-refresh"))

    loaded = await _token_store(hass, config_entry).async_load()
    assert loaded is not None
    assert loaded.refresh_token == "stored-refresh"


@pytest.mark.asyncio
async def test_rejected_seed_reports_a_fresh_session(hass, mock_smartbox, config_entry):
    """The manager's claim check can refuse a record; diagnostics must say so."""
    foreign = TokenBundle(
        access_token=make_jwt(
            {
                "userId": "u1",
                "email": "other@example.test",
                "serialId": 1,
                "exp": 9999999999,
            }
        ),
        refresh_token="foreign-refresh",
        expires_at=datetime.now(UTC) + timedelta(hours=4),
    )
    await _token_store(hass, config_entry).async_save(foreign)
    fresh = stored_token_bundle()

    with (
        patch.object(smartbox_init.TokenManager, "get_token", _REAL_GET_TOKEN),
        patch.object(
            smartbox_init.TokenManager,
            "_grant_with_retry",
            AsyncMock(return_value=fresh),
        ),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)

    assert config_entry.runtime_data.token_origin == "granted"


@pytest.mark.asyncio
async def test_transient_probe_failure_is_not_retried_with_a_fresh_grant(
    hass, mock_smartbox, config_entry
):
    """Only a deterministic rejection of a restored token earns the retry."""
    await _token_store(hass, config_entry).async_save(stored_token_bundle())
    probe = AsyncMock(side_effect=TransportUnavailableError("down"))

    with (
        patch.object(smartbox_init.TokenManager, "get_token", _REAL_GET_TOKEN),
        patch.object(
            smartbox_init.TokenManager,
            "_grant_with_retry",
            AsyncMock(side_effect=AssertionError("a grant ran")),
        ) as grant,
        patch("custom_components.smartbox.check_ws_user_support", probe),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)

    grant.assert_not_awaited()
