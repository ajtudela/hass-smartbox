# `ws_user` API contract (`api-ws-user.md`)

The API surface the CURRENT vendor apps use, in one document: the per-user `ws_user` websocket transport plus the REST endpoints the web and mobile clients call. `socket_io` is out of scope; the integration does not support it.

Purpose: the alignment reference for the HA integration, which speaks `ws_user` only. Review the code against §1–§3 and work through the checklist in §4. Written to be usable standalone next to the integration's own copy of `api-contract.md`.

> **Role.** The CURRENT vendor apps' behavior, app statics and the parity checklist for re-aligning this integration when the vendor ships a new app. Wire acceptance is settled in `api-contract.md`; the endpoint index is `api-endpoints.md`. Refresh this document per vendor app release; the checklist's status lines record the last pass.

Provenance tags (same vocabulary as `api-contract.md`; two file tags are new here):

| Tag | Meaning |
|---|---|
| `[live YYYY-MM-DD]` | Observed on the wire against real hardware on that date (probes and soaks recorded in `api-contract.md` §8). |
| `[app mobile 2.40.4 app.js@<offset>]` / `[app mobile 2.40.4 vendors.js@<offset>]` | Byte-offset excerpt from the decoded current mobile build (`com.technotherm.lhzapp` 2.40.4, serialId 17): the app bundle holds the session/websocket layer, the vendors bundle the `APIManager` REST layer. Static decode evidence, not wire-verified. |
| `[app web@<offset>]` | Byte-offset excerpt from the decoded web bundle (`app.11bc1c94.js`, the smartcontrol.lucht-lhz.de web app, decoded 2026-10-01). Static decode evidence. |
| `[app mobile app.js@<offset>]` | The 1.417.1 mobile build — cited only where a contrast matters. |
| `[code file:line]` | As implemented in the 2.7 `smartbox` reference library; the 3.0 `smartbox.protocol` library supersedes it. |
| `[file:line]` | As implemented in this repository (`custom_components/smartbox/`). |
| `[app mobile 2.41.0 vendors.js@<offset>]` | Byte-offset excerpt from the 2.41.0 mobile build's API-manager bundle (`assets/www/js/chunk-vendors.3ed6a158.js`). Static decode evidence, not wire-verified. |
| `**[unverified]**` | Assumed or never probed. |

Companion docs: `api-contract.md` (full contract and open questions), `api-endpoints.md` (endpoint index with last-confirmed builds and consumers).

---

## 1. Transport — `ws_user`

### 1.1 URL and connect

- ONE socket serves the whole ACCOUNT — every home and every device of the user `[app mobile 2.40.4 app.js@1452270]` `[app web@1125284]` `[code ws_user.py:1-19]`.
- URL shape: `wss://{host}:443/api/v2/ws_user?token=<urlencoded access token>&user_id=<uid>`
  - `host` is the app's build-time API endpoint. The integration uses the reseller row's `api_host`: `users-api.helki.com` for most keys, `api-elnur.helki.com` for Elnur. The 2026-11-25 re-audit found thirteen SPA bundles on the shared host and Elnur on its own; ws_user is served on both `[app mobile 2.40.4 vendors.js@1561267]` `[live 2026-10-02, api-lhz]`.
  - `user_id` is the `userId` claim of the access token — both apps read it from the JWT `[app mobile 2.40.4 app.js@1452270]` `[code ws_user.py:109-133]`.
  - The token is fetched FRESH for every connect (the apps call their bearer-token getter inside the URL builder) `[app mobile 2.40.4 app.js@1452270]`.
- Public web-app audit (2026-11-25): fourteen public SPA builds ship the `ws_user` client. Thirteen are built against `https://users-api.helki.com`: Helki, Lucht LHZ, Climastar, Perry, Valderoma, iHeat Control, HJM, Electrorad, Haverland, Tevolve, Ducaheat, Technotherm and SmartControl. Elnur is the exception: its current bundle sets `VUE_APP_API_ENDPOINT=https://api-elnur.helki.com` and contains no `users-api` string. The EHC public app remains the 19.3 MB legacy `app.js` (`HelkiFront` 1.416.4-1) with `socket.io` and no `ws_user`/`users-api` on `api-ehc.helki.com`. Electrorad migrated to the SPA + `users-api` + `ws_user` on 2026-11-06; its root `/app.js` is still served but unused (2025-11-11). An unauthenticated `GET`/upgrade to `/api/v2/ws_user` is not a capability test: `api-elnur` returned 401, while `users-api` and `api-ehc` returned 404; the setup probe with a real account remains authoritative.
- Handshake semantics `[live 2026-10-02]`:
  - expired / invalid token → HTTP 401; a fresh token per connect is load-bearing;
  - 404/410 → the host does not serve ws_user (transport policy: deterministic rejections only, `api-contract.md` §4.2);
  - an open socket is cut by the server exactly at the token's `exp` (≈4 h) with close code 1006 — a fresh-token reconnect is MANDATORY at least every token lifetime.
- After open, the app arms an `all_data` watchdog: no snapshot within base-delay + 5 s (1 s + 5 s on the first attempt) → reconnect `[app mobile 2.40.4 app.js@1454060]`.

### 1.2 Client → server

- Exactly ONE message type: `{"event":"all_data"}`, sent on open `[app mobile 2.40.4 app.js@1454340]` `[app web@1126978]`.
- Re-sent on every server `should_sync`, debounced 1000 ms (`SHOULD_SYNC_DEBOUNCE_MS = 0x3e8`) `[app mobile 2.40.4 app.js@1465516, @1465775]` `[app web@1127910, @1120293]`.
- Nothing else is sent. There is NO app-level ping or heartbeat in this protocol (neither current app contains one) — see §1.6.

### 1.3 Server → client

Three events, JSON text frames:

| Event | Shape | Client behavior |
|---|---|---|
| `all_data` | `{"event":"all_data","data":[homes]}` — full snapshot | replaces the home/device list; one `devData` per device `[app mobile 2.40.4 app.js@1454577]` `[app web@1126978]` |
| `update` | `{"event":"update","devid":"<dev>","data":{"path":"/...","body":...}}` | routed per §1.4; the path/body pair lives UNDER `data` `[app mobile 2.40.4 app.js@1455145]` |
| `should_sync` | `{"event":"should_sync"}` | schedules the debounced `all_data` re-request `[app mobile 2.40.4 app.js@1455273]` |

- Unknown events are ignored; a malformed frame closes the socket and triggers the reconnect path (the app wraps the whole message handler) `[app mobile 2.40.4 app.js@1454408]`.
- Update frames are applied only to devices whose `devData` already exists (the store drops frames for unknown dev ids silently) `[app mobile 2.40.4 app.js@1383232]`.

### 1.4 Update-frame routing (the path table)

Both current clients route the SAME set; a frame is processed only when `path` and `body` are both truthy; unlisted paths are ignored `[app mobile 2.40.4 app.js@1459389]` `[app web@1131954]`.

| `data.path` | `data.body` | Applies as (both clients) |
|---|---|---|
| `/connected` | object carrying `connected: bool` | merged into `devData` (top-level merge) `[app mobile 2.40.4 app.js@1460260]`; consumers read `body.connected` `[code update_manager.py:337-350]` |
| `/geo_data` | geo-data object | merged into `devData` (note the snapshot's duplicate `geoData` spelling) |
| `/mgr/discovery` | discovery object | `devData.discovery` (bare key) |
| `/mgr/away_status` | away-status object (consumed as-is; the GET serves `{away, enabled, forced}`) | `devData.away_status = body` |
| `/mgr/nodes` | `{"nodes":[...]}` | node list merged PER `addr` over the existing node data (`installed`/`lost`/`name`/slots patch) `[app mobile 2.40.4 app.js@1383564]` `[app web@1136881]` |
| `/htr_system/power_limit` | digit STRING, or an object with `power_limit` | `htr_system.power_limit.power_limit = Number(body-or-body.power_limit)` — both clients coerce object-or-scalar `[app mobile 2.40.4 app.js@1463639]` `[app web@1136204]` |
| `/pmo_system` | pmo-system object | `devData.pmo_system = body` |
| `/pmo/{addr}/power_limit` | body | node `power_limit` slot = body |
| `/pmo/{addr}/setup` | setup object | node `setup` slot = body |
| `/{thm\|htr\|htr_mod\|acm}/{addr}/status` | full status body | node `status` slot = body |
| `/{type}/{addr}/prog` | day-keyed prog object | node `prog` slot = body |
| `/{type}/{addr}/setup` | full setup object | node `setup` slot = body |
| `/{type}/{addr}/version` | version object | node `version` slot = body; the app regex is `^/[a-z]{3}/[0-9]{1,2}/version$`, so it matches `pmo`/`thm`/`htr`/`acm` and NOT `htr_mod` `[app mobile 2.40.4 app.js@1460021]` `[app web@1132532]` |

- `/htr_system/setup` frames are pushed by the server on power-limit writes (live-verified: the box limit write pushes BOTH `/htr_system/setup` as an INT and `/htr_system/power_limit` as a digit string) but are NOT routed by either current app `[live 2026-10-16]`. A consumer merging both copies into one slot must coerce to int (the library does; `api-contract.md` §5.3, §9).
- Node/status frame bodies are FULL bodies (no deltas) `[live 2026-09-26/10-04]`.

### 1.5 Snapshot shape (`all_data`)

- `data` = array of homes; each home carries `name` and `devs[]`; each dev entry carries `dev_id` and `devData` — the apps require both and sort homes by `name` `[app mobile 2.40.4 app.js@1454577, @1502491]`.
- `devData` is the per-device snapshot (the same shape the REST `dev_data` endpoint serves). Live-captured on the 5×htr SmartControl box, fw 1.9 `[live 2026-10-16]`:
  - top-level keys: `away_status`, `connected`, `discovery` (bare), `geoData` AND `geo_data` (both spellings), `htr_system`, `nodes`, `pmo_system`;
  - `nodes[]` entries: `type`/`addr`/`installed`/`lost`/`name` plus `status`/`setup`/`prog`/`version` slots; the htr family carries NO per-node power key (pmo-only);
  - the box power limit lives in TWO copies: `htr_system.power_limit.power_limit` (digit string) and `htr_system.setup.power_limit` (int); clearing the limit EMPTIES the object copy and serves `setup.power_limit: 0`.
- Updates patch `devData` incrementally; a new snapshot replaces it wholesale.

### 1.6 Liveness, close codes, reconnect

- No client ping (see §1.2). Data silence of many minutes is NORMAL — max observed gap 789 s on a healthy socket; the proxy/server sends a WebSocket PING roughly every 30 s and it is answered below the app layer `[live 2026-10-01/02]`.
- Consequence for any consumer: the apps carry NO dead-path detection of their own. A library/integration must add one — the reference implementation uses a 60 s aiohttp heartbeat (live-verified: dead-path detection ≤ ~60 s, zero false positives across a 4 h idle soak) `[code ws_user.py:66, 190-196]`.
- Close codes seen/used: 1006 for server cuts at token `exp` (and for locally detected deaths); 3000 is the apps' own CLIENT_FORCED_CLOSE_CODE `[app mobile 2.40.4 app.js@1446559]` `[app web@1119953]`.
- App reconnect policy (both current clients, same constants) `[app mobile 2.40.4 app.js@1456156, @1446559, @1446633, @1446707, @1446751, @1446899]` `[app web@1119953, @1120027, @1120145, @1120293]`:
  - base delay `1 s · 2^n`, one-sided `+0–20 %` jitter, cap 30 s;
  - give-up: `5 attempts OR 30 s since first failure` → permanent-failure state (the app shows `/lostConnection`; it does not retry until the user acts) `[app mobile 2.40.4 app.js@1458026]`;
  - while connectivity recovery is suspended (device provisioning) a flat `5 s + 0–10 %` delay is used instead;
  - triggers: socket close (unless code 3000), socket error, the `all_data` watchdog, a message parse error.
- On any non-forced close the apps first run a session check (`GET /client/sessions`) and then reconnect `[app mobile 2.40.4 app.js@1455499, @1455680]`.
- The reference library and this integration deliberately diverge on the retry budget (10 attempts per loop instead of 5; the jitter form is one-sided `+0–20 %`, aligned with the apps) — a LIB policy documented in `api-contract.md` §4.1/§9, not vendor parity.

### 1.7 Token lifecycle

- Access tokens are JWTs with ~4 h TTL; the socket dies at `exp` (§1.1). Refresh grant: `POST {host}/client/token` with `grant_type=refresh_token` (form-encoded, Basic reseller auth); refresh tokens ROTATE and old ones stay valid `[live 2026-10-02]` `[app mobile 2.40.4 app.js@1487544]`.
- The apps refresh proactively: `hasTokenExpired` = under 10 minutes of lifetime remaining `[app mobile 2.40.4 app.js@1498423]` — the library refreshes at a 60 s margin instead (no wire impact).
- REST 401 handling (both apps): any 401 from a non-token request triggers a FULL fresh restart (clear session → sign-in) and rejects with `ApiUnauthorisedError` `[app mobile 2.40.4 app.js@1466502, @1468006]`. The library instead re-auths and resends the request once `[code session.py:519-531]`.

### 1.8 Discovery socket (`dev_id=*`) — installation only

- A second ws_user socket with `dev_id=*` instead of `user_id` is used for smartbox installation: it resolves on `{"event":"dev_handshake","data":{"dev_id":…}}` with a 30 s timeout `[app mobile 2.40.4 app.js@1504771, @1505181, @1508893]` `[app web@1172511]`.
- Not needed for device control; listed for completeness.

---

## 2. REST surface

Everything below is what the current apps call (complete inventory from the 2.40.4 `APIManager` + app service layer `[app mobile 2.40.4 vendors.js@1524171]`; the web app's API-manager lives in lazy chunks that were not decoded — the mobile inventory is the reference). Bodies are as the apps construct them (app evidence); wire-acceptance notes and open questions live in `api-contract.md`. Headers on every authenticated call: `Authorization: Bearer <token>`, `Content-Type: application/json`, `x-serialid: <serialId>` (the current mobile build does NOT set `x-referer`; the 1.417.1 build did — this integration's token grant sends it too, and the endpoint tolerates it) `[app mobile 2.40.4 vendors.js@1524738]` `[app mobile app.js@17307858]`.

### 2.1 Auth and sessions

The grant and refresh shapes are `api-contract.md` §2.1 (E3). App-only details:
the 2.40.4 mobile build no longer sets `x-referer` (1.417.1 did, and the
endpoint tolerates it), and the session endpoints (X2/X3) are also used for
the post-close session check in §1.6. Token and session acceptance stay in
api-contract.md §2.

### 2.2 Reads

The app reads the endpoint set indexed in `api-endpoints.md` §2–§4.
App-specific read notes:

- `GET .../{type}/{addr}/power` is the pmo reading; the box/node power-limit
  asymmetry is api-contract.md §3.2 E17/E47.
- `GET /api/v2/device_data` is the per-serial metadata used during config init
  (X1).
- `GET /api/v2/groups/{gid}/extra_data` returns 404 until populated; the app
  tolerates it.
- `GET .../samples` returns a different row shape for pmo and accumulator
  nodes; the accepted shape is api-contract.md §3.2 E19.
- The app still lists `GET /api/v2/devs/{dev}/mgr/rtc/time` (E13) but never
  parses the response.
- `GET .../version` drives the discovery PID; the read itself is Contract
  E24.

### 2.3 Writes

Accepted bodies and the one-control-key rule are api-contract.md §3.2 and §5.1–§5.2.
The app-specific constructions and sequencing live here; endpoint rows are in
`api-endpoints.md`.

| Catalog | App construction / sequencing |
|---|---|
| E10 | away `{away, enabled, forced}`; the server sets `forced: true` on the write, and `{away}` alone is also honoured |
| E21 | status: the htr_mod mixed body below; one control key per POST otherwise |
| E23 | setup: full GET-merge-POST; the readback lags several seconds |
| E30 | prog: `{"prog": <day-keyed object>}`; the array shape returns 400 |
| E26 | lock: integer `0`/`1`/`2` on integer-lock families; the base path posts a boolean to `/status` |
| E27/S4 | boost: storage `/boost {boost, boost_time, stemp, units}`; full `/boost_mode` below |
| E18 | box power_limit: stringified integer; `0` clears the limit; the box write pushes both copies |
| E47 | pmo power_limit: the same string body; node-level `"0"` acceptance is unverified |
| S2 | pmo_system: `{main_circuit_pmos, max_power_config}`; partial bodies are allowed |
| E12 | discovery: `{discovery, type?, addr?, limit?}`; undefined keys are omitted |
| E14/E15/E16/E36/S5/S6 | fleet/home writes: app-shaped names, group moves, purge and geo/extra bodies; see the catalogue |
| E38–E40/E42/E43 | invite and presence writes: app-shaped; see the catalogue |

**htr_mod status bodies (app-shaped).** The 2.41.0 mobile build's htr_mod manager sends a MIXED-category body for a setpoint: `{on: true, mode: "manual", selected_temp: "eco"|"comfort", comfort_temp, eco_offset}` — and `{on: true, mode: "manual", selected_temp}` for a profile, `{on: false}` for off, and `{on: true, mode}` for the automatic modes. The body builder is decoded at `[app mobile 2.41.0 vendors.js@5048320]`, the profile-temp writer at `@5035003`, and the mode writer at `@5035988`. The vendor app exercising this body in production is the evidence that a live box accepts it; live acceptance on htr_mod hardware is still unprobed.

**Full boost (`/boost_mode`) — app logic.** The 2.41.0 mobile bundle defines `boost_config` as `0` unsupported, `1` `MAX_TEMP_FIXED_TIME`, `2` `SET_TEMP_AND_TIME`, `3` `FULL` `[app mobile 2.41.0 vendors.js@2891902]`. Only `3` owns the `/boost_mode` endpoint:

- `setBoost({active, temperature, timeInMinutes})`:
  - `1` → `setBasicBoost(true)` (status `{"boost": true}`);
  - `2` or `3` → activation calls `activateBoost`, deactivation `deactivateBoost`.
- `activateBoost` on `3` posts `{active: true, mode: "temp_and_time", temp: temperature.toFixed(1), time: timeInMinutes}`; omitted values are omitted from the body `[app mobile 2.41.0 vendors.js@2893116]`.
- `deactivateBoost` on `3` posts `{"active": false}` alone.
- `advanced_slot` posts `{active: true, mode: "advanced_slot"}`; `runback` posts `{active: true, mode: "runback", temp, time}`.
- `boost_modes_available` in status lists the modes a node accepts (runback is gated on it); `boost_mode` in status carries the active mode, and `boost` carries the active flag — the status frame is the write's confirmation `[app mobile 2.41.0 vendors.js@2860965]`.
- On `2` (the legacy path) activation first posts the full setup with `extra_options.boost_temp`/`boost_time`, then the status `{"boost": true}`; deactivation is the status `{"boost": false}`.
- Storage controllers do not use that setup path: `HelkiStorageHeaterController.setBoost` posts `/boost` with `{boost, boost_time, stemp, units}`; deactivation posts `/boost {boost: false}` and then `/mode` AUTO/MANUAL according to `setup.operational_mode`. The integration now exposes this path for Helki 1C/2E: `boost_available` follows the storage controller and `WriteApi.set_boost_legacy` posts `/boost`; live acceptance on real storage hardware is still open.
- **Scale rule for those setup fields:** `setup.extra_options.version >= 2` means `boost_temp`, `default_runback_temp`, `max_runback_temp` and `setback_temp` are Celsius; older nodes keep them in the node's display unit (`hasExtraOptionTempsInCelsius`) `[app mobile 2.41.0 vendors.js@2875498]`. Runback activation uses `extra_options.default_runback_temp` (fallback `status.stemp`) and `extra_options.boost_time` for its duration; its lower bound comes from `status.ice_temp` (or `setback_temp` where setback exists) and its upper bound is `max_runback_temp`, clamped by the global limit `[app mobile 2.41.0 vendors.js@2866225]`.

The vendor app is the specification here; these bodies are app-evidence only until a full-boost unit is probed live.

### 2.4 Account/app endpoints (not device control)

The account/session surface is indexed as X1–X19 in `api-endpoints.md` §7.
App-only details: `/client-data/{serialId}/*.json` is the per-serial statics
described in §2.5; `POST /api/v2/events` is analytics; `/client/code` is the
bundled OAuth device-link page; the invitation, sign-up, privacy and
account-deletion paths are app-only account flows; encrypted Wi-Fi credentials
are E44. None of these is a device-control row.

---

### 2.5 Per-tenant client data (`client-data/<serial>/*.json`)

Each brand's frontend fetches a static capability file from
`https://<frontend-host>/client-data/<serial>/capabilities.json`
(fetched 2026-11-25 on every vendor host; Elnur serves it from
`https://resources.heatingcontrol.eu/client-data/7/`, and legacy EHC serves
it at `client-data/3/` while also embedding the config in `app.js`). It is
an **app surface**, not the wire contract: it tells the add-device flow which
product categories the brand offers and which optional app features it
enables.

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

Current builds fetched: Helki 2.55.0; Lucht LHZ, Climastar, Valderoma, iHeat, HJM, Haverland and Technotherm 2.49.4; Perry 2.52.1; Electrorad 2.52.0; SmartControl 2.47.0. Elnur 3.7.0 and Tevolve/Ducaheat 2.8.8 are separate frontend lines; EHC 1.416.4-1 remains the legacy socket.io app.

The per-tenant values are fetched at runtime, not embedded in the JS bundle:
`appParameters.json`, `capabilities.json`, `nodeParameters.json`,
`resources.json` and `supportData.json`, plus `assets/maxPowerConfig.json` and
`i18n/<serial>/<lang>.json`. A headless load of the Helki app confirms exactly
these requests at startup. Elnur is the exception: it fetches the same files
from `https://resources.heatingcontrol.eu/client-data/7/`, not from its SPA
host. EHC embeds its `window.CONFIG.clientData` in `app.js` and also serves
the same `client-data/3/*.json` files; its legacy transport is recorded
without decoding its API. Ducaheat's `appParameters.json`
reports the Tevolve title and frontend host while the bundle is the
Ducaheat/ducasa build. Helki 2.55.0 additionally calls
`GET /api/v2/user/feature-flags`, which returns `{floorPlan, chatBot}` (both
UI-only); the 2.54.2 mobile bundle has no `featureFlags`/`floorPlan`/`chatBot`
strings.

Fetched matrix (serial, brand, categories; fetched 2026-11-25):

| Serial | Brand | Categories |
|---|---|---|
| 1 | Helki | thermostat, thermostatic_valve, heater, towel_rail_heater, wifi_heater, storage_heater, power_meter |
| 2 | Lucht LHZ | heater, towel_rail_heater, floor_thermostat, storage_heater, four_button_wifi_heater, two_button_wifi_heater, towel_rail_wifi_heater, power_meter |
| 3 | EHC | heater, towel_rail_heater, storage_heater, power_meter |
| 5 | Climastar | heater, wifi_heater |
| 6 | Perry | power_meter, thermostat |
| 7 | Elnur | thermostat, heater, storage_heater, solar_storage_heater, power_meter, water_storage_heater, wifi_heater |
| 8 | Valderoma | heater, wifi_heater |
| 9 | iHeat Control | heater, storage_heater, wifi_heater, power_meter |
| 10 | HJM | heater, wifi_heater, power_meter |
| 12 | Electrorad | heater, storage_heater, power_meter |
| 14 | Haverland | heater, modified_heater, wifi_heater, storage_heater |
| 15 | Tevolve / Ducaheat | thermostat, thermostatic_valve, heater, wifi_heater, storage_heater, power_meter |
| 16 | Technotherm | heater, towel_rail_heater, floor_thermostat, storage_heater, four_button_wifi_heater, two_button_wifi_heater, towel_rail_wifi_heater, power_meter |
| 17 | SmartControl | heater, towel_rail_heater, floor_thermostat, storage_heater, four_button_wifi_heater, two_button_wifi_heater, towel_rail_wifi_heater, power_meter |

Feature flags in the same file (notable per-tenant differences):

| Serial | Brand | Flags that differ from the common set |
|---|---|---|
| 1 | Helki | `changeNodeDisplayAvailable`, `appTourEnabled`, `pushNotificationsEnabled`, `darkModeEnabled`, `energyInsightsEnabled`, `temperatureRulerEnabled` all true |
| 2 | Lucht LHZ | `sampleDownloadEnabled`, `temperatureRulerEnabled`; `wifiSmartboxAvailable` false |
| 3 | EHC | common set; `wifiSmartboxAvailable` false; no `provisioningMethods` |
| 5 | Climastar | common set |
| 6 | Perry | common set; heat eco threshold 18 (see `nodeParameters.json`) |
| 7 | Elnur | `htrPowerLimitAvailable`, `sampleDownloadEnabled` |
| 8 | Valderoma | common set |
| 9 | iHeat Control | common set |
| 10 | HJM | `wifiSmartboxAvailable` false |
| 12 | Electrorad | common set |
| 14 | Haverland | `boostAvailable` false; `htrPowerLimitAvailable`, `sampleDownloadEnabled`; `provisioningMethods` AP only |
| 15 | Tevolve / Ducaheat | `wifiSmartboxAvailable` false; `samplesUnsupportedProductIds` `["0A4C", "0D4D"]` |
| 16 | Technotherm | `sampleDownloadEnabled`, `temperatureRulerEnabled`; `wifiSmartboxAvailable` false |
| 17 | SmartControl | `sampleDownloadEnabled`, `temperatureRulerEnabled` |

The common set is `geolocationAvailable`, `inviteEnabled` and
`powerScheduleAvailable` true; `boostAvailable` true; `htrPowerLimitAvailable`
and `sampleDownloadEnabled` false; `hideApiHost` false; `changeNodeDisplayAvailable`,
`appTourEnabled`, `pushNotificationsEnabled`, `darkModeEnabled`,
`energyInsightsEnabled` and `temperatureRulerEnabled` false.

`nodeParameters.json` carries the thermostat UI defaults under
`tempThresholds`: most brands use heat eco/comfort `15/21.5` and cold
`26/22.5`; Perry uses heat eco `18`; Elnur keys the block as `thermostat`
rather than `thm` and uses heat `15/20`. The integration does not expose
thermostat threshold entities, so these are app-surface values.

`samplesUnsupportedProductIds` gates the app's samples/records download
surface together with `sampleDownloadEnabled`; the decoded code uses it in a
UI availability filter, not in the `getSamples` request path. Do not treat it
as a wire capability.

The Tevolve and Ducaheat app carries a demo mode (`store.demo`,
`TITLES.DEMO`, `MESSAGES.INFO.REMEMBER_YOUR_ARE_IN_DEMO`, `/demoData.json`).
Its data is synthetic and explicitly not saved or applied; never treat a
demo payload as device evidence.

These flags are app/client gates, not wire capabilities. `changeNodeDisplayAvailable`,
`appTourEnabled`, `darkModeEnabled` and `energyInsightsEnabled` are display and
onboarding preferences; `pushNotificationsEnabled` gates the client's push
registration; `provisioningMethods` selects the AP/BT Wi-Fi onboarding flow.
None changes device status or write semantics. `temperatureRulerEnabled` is not
referenced by the decoded 2.49.4/2.55.0 or 2.54.2 bundles; it is a config field
with no consumer in the builds we have. The integration continues to gate
entities on wire state (status/setup fields), which is the authoritative source;
the flags explain why the vendor app hides a feature on a given brand and are
recorded so a missing entity is not mistaken for a wire gap.

### 2.6 Thermostat static (`thm`) shape

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

### 2.7 Control-mode UI lists (`setup.control_mode`)

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

### 2.8 Schedule model

- `prog.prog` = **7 day-arrays** (Sun..Sat; day order confirmed in `api-contract.md` §5.4), each holding **N profile-index ints**, one per slot: 24 slots @ 60 min (hourly, `prog_resolution: 0`), or 48 @ 30 min when capability `MULTIPLE_SCHEDULE_RESOLUTIONS` is active (`prog_resolution: 1`).
- Profile index order (plain htr): **0=ICE, 1=ECO, 2=COMFORT** (`getIndexSortedScheduleProfiles()`); UI profile id → API index via `getApiScheduleProfile()` = index in that list (unknown → 0).
- UI schedule = per day, slots `{start, end, id}` in **minutes from midnight**, `id` = profile. Conversion to raw: expand each slot into `ceil((end-start)/slotMinutes)` repeated indices; pad the day to 24 h. Reconstruction (raw → UI): collapse runs of equal indices; last slot ends
  1440.
- Resolution change: `setScheduleMethod(method)` → **setup POST `{prog_resol: m, prog_resolution: m}`** (2-key setup write; the app merges into the full setup first — merge rules as §2.3).
- Temperature edit per profile: app clamps against neighbour profiles (`scheduleTemperatureOffset`) then sends the 4-key status post (`api-contract.md` §5.2). Simpler equivalent: single-key posts per temp key + units — **verify one variant live before relying on it**.
- Websocket: schedule changes arrive as `/htr/<addr>/prog` update frames (observed live) — the integration's websocket handler must store `prog` frames like `status` frames (verify current handling during implementation).

## 3. Write → push semantics (what the socket delivers)

Accepted confirmation frames and settle rules are in `api-contract.md` §3.3,
`api-contract.md` §5.3 and `api-contract.md` §6.1. App-side observations kept
here:

- A node `/status` write on a live node: full-body status echo frame in ~0.3–0.5 s; the ABSENCE of the frame is the only fast failure signal `[live 2026-09-26/30]`.
- A node `/setup` write (fw-1.9): a ~0.2 s burst of THREE frames — `/mgr/nodes` + `/{type}/{addr}/status` + `/{type}/{addr}/setup` `[live 2026-10-04]`.
- A box power-limit write: BOTH `/htr_system/power_limit` (the posted body's stringified value; a bare digit-string body also occurs, §1.4) and `/htr_system/setup` (int object); snapshot copies stay in step; clearing serves an empty object copy with `setup.power_limit: 0` `[live 2026-10-16]`.
- Value-equal (no-op) writes push NOTHING `[live 2026-09-30]`.
- Writes to an unreachable NODE are accepted (HTTP OK, ~50–60 ms) with no frames; writes to an offline BOX are not queued — applied-or-errored `[live 2026-09-30/10-02]`.
- REST readback lags several seconds behind an accepted write (settle lag) `[live 2026-09-27]`.

---

## 4. Alignment checklist (review the code against this)

Status lines as of 2026-10-18 (this integration's alignment pass; the deliberate divergences are recorded in `api-contract.md` §9). Re-run the list when the vendor ships a new app.

1. **URL/connect**: `wss://{host}:443/api/v2/ws_user?token=&user_id=`; `user_id` from the JWT; token fetched fresh per connect; 401 vs 404/410 classified as in §1.1. — **Status: aligned.**
2. **Client messages**: `{"event":"all_data"}` on open; re-request on `should_sync` with ~1 s debounce; nothing else; no client ping is expected by the server. — **Status: aligned.**
3. **Snapshot**: homes → `devs[]` → `dev_id`/`devData` fan-out; `devData` key set per §1.5 (incl. the bare `discovery` and the duplicate geo spellings). — **Status: aligned** (the unread devData keys are scope decisions, `api-contract.md` §6.6).
4. **Update frames**: `{devid, data:{path, body}}` nesting; frames require path+body; frames for unknown dev ids are dropped. — **Status: aligned** (an empty body is ignored, and a frame for an unknown node is dropped too).
5. **Routing**: the §1.4 table — in particular `/mgr/nodes` (per-`addr` merge, install/removal visibility), `/pmo_system`, `/mgr/discovery`, `/geo_data`, `/mgr/away_status`. — **Status: aligned for the consumed slots** (`connected`, `away_status`, `htr_system/power_limit`, node `status`/`setup`/`prog`/`version`, `/mgr/nodes` membership); the node `power_limit` write endpoint is consumed (number + no-limit switch pair on nodes whose setup carries the key); the remaining unread device paths are ignored by policy (`api-contract.md` §6.6).
6. **Power-limit dual copies**: `htr_system.power_limit.power_limit` (string) vs `htr_system.setup.power_limit` (int); frames on both paths; coercion to one numeric slot; empty-object + `0` means unlimited. — **Status: aligned** (both body forms and the setup frame's int copy land in the one numeric slot; the client-posted `"0"` clear is live-verified `[live 2026-10-18]`).
7. **`/htr_system/setup` frames**: the server pushes them; the apps do not route them — decide and document the consumer's policy (the lib coerces into the same int slot). — **Status: decided + implemented** (the frame's `power_limit` key feeds the same numeric slot: the "0" clear pushes ONLY this frame `[live 2026-10-18]`, so ignoring it would miss an externally-cleared limit; the frame's other keys are unread; `api-contract.md` §7 item 15).
8. **Version frames**: app regex matches 3-letter types only; decide whether `htr_mod` version frames are handled defensively. — **Status: aligned** (version frames are stored for any node type whose node the snapshot holds, `htr_mod` included).
9. **Liveness**: consumer-side heartbeat required (apps have none); multi-minute silence is normal and must not be treated as failure. — **Status: aligned** (60 s heartbeat; a 10 s connect watchdog; a 45 min silence teardown, `api-contract.md` §4.1/§6.5).
10. **Reconnect**: token-`exp` close (1006) → fresh-token reconnect; budget/jitter policy documented as a deliberate choice vs the apps' `5 attempts / 30 s`; permanent-failure state has no automatic retry in the apps. — **Status: aligned on the cut and the base policy**; the budget stays a documented deliberate divergence (10 attempts per cycle, endless re-probe with a repair issue, `api-contract.md` §4.1/§9).
11. **Writes**: bodies per §2.3 — `power_limit` as a string; away `{away, enabled, forced}`; discovery with omitted-undefined keys; setup as full merge; prog day-keyed; samples `start`/`end` epoch seconds. — **Status: aligned** (`power_limit` stringified at both the box and the node endpoint; the BOX `0` = no limit is live-verified, the node-level `"0"` remains app-evidenced; the away body is the app's 3-key shape; setup full-merge; prog day-keyed and its confirming frame live-confirmed as the whole week + `sync_status`; samples `start`/`end`; °F setpoints quantize to the app's integral grid in its `.1f` body form `[live 2026-10-18]`).
12. **Headers/auth**: Bearer + `x-serialid`; token endpoint Basic + form-encoded; refresh-token grant; 401 → one refresh attempt, full re-login on failure. — **Status: aligned** (every authenticated call carries `x-serialid`; 401 → one resend, then the reauth flow).
13. **Device profile facts**: re-extract `EProductCodeValue`, the controller factory switch, `getActuationType`, `getUIHeatingControlModes`/`getIndexSortedUIHeatingControlModes`, `getUserDefinedTempLimits`/`getProgTempProperties`, the storage UI-mode tables, the lock/boost methods and the `getSettings`/`isSettingAvailable` gates (including `MAX_STEMP_LIMIT_UNSUPPORTED_PIDS`); diff them against `smartbox.device.codes`/`profile`/`control_modes`/`storage`/`modes`/`settings`. Update the shared fixture's row columns and control-mode block, refresh the decoded sha/byte count in `docs/api-contract.md` §1.9 and `docs/profile-audit.md`, then run the library code-table/profile/control-mode/storage/modes/settings tests and the integration parity suite. — **Status: tracked** in `docs/profile-audit.md` and the library code-table consistency tests.

## 5. App-side gaps

`api-contract.md` §7 is canonical for wire open questions. The app-side gap
kept here is the client's 6 s `all_data` watchdog: it is client behavior, and
server snapshot latency is not separately measured.

