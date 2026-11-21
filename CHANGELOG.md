# Changelog

## Unreleased

### Added

- Thermostat (`thm`) nodes are modelled as climate entities: `AUTO` and `OFF` (the app's ON resolves to `AUTO`), with `HEAT`/`COOL` as a manual override the app does not list, a 7–35 °C target range (the app's `minTempCelsius: 7` floor and the vendor's 35 °C maximum), the current temperature, and a low-battery binary. Setpoints use the app's plain `{stemp, units}` body: the controller deletes `modified_auto` before POSTing. A thermostat has no schedule entity or schedule-service target yet; its programme is a per-day `{m, t}` array with a `{prog, units}` write, not the heater int-slot shape. The shape is verified against the vendor 2.54.2 `ThermostatController` (`mode`/`stemp`/`mtemp`/`active`/`units`, `setup.type`, `load_type`/`control_mode`/`anti_grippage`/`away_offset`); the demo-only `batt_level` and `error_code` fields are not exposed, and `presence` (a live panel-touch artifact, not a thermostat field) is not used. The wire taxonomy is recorded in `docs/api-contract.md` §3 and the per-tenant capability surface in `docs/webapi-spec.md` §3.2.
- Node devices show a product category derived from the vendor PID (triac/relay heater, heater module, heater, storage heater, water storage heater, thermostat, thermostatic valve, WiFi heater or power monitor), the wire type/address and the node firmware version. The label is device-level info, not an entity; a towel rail is an add-device category rather than a PID code, so it shows its controller category.
- Storage-heater telemetry that had no entity: **Target Charge** (with charging slots and active days as attributes) and **Charging**. Water storage heaters (product code `2A`) expose **Water Ready** (50 %) and **Water Fully Ready** (100 %) instead of Charging, matching the app's water-storage card, and the Elnur stepped storage controller's `charge_level` is scaled from its 0–5 steps to 0–100. On heater nodes, **Window Open** and **PIR Presence** are created for the applicable node types; PIR reads `french_rule_pir_state` and is gated on `setup.extra_options.pir_enabled`, and **Low Battery** (`low_batt`) is offered on thermostats, where the app shows the battery icon. The `error_code` field is not used; `presence` is a generic panel-touch artifact, not a PIR flag.

### Changed

- Node types are handled against the API's five wire values (`htr`, `htr_mod`, `thm`, `acm`, `pmo`). Product/UI categories from the per-tenant `capabilities.json` (towel rails, WiFi heater variants, floor thermostats, water/solar storage) are not wire types. An unknown wire type is skipped with one discovery warning instead of getting meaningless power/consumption entities.
- Power and total-consumption sensors are created only for `htr`, `htr_mod`, `acm` and `pmo`. The app hides per-node kWh for thermostats (it shows usage time), so both old THM registry entries (`{node_id}_power`, `{node_id}_total_consumption`) are removed at setup.

## 3.2.1 (2026-11-20)

### Added

- A per-heater **Power Limit Factor** select (Off / LST / Baby Care): the node's own duty cap at `extra_options.user_duty_limit`, built from the unit's preset percentages the way the vendor app builds it and offered only where the setup carries the factor, the presets and a non-zero rated power. The heater applies the selection at its next start; a stored value outside the presets reads as unknown, as the app leaves its field blank (hass-smartbox docs/api-contract.md §5.7, §9).

### Fixed

- The boxless power-limit purge now fires on a real boxless home. The confirmation required a `connected` key that REST `/dev_data` never carries, so the box power-limit number and the "no power limit" switch survived and were rebuilt on every setup. Absence is now confirmed from a `nodes` list and an `away_status` object plus the absence of a power-limit configuration, while a malformed snapshot or a present-but-unreadable limit copy keeps the existing entities (hass-smartbox docs/api-contract.md §3.2 E46, §7 item 17).

### Changed

- Radiator priority is only offered on heaters paired with a Smartbox, where the box arbitrates the load; the app hides the same row on a WiFi heater. Boxless homes lose the priority select on upgrade and its registry entry is removed (hass-smartbox docs/api-contract.md §9).

## 3.2.0 (2026-11-18)

### Fixed

- The total-consumption sensor accepts an accumulator's `counters` sample shape and reads its first entry as the vendor app does, so `acm` nodes no longer drop every sample row and report `unknown` (hass-smartbox docs/api-contract.md §7 item 19). Heaters keep the scalar `counter` shape.
- Box power-limit entities are no longer created for boxless heaters (heaters not paired with a Smartbox controller), whose box power-limit endpoint the API rejects. Existing box power-limit entities are removed for those devices on setup. The parent device, connected state and away switch remain.
- `htr_mod` climate entities now follow the vendor app's setpoint contract: a 1 °C step, the union of the comfort and eco ranges while either is active (10–28 °C; ice fixed to the slot's temperature, in whole degrees on Fahrenheit nodes too), the app's recomposition in both directions (a target at or below 18 °C selects `eco` and places `comfort_temp`/`eco_offset` within 4–8 °C, keeping the stored comfort unless the gap cannot reach; above 18 °C selects `comfort` with the stored offset; an off-grid stored comfort is snapped to a whole degree), a target reported clamped into that range, and `mode: manual` on every setpoint write. The limits apply to the Haverland product codes the app dispatches on (`pid[2:4]` = `0A`/`FF`) and to nodes whose version has not been read yet; other `htr_mod` hardware keeps the previous temperature arithmetic (hass-smartbox docs/api-contract.md §7 item 7).
- A write-confirmation timeout now runs one GET of the written slot instead of marking the node Unavailable. A lost-marked read marks it Unavailable; data is reconciled; an API failure changes nothing. A write the server accepted but ignored leaves the entity usable and logs the mismatch. Lost frames during a pending write arm the normal lost window. This follows the vendor app’s lost/feed availability model; offline detection now waits for lost evidence rather than a write timeout.
- The `locked` attribute is normalized to `bool`; htr nodes report a boolean and acm nodes report `0`/`1` (hass-smartbox docs/api-contract.md §7 item 18). Templates that compared the acm attribute to `0`/`1` should compare to `false`/`true`.

### Changed

- `climate.set_temperature` on `htr_mod` nodes now accepts any target in the 10–28 °C union while comfort or eco is active; the write selects the profile from the target, so an automation can set 17 °C from `comfort` or 21 °C from `eco` without a preset change. Only targets outside 10–28 °C are rejected, and the `ice` profile stays fixed to the slot's own value.
- The integration pins `smartbox>=3.2.0,<3.3`, which carries the write-timeout verification and the app-shaped htr_mod body without `units`. The upper bound keeps a future minor or major release from installing until the integration is updated for it.

## 3.1.3 (2026-11-17)

### Added

- The supported-reseller table lists Perry Home (`api-perry`). The library row is selectable; a live login remains unverified.
- The token bundle is persisted in Home Assistant's storage and resumed on setup, so a restart continues the existing vendor session instead of minting a new login; the rotated refresh token is written after every grant. Reauth, reconfigure and entry removal clear the stored bundle. The record is stamped with the account and effective API host, so a host override or credential change starts fresh.
- Node devices show the node type and wire address in the device model field, e.g. `htr_mod (addr 7)`.

### Changed

- The integration pins `smartbox>=3.1.0`, the current library release.

### Fixed

- Setup no longer aborts when a box lists empty node slots (`installed: false`). Node discovery skips them before reading status/setup/version, and membership treats them as absent so an emptied slot cannot keep a stale registry device alive. The library keeps empty slots out of the engine's node membership, so a later installation in that slot is still discovered as a new node.

## 3.1.2 (2026-11-15)

### Added

- The climate entity on `htr_mod` nodes exposes the device's live `selected_temp` profile (`comfort`/`eco`/`ice`/`off`) as a read-only state attribute when the status carries it. The Schedule sensor remains the programme view.
- `custom_components/smartbox/reseller.py` has a `RESELLER_OVERRIDES` table for manual connection tests. It can replace `api_host`, `basic_auth`, `serial_id` and `web_url` for an existing config-entry brand. An active override is logged once and reported in diagnostics without printing the credential.

### Changed

- The `smartbox` library is now published on PyPI; the dependency pins use the registry instead of the GitHub tag tarball: `manifest.json` requires `smartbox>=3.0.2`, the pyproject dependency and the tox test environments match, and the CI workflow no longer needs a separate library install step (`uv sync` covers it). A test keeps the manifest and pyproject pins identical.

### Fixed

- Elnur uses its own API host (`api-elnur.helki.com`); the reseller table now carries a per-row host, and the token grant, REST client, ws_user probe and transport all use it. Other ws_user brands keep the shared `users-api.helki.com` host; the legacy EHC row carries `api-ehc.helki.com` but remains unsupported by the 3.x socket transport.

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
