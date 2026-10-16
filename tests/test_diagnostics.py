"""Tests for the Smartbox diagnostics."""

import json

from custom_components.smartbox.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redact_identifiers(hass, mock_smartbox, config_entry):
    """Account data and device identifiers never reach the diagnostics."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)

    assert diagnostics["runtime_data"]["devices"]
    assert diagnostics["runtime_data"]["nodes"]
    assert diagnostics["hass_devices"]
    dump = json.dumps(diagnostics, default=str)
    # Entity ids derive from entity names and are legitimately present.
    for secret in (
        "test_username_1",
        "test_password_1",
        '"device_1"',
        '"device_2"',
        "serial_id_",
        "web_url",
    ):
        assert secret not in dump, secret
