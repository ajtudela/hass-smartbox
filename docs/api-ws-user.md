# `ws_user` API contract (`api-ws-user.md`)

The API surface the CURRENT vendor apps use, in one document: the per-user `ws_user` websocket transport plus the REST endpoints the web and mobile clients call. `socket_io` is deliberately OUT of scope (it is the retired transport, kept on the record in `api-contract.md` Appendix A.1).

Purpose: the alignment reference for the HA integration, which speaks `ws_user` only. Review the code against §1–§3 and work through the checklist in §4. Written to be usable standalone next to the integration's own copy of `api-contract.md`.

> **Role.** The CURRENT vendor apps' behavior and the parity checklist for re-aligning this integration when the vendor ships a new app. Wire acceptance is settled in `api-contract.md`; the web-app statics are `webapi-spec.md`. Refresh this document per vendor app release; the checklist's status lines record the last pass.

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

Companion docs: `api-contract.md` (full contract incl. socket_io history and open questions), `webapi-spec.md` (vendor API surface).

---

## 1. Transport — `ws_user`

### 1.1 URL and connect

- ONE socket serves the whole ACCOUNT — every home and every device of the user `[app mobile 2.40.4 app.js@1452270]` `[app web@1125284]` `[code ws_user.py:1-19]`.
- URL shape: `wss://{host}:443/api/v2/ws_user?token=<urlencoded access token>&user_id=<uid>`
  - `host` is the app's build-time API endpoint. The integration uses the reseller row's `api_host`: `users-api.helki.com` for most keys, `api-elnur.helki.com` for Elnur. The 2026-11-15 public-app audit found eleven SPA bundles on the shared host and Elnur on its own; ws_user is served on both `[app mobile 2.40.4 vendors.js@1561267]` `[live 2026-10-02, api-lhz]`.
  - `user_id` is the `userId` claim of the access token — both apps read it from the JWT `[app mobile 2.40.4 app.js@1452270]` `[code ws_user.py:109-133]`.
  - The token is fetched FRESH for every connect (the apps call their bearer-token getter inside the URL builder) `[app mobile 2.40.4 app.js@1452270]`.
- Public web-app audit (2026-11-15): twelve public apps ship the `ws_user` client. Eleven are built against `https://users-api.helki.com`: Helki, Lucht LHZ, Climastar, Valderoma, iHeat Control, HJM, Electrorad, Haverland, Tevolve, Technotherm and SmartControl. Elnur is the exception: its current bundle sets `VUE_APP_API_ENDPOINT=https://api-elnur.helki.com` and contains no `users-api` string. The EHC public app remains the 19.3 MB legacy `app.js` (2025-11-28) with `socket.io` and no `ws_user`/`users-api` on `api-ehc.helki.com`. Electrorad migrated to the SPA + `users-api` + `ws_user` on 2026-11-06; its root `/app.js` is still served but unused (2025-11-11). An unauthenticated `GET`/upgrade to `/api/v2/ws_user` is not a capability test: `api-elnur` returned 401, while `users-api` and `api-ehc` returned 404; the setup probe with a real account remains authoritative.
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

### 2.1 Auth

| Call | Body | Notes |
|---|---|---|
| `POST {host}/client/token` | form-encoded `grant_type=password` + `username`/`password`, or `grant_type=refresh_token` + `refresh_token`; `Authorization: Basic <reseller secret>` | the token endpoint sits OUTSIDE `/api/v2`; response `{access_token, refresh_token, expires_in, token_type}` `[app mobile 2.40.4 vendors.js@1525968]` `[live 2026-10-04]` |
| `GET /version` | — | no auth `[app mobile 2.40.4 vendors.js@1525022]` |
| `GET /health_check` | — | no auth |
| `GET /client/sessions`, `DELETE /client/sessions/{id}`, `DELETE /client/sessions`, `POST /client/logout` | — | session management; also used as the post-close session check (§1.6) `[app mobile 2.40.4 app.js@1472543, @1472975, @1473325, @1465906]` |

### 2.2 Reads

| Call | Notes |
|---|---|
| `GET /api/v2/grouped_devs` | homes list — `id`, `name`, `devs[]`, `extraData`, `owner`; the app's device-list fetch `[app mobile 2.40.4 vendors.js@1527763]` |
| `GET /api/v2/devs/{dev}/mgr/nodes` | node list; entries keyed `type`/`addr` (+`name`,`installed`,`lost`) `[live 2026-10-04]` |
| `GET /api/v2/devs/{dev}/connected` | `{connected}` `[live 2026-09-27/30]` |
| `GET /api/v2/devs/{dev}/mgr/away_status` | `{away, enabled, forced}` `[live 2026-09-27]` |
| `GET /api/v2/devs/{dev}/mgr/discovery` | `{"discovery": "off"\|"on"}` `[live 2026-09-27]` |
| `GET /api/v2/devs/{dev}/mgr/rtc/time` | `{d,h,m,n,s,w,y}` `[live 2026-09-27]` |
| `GET /api/v2/devs/{dev}/htr_system/power_limit` | `{power_limit}` (int-ish) |
| `GET .../{type}/{addr}/power` | pmo/consumption power read |
| `GET .../{type}/{addr}/status`, `/setup`, `/prog`, `/version` | node slots |
| `GET .../{type}/{addr}/samples?start=&end=` | epoch SECONDS; pmo sample shape differs from htr `[live 2026-10-04]` |
| `GET /api/v2/devs/{dev}/{type}/{addr}` | single node data |
| `GET /api/v2/groups/{gid}/geo_data` | home geo data |
| `GET /api/v2/groups/{gid}/extra_data` | 404 until populated `[live 2026-09-27]` |
| `GET /api/v2/groups/{gid}/guest_users` | group guests |
| `GET /api/notifications/v1/{gid}/presence/config` | `{excluded_nodes:[{devid, nodes}]}` `[live 2026-09-27]` |
| `GET /api/location/v1/coordinates` | country/state/city/zip params |
| `GET /api/v2/device_data` | per-serial device metadata (config init) |

### 2.3 Writes

| Call | Body | Notes |
|---|---|---|
| `POST /api/v2/devs/{dev}/mgr/away_status` | `{away, enabled, forced}` — the current app sends all three with `enabled: true`, `forced: false` hardcoded `[app mobile 2.40.4 vendors.js@1528985]`; `{away}` alone is honored `[live 2026-09-27]` | the SERVER sets `forced: true` on the write (the posted `false` does not stick) `[live 2026-10-18]`; broader semantics unprobed |
| `POST .../{type}/{addr}/status` | full/partial status; `stemp` requires `units`; one control key per POST is the safe pattern `[live 2026-09-26/28]`, except the app-shaped htr_mod body below | pass-through in the lib |
| `POST .../{type}/{addr}/setup` | FULL merged setup object (GET-merge-POST) `[live 2026-10-04]` | readback lags several seconds |
| `POST .../{type}/{addr}/prog` | `{"prog": <day-keyed object>}` — array shape 400s `[live 2026-09-27]` | day-level merge |
| `POST .../{type}/{addr}/mode` | e.g. `{mode, stemp, units}` | |
| `POST .../{type}/{addr}/lock` | `{"locked": bool}` | |
| `POST .../{type}/{addr}/boost` | legacy boost on full-boost units: `{boost: true, boost_time: <minutes>, stemp: "<n.n>", units}` / `{boost: false}`; other node families use `/status {"boost": bool}` `[app mobile 2.40.4 vendors.js@1474626, @1488818]` `[app mobile app.js@5013785]` | app-evidence only; superseded by `/boost_mode` for `boost_config` 3 |
| `POST .../{type}/{addr}/boost_mode` | `{active, mode, temp?, time?}`; modes `temp_and_time`/`max_temp_fixed_time`/`advanced_slot`/`runback`; used when `setup.factory_options.boost_config` is the integer `3` (FULL); availability via status `boost_modes_available` `[app mobile 2.41.0 vendors.js@2893116, @2891902]` | new in 2.40.4; app is the spec, live acceptance pending |
| `POST .../{type}/{addr}/prog_temps` | pass-through; app equivalent is the 4-key `/status` body `{ice_temp, eco_temp, comf_temp, units}` | |
| `POST .../{type}/{addr}/select` | `{"select": bool}` | |
| `POST .../{type}/{addr}/name` | `{"name": …}` | |
| `DELETE /api/v2/devs/{dev}/{type}/{addr}` | `{"purge": bool}` | destructive |
| `POST .../{type}/{addr}/power_limit` | `{"power_limit": "<int as string>"}` — `toFixed(0)` in both apps `[app mobile 2.40.4 vendors.js@1531416]` | |
| `POST /api/v2/devs/{dev}/htr_system/power_limit` | `{"power_limit": "<int as string>"}` `[app mobile 2.40.4 vendors.js@1531672]` | pushes BOTH copies (§3) |
| `POST /api/v2/devs/{dev}/pmo_system` | `{main_circuit_pmos: [addr…], max_power_config: {profiles, slots}}` | partial bodies allowed (e.g. only `main_circuit_pmos`) |
| `POST /api/v2/devs/{dev}/mgr/discovery` | `{discovery: "on"\|"off", type?, addr?, limit?}` (undefined keys omitted) | |
| `POST /api/v2/devs/{dev}/name`, `POST /api/v2/groups/{gid}/name` | `{"name": …}` | |
| `POST /api/v2/devs` | add device (3000 ms timeout) | |
| `POST /api/v2/devs/{dev}/group`, `DELETE /api/v2/devs/{dev}` | move / remove device | |
| `POST /api/v2/groups/{gid}/geo_data`, `/extra_data` | pass-through | |
| `POST /api/v2/users/{uid}/invite`, `DELETE .../invite`, `POST .../invite_confirmation` | `{email, groupid, confirmation_url}` / `{groupid, email}` / `{pass, code}` | |
| `POST /api/notifications/v1/{gid}/presence/config`, `/test` | pass-through | |

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
- **Scale rule for those setup fields:** `setup.extra_options.version >= 2` means `boost_temp`, `default_runback_temp`, `max_runback_temp` and `setback_temp` are Celsius; older nodes keep them in the node's display unit (`hasExtraOptionTempsInCelsius`) `[app mobile 2.41.0 vendors.js@2875498]`. Runback activation uses `extra_options.default_runback_temp` (fallback `status.stemp`) and `extra_options.boost_time` for its duration; its lower bound comes from `status.ice_temp` (or `setback_temp` where setback exists) and its upper bound is `max_runback_temp`, clamped by the global limit `[app mobile 2.41.0 vendors.js@2866225]`.

The vendor app is the specification here; these bodies are app-evidence only until a full-boost unit is probed live.

### 2.4 Account/app endpoints (not device control)

Used by the apps but outside the device-data path: `users/*` (privacy, preferences, `new_pass` — now with `use_code: true` — `new_pass/confirm` — now with `new_pass` — `change_pass`, `change_email`, `confirm`, delete account), `/client/code` (OAuth device linking, used by the bundled web pages), `/client-data/{serialId}/*.json` (per-serial client config), `POST /api/v2/events` (analytics), `GET /api/v2/encrypted_wifi_credentials?ssid=&pass=` (wifi setup). Listed so a reviewer does not mistake them for missing functionality `[app mobile 2.40.4 app.js@1474629, @1474857, @1513874, @1396801]`.

---

## 3. Write → push semantics (what the socket delivers)

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

## 5. Known gaps / open

Live-open items (December RTC indexing, boost on capable units, PMO behavior, the `should_sync` trigger) live in `api-contract.md` §7; this list keeps the app-side gaps.

- `should_sync` has never been observed live (trigger unknown) — the debounce behavior is code-verified only `[live 2026-10-02]`.
- `htr_mod` version frames: the app-side regex excludes them; whether the server pushes them at all is unprobed `**[unverified]**`.
- `forced` semantics on the away write (what `forced: true` does) are unprobed `[live 2026-09-27/29]`.
- `/boost_mode` (2.40.4 addition) is implemented from the 2.41.0 app decode (§2.3); it has no live observation, so the body/response are app-evidence only `**[unverified]**`.
- ws_user deployment is app-evidenced on `users-api.helki.com` and `api-elnur.helki.com`; the shared host is live-verified by this project on a SmartControl account (2026-10-18). Elnur's host is app-evidenced and route-probed (401), but no live Elnur account has been tested here; other tenant deployments remain unproven.
- The apps' 6 s `all_data` watchdog is client-side behavior; server expectations around snapshot latency are not separately measured.
