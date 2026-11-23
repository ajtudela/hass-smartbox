# Project status

> **Role.** The short, curated record of what is in flight: current work, post-release watch, and the open items that wait on hardware or on a decision. Long-lived facts live in the code and the other `docs/` files; completed work lives in git. Release history and CI status live in `CHANGELOG.md`, tags/releases and GitHub Actions; they do not belong here.

*Updated 2026-11-22. Keep this a 90-second read and prune entries as they close.*

## State of 3.2.1

- Library `3.2.1` and integration `3.2.1` are the current stable releases. The integration pins `smartbox>=3.2.1,<3.3`; the upper bound keeps a future minor from installing before the integration is re-pinned.
- Working tree after 3.2.1 (unreleased):
  - Per-controller setpoint and programme ranges from the decoded 2.54.2 table (`controller.py`), applied to climate, programme numbers, max-limit and boost numbers, and the programme sanitizer. The Tevolve WiFi/towel-rail rows, Ferroli 4D, factory-default 0D/18/24/43 and the unknown-acm HelkiStorage fallback now match the factory.
  - Controller-specific schedule catalogues: base three-profile, Helki/Haverland four-profile with OFF, and htr_mod off-first. The schedule sensor and program card accept a 24- or 48-slot day and render/edit the OFF slot; 48-slot storage is live-confirmed. THM code 28 gets the heater catalogue and surface; 04/09/0F keep the thermostat `{m,t}` shape.
  - Integer child-lock families expose an Off / Local lock / Remote lock select; boolean families keep the switch. Helki/Lhz heater nodes take the integer select only with `factory_options.super_lock_available`; Haverland 1D joins at fw 1.7, 47 stays integer, and 43/0D/18 stay boolean.
  - Boost availability follows each controller's app mode list; activation is family-aware (storage `/boost`, triac legacy setup+status even at FULL, other full-boost controllers `/boost_mode`); the full-boost presets and temperature editor follow the same endpoint decision; EASY and RUNBACK are separate gates; boost end time uses home-local `boost_end_min` and applies `boost_end_day`; duration is capped per controller (1440 base, 120 ATC, 300 Climastar); legacy boost temperatures are written back as Celsius for `extra_options.version >= 2` and formatted to one decimal.
  - Lhz duty presets `0/80/90` plus 70/Baby Care/LST on the LhzHeater chain; Helki duty and max-limit gates follow the app. Storage 2A filters AUTO/MANUAL through `extra_nrg_conf`, withholds the generic schedule/programme numbers, away/window/true-radiant settings and the charging-schedule service target, and keeps only the read-only target-charge sensor.
  - Tevolve EASY/RUNBACK switches (blind implementation from the decoded app; live hardware still needed). RUNBACK is withheld on full-boost WiFi/ATC until its `/boost_mode` parameters are modelled; SETBACK remains open.
  - Lost-at-discovery nodes keep their programme-temperature numbers; the gate uses the controller catalogue instead of transient status keys. Schedule options and runtime storage/boost/lock/limit decisions use a late version PID. Entity creation still uses discovery, so a PID first seen later needs a reload to add/remove entities.
  - Water storage in the combined extra-energy modes resolves ON to AUTO (AUTO/OFF); EXTRA_ENERGY_USAGE keeps HEAT/OFF. ATC 4C/4D use the WiFi integer lock. THM 28 with NO_PROBES hides temperature control, profile numbers, boost/EASY/RUNBACK, away/control-mode/window/true-radiant settings and the generic heater schedule, and rejects direct temperature writes. Fixed-time boost has no parameter numbers on Haverland 17/1D/47; config-less storage/LhzRelay/Tevolve triac keep them.
  - Elnur storage UI-mode choices follow the product (`14`: classic combined/accumulator; `20/25`: Ecombi/classic combined; water `2A`: no setting), and the setting is hidden while the unit is in ECOMBI. Haverland WiFi `1D/47` BOOST is present whenever `boost_config` is not the fixed-time value 1, and at config 3 its activation uses `/boost_mode` with the fixed-time mode.
  - Schedule writes reject profile indexes outside the controller catalogue; malformed temperature/duty/power/charge values read as unknown; legacy PIDs omit `extra_options` from setup POSTs; invited accounts work in the CLI.
  - Coverage: `test_product_code_parity.py` pins all 51 decoded product codes and their family/bounds (climate, programme, max-limit, boost)/profiles/boost-mode policy/duty presets/EASY/RUNBACK/lock/firmware gates; `test_entity_surface_matrix.py` pins the domains and unique keys per representative family and variant (capability flags, boost config 0, NO_PROBES, fw-1.6 lock, PIR, FULL temp/fixed/empty mode lists), snapshots options, bounds and current states for 15 representative families, and covers the box-level entities.
- Live-confirmed in this cycle: HJM `0132` schedule OFF index and 48-slot day, 0402/HJM controller ranges, child-lock `1`/`2`/`0` and the ignored boolean, setpoint while OFF, boost config 2 app path, and the lost-at-discovery/reload symptom on `Spálňa`.
- No soak was run for the working tree; the prepared soak bundle was scrapped because it required an unpublished library. Most library changes are additive; the new `set_extra_options` omission and the `SlotReader.version` protocol member are not purely backward-compatible, so the next library release is a minor and the integration pin must move with it.

## Post-release watch

- Watch issue reports from fork users and the upstream thread.
- After the next integration release: confirm the integer-lock select replaces the old switch without an orphan, the programme-temperature numbers survive a lost-at-discovery reload, and the new controller ranges match the app on one unit per family.
- After 3.2.1: the boxless reporter to confirm the power-limit entities and priority select stay gone, and watch reports on the Power Limit Factor select. A stored value outside its presets reads as unknown in HA; the app's heater card shows `"<value> %"`, while its settings select is blank.
- After 3.2.0: htr_mod reporters (#66) and the accumulator reporter (#54) to confirm what the maintainer's units cannot; acm users to confirm the integer `locked` surface.
- After 3.2.0: confirm HA restarts stop adding `/client/sessions` rows (same `sid` across a restart), and that reauth, reconfigure and entry removal clear the stored token bundle.
- After the next release: a Ducasa/Ducaheat user to confirm the provider row completes setup (static-bundle evidence only today; the portal decode is in `docs/api-contract.md` §1.1).
- Stale-device cleanup (unchanged since 3.1.0-rc.3): the unpair, power-cut and re-pair checks are the only release-adjacent live checks not recorded here. Record the results when they are exercised.
- Token reconnect: the rc series showed the expected quiet cut every 4 hours. Revisit only if a cut lasts far longer than ~4 s or happens more often than every 4 h.
- Write confirmation: a timed-out write now ends in one verification GET after the 6 s window plus a 1 s settle; a frame that overtakes the read wins. Revisit if ignored writes appear as `accepted but not applied` without a corresponding device change.
- Public forum announcement remains on hold.

## Open items (hardware-gated)

`docs/api-contract.md` §7 is canonical for the wire questions and their statuses. The items that need matching hardware:

- Storage: E2/extra-energy and water-storage settings; live confirmation of the classic-accumulator/boost/charging paths on real storage hardware. The app's MANUAL setpoint and mode writes use `/mode`, while the integration keeps `/status`; compare both on one unit before changing the path.
- Full-boost WiFi/ATC: BOOST availability at config 3 depends on the live `status.boost_modes_available` list in the app, while the integration's setup gate still shows BOOST whenever config is not unsupported. The parameter surfaces now follow the advertised modes (temp-and-time only), and runback carries its own temperature/duration; confirm the list's stability and the `/boost_mode` acceptance on a full-boost unit before deciding whether BOOST presence should follow it too.
- Tevolve thermostat 28 NO_PROBES: the app's ON/OFF timer programme is withheld from HA because the heater profile catalogue cannot represent it. Model it only if a user needs the timer from HA.
- Haverland WiFi `1D/47`: the corrected BOOST gate (absent only at config 1) and the `/boost_mode` fixed-time activation at config 3 are static-decoded; confirm on one unit.
- PMO: reverse/generation sign and sample keys, and `pmo_system` configuration.
- SETBACK activation path; EASY/RUNBACK live confirmation on a Tevolve unit.
- Full-boost live acceptance of `/boost_mode` (2.54.2 decode and config-2 live path are recorded).
- `window_open` retry, cold night only: open the window AND door BEFORE starting heat, setpoint 23 °C, outside below 5 °C; success is `window_open: true` in the frame plus the heater self-pausing (active false, duty 0) while the temperature keeps falling.
- RTC `n` month indexing — waits for December (the endpoint is app-orphaned; live only).
- Quality-scale brands submission (logo assets and the `home-assistant/brands` entry) is postponed; the manifest keeps `"quality_scale": "custom"` until it lands.

Record results in `docs/api-contract.md` §7 as they are worked.

## Pending decisions

- Device-semantics extraction: the design and migration plan are recorded in
  `docs/device-semantics.md`; the trigger is visible (duplicated PID/family
  facts and the CLI/diagnostics second consumer), but it starts only after the
  current feature batch is released and behavior is frozen.

- Minimum Home Assistant: 2026.5.0 is the supported floor; `tox -e min` (CI job `minimum-home-assistant`) pins the harness that exercises it.
- Marketplace/HACS metadata: the 2026.11 release replaces HACS with Marketplace. The repository metadata and HACS Action are unchanged at the 2026.5 baseline; revisit when 2026.11 becomes the minimum.
- Discoverability: the integration title stays "Smartbox", so Home Assistant's add-integration search does not match brand names. Ducasa/Ducaheat/Tevolve/Haverland discovery relies on the README and the GitHub description/topics; revisit if Marketplace search indexes more.
