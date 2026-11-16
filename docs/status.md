# Project status

> **Role.** The short, curated record of what is in flight: current work, post-release watch, and the open items that wait on hardware or on a decision. Long-lived facts live in the code and the other `docs/` files; completed work lives in git. Release history and CI status live in `CHANGELOG.md`, tags/releases and GitHub Actions; they do not belong here.

*Updated 2026-11-15. Keep this a 90-second read and prune entries as they close.*

## State of 3.1.2

- Library `3.0.2` and integration `3.1.2` are the current stable releases.
- The library split, native temperature properties, logger metadata, heater to box device linking, bundled program card, Sunday-first schedule fix and the `selected_temp` climate attribute are shipped, along with the `smartbox` 3.0.2 per-reseller API host work.
- Integration 3.1.2 pins `smartbox>=3.0.2`, adds `RESELLER_OVERRIDES`, and reports active overrides in diagnostics.
- Elnur account verification and the corresponding public-app docs/audit are the remaining external checks; the app-bundle evidence is recorded in `docs/api-ws-user.md` and `docs/api-contract.md`.

## Post-release watch

- Watch issue reports from fork users and the upstream thread.
- Stale-device cleanup (unchanged since 3.1.0-rc.3): the unpair, power-cut and re-pair checks are the only release-adjacent live checks not recorded here. Record the results when they are exercised.
- Token reconnect: the rc series showed the expected quiet cut every 4 hours. Revisit only if a cut lasts far longer than ~4 s or happens more often than every 4 h.
- Write confirmation: the 6 s countdown starts when the POST returns; the in-flight hold covers a token refresh or a slow request. Revisit if writes to a box still look slow.
- Public forum announcement remains on hold.

## Open items (hardware-gated)

`docs/api-contract.md` §7 is canonical for the wire questions and their statuses; the max-limit `"0.0"` and 4-key prog-temp probes live in `docs/api-contract.md` §5.2 and E28. The ones that need the maintainer's hardware — or a season:

- `window_open` retry, cold night only: open the window AND door BEFORE starting heat, setpoint 23 °C, outside below 5 °C; success is `window_open: true` in the frame plus the heater self-pausing (active false, duty 0) while the temperature keeps falling.
- RTC `n` month indexing — waits for December (the endpoint is app-orphaned; live only).
- `htr_mod` preset/comfort writes — the 2.41.0 app sends the mixed-category body (`on`+`mode`+`selected_temp`+`comfort_temp`+`eco_offset`), so the shape is app-evidenced; live acceptance still needs an htr_mod unit.
- PMO cadence, `pmo_system` frames and power-limit behavior — needs a pmo unit.
- Boost write path: the app's dispatch is implemented from the 2.41.0 bundle — `boost_config` 1/2 use status/setup, 3 (FULL) posts `/boost_mode {active, mode, temp?, time?}`. Live acceptance on a full-boost unit remains open.
- Full-boost UI: Runback/Advanced-boost presets are gated by `boost_modes_available`; the number entities remain defaults, the climate target is the active temperature, and `boost_mode`/ `boost_end_time`/`boost_remaining_minutes` are attributes. `smartbox.set_boost_mode` covers active duration and explicit modes.
- Live 2026-10-20 (htr fw 1.9): `max_stemp_limit "0.0"` write accepted and persisted (restored `"24.0"`), and the app-shaped 4-key `/status` prog-temp body accepted and echoed (originals restored). The sentinel's enforcement is confirmed: with the ceiling at `"24.0"`, `stemp 24.5` clamped to `"24.0"`; with `"0.0"`, the same setpoint was accepted and echoed. The dedicated `/prog_temps` endpoint is dormant: the vendor apps still use the 4-key `/status` body, so revisit it only if a future Android app release calls the endpoint.
- Programme-temperature writes now apply the app's per-profile clamp, ice/eco/comfort offset, and post-limit correction; the max-limit switch still deliberately restores the last remembered limit rather than the app's global maximum. Live 2026-10-20: lowering the ceiling from 24 to 19 pushed the corrected `18.0/18.5/19.0` status frame; both values were restored.
- Statistics completion is recorded when the rows are queued to the recorder, not when the recorder commits them. A recorder job dropped after queueing would leave that node marked imported; revisit only if recorder failures are observed.
- Tevolve tenant (`api-tevolve`, serial 15) is selectable. Its constants came from the vendor web bundle; a live login is still unverified.
- Quality-scale brands submission (logo assets and the `home-assistant/brands` entry) is postponed; the manifest keeps `"quality_scale": "custom"` until it lands.

Record results in `docs/api-contract.md` §7 as they are worked.

## Pending decisions

- Minimum Home Assistant: 2026.5.0 is the supported floor; `tox -e min` (CI job `minimum-home-assistant`) pins the harness that exercises it.
- Marketplace/HACS metadata: the 2026.11 release replaces HACS with Marketplace. The repository metadata and HACS Action are unchanged at the 2026.5 baseline; revisit when 2026.11 becomes the minimum.
