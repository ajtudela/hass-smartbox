# Changelog

## Unreleased

### Changed

- The `smartbox` library is now published on PyPI; the dependency pins use the registry instead of the GitHub tag tarball: `manifest.json` requires `smartbox>=3.0.0`, the pyproject dependency and the tox test environments match, and the CI workflow no longer needs a separate library install step (`uv sync` covers it). A test keeps the manifest and pyproject pins identical.

## 3.1.0 (2026-11-12)

Stable release of the library-split line.

### Changed

- The protocol and core layers now come from the separate `smartbox` library, pinned in `manifest.json` to `v3.0.0`. The vendored copies are removed; HACS/Home Assistant installs the requirement automatically. Manual installs need the matching `smartbox` package.
- The library dependency is Home Assistant-free and can be used on its own.
- No configuration, entity-id or history changes are required.

### Fixed

- Climate entities now provide `native_temperature_unit`, `native_current_temperature` and `native_target_temperature`. The deprecated `temperature_unit`, `current_temperature` and `target_temperature` overrides are gone on HA 2026.11+, removing the startup warnings; the old names remain only on older supported versions.
- The program card maps the wire's Sunday-first day keys correctly and follows the Home Assistant user's first-day-of-week setting. The schedule sensor uses the same mapping for the current profile and next boundary.
- Heater devices are linked to their box in the device registry, with the box listing its heaters under Connected devices and each heater showing Connected via the box.

### Added

- The manifest declares `"loggers": ["smartbox"]`, so the integration's Enable debug logging option and Home Assistant 2026.11's Live log include the `smartbox.protocol` and `smartbox.core` messages.
- A bundled Lovelace program card, `custom:smartbox-program-card`, with a seven-day grid, profile brushes, drag/touch editing, undo, reset, copy-day, equal-slot merging and a native discard confirmation. It loads as an extra frontend module; no separate HACS dashboard item or Lovelace resource is needed.

## 3.0.0 (2026-11-03)

First stable release of the 3.0.0 line, published from the maintained fork. The original integration and 2.6.x line are by Alberto Tudela and the upstream contributors. The `3.0.0-rc.4` functional code is unchanged; only release metadata and documentation changed. This section lists the changes since the 2.6.0 release.

### Breaking

- **socket_io fallback removed.** Only the `ws_user` websocket transport is supported. An account whose host does not serve it cannot work with 3.0.0 and should stay on 2.6.x. The public-app check re-run on 2026-11-03 still found the EHC and Electrorad apps on the legacy `socket.io` bundle, and unauthenticated `/api/v2/ws_user` probes returned 404 on both API hosts.
- **Library dependency removed.** The `smartbox` library is no longer a runtime dependency; `smartbox_protocol` and `smartbox_core` ship inside the integration, and `manifest.json` has empty `requirements`. Users of the library's CLI tooling install it separately.
- **`SMARTBOX_WS_BACKEND` removed.** The source constant disappeared with the socket_io fallback. It was never a config-entry option, so there is no setting to delete.

### Upgrade from 2.6.x

- Existing config entries are reused; entity registry identities, areas and history are preserved. No re-add is required.
- The duty-cycle and RTC-drift sensors are disabled by default for newly created entities. Existing enabled entities keep their state.
- If setup reports `unsupported`, the account needs `socket_io` and should stay on 2.6.x.
- After a restart, entities may briefly show Unavailable until the first websocket snapshot arrives.

### Architecture and transport

- Rebuilt as a three-layer stack: `smartbox_protocol` → `smartbox_core` → the Home Assistant adapter.
- Setup probes the API host once and uses the vendor apps' `ws_user` websocket; the scheduled token refresh cuts the socket for a few seconds every ~4 hours and reconnects with a fresh token.
- A feed outage is visible: when the socket closes and no frame arrives within 10 seconds, entities render Unavailable and the outage is logged once. An open socket may stay quiet for minutes without being treated as down.
- A repair issue reports a host that keeps refusing the `ws_user` handshake; the integration re-probes it in place behind a capped backoff.
- Node availability derives from both the status and setup lost markers.
- Every node write arms a confirmation window. Entities apply the write optimistically and turn Unavailable if the device does not confirm; the 6-second countdown starts when the POST returns, so a token refresh or a slow request cannot expire it mid-flight.
- REST GET and token-grant retries use capped exponential backoff with one-sided jitter.

### Entities and climate

- The 2.6.0 entity surface (climate, sensors, binary sensor, switches, numbers, selects, services) plus the node-level power-limit number and “no node power limit” switch that PMO nodes expose. Existing registry identities are unchanged.
- The Away switch refreshes climate cards immediately, and the “no power limit” switch reports when it has nothing to restore.
- The schedule sensor follows the websocket; its old 15-minute REST refetch is gone.
- Model-code derivation matches the app's PID slice, and the charge-level sensor reads the live `/version` slot so a later frame can correct it.
- Programme temperature writes clamp per-profile, apply the ice/eco/comfort offset, and correct the 4-key `/status` body after max-limit changes. The `max_stemp_limit "0.0"` write is accepted and persists, and the sentinel enforces the limit.
- Boost follows the vendor app's `boost_config` dispatch: 1/2 use the status/setup path, 3 (full) posts `/boost_mode`. Full-boost nodes expose Runback and Advanced boost as climate presets, with `boost_mode`, `boost_end_time` and `boost_remaining_minutes` attributes and a `smartbox.set_boost_mode` service. Runback uses the node's own limits; `extra_options.version >= 2` stores boost/runback temperatures in Celsius.
- An unmodelled firmware mode or `selected_temp` renders an unknown HVAC mode or preset and logs one warning per distinct value. Malformed server numbers read as unknown instead of raising.
- The duty-cycle and RTC-drift sensors are diagnostic and opt-in.

### Membership

- A heater or box added to the account after setup is created in place, whether the snapshot or `/mgr/nodes` reports it. A lagging identity is retried, and a failed refresh falls back to a repair issue asking for a reload.
- A heater or box removed from the account is removed after a grace and a fresh full-account confirmation. A node that merely lost power stays Unavailable and is not removed. Recorder history and long-term statistics are kept, and a re-paired heater restores the same device and entity IDs, area and history. An unconfirmed device can be deleted from its device page.

### Consumption statistics

- Reworked the existing consumption-statistics import; this is not a new data source. Hourly rows are built in UTC, the import skips cleanly when the recorder is absent, transient failures retry with backoff, and first-setup progress is tracked in diagnostics.

### Diagnostics, system health and errors

- Diagnostics report the supervised `ws_user` transport, supervision state, token expiry, feed state, engine store, armed deadlines and statistics-import progress.
- Diagnostics redact the account's device and home identifiers, serials and registry unique IDs. Device IDs become stable `device_N` labels, so the engine, membership and statistics sections stay correlatable.
- System health reports every configured account, counts accounts without a live runtime, labels them by reseller and index, and bounds the API version and health reads independently.
- Service and entity errors are translated (de/en/es/fr). Invalid input is a `ServiceValidationError`; a state restriction or failed write, including a protocol failure from an entity action, is a translated `HomeAssistantError`.
- The existing account's username or password can be corrected without deleting the integration entry, so existing entities and history are kept. Changing the reseller, or switching accounts on an entry that already has entities, requires removing and re-adding the integration.
- The integration title is “Smartbox”.

### Documentation and quality

- The README documents practical use cases, known limitations, the supported-reseller table and HACS installation.
- The quality-scale audit is complete except the `brands` submission, which is postponed; the manifest keeps `"quality_scale": "custom"`. `PARALLEL_UPDATES` is declared per platform.
- `tools/live_probe.py` verifies wire behaviour against real hardware, and the in-repo records (`docs/api-contract.md`, `docs/webapi-spec.md`, `docs/api-ws-user.md`) keep every wire claim and its provenance self-contained.
- The Tevolve tenant is selectable; its live login remains unverified.
