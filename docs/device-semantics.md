# Device semantics extraction — design record

> **Status: design, not scheduled.** The extraction starts only after the
> current feature batch is released and its behavior is frozen. Until then this
> document is the reference for the target boundary and the migration order.

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

1. A second consumer needs the device model. The `smartbox` CLI and
   diagnostics currently see the wire and the store but not the controller
   profile.
2. Duplicated device facts cause a bug. The same PID→family facts currently
   live in several adapter modules; a missing explicit family entry or a
   charge-level dispatch mistake has already produced user-visible bugs.
3. A new product family needs the same logic in two or more places.

The trigger is visible now. The release gate is not: a behavior-preserving
extraction is much easier to review and to trust once the current feature batch
is released and its entity behavior is fixed.

## Current duplication (pre-extraction inventory)

This table describes the adapter as it is before the extraction. Delete the
relevant row as each slice lands.

| Fact | Current homes |
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

`storage.py` currently imports Home Assistant's `HVACMode`. That is a boundary
violation to remove as part of the storage slice, not something to move into the
library as-is.

## Target architecture

Library:

```
smartbox/
  device/
    codes.py         product code -> label, manufacturer, family defaults
    profiles.py      ControllerProfile, resolve_profile(...)
    temperatures.py  climate/programme/max-limit bounds
    control_modes.py catalogue + wire index mapping
    storage.py       storage mode resolution and write intent (neutral)
    thermostat.py    thermostat capabilities and schedule shape
    settings.py      per-profile setting availability and option sets
```

Integration:

```
adapter/entity platforms
  -> profile = resolve_profile(pid, node_type, fw_version, setup)
  -> HA presentation: HVACMode, units, labels, entity gates, services
```

Boundary rules:

- `smartbox.device` imports no Home Assistant modules.
- No vendor-controller class hierarchy is reproduced. Profiles plus a small
  number of strategies encode only the behaviour the integration and CLI use.
- Wire write bodies stay in `smartbox.protocol.writes`; device semantics decide
  which operation and which fields, not how HTTP is posted.
- HA-specific values (`HVACMode.HEAT`, `UnitOfTemperature`, translated labels,
  entity categories) stay in the adapter.

## ControllerProfile

One frozen dataclass per resolved controller. Proposed fields, grouped:

- Identity: `code`, `label`, `family`, `actuation`, `source` (explicit PID,
  node-type default, unknown).
- Climate: `min_c`, `max_c`, `ice_min_c`, `ice_max_c`, `profile_offset_c`,
  `programme_profiles` (ordered neutral names), htr_mod policy fields.
- Limits: `max_limit_min_c`, `max_limit_max_c`, `boost_max_c`,
  `boost_max_minutes`, programme defaults.
- Modes: `control_mode_catalogue`, `storage_ui_modes`, `operational_mode`,
  `supports_easy`, `supports_runback`, `supports_setback`.
- Boost: `boost_kind` (`status`, `legacy_endpoint`, `boost_mode`, `none`),
  `boost_supported`, `boost_attribute_kind` (`device` for legacy
  `extra_options`, `ha_prefs` for FULL `/boost_mode`, `none`), and the
  runback-endpoint flag. The attribute values themselves live on the device
  for legacy controllers and in the entry's `.storage` boost-preference record
  for FULL ones.
- Lock: `lock_kind` (`bool`, `int_status`, `int_endpoint`).
- Thermostat: `load_types`, `schedule_available`, `anti_grippage_available`,
  `manual_mode_extra` (the documented integration extension).
- Options: `duty_presets` (Off plus fixed/factory values), `away_offset`,
  `max_limit`, `prog_temps`.

Names are neutral strings or enums, for example `"acm_helki"`,
`"acm_elnur"`, `"htr_mod"`, `"thm"`; the adapter maps them to HA types.

### Resolution inputs

`resolve_profile(pid, node_type, fw_version, setup)`:

- `pid` is the live/discovery `version.pid`; the code is `pid[2:4].upper()`.
- `node_type` is the wire type.
- `fw_version` gates Helki storage profiles, lock payloads and similar.
- `setup` supplies `factory_options.ui_mode`, `operational_mode`,
  `boost_config`, `min_stemp`/`max_stemp`, `extra_options` and model fields.
- Live `status` is **not** an input for entity presence. A lost node at
  discovery must still resolve the same profile; reading transient status keys
  to build entities has already produced a permanent-missing-entity bug. The
  one deliberate exception is the boost/runback attribute entities: a FULL
  node's `status.boost_modes_available` decides whether a parameter slider has
  anything to configure, so those attributes can be absent until the mode list
  is observed (a reload re-evaluates them).

Fallbacks:

- Unlisted htr/htr_mod PID → ElectricHeater defaults.
- Unlisted acm PID → HelkiStorage defaults (off-first wire, relay actuation).
- Unlisted thm PID → withheld/unknown profile; no control-mode select.
- Missing PID → conservative unknown profile: no mode catalogue, HA default
  climate bounds, no storage/thermostat assumptions.

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
- Write confirmation and optimistic merges (library engine).
- Card assets and frontend behaviour.

## Migration progress

- **Slice 1 (codes, profiles, limits) landed.** `smartbox.device.profile`
  owns the neutral `ControllerLimits`, the code/family sets, bounds helpers
  and `resolve_profile`. The integration's `controller.py` re-exports them and
  keeps the not-yet-moved gates. The library golden test pins all 51 codes;
  the temporary `tests/test_profile_equivalence.py` compares the old helpers
  to the library resolver across 51 codes × 4 firmware branches × 2 setup
  shapes. Behaviour is unchanged.
- **Slice 2 (control modes) landed.** `smartbox.device.control_modes` owns
  the decoded family/actuation maps, UI/wire arrays, labels and availability.
  The integration's `control_mode.py` re-exports it; the entity keeps the HA
  select shape.
- **Slice 3 complete.** `smartbox.device.modes` owns the lock/boost policy and
  `smartbox.device.storage` owns the storage semantics with a neutral
  `StorageIntent`; the integration's `storage.py` maps intents to `HVACMode`.
  No Home Assistant type remains in the moved storage logic.
- **Slice 4 complete.** `smartbox.device.settings` owns the entity-setting
  gates (window mode, true radiant, away offset, max-temperature limit, node
  power limit, duty presets, priority, programme temperatures, boost
  availability). The integration's `identity.py` keeps thin wrappers over the
  frozen node object.
- **Slice 5 is in progress.** `smartbox profile` is the library's second
  consumer, diagnostics carry the resolved profile per node, and the migration
  branch installs the sibling checkout via `tools/dev_install_library.sh` and
  the tox `-e ../smartbox` dependency. The version bump, pin and release
  remain deferred. The adapter keeps boost activation, preferences and the
  FULL attribute visibility throughout.
- The migration branch imports `smartbox.device`, which is not in the
  published 3.2.1 wheel. Develop against the sibling editable checkout and do
  not merge or release until a library minor with these modules is published
  and the integration pin is raised (slice 5).

## Migration slices

Each slice is behavior-preserving, lands with the adapter copy deleted in the
same change, and has a golden comparison.

1. **Codes, profiles and limits.** Add `device/codes.py`,
   `device/profiles.py`, `device/temperatures.py`; move the controller table
   and bounds. The integration imports the profile; the old adapter copy is
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

## Golden tests

- One table-driven library test, independent of Home Assistant, covering the
  current product codes and setup branches. `tests/test_product_code_parity.py`
  is the seed: 51 codes, 20 behaviour rows, limits, programmes, boosts, lock
  (including `super_lock_available`), duty presets, PIR's 3.5 °C gap and
  read-only ICE slot, and the firmware gates. Port it to the library profile
  and delete the integration's controller imports from that test.
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
  verifies the five device modules import).
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

- **Neutral mode types.** `storage.py`'s `HVACMode` dependency must become a
  neutral intent enum. The adapter mapping needs tests for every mode.
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
- **Label ownership.** Product labels are device metadata; translated
  descriptions belong to the adapter. Decide whether the library carries the
  stable English label or only the code.

## Definition of done

- No Home Assistant imports under `smartbox/device` or the moved modules.
- The integration platforms have no product-code conditionals; they consume a
  profile and map presentation.
- CLI and diagnostics use the same profile.
- Golden tests and the existing suites pass unchanged.
- One documented library release and integration pin update.
