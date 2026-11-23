<!-- record:begin -->
> **Maintained here.** This is the decoded web app's statics record this repository cites (`docs/webapi-spec.md`); corrections and new findings are recorded in this file.
<!-- record:end -->

> **Role.** The decoded web app's API surface (statics): endpoints, bodies, the schedule model, and the local box API. It records what that bundle does. Wire acceptance is settled in `api-contract.md`; the current apps' behavior (mobile 2.40.4 + web) is `api-ws-user.md`.

# Web API spec — as used by the vendor app (Helki platform)

**Provenance:** statically extracted from the official Android app `com.technotherm.lhzapp` v1.420.1 (APK sha256 `43df21cf…d4df1`), `assets/www/app.js` — the Cordova packaging of the same "Helki" Vue SPA that powers `app.smart-control.eu` (plus native BLE/ESP-Touch provisioning bridges). Cross-checked against live wire probes (see `api-contract.md`) ("Observed behaviour") and against the 2.7 `smartbox` library's `session.py` surface; the 3.0 `smartbox` library supersedes that implementation.

**Status markers:** ✔ = matches the 2.7 reference library's behaviour at the time of extraction; ⚠ = discrepancy or unverified-on-our-hardware item (each has an entry in §Discrepancies). These markers are historical: the 3.0 integration's implemented surface is `api-contract.md` §3.2/§9, and a ✔ here does not mean the 3.0 client calls that endpoint. Everything here is cloud-API over HTTPS — most current builds dial `https://users-api.helki.com`, Elnur dials `https://api-elnur.helki.com`, and the legacy EHC app stays on `api-ehc.helki.com` (`api-contract.md` §1.1 has the host history and the 2026-11-15 audit); the local-LAN API of the box is documented at the end (§Local API, informational).

---

## 1. Auth ✔

- `POST /client/token`, headers `Authorization: Basic <client_basic_auth>`, `Content-Type: application/x-www-form-urlencoded`, body form-encoded:
  - sign-in: `grant_type=password`, `username`, `password`
  - refresh: `grant_type=refresh_token`, `refresh_token`
- Response: `{access_token, refresh_token, expires_in}` → all later calls `Authorization: Bearer <access_token>`.
- Brand headers on every call: `x-serialid: <reseller serial id>`, and `x-referer: <scheme+host of frontend>` where set (matches `smartbox/reseller.py`). Library implementation ✔ identical.

## 2. Device / home level

| Method & path | Body / notes |
|---|---|
| `GET /api/v2/devs` | device list (incl. `invited_to`) ✔ |
| `GET /api/v2/grouped_devs` | homes/groups ✔ |
| `GET /api/v2/devs/<dev_id>/connected` → `{connected}` ✔ |
| `GET /api/v2/devs/<dev_id>/mgr/nodes` ✔ |
| `GET /api/v2/devs/<dev_id>/dev_data` | full device+nodes snapshot |
| `POST /api/v2/devs/<dev_id>/name` | `{name}` — ✔ implemented in 2.6.0 (unverified live) |
| `POST /api/v2/devs/<dev_id>/group` | group move — ✔ implemented in 2.6.0, body pass-through (unverified live) |
| `DELETE /api/v2/devs/<dev_id>` | remove device — ✔ implemented in 2.6.0 (destructive; unverified live) |
| `GET/POST /api/v2/devs/<dev_id>/mgr/away_status` | **✔ payload verified live (2026-09-27)**: app sends `{away: bool, enabled: bool}` (enabled defaults true in app code); server honours both `{away, enabled}` and `{away}` alone — library defaults `enabled: true` since 2.6.0 |
| `GET /api/v2/devs/<dev_id>/mgr/discovery` → `{discovery: 'on'|'off'}` ✔ (verified live) |
| `POST /api/v2/devs/<dev_id>/mgr/discovery` | `{discovery: 'on'|'off', type?, addr?, limit?}` — ✔ implemented in 2.6.0 (unverified live) |
| `GET /api/v2/devs/<dev_id>/mgr/rtc/time` | device clock — ✔ implemented in 2.6.0 (unverified live) |
| `GET/POST /api/v2/devs/<dev_id>/htr_system/power_limit` | `{power_limit: "<int as string>"}` ✔ (library sends `str(int)` ✔) |
| `POST /api/v2/devs/<dev_id>/pmo_system` | PMO-only, out of scope |
| `GET /api/v2/groups/<home_id>/geo_data`, `.../name` `{name}`, `.../extra_data` |
| `GET /api/v2/groups/<home_id>/guest_users`, invites (`/users/<uid>/invite` POST/DELETE, `/invite_confirmation`) |
| `GET /api/notifications/v1/<group_id>/presence/config` (+`/test`) | "quiet home" notifications — ✔ implemented in 2.6.0: config GET/POST + test POST (paths confirmed in the app bundle; unverified live) |
| `GET /api/v2/encrypted_wifi_credentials?ssid=&pass=` → `{encrypted_pass}` | provisioning only; AES-256-CBC PBE under hardcoded key `5786647123672357` (salt=IV=ASCII bytes, 10 000 iters, `PBEWITHSHA256AND256BITAES-CBC-BC`) — see research notes |
| `GET /api/location/v1/coordinates?country=&state=&city=&zip=` | geocoding helper |

## 3. Node level (`<type>` ∈ htr, htr_mod, thm, acm, pmo; `<addr>` int)

| Method & path | Body / notes |
|---|---|
| `GET /api/v2/devs/<dev_id>/<type>/<addr>` | full node data ✔ (library uses per-section GETs instead — equivalent) |
| `GET .../status` ✔ | |
| `POST .../status` | **write semantics — see §4** ✔ shape-compatible |
| `GET .../setup` ✔ | |
| `POST .../setup` | **full merged setup object** — library already merges before POST ✔ (session.py `set_node_setup`) |
| `GET .../version` ✔ | |
| `GET .../prog` | **schedule read** — returns `{prog: {…}}`; per the app, `prog.prog` = 7 day-arrays of ints (profile indices) — ✔ captured live 2026-09-27: string day keys `"0".."6"`, 24 hourly slots at prog_resolution 0; HJM 2026-11-22 stored 48 half-hourly slots with the same `prog_resolution: 0` |
| `POST .../prog` | **schedule write** — body `{prog: …}` — ✔ implemented in 2.6.0 (`set_node_prog`); day-keyed object shape; the JSON-array shape is rejected with 400 (observed live) |
| `POST .../prog_temps` | dedicated prog-temperature endpoint (in `APIManager`; app's manager actually uses `POST .../status` with `{ice_temp, eco_temp, comf_temp, units}` — see §4; no caller found for plain htr) |
| `POST .../mode` | dedicated mode endpoint (in `APIManager`; node managers use `POST .../status {mode…}` — see §4) |
| `POST .../lock` | dedicated lock endpoint (managers use `POST .../status {locked}` ✔) |
| `POST .../boost` | boost config `{active, temperature, units, time}` — boost-capable products only; **our 5× htr are factory boost-disabled (N/A)** |
| `POST .../power_limit` | `{power_limit: "<int>"}` (node-level, PMO) |
| `POST .../name` `{name}` / `POST .../select` `{select}` / `GET .../power` / `DELETE .../<type>/<addr>` (`{purge}`) | fleet-management ops — ✔ implemented in 2.6.0 (unverified live) |
| `GET .../samples?start=<unix>&end=<unix>` ✔ | app additionally uses classified/E2/hi-res variants and generation/surplus sample kinds (PMO/PV context) |

### 3.1 Charge level field by model code

The web app's `getChargeLevel` derives the model code from the node version's `pid` and picks the status field by it:

```js
switch (((data.version || {}).pid || "").toUpperCase().slice(2, 4)) {
  case "1C":
    return data.status.current_charge_per;
  default:
    return data.status.charge_level;
}
```

The slice is positional: characters 3–4 (indices 2–3) of the PID, so the four-character PID `081c` yields `1C`. Shorter PIDs yield the default branch. Provenance: decoded web-app helper; the snippet and the storage-heater case (`081c`) were reported against the upstream integration in PR #45 (2025-12-07). The version endpoint carrying `pid` is recorded in `api-contract.md` §3.2 E24.

### 3.2 Per-tenant capabilities (`client-data/<serial>/capabilities.json`)

Each brand's frontend serves a static capability file at
`https://<frontend-host>/client-data/<serial>/capabilities.json`
(fetched 2026-11-21; the same files are served under the Helki, Haverland,
Tevolve/Ducaheat, SmartControl, EHC, Electrorad, Climastar, Valderoma,
iHeat, HJM, Perry and Technotherm hosts). It is an **app surface**, not the
wire contract: it tells the add-device flow which product categories the
brand offers and which optional app features it enables.

`supportedNodeTypes` uses the app's **product/UI enum**, not the API node
type enum. The two are distinct:

- **Wire node types** (API paths and `nodes[].type`): `htr`, `htr_mod`,
  `thm`, `acm`, `pmo`. The API enum `EAPINodeTypeValue` is `htr, htr_mod, thm, acm, pmo`
  `[app mobile 2.54.2 new_vendors.decoded.js@5758915]`; the product factory
  throws on any other value `[app mobile 2.54.2 new_vendors.decoded.js@4845795]`.
  The status paths cover htr/htr_mod/acm/thm; `pmo` has no status path.
- **Product/UI categories** (this file): `heater`, `modified_heater`,
  `wifi_heater`, `four_button_wifi_heater`, `two_button_wifi_heater`,
  `towel_rail_heater`, `towel_rail_wifi_heater`, `storage_heater`,
  `thermostat`, `thermostatic_valve`, `floor_thermostat`, `power_meter`.
  The full enum also carries `solar_storage_heater`, `water_storage_heater`
  and a `timer` subtype (`[app web elnur@5809746]`).

Do not map a product category to a new wire type. A towel rail, for example,
is a product category that is expected to report `htr` (or `htr_mod`) on the
wire; a floor thermostat is a `thm` with floor-probe settings.

The node device page gets a product label from `version.pid[2:4]` using the
vendor's `EProductCodeValue` categories: triac heater, relay heater, heater
module, storage heater, water storage heater, thermostat, thermostatic valve,
WiFi heater or power monitor `[app web elnur@6159159]`. A towel rail is an
add-device/UI category (the per-tenant `supportedNodeTypes`/`supportedDevices`
model picker), not a PID code: a towel rail reports its controller category,
so it cannot be distinguished from an ordinary heater by PID alone. The
device model text keeps the wire address for diagnostics, and the firmware
version is shown as the device software version.

Fetched matrix (serial, brand, categories):

| Serial | Brand | Categories |
|---|---|---|
| 1 | Helki | thermostat, thermostatic_valve, heater, towel_rail_heater, wifi_heater, storage_heater, power_meter |
| 2 | Lucht LHZ | heater, towel_rail_heater, floor_thermostat, storage_heater, four_button_wifi_heater, two_button_wifi_heater, towel_rail_wifi_heater, power_meter |
| 3 | EHC | heater, towel_rail_heater, storage_heater, power_meter |
| 5 | Climastar | heater, wifi_heater |
| 6 | Perry | power_meter, thermostat |
| 8 | Valderoma | heater, wifi_heater |
| 9 | iHeat Control | heater, storage_heater, wifi_heater, power_meter |
| 10 | HJM | heater, wifi_heater, power_meter |
| 12 | Electrorad | heater, storage_heater, power_meter |
| 14 | Haverland | heater, modified_heater, wifi_heater, storage_heater |
| 15 | Tevolve / Ducaheat | thermostat, thermostatic_valve, heater, wifi_heater, storage_heater, power_meter |
| 16 | Technotherm | heater, towel_rail_heater, floor_thermostat, storage_heater, four_button_wifi_heater, two_button_wifi_heater, towel_rail_wifi_heater, power_meter |
| 17 | SmartControl | heater, towel_rail_heater, floor_thermostat, storage_heater, four_button_wifi_heater, two_button_wifi_heater, towel_rail_wifi_heater, power_meter |

Elnur (serial 7) served the SPA shell rather than JSON for `capabilities.json`
at the fetch time; its bundle still carries the product enum, so it is omitted
from the JSON matrix above.

Feature flags in the same file (notable per-tenant differences):

- `boostAvailable`: true everywhere fetched except Haverland (14).
- `htrPowerLimitAvailable`: true only for Haverland (14).
- `sampleDownloadEnabled`: true for Lucht LHZ (2), Haverland (14), Technotherm (16) and SmartControl (17).
- `energyInsightsEnabled`: true only for Helki (1).
- `wifiSmartboxAvailable`: false for Lucht LHZ (2), EHC (3), HJM (10), Tevolve/Ducaheat (15) and Technotherm (16).
- `powerScheduleAvailable` and `inviteEnabled`: true for every fetched tenant.
- Also present: `supportedLanguages`, `supportedTimezones`, `provisioningMethods`, `hideApiHost`, `changeNodeDisplayAvailable`, `appTourEnabled`, `pushNotificationsEnabled`, `darkModeEnabled`, `geolocationAvailable`.

These flags are UI gates. The integration continues to gate entities on
wire state (status/setup fields), which is the authoritative source; the
flags explain why the vendor app hides a feature on a given brand and are
recorded so a missing entity is not mistaken for a wire gap.

### 3.3 Thermostat (`thm`) shape

The controller-backed status/setup shape, mode list, setpoint body and
schedule shape are recorded in `api-contract.md` §3.5. This file keeps only
the web-app static: the temperature-control service is constructed with
`minTempCelsius: 7` and clamps every node minimum with
`Math.max(minCTemp, 7)` `[app web ducaheat@659179]` `[app web ducaheat@4934361]`;
with the `ThermostatController`'s 35 °C global maximum, the thermostat range
is **7–35 °C**. The demo payload is a synthetic fixture; its `batt_level` and
`error_code` fields are not app-backed, and `presence` is a generic
panel-touch artifact (`api-contract.md` §6.1), not a thermostat field. A live
`thm` read remains `**[unverified]**`.

### 3.4 Control mode (`setup.control_mode`)

The wire value is the position in the controller's wire-order array,
`getIndexSortedUIHeatingControlModes()`; `getUIHeatingControlModes()` is the
separate display/accepted list. The app converts a selected UI value to the
wire index with `findIndex` in the wire array. The wire arrays, the live 0402
cycle, the product-code family routing and the thermostat withholding are
recorded in `api-contract.md` §1.9.

The app-side UI lists are actuation-dependent: triac `pid, hyst_02, hyst_03,
hyst_05, hyst_07`; relay `pid_15, pid_30, hyst_02, hyst_03, hyst_05,
hyst_07`; a relay with `extra_options.sensor_mode == 2`
(`ESensorModeValue.FLOOR_PROBE`) offers only the four hysteresis entries
`[app mobile 2.54.2 new_vendors.decoded.js@3632627]`
`[app mobile 2.54.2 new_vendors.decoded.js@5767681]`. The storage family
inherits the relay list; `off` is not offered. Labels are the app's i18n,
with the Fahrenheit map in `new_app.decoded.js`
`[app mobile 2.54.2 new_app.decoded.js@1038429]`.

The app hides the setting on Elnur water storage (code `2A`, omitted from
`getSettings()`) `[app mobile 2.54.2 new_vendors.decoded.js@5775448]` and on
Elnur storage (code `14`, inherited by `20`/`25`) when
`setup.factory_options.ui_mode` is `CLASSIC_COMBINED` (1) or
`CLASSIC_ACCUMULATOR` (2) `[app mobile 2.54.2 new_vendors.decoded.js@3682752]`.
The integration mirrors both gates. It withholds the `04`/`09`/`0F`
thermostat family for the `pid_60` mismatch and other THM codes by
integration choice, except explicit htr-family products such as code `28`
(the Tevolve floor thermostat). An index outside the product's UI map is not
offered; the select keeps the product's choices and renders the stored value
as unknown.

## 4. Status write semantics (as exercised by the app)

(The current apps' write surface — endpoints and bodies — is `api-ws-user.md` §2.3; this section keeps the decoded web app's view.)

App-side node managers build these bodies (all to `POST .../status`):

1. Setpoint: `{stemp: "20.0", units: "C"}` — ✔ identical to integration.
2. Setpoint in auto: same body; app *optionally* adds `"mode": "modified_auto"` (auto→temp-bump mapping) gated on capability `INDEPENDENT_TEMP_AND_MODE_ON_UPDATE`; a live probe shows fw 1.9 does **not** flip mode on the wire when only `{stemp, units}` is sent.
3. Mode change: `{mode: "manual"|"auto"|"off"|…}`; when the capability above is absent, MANUAL carries the temp too: `{mode, stemp, units}` (3-key!).
4. Profile temps: `{ice_temp, eco_temp, comf_temp, units}` (4-key, all temperature-category).
5. Lock: `{locked: bool}` ✔ single-key.

**Refined one-key rule (supersedes the absolute pin in the pre-contract notes):** multi-key status POSTs are accepted **within a category** (temp-keys+units, mode+stemp+units) — the app does this in production. The observed silent drop happened on a **4-key mixed-category** body (`{locked, stemp, units, mode}`). Integration's sequential single-key posts remain the safe pattern; do not change without a live A/B probe.

Mode-change sequencing: before switching away from `boost`/`runback`, the app first POSTs the deactivate for the previous mode, then activates the new one (`deactivatePreviousMode()` → `activateMode()`). Not applicable to plain htr without those modes active.

`modified_auto`: exists as an app-level mode value; on fw 1.9 the device does not reflect it on the wire when not explicitly sent. Sending it explicitly (the §4 item 2 body) DOES engage it (live-probed 2026-09-28): the mode flips, the heater follows, and the override persists until reverted with `{"mode": "auto"}` (no self-revert within a 60 s window; the app's UX reverts at the next differing programme temperature). CONFIRMED by direct capture of the web app on this hardware (2026-09-28): bumping the temp in auto posts exactly `{"stemp":"20.5","units":"C","mode":"modified_auto"}` — the 3-key body is the app's production behavior on these units. The HA integration now sends this body for setpoints in auto, gated on `modified_auto_span` in setup (the app's INDEPENDENT_TEMP_AND_MODE_ON_UPDATE capability is not wire-visible). Also: off-grid setpoints silently quantize to the 0.5 °C grid — round before POSTing; the app's UI step is 0.5 °C / 1 °F (user-verified against the app, 2026-09-28).


## 5. Schedule model (the scheduling feature's core)

- `prog.prog` = **7 day-arrays** (Sun..Sat; day order confirmed in `api-contract.md` §5.4), each holding **N profile-index ints**, one per slot: 24 slots @ 60 min (hourly, `prog_resolution: 0`), or 48 @ 30 min when capability `MULTIPLE_SCHEDULE_RESOLUTIONS` is active (`prog_resolution: 1`).
- Profile index order (plain htr): **0=ICE, 1=ECO, 2=COMFORT** (`getIndexSortedScheduleProfiles()`); UI profile id → API index via `getApiScheduleProfile()` = index in that list (unknown → 0).
- UI schedule = per day, slots `{start, end, id}` in **minutes from midnight**, `id` = profile. Conversion to raw: expand each slot into `ceil((end-start)/slotMinutes)` repeated indices; pad the day to 24 h. Reconstruction (raw → UI): collapse runs of equal indices; last slot ends
  1440.
- Resolution change: `setScheduleMethod(method)` → **setup POST `{prog_resol: m, prog_resolution: m}`** (2-key setup write; the app merges into the full setup first — merge rules as §3/setup).
- Temperature edit per profile: app clamps against neighbour profiles (`scheduleTemperatureOffset`) then sends the 4-key status post (§4 item 4). Simpler equivalent: single-key posts per temp key + units — **verify one variant live before relying on it**.
- Websocket: schedule changes arrive as `/htr/<addr>/prog` update frames (observed live) — the integration's websocket handler must store `prog` frames like `status` frames (verify current handling during implementation).

## 6. Discrepancies & open items (app vs library vs integration)

History: the adjudications below were made against the pre-rewrite stack (the retired library plus the integration); the CLOSED rows stay as the record, and the still-open rows are marked.

| # | Item | Severity | Action |
|---|---|---|---|
| D1 | Away write: app `{away, enabled}`; integration `{away}` only | **CLOSED (2026-09-27, live)**: server honours both shapes — `enabled` tolerated, not required; library defaults it like the app since 2.6.0 |
| D2 | `POST/GET .../prog` absent from library | **CLOSED (2.6.0)**: `get_node_prog`/`set_node_prog` in `session.py` (+ sync `Session`, `NodeProg`, websocket helper, CLI) |
| D3 | Multi-key status semantics: pin refined (category-scoped, not absolute) | **CLOSED (2026-09-27)**: the pre-contract notes were rewritten to the category-scoped rule |
| D4 | Dedicated `/mode`, `/lock`, `/boost`, `/prog_temps` endpoints unused by library | **CLOSED (2.6.0)**: implemented to spec; boost marked **unverified-on-hardware** (factory boost-disabled units) |
| D5 | `prog_resolution` / `MULTIPLE_SCHEDULE_RESOLUTIONS` on fw-1.9 htr unverified | **CLOSED (2026-11-22, live)**: HJM 0132 fw-1.8 reported `factory_options.prog_resolution: 0` and stored a 48-slot half-hourly programme; `prog_resolution` is not a hard capability gate. |
| D6 | No live `GET .../prog` sample exists | **CLOSED (2026-09-27)**: captured from heater 5 + all nodes; the shape is recorded in `api-contract.md` §5.3 (the repo ships setup/status live fixtures, not the prog week) |
| D7 | Local-LAN API (HelkiDigest) — no vendor client exists; dead end by design | CLOSED | summarized in the appendix below |

## Appendix — Local API of the box (informational, CLOSED)

`http://<box>:80`: `GET /info` open (devid, timezone); everything else 401 + `WWW-Authenticate: HelkiDigest <16 printable-ASCII bytes, hex-encoded>`; response token = 32 chars, length-validated only; nonce reusable; wrong-but-well-formed → 401 + fresh nonce; malformed → 400. Routes exist behind auth (`/mgr/nodes`, `/dev_data`, `/htr/N/*`, `/mgr/cloud`, `/mgr/status`, `/mgr/away_status`, `/mgr/rtc/time`, `/mgr/discovery`, …) — mirroring the cloud API. `/mgr/wifi/scan|network|start` exist only in SoftAP provisioning mode (open, no auth; body `{essid, pass|encrypted_pass}`). No vendor client implements the digest (web SPA, APK dex and JS all lack it) — the box is cloud-tethered; local control would require impersonating the provider endpoint. Investigation closed 2026-09-26.
