# Device semantics extraction — design record

> **Status: migration in progress.** Slices 1-4 have landed on
> `migration/device-semantics`; slice 5 (CLI/diagnostics/pin) is partly done.
> The library minor and the integration pin are deferred. See
> [Migration progress](#migration-progress) for the landed boundary.

## Purpose

Move the device knowledge out of the Home Assistant adapter and into the
`smartbox` library, so one controller profile answers the same questions for
the entities, the CLI and diagnostics:

- Which controller family owns this product code?
- What are its setpoint and programme ranges?
- Which control-mode catalogue and wire indexes apply?
- Which schedule/profile order does it use?
- Is boost, lock, EASY/RUNBACK, storage UI mode or a thermostat schedule
  available?
- Which request shape does a mode, boost, lock or storage write use?

The adapter keeps Home Assistant presentation: entity creation, units, labels,
`HVACMode` mapping, registry identity, services and translations.

## Trigger

The extraction is justified when any of these becomes true:

1. A second consumer needs the device model. At design time the `smartbox`
   CLI and diagnostics saw the wire and the store but not the controller
   profile; slice 5 has since landed both.
2. At design time, duplicated device facts caused a bug: the same PID→family
   facts lived in several adapter modules; a missing explicit family entry or
   a charge-level dispatch mistake has already produced user-visible bugs.
3. A new product family needs the same logic in two or more places.

The trigger is visible now. The release gate was the current feature batch;
what remains is the 3.3.0 delivery method before the branch can install.

## Current duplication (pre-extraction inventory)

This table describes the adapter as it was before the extraction. The slices
have moved the listed facts into `smartbox.device`; the integration keeps only
thin re-exports/adapters. Use the table as the migration checklist, not as a
current duplication map.

| Fact | Pre-extraction homes |
|---|---|
| Product code and label | `product.py`, `controller.py`, `control_mode.py`, `storage.py`, `mapping.py` (`htr_mod_policy` codes), library `boost_config` |
| Controller family | `controller.py` code sets, `control_mode.EXPLICIT_FAMILY`, `storage.is_helki_storage/is_elnur_storage`, `mapping.htr_mod_policy`, `identity` gates |
| Setpoint range / floor | `controller.ControllerLimits`, `mapping.HtrModPolicy`, `adapter.node_max_stemp_limit`, `number`/`climate` entity bounds |
| Programme bounds / order | `controller.profile_bounds`/`prog_bounds_c`, `mapping` (htr_mod), `const.PROG_PROFILE_NAMES` (legacy default), card fallback |
| Control-mode catalogue | `control_mode.py` (family, actuation, UI and wire arrays), `controller` PID classification |
| Lock payload kind | `controller.lock_kind`, `identity` gates, `select.ChildLockMode`, `switch.ChildLockSwitch` |
| Boost support and kind | `identity.boost_available`, library `boost_config`, `storage.boost_supported`, `storage.write_for`, engine `set_boost` |
| Storage UI/operational modes | `storage.py`, `controller_limits` (storage min/max), `climate` |
| htr_mod state machine | `mapping` policy/limits/writes, `controller` limits and gates, `climate` |
| Thermostat schedule/anti-grippage | `sensor.ThermostatScheduleSensor`, `services`, `switch.AntiGrippage`, docs |
| Duty presets / Lhz filter | `identity.duty_limit_options` + `controller.is_lhz_family` |
| Feature gates | `identity` booleans built from setup keys + controller facts |

The pre-extraction `storage.py` imported Home Assistant's `HVACMode`; slice 3
replaced that with the neutral `StorageIntent`.

## Target architecture

Shipped library layout:

```
smartbox/device/
  codes.py          reviewed product-code table (family/actuation/limits/surface/label)
  capabilities.py   operation eligibility checks over NodeFacts (storage UI-mode shared with the adapter; the rest back the CLI)
  profile.py        ControllerLimits/_CODE_LIMITS, bounds, resolve_profile and resolve_stored_profile
  control_modes.py  catalogue, wire index mapping and availability
  modes.py          identity-only lock/boost policy
  storage.py        StorageIntent/StorageWrite and storage semantics
  settings.py       entity-setting gates and option sets
```

Integration:

```
adapter/entity platforms
  -> profile = resolve_profile(node_type, pid, fw_version, setup)
  -> HA presentation: HVACMode, units, labels, entity gates, services
```

Names are neutral strings or enums, for example `"htr_mod"`, `"thresholds"`,
`"on_off_auto"`; the adapter maps them to HA types.

### Resolution inputs

`resolve_profile(node_type, pid, fw_version, setup, max_limit_c=<override>)`
resolves against identity and setup:

- `pid` is the live/discovery `version.pid`; the code is `pid[2:4].upper()`.
- `node_type` is the wire type.
- `fw_version` gates Helki storage profiles, lock payloads and similar.
- `setup` supplies `factory_options.ui_mode`, `operational_mode`,
  `boost_config`, `min_stemp`/`max_stemp`, `max_stemp_limit`,
  `extra_options` and model fields.
- `max_limit_c` is an explicit override: `None` keeps the controller
  catalogue, a positive value previews a proposed limit. Use
  `resolve_stored_profile(node_type, pid, fw_version, setup)` to apply the
  setup's stored `max_stemp_limit` (`<= 0` means disabled); diagnostics and
  the CLI use that when no override is given.
- Live `status` is **not** an input for entity presence. A lost node at
  discovery must still resolve the same profile; reading transient status keys
  to build entities has already produced a permanent-missing-entity bug. The
  one deliberate exception is the boost/runback attribute entities: a FULL
  node's `status.boost_modes_available` decides whether a parameter slider has
  anything to configure, so those attributes can be absent until the mode list
  is observed (a reload re-evaluates them).

Fallbacks:

- Unlisted htr/htr_mod PID → ElectricHeater defaults.
- Unlisted acm PID → HelkiStorage defaults (off-first wire, relay actuation);
  the same default covers the `00` UNKNOWN and `03`/`22` power-meter codes on
  an acm node, because the app's heating switch has no case for them.
- Unlisted thm PID → thermostat surface with the thermostat 7-35 limits; the
  control-mode select stays withheld.
- Missing PID → base ElectricHeater for htr, HelkiStorage for acm and the
  thermostat 7-35 limits for thm; auth/version failures leave the resolved
  profile absent until a PID is seen.

### Coverage in place

The integration now pins the current profile data with two exhaustive tests:
`tests/test_product_code_parity.py` covers all 51 `EProductCodeValue` entries
(family, bounds, profiles, boost configs and parameter policy, duty presets,
EASY/RUNBACK, lock kind, firmware and setting gates), and
`tests/test_entity_surface_matrix.py` covers the domains and unique keys for
each representative family and variant, plus the box-level entities. The
extraction consumes these as golden expectations: move the rows to the library
with the profile, and keep the entity-surface matrix in the adapter.

## Strategies to move

1. **Temperature and programme bounds** — the current controller table,
   `climate_bounds`, `max_limit_bounds`, `boost_temperature_bounds`,
   `profile_bounds`, `prog_bounds_c`, and the htr_mod policy. Output Celsius;
   the adapter converts to the node/HA unit.
2. **Control-mode catalogue** — explicit family and actuation maps, UI/wire
   arrays, sensor-mode filtering, choices/availability/labels. Return neutral
   mode names and wire indexes; the adapter maps labels.
3. **Lock and boost** — lock payload kind, boost support/kind; the engine
   chooses status versus `/lock`, and `/boost` versus `/boost_mode`, from the
   profile.
4. **Storage semantics** — `ui_mode`/`operational_mode`, available modes,
   classic-accumulator target-charge intent, non-classic mode intent. Use
   neutral `StorageIntent`/mode enums; the adapter maps to `HVACMode`.
5. **Thermostat semantics** — schedule availability, `{m,t}` validation,
   anti-grippage availability. The HA sensor and service stay in the adapter.
6. **Setting gates** — programme temps, max-limit, away offset, duty options,
   window mode, true radiant and priority. These are profile plus setup facts;
   the adapter still decides entity creation.

## What stays in the adapter

- `HVACMode`, `HVACAction`, unit conversion, `UnitOfTemperature`, sensor
  device classes, entity names/keys/translations, `EntityCategory`,
  `RestoreEntity`.
- Entity lifecycle, registry identity, unique IDs and stale-entity pruning.
- Services, service schemas, target resolution and translated errors.
- Entity-facing confirmation presentation; the confirmation and optimistic
  merge themselves are library (`smartbox.core`).
- Card assets and frontend behaviour.

## Migration progress

- **Slice 1 (codes, profiles, limits) landed.** `smartbox.device.profile`
  owns the neutral `ControllerLimits`, the code/family sets, bounds helpers
  and `resolve_profile`. The integration's `controller.py` re-exports them and
  keeps the not-yet-moved gates. The library golden test pins all 51 codes
  against the reviewed fixture. Behaviour is unchanged.
- **Slice 2 (control modes) landed.** `smartbox.device.control_modes` owns
  the decoded family/actuation maps, UI/wire arrays, labels and availability.
  The integration imports it directly (the `control_mode.py` shim was
  deleted); the entity keeps the HA select shape.
- **Slice 3 complete.** `smartbox.device.modes` owns the identity-only
  lock/boost policy; the status-reading boost mode helpers stay in the
  adapter's `controller.py`, and
  `smartbox.device.storage` owns the storage semantics with a neutral
  `StorageIntent`; the integration's `storage.py` maps intents to `HVACMode`.
  No Home Assistant type remains in the moved storage logic.
- **Slice 4 complete for the setting gates.** `smartbox.device.settings`
  owns window mode, true radiant, away offset, max-temperature limit, node
  power limit, duty presets, priority, programme temperatures and boost
  availability. The integration's `identity.py` keeps thin wrappers over the
  frozen node object. Thermostat schedule and anti-grippage remain
  adapter-side by design.
- **Slice 5 is in progress.** `smartbox profile` is the library's second
  consumer, diagnostics carry the resolved profile per node, and the migration
  branch installs the sibling checkout via `tools/dev_install_library.sh` and
  the tox `-e ../smartbox` dependency. The version bump, pin and release
  remain deferred. The adapter keeps boost activation, preferences and the
  FULL attribute visibility throughout.
- The library is bumped to **3.3.0** in its own repo, but the integration's
  dependency is PyPI-only at the published `>=3.2.1,<3.3` after the temporary
  path override was removed. The branch imports `smartbox.device`, so a fresh
  install will fail until an approved 3.3.0 delivery method is chosen. The pin
  must move with that release.

## Migration slices

Each slice is behavior-preserving, lands with the adapter copy deleted in the
same change, and has a golden comparison.

1. **Codes, profiles and limits.** Land `device/profile.py` (codes, limits,
   programme catalogues and bounds) and move the controller table. The integration imports the profile; the old adapter copy is
   deleted. No entity state change.
2. **Control-mode catalogue.** Move the family/actuation/UI/wire data and
   resolution; the adapter keeps labels and `HVACMode` options. Delete the
   duplicated maps.
3. **Lock, boost and storage semantics.** Move lock kind, boost support/kind
   and storage mode resolution. Replace `storage.py`'s `HVACMode` import with
   neutral types. The engine chooses endpoints from the profile. Boost
   attribute visibility and the HA-side preference store stay in the adapter:
   the profile describes the controller, while the advertised mode list and
   the user's chosen values are runtime/adapter concerns.
4. **Thermostat and setting gates.** Move schedule/anti-grippage availability
   and the identity setting gates. Entity creation and service handling stay
   in the adapter.
5. **Second consumer.** Add `smartbox profile` to the CLI and profile data to
   diagnostics. This proves the extraction and closes the trigger.

## Changing a controller fact

1. Edit `smartbox/device/codes.py` (code row) and/or the owning module
   (`profile.py` limits, `control_modes.py` arrays, `modes.py` lock/boost
   policy, `storage.py` intents, `settings.py` gates).
2. Update the reviewed row in `tests/test_product_code_parity.py` (the
   integration's `Expectation` table) and mirror it into
   `smartbox/tests/fixtures/product_codes.json`; the parity test compares the
   two and reads the library copy from the sibling checkout (set
   `SMARTBOX_LIB` or keep `../smartbox`). Add the code row's
   `family`/`actuation`/`limits_family`/`surface`/`label`/
   `supports_extra_options`/`lhz_duty`/`thm_heater`/`fixed_time_boost`/`water`/
   `stepped_charge` columns and the stored profile facts. The same file carries the reviewed
   control-mode wire/UI arrays, labels and the storage UI-mode options, so a
   catalogue change updates its top-level `control_modes` block or the
   affected row. Bump `schema` only when the fixture shape itself changes.
3. Run the library tests, then the integration parity/attribute/registry/
   diagnostics tests. For a deliberate surface change, regenerate a JSON
   golden with `UPDATE_SNAPSHOTS=1 pytest <test>` and review the diff; the
   attribute matrix's `SNAPSHOTS` is a literal dictionary in
   `tests/test_entity_surface_matrix.py`, so edit that entry directly.
4. Update `docs/api-contract.md` (with the decoded class/offset), `profile-audit.md`
   and, if the difference is deliberate, `vendor-deviations.md`.
5. Re-run item 13 of the `docs/api-ws-user.md` checklist and update its
   status/evidence when the app adds a class or enum entry.

## Golden tests

See [profile-audit.md](profile-audit.md) for the decoded profile formulas,
the per-family table and the max-stemp programme rule.

- One reviewed product-code table (`smartbox/device/codes.py`) and one shared
  fixture (`smartbox/tests/fixtures/product_codes.json`, consumed by the
  integration parity test). The library device-code test asserts the table,
  the derived sets/maps and the control-mode catalogue against that fixture;
  the library profile/control-mode/storage tests assert the per-code facts.
- One integration snapshot test asserting entity domains, unique keys,
  options, bounds and current states versus the pre-extraction baseline.
  `tests/test_entity_surface_matrix.py` now does this for every surface case
  (33 node cases, live-frame state cases and the box-level entities), plus the
  full entity-registry and diagnostics goldens; keep it adapter-side and
  extend it as new entities appear.
- Use the known live fixtures: the HJM Helki base heater (four profiles,
  integer lock), the base 0402 heater (three profiles, boolean lock, max limit
  23), storage 1C/2E and Elnur 14/20/25/2A setup branches, htr_mod 0A/FF,
  Tevolve triac/WiFi/ATC and the FULL temp-and-time/fixed-only/empty mode
  lists.
- Explicitly cover lost-at-discovery resolution: the profile answer must not
  depend on `status`. The one exception is the adapter's boost/runback
  attribute visibility, which reads `status.boost_modes_available` to decide
  whether a slider exists; keep that out of `resolve_profile` and covered by
  the surface matrix's FULL cases.
- Boost activation planning and validation (`boost_activation.py`), the
  per-entry preference store (`boost_prefs.py`) and the runback parameter
  entities stay in the adapter; their tests remain in this repo.

## Testing without a published wheel

Until the library minor is released, the integration imports
``smartbox.device`` from the sibling checkout:

- Check the library out next to this repo (``../smartbox``) or set
  ``SMARTBOX_LIB=/path/to/smartbox``.
- Run ``tools/dev_install_library.sh`` (it installs the sibling editable and
  verifies the seven device modules import).
- The tox environments install ``-e {toxinidir}/../smartbox`` instead of the
  PyPI pin; CI must check out both repositories at matching branches for the
  migration run. The release pin is a separate, later change.
- ``tests/conftest.py`` fails with an actionable message when the sibling
  modules are missing, instead of a raw import error.

## Release and pin

- The extraction adds public library modules; treat it as a library minor, not
  a patch, and raise the integration's pin range in the same release.
- Land slices 1–2 behind one library version and slices 3–4 behind the next if
  needed. The integration must never span a library version whose profile API
  is absent.
- The feature batch that precedes the extraction ships on its own release and
  remains the behavioral baseline.

## Risks and open questions

- **Capability data.** The app has per-serial/runtime capability flags; the
  first slice uses profile plus setup only. If a gate needs a capability flag,
  add it as an explicit optional input rather than reading live status.
- **Firmware branches.** Helki storage 1.8, lock 1.7, `extra_options` versions
  and controller-specific boost caps need explicit inputs and tests.
- **Unknown/new products.** The fallback profile must stay conservative and log
  once; a new product family should not silently inherit htr ranges.
- **Deferred features.** PMO reverse/generation, E2/water settings and SETBACK
  stay out of the extraction. Add profile fields only when those are decoded
  and tested.

## Definition of done

- No Home Assistant imports under `smartbox/device` or the moved modules.
- The integration platforms have no product-code conditionals; they consume a
  profile and map presentation.
- CLI and diagnostics use the same profile.
- Golden tests and the existing suites pass unchanged.
- One documented library release and integration pin update.
