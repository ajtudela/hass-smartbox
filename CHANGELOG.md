# Changelog

## Unreleased

### Added

- Thermostat (`thm`) nodes are modelled as climate entities: `AUTO` and `OFF` (the app's ON resolves to `AUTO`), with `HEAT`/`COOL` as a manual override the app does not list, a 7–35 °C target range (the app's `minTempCelsius: 7` floor and the vendor's 35 °C maximum), the current temperature, and a low-battery binary. Setpoints use the app's plain `{stemp, units}` body: the controller deletes `modified_auto` before POSTing. A thermostat programme is a per-day `{m, t}` array with a `{prog, units}` write, not the heater int-slot shape; the schedule sensor and service are described below. The shape is verified against the vendor 2.54.2 `ThermostatController` (`mode`/`stemp`/`mtemp`/`active`/`units`, `setup.type`, `load_type`/`control_mode`/`anti_grippage`/`away_offset`); the demo-only `batt_level` and `error_code` fields are not exposed, and `presence` (a live panel-touch artifact, not a thermostat field) is not used. The wire taxonomy is recorded in `docs/api-contract.md` §3 and the per-tenant capability surface in `docs/webapi-spec.md` §3.2.
- Node devices show a product category derived from the vendor PID (triac/relay heater, heater module, heater, storage heater, water storage heater, thermostat, thermostatic valve, WiFi heater or power monitor), the wire type/address and the node firmware version. The label is device-level info, not an entity; a towel rail is an add-device category rather than a PID code, so it shows its controller category.
- A **Control Mode** select for the vendor-app catalogue: the htr family (`ElectricHeaterController` and subclasses; triac, relay and relay-with-`sensor_mode == 2` UI lists) and the storage family (`1C`/`2E`, OFF-first wire map). The `0402` relay map is live-confirmed; the other rows are derived from the decoded 2.54.2 factory and controller arrays. It requires the library `set_control_mode` API (smartbox 3.2.1). The thermostat family (`04`/`09`/`0F`) is withheld because the app's UI list offers `pid_60` while its wire array has plain `pid` (hass-smartbox docs/api-contract.md §1.9). Code `28` is a THM floor thermostat with an htr-family controller, so it keeps the select; other THM codes are withheld by integration choice. The app's Elnur water-storage (`2A`) hide and the classic-UI hide for `14`/`20`/`25` are mirrored, so those products get no select in those modes. A stored index outside the product's UI list reads as unknown while the select keeps the app's choices.
- Storage-heater telemetry that had no entity: **Target Charge** (with charging slots and active days as attributes) and **Charging**. Water storage heaters (product code `2A`) expose **Water Ready** (50 %) and **Water Fully Ready** (100 %) instead of Charging, matching the app's water-storage card, and the Elnur stepped storage controller's `charge_level` is scaled from its 0–5 steps to 0–100. On heater nodes, **Window Detection** is a diagnostic binary for the heater's inferred `window_open` status (the Window Mode switch only enables detection), and **PIR Presence** is created for the applicable node types; PIR reads `french_rule_pir_state` and is gated on `setup.extra_options.pir_enabled`, and **Low Battery** (`low_batt`) is offered on thermostats, where the app shows the battery icon. The `error_code` field is not used; `presence` is a generic panel-touch artifact, not a PIR flag.
- The supported-reseller table lists **Ducasa (Ducaheat)**. The `ducaheat.termoweb.net` portal serves the Tevolve build (serial 15, the same client credential, `users-api.helki.com` and `ws_user`), so the entry resolves to the Tevolve tenant and adds no new wire surface (hass-smartbox docs/api-contract.md §1.1). A live login remains unverified.
- Thermostat programmes: `sensor.*_thermostat_schedule` reads the day-keyed `{m, t}` segments and `smartbox.set_thermostat_schedule` writes `{prog, units}` through the new thermostat `/prog` op. A thermostat **Anti-grippage** switch writes setup `anti_grippage` `"on"`/`"off"`.
- Storage-heater modes now follow the controller class: Elnur `ui_mode` (Ecombi/Classic combined/Classic accumulator) and Helki storage `operational_mode`. Classic accumulators use ON/OFF through `target_charge_per` 100/0; other storage modes use the normal status path. The storage UI mode is a select, and `smartbox.set_charging_schedule` writes active days and valley slots.
- Helki storage `1C/2E` boost uses the legacy `/boost` body and auto-deactivates back to the operational AUTO/MANUAL mode; storage lock uses `/lock` 0/1/2.
- Tevolve EASY and RUNBACK switches. They write the app's persistent status flags (`{"easy": bool}` / `{"runback": bool}`) and clear the mutually exclusive counterpart first, matching the vendor settings.
- The schedule sensor and program card use each controller's decoded profile catalogue: the base `frost/eco/comfort`, the Helki/Haverland four-profile `frost/eco/comfort/off`, and the htr_mod `off/frost/eco/comfort` order. A 48-slot half-hourly programme renders as readily as a 24-slot one, and the card now accepts an `off` slot instead of marking the whole programme unsupported. The HJM 0132 live programme confirmed the OFF index and the 48-slot shape.
- Per-controller temperature ranges: base/Helki/Lhz 1-30 °C (the app's 1 °C UI floor over the 0-30 controller), Haverland 5-30, htr_mod 5-28 with eco 10-18 and comfort 18-28, Tevolve triac/WiFi/towel-rail 7-35 (PIR 27 keeps that range with a 3.5 °C comfort/eco programme gap and a read-only ICE slot), Tevolve relay 0-35, Tevolve/Ferroli ATC 5-30 with ice 5-10, Helki storage setup `min_stemp`/`max_stemp` with a 5/30 fallback, Elnur storage 3-30, and the factory's 0-30 ElectricHeater default for undecoded htr PIDs. The factory-default `0D/18/24/43` rows use the base 0-30 catalogue, and an unknown/`00` `acm` resolves to HelkiStorage. Programme bounds now follow the same controller table (`ice`, `eco`, `comfort` each get their own range) and the live `max_stemp_limit` still clamps the top. The programmed-temp write passes those bounds to the sanitizer, so a 1.0 °C base profile is no longer clamped to 5.
- Integer-lock controllers expose an **Off / Local lock / Remote lock** select for `status.locked` 0/1/2; boolean-lock controllers keep the child-lock switch. The HJM fw-1.8 unit confirmed `{locked: 1}` and `{locked: 2}` echo while `{locked: true}` is silently ignored.
- Boost and runback activation attributes: `number.*_config_boost_temperature`, `number.*_config_boost_duration`, `number.*_config_runback_temperature` and `number.*_config_runback_duration`. Legacy controllers keep the boost pair on the device (`extra_options.boost_temp`/`boost_time`, the same fields the app writes). FULL `/boost_mode` WiFi/ATC nodes expose the pair only when status advertises `temp_and_time`, and runback only when it advertises `runback`; those values live in a per-entry `.storage` record, survive a restart, and feed the one-click switch/preset. Runback has its own temperature and duration (30-minute grid) instead of overloading the shared `boost_time` field. A missing, malformed or off-grid value is refused with a validation error, so the frame sent to the heater is always complete.
- Test coverage now pins every decoded product code (`test_product_code_parity.py`: family, bounds, profiles, boost configs, EASY/RUNBACK, lock and setting gates) and the exact entity domains/unique keys per representative family (`test_entity_surface_matrix.py`), including the FULL-boost attribute variants (temp-and-time, fixed-only, empty mode list). The surface matrix snapshots options, bounds and current states for every case (not a sample), adds live-frame state cases and box-level attributes, and the build no longer leaves the duty/control-mode selects empty before the first status frame (they fall back to the discovery setup).
- Diagnostics now include the neutral controller profile (limits, bounds and programme ranges) for every node, and `smartbox profile` in the sibling library resolves the same data offline. The migration branch installs the sibling library editable (`tools/dev_install_library.sh`, tox `-e ../smartbox`) until a library minor is published.
- Migration validation baseline: a reviewed `tests/fixtures/product_codes.json` (51 codes) with a fixture-sync test, per-unit entity-registry and diagnostics goldens, a service-name snapshot, an activation request-shape matrix, and a per-surface attribute/state snapshot. `controller.py` now re-exports the neutral limits/profiles/surface predicates from `smartbox.device.profile`, the lock/boost policy from `smartbox.device.modes`, `control_mode.py` re-exports the catalogue from `smartbox.device.control_modes`, `storage.py` maps the neutral `StorageIntent` from `smartbox.device.storage` to `HVACMode`, and `identity.py` delegates the setting gates to `smartbox.device.settings`.

### Changed

- Node types are handled against the API's five wire values (`htr`, `htr_mod`, `thm`, `acm`, `pmo`). Product/UI categories from the per-tenant `capabilities.json` (towel rails, WiFi heater variants, floor thermostats, water/solar storage) are not wire types. An unknown wire type is skipped with one discovery warning instead of getting meaningless power/consumption entities.
- Power and total-consumption sensors are created only for `htr`, `htr_mod`, `acm` and `pmo`. The app hides per-node kWh for thermostats (it shows usage time), so both old THM registry entries (`{node_id}_power`, `{node_id}_total_consumption`) are removed at setup.
- The integration now requires `smartbox>=3.2.1,<3.3`. The 3.2.1 library adds the numeric-string write-echo handling, the Ducasa/Ducaheat alias and `set_control_mode`.

### Fixed

- Boost one-click on legacy config 1/2 WiFi/ATC/Helki controllers now follows the controller path instead of raising `boost_mode_unavailable`; storage keeps its temperature/duration at config 1. The activation request-shape matrix pins both.
- The storage adapter keeps the old `None` → translated `hvac_mode_unsupported` contract for an unsupported incoming mode instead of raising `ValueError`.
- `max_runback_time` below the 30-minute floor is clamped before validation, so the runback range cannot invert.
- Programme-temperature sliders and the write sanitizer now clamp to the stored `max_stemp_limit` (frost `max-1`, eco `max-offset`, comfort `max`), matching the vendor app. A 23 °C limit previously still allowed 23-30 °C programme writes.
- The boost-preference store no longer overwrites the stored record when the initial load failed, logs load/save failures at warning, and retries the load before a write.
- An undecoded htr PID now uses the factory's default ElectricHeater catalogue (0-30) instead of the integration's historical 7-35 fallback; an undecoded acm still resolves to HelkiStorage.
- Helki/Lhz heater child lock follows the app's `factory_options.super_lock_available`: integer payloads (the Off/Local/Remote select) only when the flag is set, boolean switch otherwise. Haverland `1D` keeps its fw-1.7 gate.
- Haverland `17`/`1D`/`47` at `boost_config` 3 now activate through `/boost_mode` with the app's fixed-time mode; configs 0-2 keep the status-flag activation.
- Tevolve PIR (27) programme temperatures use the app's 3.5 °C comfort/eco gap (eco 7.5-31.5 °C, comfort 11-35 °C) and no longer expose an editable frost number; the read-only ICE slot is still written back unchanged.
- The Lhz power-limit select lists its presets in the app's sorted order (Off, LST, Baby Care, 70 %, 80 %, 90 %) instead of insertion order.
- Storage boost deactivation now restores the app's operational AUTO/MANUAL mode with the app's bare `{mode}` body, including the Home preset path.
- Helki storage at `operational_mode == 1` now offers HEAT alongside AUTO/OFF. The app's own boost deactivation writes wire mode `manual` on that configuration, so the previous picker-only list could report a current mode it did not expose.
- Boost temperature/duration entities and their `set_boost_params` targets are no longer built for product codes that reject `extra_options`, and no boost surface is built on the factory-default ElectricHeater codes. On those codes, the optimistic setup merge keeps the last known `extra_options` instead of blanking it after an unrelated setup write.
- Boost availability now follows each controller's app mode list: Helki/Lhz config 2 (36 unconditionally), Tevolve triac/towel-rail unconditional, PIR 27 none, WiFi/ATC endpoint/config, Haverland legacy always, and Haverland WiFi whenever `boost_config` is not the fixed-time value 1 (an absent config keeps its fixed-time BOOST). Storage keeps the Electrorad/Evconfort fw-1.8 gate.
- A THM with product code 28 now gets the heater surface (heater programme, boost, EASY/RUNBACK, programme temperatures) instead of the thermostat `{m,t}` schedule; 04/09/0F keep the thermostat surface.
- EASY and RUNBACK are separate family gates: PIR keeps EASY and drops RUNBACK, ATC drops EASY and keeps RUNBACK, and RUNBACK is withheld on a full-boost WiFi/ATC node where activation requires `/boost_mode` parameters the switch does not carry.
- Lock kind follows the controller: Haverland 1D uses the integer payload at fw 1.7+, 47 stays integer unconditionally, and the factory-default 43/0D/18 use the boolean path.
- Storage UI mode is created only for Elnur storage while the unit is in a classic mode. Its choices follow the product: 14 accepts Classic combined/Classic accumulator, 20/25 accept Ecombi/Classic combined, and water storage 2A omits the setting. Water storage filters AUTO/MANUAL through `extra_nrg_conf`, gets no frost/eco/comfort programme numbers, and gets no generic heater schedule (its ON/OFF_AUTO profile shape is not modelled); it also omits the away-offset, window-detection and true-radiant settings, and its target-charge sensor is read-only (no `set_charging_schedule` target). An absent `operational_mode`/`ui_mode` no longer infers AUTO.
- Duty-limit entities are gated on the Helki/Lhz heater chain, and max-stemp entities are withheld on storage, Tevolve triac/WiFi/towel-rail, and Haverland 1D, matching the app's controller settings.
- Boost temperatures are formatted to one decimal on both the number and legacy activation paths, so a Fahrenheit-to-Celsius conversion writes `21.1` rather than a long float.
- Runtime device decisions now use the live version PID: storage modes/writes, boost availability, lock/option choices, controller limits and the schedule catalogue update when a late version frame supplies a PID that the discovery read missed. Entity creation still uses the discovery snapshot, so adding or removing entities after a late PID needs a reload.
- Unknown/`00` ACM nodes use the HelkiStorage mode fallback, and water storage in the combined extra-energy modes offers AUTO/OFF (HEAT/OFF in EXTRA_ENERGY_USAGE), matching the app's default-mode resolution.
- ATC 4C/4D use the integer lock payload inherited from the WiFi controller.
- THM 28 with `sensor_mode == NO_PROBES` hides temperature control, the frost/eco/comfort numbers, the boost/EASY/RUNBACK entities, the away-offset/control-mode/window/true-radiant settings and the generic heater schedule; a direct `climate.set_temperature` call is rejected. The ON/OFF timer programme is not modelled.
- Boost temperature/duration numbers follow the app's supported boost mode: config 2, or config 3 on Helki storage and the Tevolve triac family, or an absent config on the controllers whose BOOST is unconditional and defaults to set-temp-and-time (Helki storage, LhzRelay 36, Tevolve triac/towel-rail/28). The Haverland fixed-time families (17/1D/47) never expose numbers, even at config 2/3. Helki storage's numbers no longer depend on `boost_config`.
- `supports_boost_mode` also requires `boost_available`, so a config-3 base/Helki/Climastar/Haverland-WiFi node that never offers BOOST cannot expose `/boost_mode` presets, temperature editing or the service. LhzRelay 36 config 3 remains positive. A direct `climate.set_preset_mode("boost")` call now refuses with `boost_unsupported` instead of reaching the engine.
- Boost activation is family-aware: storage uses `/boost`, Tevolve triac/towel-rail/28 always use the legacy setup+status path even at config 3, the Haverland fixed-time families (17/1D/47) use the status-only `setBasicBoost` path even at config 3, and other full-boost controllers use `/boost_mode`. Config-less legacy activation now writes the temperature/duration defaults into the setup before the status flag. Config-3 WiFi/ATC activation carries the stored `extra_options` temperature/duration even though its parameter number entities are withheld. The `set_boost_mode` service, full-boost presets (RUNBACK/ADVANCED_SLOT) and the active-boost temperature editor are withheld from storage, the triac family and the Haverland fixed-time families.
- Max-limit entities wait for a known PID and require a heater surface; PMO and true thermostat nodes do not get them.
- Thermostat `{m,t}` segments accept integral numeric-string minutes, an active synced heater with no `power` field reads 0 like the app, schedule options re-resolve when a late version frame arrives, and the card's fallback catalogue is the base three-profile list.
- A storage target temperature reads as unknown while the node is off.
- Code FF is treated as htr_mod only on the htr_mod wire type; the family predicates agree.
- Elnur storage no longer inherits the Helki boost switch when its setup carries a `boost_config`.
- htr_mod's manual-off setpoint range advertises the 10-28 °C range it can actually write.
- Lock entity kind comes from the controller family, so a lost/mistyped discovery status cannot switch a known integer-lock unit to the boolean path.
- Programme-temperature writes fall back to setup units before the first status frame.
- Classic accumulators no longer get programme-temperature numbers; their temperature control is unavailable.
- Charging/thermostat service handlers distinguish authentication and transport failures from generic API errors.
- The program card's stale-state fallback knows the four-profile OFF catalogue.
- A classic-accumulator power-on now selects HEAT when AUTO is not part of its mode list, and the missing unsupported-mode translation is present.
- htr_mod HEAT from the OFF profile enters comfort instead of re-sending `selected_temp: "off"`.
- Storage UI mode is now actually built as a select for ACM setups carrying `factory_options.ui_mode`.
- Thermostat schedule segments use the documented `m` end-minute semantics, `m=1440` lands at the next midnight, and pushed programme changes re-arm the boundary tracker. A thermostat `{m, t}` `/prog` frame now confirms the pending write instead of waiting for the verification GET.
- The integer child-lock select reads numeric-string and float `locked` values, and the thermostat-schedule icon entry uses the standard `{"default": …}` shape.
- htr_mod follows the app's profile state machine: `selected_temp: "off"` is OFF in manual, a setpoint from that state enters eco/comfort, AUTO reads the current programme profile, and AUTO bounds use the global 5–28 °C range. htr_mod hides control mode, away offset and max-limit where its settings list does not offer them.
- Power sensors follow the controller: storage reports `status.power` while idle, htr_mod uses `setup.power` when active/heating, and base htr requires `sync_status: ok`.
- Helki storage code 2E reads `current_charge_per`, like 1C; Elnur 14 keeps the stepped ×20 and E2 20/25 keep raw `charge_level`.
- Boost availability is controller-aware: legacy Haverland (17) always offers its fixed-time boost, Haverland WiFi (1D/47) offers it unless `boost_config` is the fixed-time value 1, and other families keep the `boost_config` gate. Storage boost uses the legacy `/boost` path independently of `boost_config`, and deactivates back to the operational AUTO/MANUAL mode.
- Control-mode selection completes the explicit htr product codes and falls back to the Helki storage/relay wire for an otherwise-unlisted acm.
- Selecting the **home** preset now clears an active boost, Tevolve EASY/RUNBACK flag and away state instead of leaving the special mode running. This mirrors the app's `deactivateCurrentMode` before a mode switch; the device has no "home" command of its own.
- `smartbox.set_schedule` rejects profile slots outside the target controller's catalogue (for example `3` on a 3-profile 0402 base heater) with the existing `schedule_invalid` error instead of posting a value the controller does not define.
- Malformed temperature, duty, power and charge values now read as unknown in entity properties instead of raising from `float()`/`int()`; the mapping target-temperature read path uses the same defensive parser.
- The three programme-temperature number entities no longer disappear when a heater is `lost` during integration setup/reload. The gate now reads the controller catalogue instead of the transient `status.ice_temp/eco_temp/comf_temp` keys, so a later ok frame restores their values without a second reload.
- Boost temperature writes now convert the node-scale value back into `extra_options`' Celsius form when `extra_options.version >= 2`, matching the app instead of storing a Fahrenheit value in a Celsius field.
- The Boost End sensor and boost attributes interpret `status.boost_end_min` as the home-local minute of day and apply `boost_end_day`, instead of treating it as UTC. A 60-minute CET boost at 13:49 now ends at 14:49, not 15:49.
- The Lhz controller family's duty-limit select now offers its hard-coded 0/80/90 choices (plus 70 when its LST value is not 70, and its Baby Care/LST values), matching the vendor app; other families keep Off/LST/Baby Care.
- Boost duration is limited by the controller family rather than a fixed 240 minutes: the base/Helki/Haverland family allows 1440 (the app reached 24:00 on HJM), Tevolve ATC 120, and the Climastar WiFi triac 300. The `set_boost_params` schema accepts up to 1440 and the number entity still enforces the controller's own maximum.
- Write verification no longer reports a numeric-string echo (`"20"` for a posted `"20.0"`) as `accepted but not applied` or falls through to a verification GET for a write the heater applied (#66).

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
