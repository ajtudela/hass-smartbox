<!-- record:begin -->
> **Maintained here.** This is the wire-fact record this repository cites (`docs/api-contract.md`); new probe results (`tools/live_probe.py`) and corrections are recorded in this file.
<!-- record:end -->

> **Role.** The wire-fact record this repository cites: live-probed facts with their evidence, the open questions, and the deliberate divergences from the 2.7 reference library (superseded by the 3.0 `smartbox` library). Wire ACCEPTANCE is settled here. The app-derived material lives in `webapi-spec.md` (the decoded web app's surface) and `api-ws-user.md` (the current apps' behavior and the parity checklist); where they disagree with a live observation, the live observation wins and the disagreement is recorded, not silently resolved.

# Smartbox API contract (`api-contract.md`)

The API-contract form of the pre-contract notes. Scope: the wire contract this integration implements (REST + websocket transports + write semantics), observed on live hardware and cross-checked against the decoded vendor apps and the 2.7 `smartbox` library's client (the 3.0 library is the successor used by this integration). The full vendor-API surface (including schedule UI conversion and the local-LAN API) remains in `webapi-spec.md`; endpoint shapes are cited from it by reference rather than duplicated. The pre-contract notes held the raw diary — this file is the citable contract distilled from them.

## Provenance tags (THE rule)

Every claim in this file carries AT LEAST ONE tag; compound claims may carry several (a `[code]` + `[live]` pair, plus a `[superseded]` note where applicable):

| Tag | Meaning |
|---|---|
| `[live YYYY-MM-DD]` | Observed on the wire against real hardware on that date; may carry an inline qualifier (`user-verified`, `web-app decode`, `integration round`) narrowing HOW it was observed (see §8 for the evidence trail). |
| `[code file:line]` | As implemented in the retired `smartbox` library at commit `807e873` (read 2026-10-14): `src/smartbox/<file>` is the client this integration's protocol layer replaces. This tag never names this repository. |
| `[doc webapi-spec.md §N]` | Statically extracted from the vendor app bundle; not independently wire-verified here. |
| `[doc: pre-contract notes, <section or date>]` | A provenance mark for the pre-contract diary this file replaced. The notes file does not ship; the durable claim lives in this file. |
| `**[unverified]**` | Assumed, inferred, or never tested. Visually loud on purpose: an unverified claim must LOOK unverified. Do not strip the tag when citing. |
| `[superseded: claim -> evidence]` | An older recorded claim that newer evidence contradicted; kept only to prevent regression. |
| `[file:line]` | As implemented in THIS repository (`custom_components/smartbox/`, the protocol and core layers), with a path resolvable from the repo root. |
| `[app helki.js@<offset>]` / `[app mobile app.js@<offset>]` | Byte-offset excerpt from a decoded vendor-app bundle — `[app helki.js@N]` = the web build of the Helki SPA (`app.helki.com.app.611c74a6`, obfuscated, single-line 1.6 MB); `[app mobile app.js@N]` = the unobfuscated mobile build of the same SPA (the Cordova packaging `webapi-spec.md` was extracted from), used where the web bundle's API-manager/node layer sits in lazy chunks absent from its single-file decode. Static decode evidence, not wire-verified. |
| `[app mobile 2.41.0 vendors.js@<offset>]` | Byte-offset excerpt from the 2.41.0 mobile build's API-manager bundle (`assets/www/js/chunk-vendors.3ed6a158.js`), used for the htr_mod write bodies. Static decode evidence, not wire-verified. |
| `[app mobile 2.54.2 <file>@<offset>]` | Byte-offset excerpt from the 2.54.2 Android bridge: either the shell (`app.5ae75d82.js` in the Haverland APK, `app.5759565e.js` in the SmartControl APK) or the deobfuscated shared manager bundle (`new_vendors.decoded.js`, decoded from the SmartControl APK; the same modules live in the Haverland APK's `chunk-vendors.f809b153.js`). Static decode evidence, not wire-verified. |
| `[app web tevolve@<offset>]` | Byte-offset excerpt from the decoded Tevolve SPA bundle (`https://tevolve.termoweb.net/js/app.0335ab61.js`, read 2026-10-20). Static decode evidence, not wire-verified. |
| `[app web ducaheat@<offset>]` | Byte-offset excerpt from the decoded Ducaheat SPA bundle (`https://ducaheat.termoweb.net/js/app.bd52ba45.js`, read 2026-11-21; its `chunk-vendors.baebe993.js` carries the same environment defaults). Static decode evidence, not wire-verified. |
| `[app web elnur@<offset>]` | Byte-offset excerpt from the decoded Elnur SPA bundle (`https://remotecontrol.elnur.es/js/chunk-vendors.cd43f63e.js`, read 2026-11-21). Static decode evidence, not wire-verified. |

Web and mobile bundles are interchangeable evidence for device/wire behavior: both clients reach the same backend and hardware, so a fact decoded from either applies to both. UI-only details remain specific to the cited client.

A claim with NO tag is a defect. Anything not in the map/code/diary is either absent or explicitly `**[unverified]**`.

---

## 1. Conventions

### 1.1 Base URLs and endpoint families

- The API host is per reseller row. The 2026-11-15 public-app audit: eleven current SPA bundles (Helki, Lucht LHZ, Climastar, Valderoma, iHeat Control, HJM, Electrorad, Haverland, Tevolve, Technotherm, SmartControl) are built against `https://users-api.helki.com`; Elnur's current bundle sets `https://api-elnur.helki.com`; the EHC public app remains legacy `socket.io` on `api-ehc.helki.com`. This integration uses the row host (default `users-api`, Elnur `api-elnur`), live-verified with an `api-smartcontrol` account (grant + ws_user probe + reads, 2026-10-18); Elnur's host is app-evidenced and route-probed, not live-account tested. The reseller table's `api_url` is the BRAND KEY (Basic credential, serial id, `x-referer`, entry unique id), not a host.
- Tevolve is a separate tenant on that host: key `api-tevolve`, serial 15, web app `https://tevolve.termoweb.net/`. Its bundle embeds the same `VUE_APP_API_ENDPOINT` and its own Basic credential `[app web tevolve@1449750]`; a live login is still `**[unverified]**`.
- The Ducaheat portal is that same tenant, not a second one: `https://ducaheat.termoweb.net/` serves the Tevolve SPA build (`app.bd52ba45.js`) with `VUE_APP_DEFAULT_THEME: "ducasa"` `[app web ducaheat@719263]`, `VUE_APP_FRONTEND_HOST=https://ducaheat.termoweb.net` `[app web ducaheat@719222]`, `VUE_APP_SERIAL_ID: "15"` `[app web ducaheat@719645]`, the same Basic credential `[app web ducaheat@119607]`, `VUE_APP_API_ENDPOINT=https://users-api.helki.com` `[app web ducaheat@192394]` and the `:443/api/v2/ws_user` client `[app web ducaheat@14381]`; the bundle contains no `socket.io` string. Ducasa/Ducaheat accounts therefore use the `api-tevolve` row, which the `api-ducaheat` picker alias resolves to; a live login is `**[unverified]**`.
- (Superseded for the shared-host apps, kept for the record:) the per-reseller hosts of the form `https://{api_url}.helki.com` are what the retired library dialed `[code session.py:216-223, reseller.py:19-101]`; several keys SHARED one host (`api-lhz`/`api-technoterm`/`api-smartcontrol` all mapped to `api-lhz.helki.com`). Elnur still uses its host; EHC remains on the legacy host/transport.
- Several keys share a tenant: `api-valderoma` uses the Climastar credentials; `api-iheatcontrol` uses the Electrorad credentials; `api-technoterm` and `api-smartcontrol` use the Lucht LHZ credentials `[code reseller.py:28-101]`. They dial the shared `users-api` host; Elnur is the per-host exception.
- The token endpoint sits OUTSIDE `/api/v2`: `POST {host}/client/token` `[code session.py:357]` `[live 2026-10-04]`. [superseded: "`/api/v2/client/token`" -> that path 404s on the probed host (api-lhz, several reseller keys), password grant probed 2026-10-04 `[live 2026-10-04]`; other hosts assumed `**[unverified]**`; the pre-2026-10-04 notes text said `/api/v2/client/token`.] Every endpoint listed in this file other than the token endpoint and the three host-relative families below IS under `/api/v2`.
- Host-relative (non-`/api/v2`) families the library dials directly: `/health_check` and `/version` (no auth) `[code session.py:330-343]`, `/api/notifications/v1/...` `[code session.py:1293-1321]`, and `/api/location/v1/coordinates` `[code session.py:1339-1355]`.

### 1.2 Reseller identity headers

- The token request carries `Authorization: Basic <reseller.basic_auth>` verbatim (still base64-encoded; decodes to `client_id:client_secret`, which the library never needs decoded) and `Content-Type: application/x-www-form-urlencoded` `[code session.py:350-355]` `[doc webapi-spec.md §1]`.
- Every later request carries `Authorization: Bearer <access_token>` and `x-serialid: <serial_id>`; JSON bodies set `Content-Type: application/json`. The retired library also sent `x-referer: <web_url>` where the reseller set one `[code session.py:242-249, reseller.py:17-26]`; the current mobile build does not, and this integration sends it only on the token grant `[doc api-ws-user.md §2]`.
- The Basic-auth credential is a reseller integration secret; capture via the web-app devtools or an HTTPS proxy (the capture recipe is not shipped) `[doc: pre-contract notes, Auth]`.
- Brand identity is per KEY, not per host: using the wrong key for your account yields `InvalidAuthError` even with valid user credentials (e.g. Lucht LHZ customers must use `api-lhz`, SmartControl customers must use `api-smartcontrol`) `[doc: pre-contract notes, Reseller keys]` `[code reseller.py:28-101]`. When diagnosing auth failures, check the reseller key first.

### 1.3 Units

- Temperature values travel as strings (`"20.0"`, `"19.5"`) and every temperature-bearing `/status` write carries `units` (`"C"` observed) — the app's 4-key profile-temp body includes it, and this integration refuses a unitless profile-temp body rather than posting one the app never sends `[doc webapi-spec.md §4 item 4]`; the app-shaped 4-key `/status` body is accepted and echoed live `[live 2026-10-20]`. The dedicated `/prog_temps` endpoint exists on the wire but this integration does not call it; it posts the app-shaped 4-key `/status` body `[writes.py:281-332]`.
- The app's UI step is 0.5 °C / 1 °F on the `htr` family `[live 2026-09-28, user-verified]` `[app mobile app.js@473398]`; the `htr_mod` controller runs a 1 °C step with its own profile bounds (§7 item 7).
- °F model (app-pinned): the °F grid is an INTEGER 1 °F grid (non-integer °F input is rejected client-side), the °F↔°C converters are fixed-point helpers that SNAP offsets to the 0.5 °C grid (1 °F ↔ 0.5 °C), and the wire `stemp` is always written in the node's own `setup.units` (an °F node receives e.g. `"69.0"` with `units: "F"`) `[app mobile app.js@506258, @2485204, @485218]`; server-side °F quantization remains `**[unverified]**` (§7).
- Off-grid setpoints are silently quantized to the 0.5 °C grid by the server (see §5) `[live 2026-09-28]`; the vendor app instead REJECTS off-grid input client-side (§5.2) `[app mobile app.js@506258]`.

### 1.4 Error hierarchy and HTTP mapping

Hierarchy `[code error.py:6-54]`:

- `SmartboxError` — base of every library error `[code error.py:6-7]`.
- `InvalidAuthError` — credentials/token rejected; triggers reauth flows `[code error.py:41-42]`.
- `APIUnavailableError` — transient trouble (also an `aiohttp.ClientConnectionError` subclass for consumer compatibility) `[code error.py:45-50]`.
- `SmartboxValidationError` — typed-mode payload drift; carries the wire `payload` attribute `[code error.py:23-38]`.
- `WsUserUnsupportedError` — the host does not serve `ws_user` (or the token is not a JWT); in the retired library callers fell back to socket_io, which this integration no longer ships `[code error.py:10-21]`.
- `ResellerNotExistError` — unknown `api_name` `[code error.py:53-54]`.

HTTP mapping, REST path (`_send_mapped`) `[code session.py:599-624]`:

| Condition | Raised as |
|---|---|
| HTTP 401 | `InvalidAuthError` `[code session.py:602-604]` |
| HTTP 5xx | `APIUnavailableError` (retryable on GETs) `[code session.py:603-609]` |
| Any other 4xx | `SmartboxError` `[code session.py:610-612]` |
| Connection error / truncated body / timeout | `APIUnavailableError` `[code session.py:613-621]` |
| 204, empty 200, or non-JSON 200 | returns `{}` `[code session.py:588-596]` |
| Undecodable JSON in a 2xx | `SmartboxError` `[code session.py:622-624]` |

HTTP mapping, token endpoint (`_authentication`) `[code session.py:399-438]`:

| Condition | Raised as |
|---|---|
| 429 | `APIUnavailableError` (rate limit is transient, never "bad credentials") `[code session.py:428-435]` — OLD-LIB mapping; the wire semantics of a token-endpoint 429 are `**[unverified]**`; This integration ALIGNED to this mapping (`TransportUnavailableError`, retried by the grant ladder, never the password fallback) `[2026-10-05, auth.py:310-317]` (§9) |
| Other 4xx (<500) | `InvalidAuthError` `[code session.py:436-437]` |
| 5xx | `APIUnavailableError` `[code session.py:438]` |
| Malformed 200 (wrong token shape / undecodable JSON / non-JSON body) | `SmartboxError` — a malformed success is NOT a credential rejection `[code session.py:388-397, 413-424]` |

GET retry policy: `_request_with_retry` retries `APIUnavailableError` only, on GETs only, `max(1, attempts)` attempts (default 5), backoff `backoff_factor · 2^attempt` (default factor 0.1), all sharing the call's 30 s budget (a backoff that would overrun it is skipped) `[code session.py:626-657]`.

### 1.5 Timeout and deadline model

- Per-request timeouts (30 s total / 10 s connect) apply only to sessions the library created itself `[code session.py:52-53, 318-328]`.
- EVERY library call (refresh + retries + backoff included) is bounded by `_CALL_TIMEOUT = 30 s` regardless of whose websession is used; past the deadline the call fails with `APIUnavailableError` `[code session.py:55-58]`.

### 1.6 Response-mode contract

- `raw_response=True` is the constructor default: getters return the wire payload unvalidated, with only the documented list extraction; a payload too malformed to extract from raises `SmartboxError` `[code session.py:202, 727-730]`.
- `raw_response=False` returns a typed pydantic model; drift raises `SmartboxValidationError` carrying the wire `payload` `[code session.py:730-731, error.py:23-38]`.
- Model strictness is deliberate and asymmetric: status models are strict (drift is the signal); the TOP-LEVEL setup models (`DefaultNodeSetup`/`PmoSetup`) are `extra="allow"` because setup feeds destructive full-repost writes and unknown keys must survive `[code models.py:69, 93]`. CAVEAT: the nested `NodeFactoryOptions`/`NodeExtraOptions` sub-models use pydantic's default `ignore` `[code models.py:19-59]` — a typed read-and-repost would drop any FUTURE undeclared key nested inside `factory_options`/`extra_options` (the wipe hazard the doctrine warns against); the survive-the-roundtrip doctrine holds for the top-level setup models only.
- fw-1.9 status family: int `error_code`, extra `easy`/`runback`/`version` keys, no `act_duty`/`power_pcb_temp`; `DefaultNodeStatus` accepts both families (`error_code: str | int`, the latter two optional) `[live 2026-09-26]` `[code models.py:148-177]`.

### 1.7 Secret hygiene

- Query/body keys `token`, `pass`, `password`, `user_id` are masked in log output `[code session.py:65-71]`; URLs in errors and debug lines are redacted `[code session.py:79-88, 574-576]`; requests carrying sensitive params lose the aiohttp exception cause entirely (its `__str__` embeds the full URL) `[code session.py:533-557]`. The wifi password travels as a query param and is deliberately never logged `[code session.py:1323-1337]`.

### 1.8 Sync surface

`Session` (sync) contains zero wire logic: every method is a `_run_and_close` delegation (`asyncio.run` + owned-session close) over the async class, one event loop per call `[code session.py:1358-1395]`. Legacy aliases `get_status`/`set_status`/`get_setup`/`set_setup`/ `get_prog`/`set_prog` map to the corresponding node endpoints (same paths) `[code session.py:1436-1511]` — they are aliases, not separate endpoints.

### 1.9 App builds and per-product controllers

The mobile apps are branded builds of ONE shared frontend: every build carries the full controller set and the full product-code table, and only the shell config differs. Brand identity is a baked key (`VUE_APP_SERIAL_ID`, the same value the reseller table carries), not a separate codebase.

| | Haverland build | SmartControl build |
|---|---|---|
| Package | `net.evsa.haverland` | `com.technotherm.lhzapp` |
| Version | 2.54.2 (20542000) | 2.54.2 (20542000) |
| Manager bundle | `chunk-vendors.f809b153.js`, 5,785,670 B | `chunk-vendors.94563eac.js`, 5,785,623 B |
| Shell | `app.5ae75d82.js` | `app.5759565e.js` |
| `VUE_APP_SERIAL_ID` | `14` (api-haverland) `[app mobile 2.54.2 app.5ae75d82.js@486515]` | `17` (api-smartcontrol) `[app mobile 2.54.2 app.5759565e.js@477496]` |

Decoded artifact: `new_vendors.decoded.js` sha256 `f4687447d9cff9bd9c04c4651d320501bb3e28020091b463ac177e153797ea96` (5,856,496 bytes).

Both manager bundles name all 35 controller classes (Haverland, Lhz, LhzFourButton, Helki, Elnur, Climastar, Tevolve, Ferroli, Trv, ...) and the same 51-entry `EProductCodeValue` enum over 12 manufacturers `[app mobile 2.54.2 new_vendors.decoded.js@5759823]`. The brand web SPA carries the same controllers — its `htrModList()` filters instances of `HaverlandHtrModController` `[app helki.js@408781]`. Hardware identity is resolved per node at runtime: the factory switches on `version.pid.substring(2,4).toUpperCase()` `[app mobile 2.54.2 new_vendors.decoded.js@4845231]`.

**Controller hierarchy.** One base class carries the defaults; brand controllers override constants.

| Controller | Constants (Celsius unless noted) | Provenance |
|---|---|---|
| `HeatingNodeController` (base, every heater) | setpoint step 0.5, profile offset 0.5, profile step 0.5, away offset 0-5 step 0.5, global 0-30 (32-86 F), default stemp 20 | `[app mobile 2.54.2 new_vendors.decoded.js@4629951]` |
| `ElectricHeaterController` (htr/acm) | OFF slot 0, `STEMP_LIMIT_MIN` 16, max-limit step 1; owns the server field `max_stemp_limit` | `[app mobile 2.54.2 new_vendors.decoded.js@3566389, @3577467]` |
| `HaverlandHeaterController` (legacy htr) | profile offset 2, ice 5-7, global min 5 | `[app mobile 2.54.2 new_vendors.decoded.js@5337078]` |
| `HaverlandHtrModController` | comfort 18-28, eco 10-18 (offset 4-8), setpoint step 1, global 5-28 (41-82 F) | `[app mobile 2.54.2 new_vendors.decoded.js@5782269]`; body in §7 item 7 |
| `TevolveTriacHeaterController` | global 7-35 | `[app mobile 2.54.2 new_vendors.decoded.js@2231435]` |
| `TevolveWifiHeaterController` (31/34/39) | inherits triac 7-35; no ice override | `[app mobile 2.54.2 new_vendors.decoded.js@1631259]` |
| `TevolveTowelRailHeaterController` (23) | inherits triac 7-35 | `[app mobile 2.54.2 new_vendors.decoded.js@3726995]` |
| `TevolveRelayHeaterController` (1E) | base 0-30 with max raised to 35 | `[app mobile 2.54.2 new_vendors.decoded.js@4720332]` |
| `TevolveWifiHeaterForAtcController` / `FerroliWifiTriacHeaterForAtcController` (4C/4D) | global 5-30, ice 5-10, default stemp 21, boost max 120 min | `[app mobile 2.54.2 new_vendors.decoded.js@3643870]` |
| `TevolveThermostatController` (28) | extends triac 7-35; floor probe 20-45, default 29, step 0.5 | `[app mobile 2.54.2 new_vendors.decoded.js@1570276]` |
| `MultiProgResolutionWifiHeaterController` (43) | inherits ElectricHeater 0-30 | `[app mobile 2.54.2 new_vendors.decoded.js@1708132]` |
| `ThermostatController` | heat comfort/eco 20/17, cold comfort/eco 24/27, global max 35 | `[app mobile 2.54.2 new_vendors.decoded.js@3728599]` |
| `ElnurStorageHeaterController` | global min 3 | `[app mobile 2.54.2 new_vendors.decoded.js@3664101]` |
| `HelkiStorageHeaterController` | per-node `setup.min_stemp`/`max_stemp` (fallback 5/30) | `[app mobile 2.54.2 new_vendors.decoded.js@1536465]` |

Consequences for consumers: hardware-specific edit ranges are client-side constants in the app, except the storage-heater family, which reads its range from the node's `setup`; the user-facing maximum temperature is the server field `max_stemp_limit`, which `ElectricHeaterController` owns where the controller supports it (storage and Tevolve triac/WiFi/towel-rail with `hasMaxStempLimit=false` do not expose the setting, §5.7); and `htr_mod`'s per-profile bounds and the Tevolve PIR comfort/eco gap are the profile-level constants, scoped to the product codes that dispatch to those controllers, not to the wire node type (§7 item 7). `controllerOpts.minTempCelsius` is a runtime override in the app, but the decoded 2.54.2 call sites pass only `brandName`, so the controller minima above are the effective floor; the mobile UI stops at 1.0 °C on the 0-30 family. Live 2026-11-22 on the HJM `0132` heater: the app target ranged 1.0-30.0 °C and programme comfort 1.0-30.0 °C, matching the base 0-30 controller plus the 1.0 UI floor and the `min + 2 * offset` programme rule `[live 2026-11-22]`. The integration encodes this table in `smartbox.device.profile` (`_LIMITS_BY_FAMILY` / `_CODE_LIMITS`), consumed by the adapter: base/Helki/Lhz 0-30, Haverland 5-30 with ice 5-7 and offset 2, Helki storage setup `min_stemp`/`max_stemp` (fallback 5/30), Elnur storage 3-30, htr_mod 5-28 with eco 10-18 and comfort 18-28, Tevolve triac/WiFi/towel-rail 7-35 (PIR 27 keeps that range with a 3.5 °C comfort/eco programme gap and a read-only ICE slot), Tevolve relay 0-35, Tevolve/Ferroli ATC 5-30 with ice 5-10, thermostat 7-35, and 0-30 for an undecoded htr (the factory's default ElectricHeater branch). The factory-default codes `0D/18/24/43` use the 0-30 ElectricHeater catalogue, and an unknown/`00` `acm` resolves to HelkiStorage. Programme bounds derive per profile from the same controller `[app mobile 2.54.2 new_vendors.decoded.js@3627000]` (the Haverland override has its own fixed frost range `[app mobile 2.54.2 new_vendors.decoded.js@5338169]`); the stored `max_stemp_limit` replaces the controller maximum in `getUserDefinedTempLimits`, so every profile's ceiling shifts by its offset (frost `max-2*offset`, eco `max-offset`, comfort `max`) while the floors stay at the controller minimum and offsets. Live 2026-11-23 on a fw-1.9 `0402` relay heater with a 23 °C limit: the app showed frost 0-22, eco 0.5-22.5, comfort 1-23. Boost-duration caps are 1440 base, 120 Tevolve ATC and 300 Climastar WiFi triac.

- `setup.control_mode` is the position in the controller's **wire-order** array, `getIndexSortedUIHeatingControlModes()`; `getUIHeatingControlModes()` is the separate display/accepted list. The app converts with `convertAPIControlModeToUIControlMode(index) = getIndexSortedUIHeatingControlModes()[index]` and `convertUIControlModeToAPIControlMode(uiMode) = findIndex(uiMode)` in the same array `[app mobile 2.54.2 new_vendors.decoded.js@4649476]`. On PID `0402` the `ElectricHeaterController` wire array is `[hyst_02, hyst_03, hyst_05, hyst_07, pid, pid_15, pid_30]` `[app mobile 2.54.2 new_vendors.decoded.js@3607542]`. Live 2026-11-21 on five LHZ/EHC htr nodes: all report `control_mode: 5` and the app shows **PID (15 min. period)**. Cycling the Obývačka node through every visible mode confirmed each pair — PID 15 → `5`, PID 30 → `6`, Hysteresis 0.2 → `0`, 0.3 → `1`, 0.5 → `2`, 0.7 → `3` — and the node was restored to `5` `[live 2026-11-21, LHZ/EHC htr x5, full app cycle]`. The htr wire map is:

| Wire index | Mode |
|---|---|
| 0 | `HYST_02` |
| 1 | `HYST_03` |
| 2 | `HYST_05` |
| 3 | `HYST_07` |
| 4 | `PID` (triac UI; not offered by the relay UI) |
| 5 | `PID_15` |
| 6 | `PID_30` |

The app-side UI lists and labels are recorded in `docs/webapi-spec.md` §3.4.

`HeatingNodeController.getActuationType()` selects the UI list: it returns **RELAY** for codes `02` (Helki relay), `1C`/`2E` (Helki storage), `13` (Elnur fixed-power relay), `17` (Haverland four-button relay), `10`/`16`/`12` (Climastar relay), `0D`/`18` (Lhz relay), `1E`/`23` (Tevolve relay), `28` (Tevolve thermostat), `31`/`33`/`35`/`36` (Tevolve/Lhz WiFi relay) and **TRIAC** for the remaining heating codes, including the default branch `[app mobile 2.54.2 new_vendors.decoded.js@3633782]`. The method is never consulted for the power-meter controllers (`03`/`22`) or the thermostat-family controllers (`04`/`09`/`0F`), so the table records integration-only `POWER` and `THERMOSTAT` values for those rows; the 46 heating rows match the app's RELAY/TRIAC returns.

- The product-code factory selects three families: **htr** (`ElectricHeaterController` and subclasses), **storage** (`HelkiStorageHeaterController`, codes `1C`/`2E`), and **thermostat** (`ThermostatController`/`TrvController`, codes `04`/`09`/`0F`) `[app mobile 2.54.2 new_vendors.decoded.js@4846187]`. The htr wire array is above; the storage wire array starts with `off` and continues `hyst_02, hyst_03, hyst_05, hyst_07, pid_15, pid_30`, so the inherited relay UI maps hysteresis to 1–4 and PID 15/30 to 5/6 `[app mobile 2.54.2 new_vendors.decoded.js@1538940]`. The thermostat family is **withheld**: its UI list offers `pid_60` while its wire array contains plain `pid`, so the app's own `findIndex` conversion cannot map `pid_60` and falls back to 0 `[app mobile 2.54.2 new_vendors.decoded.js@3730100]` `[app mobile 2.54.2 new_vendors.decoded.js@3733628]`. Codes absent from the explicit table use the factory's default branch, `ElectricHeaterController` (the htr wire array); the app switches on `version.pid[2:4]` only, so there is no node-type fallback. THM is withheld as an explicit integration choice, except explicit htr-family products such as code `28` (the Tevolve floor thermostat); the `04`/`09`/`0F` thermostat family is withheld for the `pid_60` mismatch above. The catalogue is encoded in `smartbox.device.control_modes` (`EXPLICIT_FAMILY` / `ACTUATION`), consumed by the adapter; the 0402 live cycle validates the htr/relay pair.

---

## 2. Auth & tokens

### 2.1 Grants

- Password grant: form body `{"grant_type": "password", "username": …, "password": …}` — the login email is passed AS `username` `[code session.py:474-480]` `[live 2026-10-04]`. [superseded: an `email`-keyed body -> rejected with 400 Bad Request (the JWT's `email` claim caused the confusion); live-probed 2026-10-04 `[live 2026-10-04]`.]
- Refresh grant: `{"grant_type": "refresh_token", "refresh_token": …}` `[code session.py:456-461]` `[live 2026-10-04]`.
- Token response: `{access_token, refresh_token, expires_in, token_type}` `[code models.py:300-305]` `[live 2026-10-02]`. The library stores the newest pair on every grant `[code session.py:366-379]`.

### 2.2 Lifetime and rotation

- Access tokens are JWTs with a ~4 h TTL; claims `{clientId, email, exp, iat, iss, scope, serialId, userId}` — the `userId` claim addresses the per-user websocket (§4.1) `[live 2026-10-02]` `[code ws_user.py:109-133]`.
- The library refreshes when <60 s of lifetime remain (`_MIN_TOKEN_LIFETIME`) `[code session.py:49-51, 451-456]`; a response with `expires_in < 60` logs a warning and refreshes on the next operation `[code session.py:373-378]`.
- Each refresh grant ROTATES the refresh token and the OLD one remains valid (reuse verified with a >4 h-old token minting fresh pairs repeatedly) `[live 2026-10-02]`. The library keeps only the newest pair `[code session.py:366-379]`.
- Parallel password logins return the BYTE-IDENTICAL access token (deterministic re-issue); multiple concurrent sockets on the same token all work; no session invalidation exists — HA, web app and mobile app coexist by design `[live 2026-10-02]`.
- Refresh-token server-side TTL was never captured (open, §7); the one-password-login fallback makes it irrelevant for availability `[live 2026-09-26]`.
- `GET /client/sessions` returns `{sid, createdAt, lastUsedAt, userAgent, ip, current}` per login `[live 2026-11-14]`. A password grant inserts a NEW session row; two consecutive refresh grants kept the same `sid` and `createdAt`, so a refresh continues the existing session `[live 2026-11-14]`. Rows persist until closed (`DELETE /client/sessions/{sid}`) or expired; 198 rows spanning six weeks were observed `[live 2026-11-14]`. Persistence policy (resuming the bundle across restarts) belongs to the integration; the wire fact is that a refresh continues the row and a password grant adds one.

### 2.3 Recovery doctrine

- Token refresh is serialized by `_refresh_lock` — the REST poller and socket loop must not race two refreshes with the same refresh token `[code session.py:236-239, 440-449]`.
- A rejected refresh (`InvalidAuthError`) clears the tokens and falls back to ONE password login before the error reaches the caller — a dead refresh token alone does not force a reauth flow `[code session.py:462-473]` `[doc: pre-contract notes, 2026-09-27]`.
- A 401 on any authenticated request invalidates the token, re-auths, and resends the request ONCE — the server acted on nothing, so writes are resent too `[code session.py:519-531]` `[doc: pre-contract notes, 2026-09-27]`.
- A second concurrent 401 must not trigger a refresh storm: `_invalidate_access_token` is a no-op if the token was already replaced `[code session.py:482-491]`.

### 2.4 Credential-change behavior

- Changing the account password does NOT revoke outstanding tokens: an integration holding pre-change access + refresh tokens kept polling unaffected `[live 2026-09-26]`. Consistent with the code: steady-state auth never touches the password `[code session.py:440-470]`.
- A forced full re-auth with a stale password fails fast (immediate 401 at `{host}/client/token`) and recovers cleanly once credentials are updated `[live 2026-09-26]`.

### 2.5 Socket-facing token facts

- The `ws_user` handshake validates `exp`: an expired token is rejected with HTTP 401; a fresh token per connect is LOAD-BEARING `[live 2026-10-02]` `[code ws_user.py:341-344, 364-370]`.
- Open sockets are cut by the server exactly at the token's `exp` with close code 1006 (§4.1) `[live 2026-10-02]`.

---

## 3. REST endpoints

`<type>` ∈ node types, `<addr>` int. Node types (C5 resolution): the library enum is `htr, thm, htr_mod, acm, pmo` `[code models.py:9-16]`; `pmo` and `htr_mod` exist in vendor fixtures and app managers `[doc webapi-spec.md §3]`. [superseded: "node type apparently can be htr, thm or acm (only htr tested)" -> code enum + webapi-spec §3 add `htr_mod`/`pmo`; only htr nodes are live-tested `[live 2026-09-26/27]`; thm/acm/htr_mod/pmo node shapes are `**[unverified]**` on live hardware.]

- The API enum `EAPINodeTypeValue` is `htr, htr_mod, thm, acm, pmo` `[app mobile 2.54.2 new_vendors.decoded.js@5758915]`; the product factory throws on any other value `[app mobile 2.54.2 new_vendors.decoded.js@4845795]`. The status paths cover htr/htr_mod/acm/thm; `pmo` has no status path. The bundle also carries a separate product/UI enum (`heater`, `modified_heater`, `wifi_heater`, `towel_rail_heater`, `towel_rail_wifi_heater`, `storage_heater`, `thermostat`, `thermostatic_valve`, `floor_thermostat`, `solar_storage_heater`, `water_storage_heater`, `power_meter`, plus a `timer` subtype) `[app web elnur@5809746]`. Those are the categories `client-data/<serial>/capabilities.json` advertises (`docs/webapi-spec.md` §3.2), not wire node types; a towel rail or floor thermostat reports an `htr`/`htr_mod`/`thm` node on the wire.


### 3.1 Product-code enum (`EProductCodeValue`)

`version.pid[2:4]` is the product code. The enum has 51 entries (including `UNKNOWN`) `[app mobile 2.54.2 new_vendors.decoded.js@5759823]`; the label column mirrors `product.py`. `00` falls back to the wire node type.

| Code | Enum key | Label |
|---|---|---|
| `00` | `UNKNOWN` | — |
| `01` | `HELKI_TRIAC_HEATER` | Triac heater |
| `02` | `HELKI_RELAY_HEATER` | Relay heater |
| `07` | `HELKI_TRIAC_HEATER_WITH_ROTATED_SCREEN` | Triac heater |
| `32` | `HELKI_WIFI_HEATER` | WiFi heater |
| `35` | `HELKI_RELAY_WIFI_HEATER_WITH_7S_DISPLAY` | WiFi heater |
| `1C` | `HELKI_STORAGE_HEATER` | Storage heater |
| `04` | `HELKI_THERMOSTAT` | Thermostat |
| `0F` | `HELKI_THERMOSTATIC_VALVE` | Thermostatic valve |
| `03` | `HELKI_POWER_MONITOR` | Power monitor |
| `22` | `HELKI_BIDIRECTIONAL_POWER_MONITOR` | Power monitor |
| `11` | `ELNUR_TRIAC_HEATER_WITH_FIXED_POWER` | Triac heater |
| `0B` | `ELNUR_FS_TRIAC_HEATER_WITH_FIXED_POWER` | Triac heater |
| `13` | `ELNUR_RELAY_HEATER_WITH_FIXED_POWER` | Relay heater |
| `1B` | `ELNUR_TRIAC_HEATER_WITH_921MHZ` | Triac heater |
| `0C` | `ELNUR_TRIAC_HEATER` | Triac heater |
| `14` | `ELNUR_STORAGE_HEATER_WITH_4_TRIACS_AND_3_PROBES` | Storage heater |
| `20` | `ELNUR_EXTRA_ENERGY_STORAGE_HEATER` | Storage heater |
| `25` | `ELNUR_EXTRA_ENERGY_STORAGE_HEATER_WITH_FAN` | Storage heater |
| `2A` | `ELNUR_WATER_STORAGE_HEATER` | Water storage heater |
| `0A` | `HAVERLAND_HEATER_MODULE` | Heater module |
| `17` | `HAVERLAND_RELAY_HEATER_WITH_4_BUTTONS` | Relay heater |
| `1D` | `HAVERLAND_WIFI_DIRECT_HEATER` | WiFi heater |
| `47` | `HAVERLAND_WIFI_DIRECT_HEATER_B` | WiFi heater |
| `FF` | `HAVERLAND_LEGACY_HEATER` | Heater |
| `08` | `CLIMASTAR_TRIAC_HEATER` | Triac heater |
| `10` | `CLIMASTAR_RELAY_HEATER_WITH_ROTATED_SCREEN` | Relay heater |
| `16` | `CLIMASTAR_RELAY_HEATER_WIFH_FIXED_MTEMP` | Relay heater |
| `12` | `CLIMASTAR_LOW_COST_HEATER` | Heater |
| `3D` | `CLIMASTAR_WIFI_TRIAC_HEATER` | WiFi heater |
| `33` | `LHZ_RELAY_WIFI_HEATER` | WiFi heater |
| `0D` | `LHZ_RELAY_HEATER_WITH_ROTATED_SCREEN` | Relay heater |
| `18` | `LHZ_RELAY_HEATER_WITH_921MHZ` | Relay heater |
| `36` | `LHZ_RELAY_HEATER` | Relay heater |
| `2E` | `LHZ_STORAGE_HEATER_RELAY_WITH_921MHZ` | Storage heater |
| `09` | `PERRY_THERMOSTAT` | Thermostat |
| `1A` | `TEVOLVE_TRIAC_HEATER` | Triac heater |
| `21` | `TEVOLVE_TRIAC_HEATER_WITH_ROTATED_SCREEN` | Triac heater |
| `1E` | `TEVOLVE_RELAY_HEATER` | Relay heater |
| `23` | `TEVOLVE_RELAY_HEATER_WITH_ROTATED_SCREEN` | Relay heater |
| `24` | `TEVOLVE_LOCAL_PIR_HEATER` | Heater |
| `27` | `TEVOLVE_PIR_HEATER` | Heater |
| `28` | `TEVOLVE_THERMOSTAT` | Thermostat |
| `31` | `TEVOLVE_WIFI_RELAY_HEATER_WITH_ROTATED_SCREEN` | WiFi heater |
| `34` | `TEVOLVE_WIFI_TRIAC_HEATER` | WiFi heater |
| `39` | `TEVOLVE_WIFI_HEATER` | WiFi heater |
| `4C` | `TEVOLVE_WIFI_TRIAC_HEATER_FOR_ATC` | WiFi heater |
| `1F` | `EVCONFORT_TRIAC_HEATER_WITH_REDUCED_SCREEN` | Triac heater |
| `38` | `EVCONFORT_WIFI_HEATER` | WiFi heater |
| `43` | `EVCONFORT_FIVE_BUTTONS_WIFI_HEATER` | WiFi heater |
| `4D` | `FERROLI_WIFI_TRIAC_HEATER_FOR_ATC` | WiFi heater |

### 3.2 Endpoint catalog

Response handling: "raw" = wire payload as-is (list extraction noted); "typed" = validated model under `raw_response=False`. All refs are `session.py` except E46 (old-lib method absent; documented and implemented here only). "WS side-effects" summarizes §5/§6 push behavior.

| # | Method & path | Body / params | Response handling | Live status | Ref |
|---|---|---|---|---|---|
| E1 | `GET /health_check` | — | raw passthrough, no auth | never probed live `**[unverified]**` (shape) | `[code session.py:330-336]` |
| E2 | `GET /version` | — | raw passthrough, no auth | API 2.37.3 seen on this host `[live 2026-09-26]` | `[code session.py:338-343]` |
| E3 | `POST /client/token` | form grant body (§2.1) | `Token` model; error mapping §1.4 | `[live 2026-10-04]` | `[code session.py:346-438]` |
| E4 | `GET /api/v2/devs` | — | raw wrapper `{devs, invited_to?}` for this client; the retired library flattened the optional `invited_to` into one list; typed `Devices` | wire shape `[live 2026-09-27]` | `[code session.py:733-746, models.py:255-259]` `[client.py:99-101]` |
| E5 | `GET /api/v2/grouped_devs` | — | raw: list of homes (`devs, extraData, id, name, owner`); typed `Homes` — typed mode DROPS wire `extraData` (no such field on `Home`) `[code models.py:262-269]` `[doc: pre-contract notes, 2.6.0 alignment round]` | wire shape `[live 2026-09-27]` | `[code session.py:748-755, 768-773]` |
| E6 | `GET /api/v2/groups/{home_id}/guest_users` | — | raw: `guest_users` list; typed `Guests` | never probed live `**[unverified]**` | `[code session.py:759-766, models.py:309-319]` |
| E7 | `GET /api/v2/devs/{dev_id}/mgr/nodes` | — | raw: `response["nodes"]`; typed `Nodes` | entries keyed `type`/`addr` (+`name`,`installed`,`lost`), NO `node_type` key `[live 2026-10-04]` | `[code session.py:775-787, models.py:221-235]` |
| E8 | `GET /api/v2/devs/{dev_id}/connected` | — | raw passthrough `{connected}`; typed `DeviceConnected` | `[live 2026-09-27/30]` | `[code session.py:790-798]` |
| E9 | `GET /api/v2/devs/{dev_id}/mgr/away_status` | — | raw passthrough `{away, enabled, forced}`; typed `DeviceAwayStatus` | `[live 2026-09-27]` | `[code session.py:800-808, models.py:237-242]` |
| E10 | `POST /api/v2/devs/{dev_id}/mgr/away_status` | `{away, enabled}`; `enabled` defaulted `true` client-side when omitted; the current 2.40.4 app also sends `forced: false` (the server overwrites it with `true`, §7 item 5) | None | writes verified `[live 2026-09-27]`; `{away}` alone also honored `[live 2026-09-27]`; app parity: both bundles send `{away, enabled}` with `enabled: true` defaulted, and the current app adds the server-owned `forced` key `[app mobile app.js@5156478, @17998851]` `[app helki.js@1231104]` | `[code session.py:810-827]` |
| E11 | `GET /api/v2/devs/{dev_id}/mgr/discovery` | — | raw only | `{"discovery": "off"}` `[live 2026-09-27]` | `[code session.py:829-835]` |
| E12 | `POST /api/v2/devs/{dev_id}/mgr/discovery` | pass-through, e.g. `{discovery: "on"\|"off", type?, addr?, limit?}` `[doc webapi-spec.md §2]` | None | `**[unverified]**` (never written live) | `[code session.py:837-851]` |
| E13 | `GET /api/v2/devs/{dev_id}/mgr/rtc/time` | — | raw only | `{d,h,m,n,s,w,y}`, `n=8` in September `[live 2026-09-27]` | `[code session.py:853-855]` |
| E14 | `POST /api/v2/devs/{dev_id}/name` | `{"name": …}` | None | `**[unverified]**` | `[code session.py:857-862]` |
| E15 | `DELETE /api/v2/devs/{dev_id}` | — | None | destructive; `**[unverified]**` | `[code session.py:864-866]` |
| E16 | `POST /api/v2/devs/{dev_id}/group` | pass-through | None | `**[unverified]**` | `[code session.py:868-878]` |
| E17 | `GET /api/v2/devs/{dev_id}/htr_system/power_limit` | — | `int(resp["power_limit"])`; pmo node variant reads `/pmo/{addr}/power` (key `power`) — asymmetry OPEN (§7) | surface exercised `[live 2026-09-29, integration round]`; GET shape not explicitly recorded in the diary — only the 0-case is explicitly open (§7) | `[code session.py:880-902]` |
| E18 | `POST /api/v2/devs/{dev_id}/[htr_system\|pmo/{addr}]/power_limit` | `{"power_limit": "<int as string>"}` — both this integration and the retired library post the stringified form `[writes.py:445-487]`; the vendor app posts the same via `powerLimit.toFixed(0)`, and its "unlimited" ships `"0"` as a string `[app mobile app.js@5159281, @5159154, @18936187]` | None | surface exercised `[live 2026-09-29, integration round]`; the box `"0"` clear is live-verified (`0 -> 4000 -> 0`, §7 item 3) `[live 2026-10-18]`; node-level `"0"` acceptance remains `**[unverified]**` (no pmo hardware) | `[code session.py:905-921]` |
| E19 | `GET /api/v2/devs/{dev_id}/{type}/{addr}/samples?start=&end=` | epoch seconds; params are `start`/`end`; defaults `start=now-3600, end=now+3600` — a 2-HOUR window `[code session.py:936-941]` | the rows arrive WRAPPED: `{"samples": [Sample{t,counter,temp} ...]}` (pmo: `PmoSample{t,counter,max,min}`; an accumulator row is `{t, counters: [int, ...], valley: bool, temp}`, §7 item 19); the client unwraps to the list `[client.py:148-173]` | `from`/`to` rejected 400 `[live 2026-10-04]` | `[code session.py:923-954, models.py:277-297]` |
| E20 | `GET .../{type}/{addr}/status` | — | raw or `NodeStatus` union | both payload families `[live 2026-09-26]` | `[code session.py:956-975, models.py:199-218]` |
| E21 | `POST .../{type}/{addr}/status` | pass-through (None-filtered); client-side `ValueError` if `stemp` without `units`; write semantics §5 | None | extensively `[live 2026-09-26/28]` | `[code session.py:977-991]` |
| E22 | `GET .../{type}/{addr}/setup` | — | raw or `NodeSetup` (`extra="allow"`) | fw-1.9 shape `[live 2026-09-27]` | `[code session.py:994-1007, models.py:62-118]` |
| E23 | `POST .../{type}/{addr}/setup` | FULL merged setup object (GET-merge-POST, read always raw) | None | round-trip (accept-only probes 2026-09-26; two watched writes with lagging REST readback 2026-10-04) `[live 2026-09-26/10-04]` | `[code session.py:1009-1031]` |
| E24 | `GET .../{type}/{addr}/version` | — | raw or `NodeVersion` `{hw_version, fw_version, uid, pid}` | `[live 2026-09-26]` (frame body) | `[code session.py:1034-1047, models.py:139-145]` |
| E25 | `POST .../{type}/{addr}/mode` | pass-through, e.g. `{mode, stemp, units}` | None | `**[unverified]**` on this dedicated endpoint (the body shape is live-proven via `/status`) | `[code session.py:1049-1065]` `[doc webapi-spec.md §3]` |
| E26 | `POST .../{type}/{addr}/lock` | `{"locked": bool}` | None | lock verified via `/status` `[live 2026-09-26]`; dedicated endpoint `**[unverified]**` | `[code session.py:1068-1083]` |
| E27 | `POST .../{type}/{addr}/boost` | `{active, temperature, units, time}` | None | `**[unverified]**` on this endpoint; boost-disabled units accept-but-ignore the `/status` variant `[live 2026-09-26]` | `[code session.py:1086-1103]` |
| E28 | `POST .../{type}/{addr}/prog_temps` | pass-through; app-shaped equivalent is the 4-key `/status` body `{ice_temp, eco_temp, comf_temp, units}` | None | dedicated endpoint `**[unverified]**`; the 4-key `/status` equivalent is accepted and echoed `[live 2026-10-20]` | `[code session.py:1106-1122]` `[doc webapi-spec.md §4 item 4]` |
| E29 | `GET .../{type}/{addr}/prog` | — | raw or `NodeProg`; returns `{prog, sync_status}` | `[live 2026-09-27]` | `[code session.py:1125-1137, models.py:126-137]` |
| E30 | `POST .../{type}/{addr}/prog` | `{"prog": <day-keyed object>}`; array shape → 400; GET-merge (day-level)-POST | None | `[live 2026-09-27]` | `[code session.py:1140-1181]` |
| E31 | `POST .../{type}/{addr}/name` | `{"name": …}` | None | `**[unverified]**` | `[code session.py:1183-1191]` |
| E32 | `POST .../{type}/{addr}/select` | pass-through | None | `**[unverified]**` | `[code session.py:1193-1205]` |
| E33 | `GET .../{type}/{addr}/power` | — | this client int-normalizes `{"power": ...}`; the retired library passed the payload through raw | `**[unverified]**` | `[code session.py:1207-1216]` `[client.py:176-195]` |
| E34 | `DELETE /api/v2/devs/{dev_id}/{type}/{addr}` | `{"purge": bool}` | None | destructive; `**[unverified]**` | `[code session.py:1218-1228]` |
| E35 | `GET /api/v2/groups/{id}/geo_data` | — | raw only | city/country/state/zip empty, `coarsePosition`, `tz_code` `[live 2026-09-27]` | `[code session.py:1230-1233]` |
| E36 | `POST /api/v2/groups/{id}/name` | `{"name": …}` | None | `**[unverified]**` | `[code session.py:1235-1240]` |
| E37 | `GET /api/v2/groups/{id}/extra_data` | — | raw only | **404** on the probed account (app stores `maxPowerProfiles` via POST; likely 404 until populated) `[live 2026-09-27]` | `[code session.py:1242-1244]` |
| E38 | `POST /api/v2/users/{uid}/invite` | `{email, groupid, confirmation_url}` (app builds `confirmation_url` as `<frontend>/invite-confirm/nserie<serial_id>`) | raw | `**[unverified]**` | `[code session.py:1246-1265]` |
| E39 | `DELETE /api/v2/users/{uid}/invite` | `{groupid, email}` | raw | `**[unverified]**` | `[code session.py:1268-1277]` |
| E40 | `POST /api/v2/users/{uid}/invite_confirmation` | `{pass, code}` — **unauthenticated** (`auth=False`) | raw | `**[unverified]**` | `[code session.py:1280-1290]` |
| E41 | `GET /api/notifications/v1/{gid}/presence/config` | — | raw only | `{"excluded_nodes": [{devid, nodes}]}` `[live 2026-09-27]` | `[code session.py:1293-1299]` |
| E42 | `POST /api/notifications/v1/{gid}/presence/config` | pass-through | raw | `**[unverified]**` | `[code session.py:1302-1311]` |
| E43 | `POST /api/notifications/v1/{gid}/test` | no body | raw | `**[unverified]**` | `[code session.py:1314-1321]` |
| E44 | `GET /api/v2/encrypted_wifi_credentials?ssid=&pass=` | wifi password in query (never logged) | raw only (`{"encrypted_pass": …}`; server-side AES-256-CBC PBE `[doc webapi-spec.md §2]`) | `**[unverified]**` | `[code session.py:1323-1337]` |
| E45 | `GET /api/location/v1/coordinates?country=&state=&city=&zip=` | — | raw only | `**[unverified]**` | `[code session.py:1339-1355]` |
| E46 | `GET /api/v2/devs/{dev_id}/dev_data` | — | raw passthrough (full device+nodes snapshot) | REST probed 2026-11-20 on two accounts: the body is the ws snapshot's `devData` object MINUS `connected` — that key reaches consumers through the ws `all_data` snapshot and through `/devs/{id}/connected` (E8) only, and `pmo_system` was present on the account that has a pmo node and absent on the boxless one `[live 2026-11-20]`. The payload was live-captured earlier through the ws `all_data` snapshot — top-level keys `away_status`/`connected`/`discovery`/`geoData`/`geo_data`/`htr_system`/`nodes`/`pmo_system`, node entries `type`/`addr`/`installed`/`lost`/`name` plus `status`/`setup`/`prog`/`version` slots, `htr_system` carrying the box power limit in TWO copies (§4.1, §5.3) `[live 2026-10-16, dedicated probe]` (previously `[live 2026-10-01]` nesting only) | `[doc webapi-spec.md §2]` |

Notes on catalog rows:

- E46 (`dev_data`) is NOT an old-lib `AsyncSmartboxSession` method — it exists in webapi-spec §2 and is implemented in this integration's protocol layer (`[client.py:119-121]`) for protocol-surface completeness; its `devData` nesting is the shape the ws `all_data` snapshot mirrors, which is what makes the row load-bearing.
- E4 flattening is load-bearing: raw mode merges the wire's optional `invited_to` list into one flat device list `[code session.py:737-745]`. On the boxless guest home the flat `devs` list was EMPTY and the unit appeared only under `invited_to` (home `owner: false`), while the grouped read served the home with its device — a consumer that reads the flat list alone sees nothing on an invited home `[live 2026-11-20]`.
- E19 default window (C1 resolution): the shipped code sends `start=now-3600 … end=now+3600` — a **2-hour** window `[code session.py:936-941]`. [superseded: "defaults: a one-hour window centred on the call time" (pre-contract notes, samples section) -> the doc's one-hour wording is arithmetically wrong for the shipped ±1 h defaults; the method's own docstring repeats the stale wording `[code session.py:932]`.]
- E23's read is ALWAYS raw, independent of `raw_response`: reading through the `extra="allow"` model would still drop every undeclared key from the re-posted dump and wipe them on the device; the retired library shared one call budget across GET+POST `[code session.py:1018-1031]`. This integration bounds each client call separately, so a GET-merge-POST op can take up to two call budgets.
- E30's input guard: a body without a `prog` dict (or with the array shape) raises client-side `ValueError` before any traffic `[code session.py:1155-1169]`; the merge is day-level and the POST body is always `{"prog": merged}` `[code session.py:1170-1181]`.
- E10 client-side defaulting: the library adds `enabled: true` when the caller omits it, matching the vendor app `[code session.py:819-823]` `[live 2026-09-27]` — the app's UI layers pass only `{away}` and the defaulting happens one layer below, in both bundles; `forced` is never written by either client `[app mobile app.js@5156478, @17998851]` `[app helki.js@1231104, @620289]`. [superseded: pre-contract notes header "POST: TODO untested" -> writes live-verified 2026-09-27; the header line is stale.]
- E13 `n` field: `8` in September — 0-indexed or another meaning; the integration assumes month = `n + 1` (§7) `[live 2026-09-27]`.
- E33/E17 asymmetry (pmo `power` GET vs `power_limit` POST) is mirrored in code with a comment flagging it `[code session.py:884-889]`.

### 3.3 websocket side-effects of REST writes

- Every accepted node write pushes transient frames for the affected node; details, shapes and the per-endpoint confirmation bundle are in §5.3 `[live 2026-09-26/10-04]`.
- Away/discovery/prog/node-list frames also appear unsolicited (idle cadence, cold-boot burst) — §6 `[live 2026-10-02]`.

### 3.4 Storage-heater telemetry fields

- Storage mode semantics are controller-class specific. Elnur (`14/20/25/2A`) uses `factory_options.ui_mode`: ECOMBI offers AUTO/MANUAL, CLASSIC_COMBINED offers MANUAL, and CLASSIC_ACCUMULATOR offers ON/OFF only, writing `target_charge_per` 100/0. Helki storage (`1C/2E`) filters AUTO/MANUAL by `setup.operational_mode` (`1` auto, `0` manual) `[app mobile 2.54.2 new_vendors.decoded.js@1554430]`. The app's boost deactivation is an exception that writes across that filter: it starts from API AUTO and overrides to API MANUAL when `operational_mode == 1` (`AUTO_OFF_AVAILABLE`) `[app mobile 2.54.2 new_vendors.decoded.js@1552367]`, while the mode picker exposes only AUTO/OFF/ON for that value `[app mobile 2.54.2 new_vendors.decoded.js@1554430]`. HA therefore offers HEAT on op=1 as well, so the wire state the app itself writes is representable; that is a deliberate superset of the picker. The neutral lists live in `smartbox.device.storage` (the adapter maps its intents to `HVACMode`); an unknown/`00` `acm` uses the HelkiStorage defaults, and an absent `operational_mode`/`ui_mode` does not infer AUTO (the app's filter compares the field directly), so those units get HEAT/OFF only. Water storage (`2A`) reads `setup.extra_nrg_conf.enabled`/`mode`: with extra energy enabled, `EXTRA_ENERGY_USAGE` removes AUTO and the other modes remove MANUAL, so HA offers HEAT/OFF in the former and AUTO/OFF in the latter (ON resolves to the remaining default mode); its programme temperatures are only ON/OFF_AUTO, so the integration withholds the generic heater schedule and programme-temperature numbers `[app mobile 2.54.2 new_vendors.decoded.js@5770842]`. Normal non-classic modes still use `/status`, while the decoded `/mode` op is kept for the assistant path.
- Storage boost is the Helki `1C/2E` `/boost` path, independent of `boost_config`: activation `{boost: true, boost_time, stemp, units}`, deactivation `{boost: false}` followed by `/mode` AUTO/MANUAL. Storage lock uses `/lock {"locked": 0|1|2}` in the app; acceptance on real storage hardware is still `**[unverified]**` (E26).
- `status.target_charge_per` is the storage heater's target charge, 0–100. `StorageHeaterController.getTargetChargePercentage()` reads it (default 0) and `getAssistantStatus().percentage` re-exposes it; in `CLASSIC_ACCUMULATOR` UI mode the app's ON writes 100 and OFF writes 0 `[app mobile 2.54.2 new_vendors.decoded.js@3491097]` `[app mobile 2.54.2 new_vendors.decoded.js@3491877]` `[app mobile 2.54.2 new_vendors.decoded.js@3677395]`.
- The Elnur storage controller's `setTargetChargePercentage(value)` accepts 0–100 in 10 % steps and requires the `heating_pecentage_control_available` capability (the wire capability string is misspelled in the app) `[app mobile 2.54.2 new_vendors.decoded.js@3686394]`. The 2.54.2 bundle has no call site for it — the identifier occurs only in the string table and the method definition — so the target has no editor in this build `[app mobile 2.54.2 new_vendors.decoded.js@1569774]`.
- Current charge: `status.current_charge_per` is the base storage controller's value `[app mobile 2.54.2 new_vendors.decoded.js@3492483]`; the Elnur controller reads `status.charge_level` and multiplies by 20; its `getAvailableCapabilities()` always includes `HEATING_STEPPED_CHARGE` for this controller, while the conditional gate is UI-side `getActiveCapabilities()` `[app mobile 2.54.2 new_vendors.decoded.js@3664943]` `[app mobile 2.54.2 new_vendors.decoded.js@3676019]`. The app's dispatch is controller-based: HelkiStorage (`1C`/`2E`) reads `current_charge_per`; `ElnurStorageHeaterController` (`14`) has `HEATING_STEPPED_CHARGE` in its available capabilities and multiplies `charge_level` by 20; the E2 subclass (`20`/`25`) removes that capability and reports raw `charge_level`; water storage (`2A`) also uses raw `charge_level`. The integration matches this after adding `2E` to the `current_charge_per` branch.
- Water storage (product code `2A`) replaces the storage `CHARGING` indicator: `ACTIVE` is `charging && charge_level != 50`, `WATER_READY` is `charge_level == 50`, and `WATER_FULLY_READY` is `charge_level == 100` `[app mobile 2.54.2 new_vendors.decoded.js@5773929]`. The integration exposes Water Ready and Water Fully Ready binaries instead of Charging for that product.
- `status.charging` is the storage controller's `CHARGING` status indicator `[app mobile 2.54.2 new_vendors.decoded.js@3664676]`.
- Power source is controller-specific: `StorageHeaterController.getPower()` returns `status.power` even while the unit is idle; `HaverlandHtrModController.getPower()` returns the configured `setup.power` while active/heating; `ElectricHeaterController.getPower()` requires `sync_status: "ok"` and `active: true` `[app mobile 2.54.2 new_vendors.decoded.js@3489731]` `[app mobile 2.54.2 new_vendors.decoded.js@5808007]` `[app mobile 2.54.2 new_vendors.decoded.js@3636346]`.
- `setup.charging_conf` holds the charging schedule: `slot_1`/`slot_2` as `{start, end}` minute-of-day objects plus `active_days` as seven booleans `[app mobile 2.54.2 new_vendors.decoded.js@1539757]` `[app mobile 2.54.2 new_vendors.decoded.js@1543214]`. `active_days` is Sunday-first: the app's `DAY_0_SHORT` is `"Su"` and its non-English display order rotates the same indices to Monday-first `[app mobile 2.54.2 new_app.decoded.js@1494750]` `[app mobile 2.54.2 new_app.decoded.js@178479]`. The picker steps in 10 minutes (`acmTimeResolution`) `[app mobile 2.54.2 new_app.decoded.js@1011135]`. `end` 0 is midnight (the app renders 1440 as `00:00`), `start > end` crosses midnight, and `start == end` disables the slot `[app mobile 2.54.2 new_app.decoded.js@1053054]` `[app mobile 2.54.2 new_app.decoded.js@1054448]` `[app mobile 2.54.2 new_app.decoded.js@892199]`.
- The E2 storage controllers (20/25) filter AUTO from the available modes when `setup.extra_nrg_conf.enabled` is true and its `mode` is not `CONFORT` (2): `ElnurE2StorageHeaterController.getAvailableUIHeatingModes` returns the inherited list without AUTO `[app mobile 2.54.2 new_vendors.decoded.js@1142337]`, and the enum is `EXTRA_ENERGY_USAGE` 0, `COMBINED` 1, `CONFORT` 2 `[app mobile 2.54.2 new_vendors.decoded.js@5766426]`. Water storage has its own filter (below). The library applies this in `available_modes`/`write_for`, so a configuration the app would not offer cannot be written.
- `status.window_open` is a base heater status indicator `[app mobile 2.54.2 new_vendors.decoded.js@3567744]`; the integration exposes it as the diagnostic **Window Detection** binary, not as a physical window contact. PIR presence is `status.french_rule_pir_state`, exposed only when `setup.extra_options.pir_enabled` is true; `pir_presence` is the indicator enum member name, not a status field `[app mobile 2.54.2 new_vendors.decoded.js@3500291]` `[app mobile 2.54.2 new_vendors.decoded.js@3500052]`.

### 3.5 Thermostat (`thm`) controller shape

- `status` used by `ThermostatController`: `sync_status`, `mode`, `stemp`, `mtemp`, `active`, `units`; `installed` is a node-entry flag read by `checkNodeReadyOrFail`, not a status key `[app mobile 2.54.2 new_vendors.decoded.js@3740994]`. Its `getStatusIndicators()` returns only `ACTIVE`, or `COOLING` for `setup.type == "cold"` `[app mobile 2.54.2 new_vendors.decoded.js@3733058]`.
- `status.low_batt` is the thermostat battery flag: the shell's `lowBatteryState` getter reads it from the node status and defaults to false when absent, and its only UI use is the thermostat card's flashing battery icon `[app mobile 2.54.2 new_app.decoded.js@899898]` `[app mobile 2.54.2 new_app.decoded.js@883242]`. No controller reads it, and the heater cards do not render it.
- `setup`: `type` (`heat`/`cold`), `control_mode`, `anti_grippage`, `away_offset`; the app's settings list is `name`, `temperature_units`, `load_type`, `control_mode`, `anti_grippage`, `away_offset` `[app mobile 2.54.2 new_vendors.decoded.js@3737609]`.
- UI heating modes are `AUTO`, `MODIFIED_AUTO`, `OFF`, `ON`; the base available list drops `MODIFIED_AUTO`, and `ON` resolves to `getDefaultMode()`, which is `AUTO` when AUTO is available. `MANUAL` is not offered for a thermostat `[app mobile 2.54.2 new_vendors.decoded.js@3737609]` `[app mobile 2.54.2 new_vendors.decoded.js@4642051]` `[app mobile 2.54.2 new_vendors.decoded.js@4653716]`.
- `setStatus` deletes `mode: modified_auto` before POSTing, so a thermostat setpoint body is `{stemp, units}` `[app mobile 2.54.2 new_vendors.decoded.js@3734957]`.
- The thermostat programme is a per-day array of `{m, t}` temperature objects; `getSchedule()` reads `prog.prog[i].m/.t`, and `setSchedule()` posts `{prog, units}`. It is not the heater int-profile array `[app mobile 2.54.2 new_vendors.decoded.js@3730742]` `[app mobile 2.54.2 new_vendors.decoded.js@3732609]`. This shape is for the thermostat factory branch (`04`/`09`/`0F`). Code `28` dispatches to `TevolveThermostatController`, which extends the Tevolve triac heater and exposes `ELECTRIC_HEATER_PROG_AVAILABLE`; it uses the heater int-slot programme and heater surfaces (boost, EASY/RUNBACK, programme temperatures), not the thermostat schedule `[app mobile 2.54.2 new_vendors.decoded.js@1570160, @1579734]`. With `setup.extra_options.sensor_mode == NO_PROBES` (3), the Tevolve thermostat removes `HEATING_TEMPERATURE_CONTROL_AVAILABLE` and exposes only ON/OFF_AUTO programme temperatures; the integration hides its target-temperature feature and profile-temperature numbers in that state.
- `setup.anti_grippage` is the string `"on"`/`"off"`; the app setting writes that key `[app mobile 2.54.2 new_vendors.decoded.js@3729467]`.
- The range is 7–35 °C: `MAX_GLOBAL_TEMP_IN_CELSIUS` is 35 `[app mobile 2.54.2 new_vendors.decoded.js@3728918]`, and the 7 °C floor comes from the web app's `minTempCelsius: 7` clamp (`docs/webapi-spec.md` §3.3). A live `thm` read remains `**[unverified]**`.

---

## 4. Websocket

Two transports are maintained in parallel by the vendor `[live 2026-10-01/02]`. `socket_io` is NOT deprecated (mobile apps built 2025-08 still use it) `[live 2026-10-01]`; the web apps use `ws_user` `[live 2026-10-01]`.

### 4.1 `ws_user` — the transport (per-user plain WebSocket)

- One connection serves every device of the account; per-device fan-out happens in the dispatchers `[code ws_user.py:1-19, 460-508]`.
- URL: `wss://{host}:443/api/v2/ws_user?token=<enc>&user_id=<id>`, with `user_id` extracted from the access token's JWT `userId` claim `[code ws_user.py:50, 138-151]`. A non-JWT token raises `WsUserUnsupportedError`; an empty token raises `SmartboxError` (caller error, retryable) `[code ws_user.py:109-133]`.
- Client→server frames: exactly ONE type, `{"event": "all_data"}`, sent on open and re-sent 1 s-debounced on every server `{"event": "should_sync"}` (debounce configurable, default 1.0 s) `[code ws_user.py:174-181, 388-389, 525-541]` `[live 2026-10-01/02]`. There is NO app-level ping in this protocol `[code ws_user.py:8-19]` `[live 2026-10-01]`.
- Server→client frames `[code ws_user.py:434-508]`:
  - `all_data` → `{"event": "all_data", "data": [homes]}` full snapshot (homes → devs → `devData`; ~8 kB for 1 home / 1 dev / 5 nodes); one `dev_data` callback per registered device `[live 2026-10-01]` `[code ws_user.py:460-479]`. Live `devData` shape (captured 2026-10-16): top-level keys `away_status`, `connected`, `discovery`, `geoData`, `geo_data`, `htr_system`, `nodes`, `pmo_system` — note the BARE `discovery` (the update path is `/mgr/discovery`) and the duplicate geo spellings; `nodes[]` entries carry `type`/`addr`/`installed`/ `lost`/`name` plus `status`/`setup`/`prog`/`version` slots (the version slot exists in snapshots even though fw-1.9 pushes no version FRAME — §5.3); `htr_system` carries the box power limit in TWO copies, `power_limit.power_limit` as a digit string (the vendor web app's read) and `setup.power_limit` as an int, and the object copy comes through EMPTY when the limit is cleared `[live 2026-10-16, dedicated probe]`.
  - `update` → `{"event": "update", "devid": …, "data": {"path", "body"}}` — the path/body pair lives UNDER the `data` key `[live 2026-10-02, correcting an earlier flat recording]` `[code ws_user.py:481-508]`. Payload fields are identical to socket_io (`sync_status`, `stemp`, `mtemp`, `power`, …) `[live 2026-10-01]`. Handler path set covers `/mgr/away_status`, `/connected`, `/geo_data`, `/mgr/nodes`, `/htr_system/power_limit`, and node `status`/`prog`/`setup` `[live 2026-10-01/02]`.
  - `should_sync` → schedules a debounced `all_data` re-request `[code ws_user.py:454-456]`. Never observed live despite two outage variants (§7) `[live 2026-10-02]`.
  - Unknown events and malformed frames are logged and ignored; they never crash the receive loop `[code ws_user.py:436-459]`.
- Liveness:
  - The server/proxy sends a WebSocket PING every ~30 s; aiohttp's autoping answers (and swallows the PONGs) `[live 2026-10-01 tcpdump]` `[code ws_user.py:404-410]`.
  - Client heartbeat default 60 s (PONG window = half of it); this is the ONLY client-side dead-path detection — data silence of many minutes is NORMAL (max observed 789 s) and must never be treated as failure `[code ws_user.py:66, 190-196]` `[live 2026-10-01/02]`. This integration's transport adds a bound well clear of that cadence: zero frames for 45 min tears the socket down, so a wedged-but-alive socket still routes its outage through the ordinary close -> grace-window path `[ws_user.py:88-91]`.
  - The all_data reply is due right after the open (the server answers the client's own frame); a socket that delivers nothing within the connect window is a wedged session, not a quiet one. This integration tears it down and retries after 10 s (the vendor apps use base-delay + 5 s, 6 s on the first attempt) `[doc api-ws-user.md §1.1]` `[ws_user.py:81-85]`.
  - Handshake bounded by a 10 s timeout (aiohttp's `ClientWSTimeout` cannot bound a handshake; the library wraps it); teardown bounded 5 s `[code ws_user.py:65-76]`.
- Token-expiry lifecycle: the socket is cut by the server EXACTLY at the token's `exp` — close code 1006 (abnormal: TCP dropped, no close frame, no HTTP error) → mandatory fresh-token reconnect every 4 h, ~4 s outage `[live 2026-10-02]`.
- Reconnect doctrine `[code ws_user.py:54-87, 543-685]`:
  - Per cycle: 10 connect attempts, backoff `1.0 · 2^n` ±20 % jitter, cap 30 s; a served cycle → token refresh + go around (the steady state is one refresh per 4 h).
  - [superseded: vendor web-app reference "max 5 attempts or 30 s total" -> the library deliberately keeps trying longer (10 attempts/loop); document as LIB policy, not vendor parity `[code ws_user.py:67]` `[live 2026-10-01 web-app decode]`.]
  - Vendor reference, now pinned to the app's exact constants: base delay `1 s · 2^n`, ONE-SIDED `+0–20 %` jitter (the app ADDS 0–20 %, never subtracts — the lib's ±20 % is slightly wider), cap 30 s, budget `5 attempts OR 30 s total since the first failure` → permanent failure (`/lostConnection` redirect) `[app helki.js@1209876, @1199977]`. The mobile build instead uses socket_io defaults (cap 5 s, ±50 % jitter) and gives up after 3 attempts `[app mobile app.js@17607196]`. The lib's 10-attempt loop remains a deliberate LIB divergence, now precisely scoped.
  - A successful cycle shorter than 30 s ("accept-then-kill") escalates the between-cycle pause via the same backoff `[code ws_user.py:79-82, 566-573]`.
  - Handshake 404/410 → `WsUserUnsupportedError` immediately `[code ws_user.py:54, 356-363]`.
  - Handshake 401/403 on a token the session trusts → token invalidation + FORCED refresh (retrying the same URL would no-op under clock skew) `[code ws_user.py:364-370, 576-578]`.
  - 3 consecutive fresh-token rejections → `WsUserUnsupportedError` (the retired library's supervisor fell back to socket_io; this integration re-probes in place and has no fallback, §4.2) `[code ws_user.py:85-87, 634-652]`.
  - Everything else (transport errors, 5xx, timeouts) is retried `[code ws_user.py:371-378]`.
- Capability probe `check_ws_user_support`: one-shot handshake on a FRESH token, 10 s timeout; 401/403/404/410 → `WsUserUnsupportedError`; anything else (5xx, 429, …) → `APIUnavailableError` (retryable, never "unsupported") `[code ws_user.py:720-760]`.
- Close-code 1006 ambiguity: 1006 surfaces BOTH for server cuts at `exp` and for locally-detected deaths (heartbeat); timestamps and context are the discriminator `[live 2026-10-02]`.
- Idle push cadence varies between runs (steady ~44 s in one run, burst-then-silence in another) `[live 2026-10-01/02]`.

### 4.2 Transport policy (no fallback)

- `ws_user` is the ONLY transport this integration speaks; `socket_io` is retired (the vendor apps use `ws_user` too). There is no fallback to switch to and no fallback trigger to evaluate: a deterministic rejection (404/410 on the handshake, or three consecutive fresh-token 401/403s) is terminal, and setup surfaces it as unsupported `[doc api-ws-user.md §1.1]` `[ws_user.py:289-296, 355-378]`.
- The retired transport's facts and the old fallback doctrine stay on the record in Appendix A; nothing there is implemented or planned.

## 5. Write semantics

### 5.1 Key-count rule

- Multi-key status POSTs of MIXED categories are silently PARTIALLY applied: a restore POST `{"locked": false, "stemp": "20.0", "units": "C", "mode": "off"}` stored `locked` and `mode` but silently dropped `stemp` (no error, no clamp); the identical value POSTed alone was stored `[live 2026-09-26]`.
- Refined rule: multi-key bodies are accepted WITHIN a category — the vendor app ships them in production: `{stemp, units}`, `{mode, stemp, units}` (manual-with-temp and the `modified_auto` engage body), `{ice_temp, eco_temp, comf_temp, units}` `[doc webapi-spec.md §4]`, and the htr_mod setpoint `{on, mode, selected_temp, comfort_temp, eco_offset}` (no `units`) `[app mobile 2.41.0 vendors.js@5048320]` `[app mobile 2.54.2 chunk-vendors.f809b153.js@5729165]`; the 3-key modified_auto body is confirmed live by a one-off A/B probe on this hardware, shape per webapi-spec §4 item 2 `[live 2026-09-28]`.
- Integration policy: one control key per POST (plus the `units` companion, which is not a control key), sequential writes — the safe pattern; do not change without a live A/B probe. The htr_mod setpoint is the deliberate exception because the vendor app itself sends that body. The library itself is a pass-through (None-filter only) and enforces NO key-count rule `[code session.py:977-991]` `[doc webapi-spec.md §4]`.
- Device-level exception: away writes are the app-shaped body `{away, enabled: true, forced: false}` (and `{away}` alone is honored) `[live 2026-09-27]` `[code session.py:810-827]`; the current app sends that shape, and the server overwrites `forced` with `true` `[live 2026-10-18]` `[app mobile app.js@5156478, @17998851]` `[app helki.js@1231104]`.

### 5.2 Value handling

- A setpoint write is accepted while the heater is OFF: on `Spálňa`/htr:5 (0402 family), `POST /status {"stemp": "18.0", "units": "C"}` with `mode: "off"` stored `stemp: "18.0"`, left `mode: "off"`, `active: false` and `mtemp` unchanged, then restored to `"20.5"` `[live 2026-11-22]`. The vendor app requires the heater to be ON before its +/- control is usable; the server itself does not reject the write.

- Thermostat samples carry `totalConsumption`/`e2Consumption` and are included in the home-level energy aggregation `[app mobile 2.54.2 new_vendors.decoded.js@4819156]`, but the app's per-node energy view sets `showsKwh=false` for THM and shows `usageTime`/hours instead of kWh `[app mobile 2.54.2 new_app.decoded.js@1191418]` `[app mobile 2.54.2 new_app.decoded.js@1193348]`. The integration therefore creates no per-THM power or total-consumption entity; old `{node_id}_power`/`{node_id}_total_consumption` registry entries are pruned at setup. Power is also absent from the controller (no `getPower`/`status.power`).

### 5.3 Confirmation frames

- Node status write: a full-body status echo frame arrives ~0.3–0.5 s after the POST on a live node (the ABSENCE of this frame is the only fast signal that a write did not land — §6) `[live 2026-09-26/30]`.
- Setup write (fw-1.9 node): a ~0.2 s burst of THREE frames — `/mgr/nodes` (full node-list re-push) + `/{type}/{addr}/status` + `/{type}/{addr}/setup` (the full merged setup body is the per-write confirmation); the immediate REST readback still lags (returned the pre-write value) `[live 2026-10-04]`. [superseded: "setup writes additionally push a full-setup body frame and a `/htr/<addr>/version` frame" (2026-09-26) -> NO `/version` frame was pushed for either watched setup write on fw-1.9; treat the version frame as absent/conditional on fw-1.9 and never rely on it `[live 2026-10-04]`.]
- Setup write on the boxless guest home (fw 1.8.0, invited user): only the `/htr/<addr>/setup` frame was pushed — no `/mgr/nodes` burst — and it arrived ~1.3 s after the POST had returned (two writes: 40 written, 0 restored) `[live 2026-11-20]`. The echo still lands well inside the 6 s confirmation window.
- Box power-limit write (`POST /htr_system/power_limit`): the server pushes BOTH `/htr_system/power_limit` (`{"power_limit": "<int as string>"}` — the value as posted, string form) and `/htr_system/setup` (the htr_system setup object: `{"power_limit": <int>, "refresh_period": 30, "extra_nrg_conf": {"enabled": true}}`, an INT) `[live 2026-10-16, one write 3600 -> 3000 with a socket listening]`. Snapshot copies stay in step across the write (`'3600'`/`3600` -> `'3000'`/`3000`); clearing the limit (unlimited) serves an EMPTY object copy with `setup.power_limit: 0` `[live 2026-10-16]`. Consequence for consumers merging frames into one slot: the two paths disagree on TYPE, so the value must be coerced to int (the old lib's `int()` does; this integration now does — §9). The "0" CLEAR is asymmetric on the push side: it delivered ONLY the `/htr_system/setup` frame (int `0`), with no `/htr_system/power_limit` frame at all `[live 2026-10-18]`, so a consumer that ignores the setup frame misses an externally-cleared limit — this integration routes its `power_limit` key into the same slot (§7 item 15).
- Prog write: one `/htr/<addr>/prog` frame arrives ~1 s after the POST carrying `{"sync_status": "ok", "prog": {"0": [...], ..., "6": [...]}}` — the WHOLE week, not just the changed day — and the immediate readback lags (the pre-write value) `[live 2026-10-18, one slot changed on an fw-1.9 htr node then restored]`.
- Node-list frame (`/mgr/nodes`): each entry is `{addr, type, name, installed, lost, uid, level, parent}`; the `lost` boolean is the untrustworthy one (§6.3) `[live 2026-10-18]`.
- Transient lost→ok sandwich (`/status {sync_status:"lost"}` then `/prog` lost then ok) was observed 2026-09-26 but did NOT reproduce in the 2026-09-30 session; treat it as defensive knowledge, not a guaranteed sequence `[live 2026-09-26/30]`.
- Device-wide node-list updates arrive as a frame whose body is `{"nodes": [...]}` with NO `path` `[live 2026-09-26]`; on ws_user the snapshot arrives inside `all_data` instead `[live 2026-10-01]`.

### 5.4 GET-merge-POST and settle lag

- `/setup` requires the WHOLE configuration to be re-posted; the library GETs, day-blind merges the caller's partial update, and POSTs the complete object (read always raw — §3.2 note) `[code session.py:1009-1031]`.
- `/prog` likewise: GET current, day-level merge, POST the complete day-keyed object; the ARRAY shape is rejected with 400 `[code session.py:1140-1181]` `[live 2026-09-27]`.
- `/prog` day keys are `"0".."6"` = Sunday..Saturday, not Monday..Sunday. Confirmed 2026-11-10 by comparing the schedule attribute of `sensor.obyvak_obyvacka_schedule` with the vendor Android app: key `"0"` matched the app's Sunday column `[live 2026-11-10]`.
- A day array is 24 or 48 slots. The HJM guest home stored 48 half-hourly slots while the app's 30-minute editor was active (`day 2`, Tuesday 05:30 = `0`/ICE), and a return to the 60-minute editor merged the display down to 24 slots without changing the node's capability. `factory_options.prog_resolution: 0` did not prevent the 48-slot program. Consumers must key the slot duration off the array length (1440 / len), and must not assume 24 `[live 2026-11-22]`.
- Settle-lag rule: a successful write is NOT immediately visible in GET — the server reads device state with a lag of several seconds (5 s verified sufficient); GET-verify flows that skip the sleep misreport writes as no-ops `[live 2026-09-27]`.
- Schedule drift caveat: heaters in `auto` change their own `stemp` (schedule setpoint transitions) between snapshot and verification — snapshot-diff restore checks must treat that as expected drift `[live 2026-09-26]`.
- The full-setup body carries a `power` field the server REWRITES from the last status value on every setup write: a body posted with `power: 1991.0` was echoed and served back as `1986.5`, the status reading at that moment `[live 2026-11-20]`. A read-merge-repost therefore cannot push a stale measurement back, and consumers must not treat `setup.power` as configuration.

### 5.5 Boost and `extra_options` on boost-disabled units

- Tevolve controllers expose EASY and RUNBACK as persistent status flags:
  `POST /status {"easy": bool}` and `{"runback": bool}`. The app disables EASY
  while RUNBACK is on and vice versa; `status.easy` / `status.runback` drive
  the corresponding UI modes. The Tevolve WiFi class also filters BOOST,
  RUNBACK and SETBACK from its UI mode list when the bootstrap/endpoint does
  not support them `[app 2.54.2 new_vendors.decoded.js@2231316,
  @2259709]`.

- BOOST availability follows each controller's mode list, not one
  `boost_config` test: the Helki/Lhz heater chain adds it only with config 2
  (LhzRelay 36 unconditionally), Tevolve triac/towel-rail add it
  unconditionally, Tevolve PIR (27) removes it, Tevolve WiFi/ATC follow their
  endpoint/config, Haverland legacy (17) always offers it, Haverland WiFi
  (1D/47) removes it only when `factory_options.boost_config` is
  `MAX_TEMP_FIXED_TIME` (1), and the factory-default ElectricHeater codes
  (`0402`, `1E`, `24`, `43`, `0D`, `18`) expose none `[app 2.54.2
  new_vendors.decoded.js@5059000, @3843019, @2248132, @5343476]`. EASY and
  RUNBACK are separate lists: PIR keeps EASY and drops RUNBACK, ATC drops EASY
  and keeps RUNBACK, and the factory-default codes have neither `[app 2.54.2
  new_vendors.decoded.js@3499388, @3644420]`. The integration withholds RUNBACK
  on a full-boost WiFi/ATC node because its activation carries a temperature
  and duration the switch does not model. An absent `boost_config` is not
  fixed-time: the base `getSupportedBoostMode()` falls back to
  SET_TEMP_AND_TIME when BOOST is available, so Helki storage, LhzRelay 36 and
  Tevolve triac/towel-rail/28 keep editable temperature/duration defaults.
  Explicit FULL (3) is also editable on Helki storage and the triac family.
  The Haverland fixed-time families (17 and 1D/47) never expose parameters,
  because `HaverlandWifiHeaterController` inherits
  `getSupportedBoostMode() == MAX_TEMP_FIXED_TIME` and `setBoost` calls
  `setBasicBoost(true)` for every config `[app 2.54.2
  new_vendors.decoded.js@5339701, @5343476]`. The integration builds the boost
  parameter numbers for config 2, config 3 on storage/triac, or that
  config-less default.
- Activation dispatch follows the controller override: the base
  `activateBoost` uses `/boost_mode` only when `boost_config` is FULL and
  otherwise writes `extra_options.boost_temp/boost_time` then `status.boost`;
  `TevolveTriacHeaterController.activateBoost` always uses the legacy
  setup+status path, including at FULL, and the Haverland fixed-time families
  take `/boost_mode` only at FULL (3), with the fixed-time mode. Storage
  always uses `/boost`. The integration forces the legacy flag for the triac
  family at every config and for Haverland below FULL; at FULL the Haverland
  path posts the fixed-time mode `[app mobile 2.54.2 new_vendors.decoded.js@3601900,
  @2249400, @5339701]`. The same endpoint decision gates the full-boost presets
  (RUNBACK/ADVANCED_SLOT) and the active-boost temperature editor, and requires
  the controller's BOOST mode to be available; storage's config 3 keeps
  `/boost` and never exposes `/boost_mode`. The BOOST preset handler also
  checks controller availability, so a direct preset call on a node without
  BOOST is refused.
- Factory boost-disabled units (no `boost_config` in `factory_options`): `POST /status {"boost": …}` is accepted without error but ignored (status keeps `boost: false`, both boost keys present and zero) `[live 2026-09-26]`. On boost-capable units the setup's `extra_options.version >= 2` switches `boost_temp`, `default_runback_temp`, `max_runback_temp` and `setback_temp` to Celsius; older nodes keep them in the node's display unit `[doc api-ws-user.md §2.3]`.
- `extra_options` setup writes are silently dropped on these units (partial and out-of-range objects both accepted, never stored, no error, no clamp); the replace-vs-merge question on boost-capable units remains open (§7) `[live 2026-09-26]`.
- The app omits `extra_options` from every setup POST for the older ElectricHeater product codes `01/02/07/08/0D/10/11/13/16/18/1B/1E/1F` (`shouldOmitSetupExtraOptions`) and posts the rest of the setup. The integration's `WriteApi` now strips it when the store's version PID is in that list; a direct CLI write with no store keeps the caller's body. The Elnur `0B`/`0C` products are absent from the app list and keep the object `[app 2.54.2 new_vendors.decoded.js@3582810]`.
- App-driven boost on a `boost_config: 2` unit (HJM 0132, fw 1.8.0, started 13:49 CET / 12:49 UTC with `boost_time: 60`) posted the legacy setup+status pair: `extra_options.boost_temp` moved from `"23.5"` to `"20.0"`, `boost_time` stayed `60`, and the status frame carried `boost: true`, `active: true`, `duty/act_duty: 100`, `boost_mode: "temp_and_time"`, `stemp: "20.0"`, `mode: "off"`, `boost_end_min: 889` and `boost_end_day: 0` `[live 2026-11-22]`.
- `boost_end_min` is the HOME-LOCAL minute of day, not UTC: 889 is 14:49, one hour after the 13:49 CET start (the default 60-minute boost). `boost_end_day` is the day offset (0 = today). The integration now interprets it in `dt_util.now()` (HA local) and applies `boost_end_day`; HA and the home are normally in the same zone `[live 2026-11-22]`.
- The HJM app's boost duration control reached 24:00 (1440 minutes) by repeated `+` from 1:00; the base `ElectricHeaterController` ceiling is 1440, with a 60-minute minimum/default `[live 2026-11-22, app screen]` `[app 2.54.2 new_vendors.decoded.js@3566709]`. The integration now uses per-controller caps (base 1440, Tevolve ATC 120, Climastar WiFi triac 300).

### 5.6 Writes to unreachable nodes / offline boxes

- Writes to unreachable nodes are silently accepted (~50–60 ms, HTTP OK, no device ack); only the absence of the confirming ok frame (~0.3–0.5 s on a live node) signals the drop `[live 2026-09-30]`.
- Writes to an offline BOX: the cloud attempts a live relay and does NOT queue — writes are applied-or-errored, never deferred; the vendor app first shows apparent success then errors within seconds; no residue is left `[live 2026-10-02]`.

### 5.7 Node duty cap (`extra_options.user_duty_limit`)

- `setup.extra_options.user_duty_limit` is a percent cap on the heater's commanded duty, and it is the field behind the vendor app's "power limit factor" select: the app's three choices wrote `0` (OFF), `25` (LST) and `50` (Baby Care) on a unit whose `factory_options` carried `lst_value: 25` and `bbc_value: 50` `[live 2026-11-20, boxless guest home, product 0132, fw 1.8.0, htr:2]`. `lst_available`/`bbc_available` are the flags behind the three choices. `factory_options.duty_limit` (0 on this unit) did not move; it is a separate field and not what the app writes.
- The app gates the control on firmware not under 1.4, an available `HEATER_POWER` setting and a non-zero `setup.power` `[app mobile 2.54.2 new_vendors.decoded.js@5343054]`; it labels the stored value OFF / Baby Care / LST from the unit's own `bbc_value`/`lst_value` and shows anything else as a percentage `[app mobile 2.54.2 new_app.decoded.js@1042758]`; and it writes `extra_options.user_duty_limit` above firmware 1.5, `factory_options.duty_limit` below it `[app mobile 2.54.2 new_vendors.decoded.js@3582023]`. This integration exposes only the modern field, so a legacy node that keeps the factor in `factory_options.duty_limit` builds no entity.
- The cap is consumed by the NEXT start; it is never applied retroactively: written while the heater ran at `duty 100`, the stored value stayed inert and the running heater kept 100; the later starts came up at `duty`/`act_duty` 50, 25, 60 and 35, each matching the value stored at that moment `[live 2026-11-20]`.
- The wire accepts any value in 0–100, not just the presets: 40, 60 and 35 were each stored, echoed in the pushed setup frame, and honoured at the next start — `duty`/`act_duty` came up at 60 and at 35 `[live 2026-11-20]`. The vendor app's heater-card label falls back to `<value> %` for a stored value outside Off/LST/Baby Care (60 displayed as `60 %`), while its settings select has no matching option and renders blank; the integration reports such a value as unknown `[live 2026-11-20, user report from the app screen; app label formatter new_app.decoded.js@1043156]`.
- With the heater idle the write stores the value silently: the setup frame is the only push, no status frame follows, and nothing runs `[live 2026-11-20]`.
- The app also hides `max_stemp_limit` for one manufacturer-scoped product: `NodeControllerManager.MAX_STEMP_LIMIT_UNSUPPORTED_PIDS` is `['0A' + '01']` (manufacturer Tevolve, product code `01`), and `applyProductSpecificOpts` uses `MAX_STEMP_LIMIT_UNSUPPORTED_PIDS.includes(version.pid.toUpperCase())` — exact array membership, so a longer pid such as `0A01xxxx` does not match — then clears `maxStempLimitAvailable` only when the caller left it unset `[app 2.54.2 new_vendors.decoded.js@4856060]`. This is a full-PID rule, not a product-code rule; the product-code table alone cannot express it.
- The Lhz controller family hard-codes additional choices `80` and `90`; when its `lst_value` is not 70 it also adds 70, then appends the available Baby Care and LST factory values. The app labels those raw percentages as `"80 %"`/`"90 %"` `[app 2.54.2 new_vendors.decoded.js@1231775]`. Other families keep the Off/LST/Baby Care set. The integration follows this split for the LhzHeater chain 33/35/38/36; the factory-default 0D/18 do not inherit it. The `USER_DUTY_LIMIT` setting is listed by the HelkiHeater/Lhz chain, so the integration gates the select on that family. The max-stemp setting is owned by `ElectricHeaterController`; storage controllers remove it, Tevolve triac/WiFi/towel-rail set `hasMaxStempLimit=false`, Haverland WiFi 1D sets it false, and the B variant 47 sets it true `[app 2.54.2 new_vendors.decoded.js@3572844, @3644077, @5341113, @4755063]`.

---

### 5.8 Controller settings surfaces (2.54.2)

- The Elnur storage UI-mode setting is per product, not one three-value list:
  `ElnurStorageHeaterController` (14) accepts `CLASSIC_COMBINED` (1) and
  `CLASSIC_ACCUMULATOR` (2); the E2 controllers (20/25) accept `ECOMBI` (0) and
  `CLASSIC_COMBINED` (1); the water controller (2A) omits the setting from its
  `getSettings` list `[app 2.54.2 new_vendors.decoded.js@3669801, @1144436,
  @5775782]`. The setting itself is only available while the unit is already in
  a classic mode, so an ECOMBI unit has no UI-mode row to switch from
  `[app 2.54.2 new_vendors.decoded.js@3669801]`. Water storage also omits
  away-offset, window-detection and true-radiant from its settings list, and
  Elnur storage hides true-radiant/window-detection in the two classic modes
  `[app 2.54.2 new_vendors.decoded.js@3669801, @5775782]`.
- `TevolveThermostatController` (28) with
  `setup.extra_options.sensor_mode == NO_PROBES` (3) switches to timer
  operation and hides the probe-dependent settings: temperature control,
  `CONTROL_MODE`, `AWAY_OFFSET`, `TRUE_RADIANT_ENABLED`,
  `WINDOW_DETECTION_ENABLED`, `EASY_MODE_ENABLED` and `RUNBACK_MODE_ENABLED`
  `[app 2.54.2 new_vendors.decoded.js@1582717, @1595073]`. Its programme catalogue
  becomes `[OFF, ON]` and it filters `BOOST` from the available modes
  `[app 2.54.2 new_vendors.decoded.js@1570276, @1582717]`. The integration
  therefore withholds the boost/EASY/RUNBACK entities, the away/control-mode,
  window and true-radiant settings, the programme-temperature numbers and the
  generic heater schedule sensor on that surface; the ON/OFF timer programme is
  not modelled.
- The Tevolve thermostat's room setpoint keeps the triac 7-35 °C range; its
  floor-probe temperature is a separate 20-45 °C setting and does not alter the
  room bounds `[app 2.54.2 new_vendors.decoded.js@1570276]`.

---

## 6. Operational behavior

Dynamics only — nothing here contradicts §3–§5; where a number came from a live round the date is the evidence pointer (§8).

### 6.1 Push latency and artifacts

- Every state change surfaces as a FULL-body status frame regardless of source (no deltas) `[live 2026-09-26/10-04]`.
- REST-driven changes echo on the socket in ~2 s; device-local changes (heater's own panel) relay in ~5–10 s `[live 2026-10-04]`. This does NOT contradict §5.3's ~0.3–0.5 s write-echo figure: the two measurements come from different rounds/transports (2026-09-26/30 socket_io-era own-client POSTs vs the 2026-10-04 ws_user session driven by a third-party REST client) — treat write-echo latency as transport/context-dependent, not a single constant `[live 2026-09-26/30, 2026-10-04]`.
- `presence: true` fires when someone touches the panel (backlight) and a `presence: false` frame follows ~10 s later — panel-UI artifacts, not heating state `[live 2026-10-04]`.
- No server-side coalescing: distinct states arrive as distinct frames `[live 2026-10-04]`.
- Write echo frames arrive with full status bodies including the new value; unrelated auto heaters push their own frames plus a `/mgr/nodes` frame when their schedules tick `[live 2026-09-26]`.

### 6.2 Redundant frames

- Tolerate redundant state frames: a replug produces TWO `/connected` true events (22 s apart cold, 9 s warm) `[live 2026-10-02]`.
- The cold-boot burst carries `/mgr/nodes` ×5 (byte-identical; one copy per node-count — cause unknown) `[live 2026-10-02]`.
- Duplicate `update` bodies for the same logical change can arrive from independent causes (the change itself vs separate measurement/UI deltas such as `mtemp` ticks) `[live 2026-10-04]`.
- An away write republishes the node status even when nothing in it changed: each `/mgr/away_status` toggle was followed by a `/htr/<addr>/status` frame with a byte-equal body (four toggles concentrated in two pairs, heater idle) `[live 2026-11-20]`. A status frame therefore carries no away signal.

### 6.3 Node reachability (node power loss, box up)

- Device-level `/devs/{id}/connected` stays `true` — node loss is ONLY visible via node-level `sync_status: "lost"` on `/status` and `/setup`; there is no per-node connected endpoint `[live 2026-09-30]`.
- Offline node `/status` GET eventually returns a BARE `{"sync_status": "lost"}` (no mode/temps/power keys); `/setup` keeps the full field set with the lost marker; `mgr/nodes`' per-node `lost` boolean stayed `false` — do not trust it `[live 2026-09-30]`.
- Server-side flip latency: with zero traffic, last-known `ok` status served for 3.5+ min; bare-lost in place ~11 min later. Under a 7 s write cadence the first bare-lost push came ~67 s in, a second ~95 s after that (pushes DO reach connected sockets once the server concludes the node is lost). An IDLE node's flip IS pushed proactively: on a stable ws_user socket with zero traffic the server pushed the bare-lost frame (power cut → Unavailable, flip ~6m43s — one sample inside the ~4–11 min window, which is NOT a constant; recovery ~5 s on clean power-on) `[live 2026-10-03]` — evidence is the 2.6.0 soak TEST B (2026-10-03); this summary is the durable record.
- Vendor app flags a node unreachable ~5–6 s after an unconfirmed command — a client-side timeout, not an API answer `[live 2026-09-30]`.
- Recovery: a full fresh ok status frame arrives ~5 s after power-on with zero commands; boot frames first carry garbage (`error_code: 5`, `mtemp: -2764.8`, `duty: 106`, `ice_temp: "2.5"`) before settling within seconds — availability consumers should not render the garbage as real state `[live 2026-09-30]`.

### 6.4 Box outage cycle

- Mains-off: `/connected` flips false after 49 s; internet-only cut: 44 s — detection is session-driven, indifferent to failure mode; the socket keeps pushing last-known data in the meantime, so `/connected` is the only offline signal `[live 2026-10-02]`.
- Replug → `/connected` true in 6 s (mains) / 14 s (internet-only), plus a redundant second true `[live 2026-10-02]`.
- Cold boot burst (box re-registering its devices): `/pmo_system`, `/htr_system/setup`, `/mgr/nodes` ×5, `/mgr/discovery`, `/mgr/away_status`, then per-node status + `/prog` frames `[live 2026-10-02]`.
- Warm reconnect: NO burst; a physical node change made during the outage reached the server ~67 s after reconnect via the box's ORDINARY reporting cadence — divergence self-corrects silently `[live 2026-10-02]`.
- 424 Failed Dependency is the answer when the backend holds the box as NOT live and the read is box-local and ON-DEMAND: `/mgr/rtc/time` returned 424 on every drift-sensor poll (12x at a 5 min cadence, 08:06-09:01 local) for the whole window in which `connected` was false (08:03:40-09:02:49) — the clock is read live from the box and the cloud will not serve a stale one `[live 2026-10-17]`.
- `connected: false` does NOT imply the box is down or its data stale: in that same window all five htr nodes kept reporting changing status (temperatures and `hvac_action`, including a heating-to-idle flip) and recovery showed NO re-sync burst, so the data path stayed up while the backend's liveness session for the box did not. Read the "`/connected` is the only offline signal" point above as "the only signal", never as "false means no fresh data" — a consumer that gates box-level availability on the flag would mark box entities unavailable for the whole window while node entities stay healthy (the pre-2.7 posture; this integration now keeps box controls usable through it and reports the flag as the connectivity sensor's STATE, `adapter.py:190-210`) `[live 2026-10-17, SmartControl 5xhtr fw 1.9 plus its HA history export]`.

### 6.5 Client-side outage and liveness

- Client-side outages are INVISIBLE to the socket: `receive()` blocks on the half-open TCP connection, zero client-side errors (Wi-Fi down does not close TCP) `[live 2026-10-02]`.
- Server-side close is UNKNOWABLE from an offline client (every close packet is lost; a lost RST is never retransmitted): "proxy alive and quiet" and "proxy closed, state dropped" are indistinguishable — treat every post-outage socket as DEAD until proven alive `[live 2026-10-02]`.
- Writes double as liveness checks: a write/ping into a dead path fails fast (RST) or times out via retransmission `[live 2026-10-02]`.
- Address change = permanent half-open: the socket stayed ESTABLISHED but a reliably-pushed state change never arrived `[live 2026-10-02]`.
- Client-initiated WS PINGs ARE answered; with `autoping=True` (default) aiohttp swallows the PONGs, so liveness tests must use the `heartbeat` parameter `[live 2026-10-02]` `[code ws_user.py:66, 404-410]`.
- Heartbeat=60 verified end to end: dead-path detection ≤ ~60 s, 13 retriable failures through a 2 min outage at the ~3.5 s backoff cadence (zero hangs, zero fatal exits), recovery 2 s after network return `[live 2026-10-02]`.
- socket_io's 85 s silence-abort cycle recurs every 3–7 min while a half-open TCP lingers; ws_user instead rides multi-minute silence and pays one ~4 s outage per 4 h token expiry `[live 2026-10-01/02]`.

---

### 6.6 Consumer frame rules

The rules this integration's store applies to pushed frames (the vendor apps' routing table is `api-ws-user.md` §1.4):

- a frame is applied only when path and body are both truthy: an empty body neither replaces a stored slot nor counts as reachability evidence `[doc api-ws-user.md §1.4]`. A snapshot's empty slot body follows the same rule: `status: {}` / `setup: {}` is “no body yet”, not a value and not a reachability signal `[merging.py:165-176]`;
- frames update what the snapshot created: a frame for an unknown dev id is dropped (both apps do), and a slot frame for a node the node list does not hold is dropped, so a removed node cannot be resurrected by a late frame `[doc api-ws-user.md §1.3/§1.4]`;
- an `installed: false` node-list entry is an empty slot: it is not a node, adds no store node, and no per-node read is attempted. The empty slot's status read returned 404 on the reporting Haverland box `[live 2026-11-16, user report on issue #50]`; setup/version behavior on an empty slot remains `**[unverified]**`;
- the box power-limit frame body is either an object carrying `power_limit` or the bare digit string; both forms land in the one numeric slot `[doc api-ws-user.md §1.4]`;
- a malformed frame is logged and dropped so the pump survives (the vendor apps close the socket and reconnect on a parse error — deliberate divergence: dropping keeps a single bad frame from costing a reconnect) `[doc api-ws-user.md §1.3]`.

---

## 7. Open questions

Answered questions stay here with their resolution and date for the record; an item is open only while it has no resolution.

1. **RTC December indexing** — `mgr/rtc/time`'s `n` was `8` in September (0-indexed or other meaning); the integration assumes month = `n + 1`. Whether `n` wraps to 0 (0-indexed) or 12 (1-indexed) in December is unknown — check live or capture a December frame `[live 2026-09-27/29]`. Vendor clients cannot adjudicate it: the mobile app's `getTime` exists but has ZERO callers, the response is never parsed, and the web bundle never touches `rtc` at all — the endpoint is app-ORPHANED, so the December check stays live-only `[app mobile app.js@18000389, @5162245]` (the current apps' endpoint inventory still lists it `[doc api-ws-user.md §2.2]`; no caller was found).
2. **°F grid ("F-grid")** — ANSWERED (live, 2026-10-18, one htr node switched to F): the vendor app's reading was right — °F is an INTEGER 1 °F grid, the node's `setup.units` IS the scale (the F node serves `units: "F"` in status and setup), and the wire body must keep the app's one decimal: a whole-degree body WITHOUT it (`"70"`) is rejected with a 400, while `"69.0"` is accepted and echoed back. The SERVER quantizes a fractional F body to whole degrees half-up (raw `"69.5"` → `"70.0"`). This integration's `format_setpoint` quantizes °F to whole degrees (half up) and formats `.1f`, matching the server grid `[values.py:108-125]` `[live 2026-10-18]` `[app mobile app.js@506258, @2485204, @485218]` `[doc api-ws-user.md §1.4]`. The sub-question is ANSWERED too: the `max_stemp_limit` FIELD is Celsius regardless of the node's unit — the app's own vendor-app screenshot shows 75.0 °F for the F node's stored `24.0`, and the app's setting model converts both ways (`convertTempFromNodeUnitsToCelsius` on write, the reverse on read, decoded 2026-10-18), while the offset follows the node's unit (its 3.0 °C-era value became 5.0 on the F node). The entity layer converts the limit for display and back on write `[values.py:80-108]` `[adapter.py:429-447]` `[writes.py:345-389]`; live write probe: 76.0 °F -> `"24.5"` on the wire, 75.0 °F -> `"24.0"` `[live 2026-10-18]`.
3. **`power_limit: 0` POST** — ANSWERED (2026-10-18): a CLIENT-posted `{"power_limit": "0"}` string body is accepted and behaves exactly like the app's own clearing flow — the readback serves `'0'`, the snapshot's object copy comes back EMPTY and `setup.power_limit: 0` — and the server pushes only the `/htr_system/setup` frame (int `0`) for it `[live 2026-10-18, live probe: 0 -> 4000 -> 0 on a live box, both directions read back through REST and the ws push]` `[live 2026-10-16, dedicated probe]`. Consumers treat `power_limit == 0` as "no limit" (user-confirmed semantics; web UI caps the editable range at 60000) `[app mobile app.js@18936187]` `[doc api-ws-user.md §2.3]`.
4. **pmo power-limit asymmetry** — ANSWERED and implemented: both endpoints are the vendor's own design — the current apps GET `/pmo/{addr}/power` (key `power`) for the reading and POST `/{type}/{addr}/power_limit` (stringified body, `0` = no limit) for the node's own limit `[doc api-ws-user.md §2.2/§2.3]`. The node power-limit write is therefore schema-keyed (`setup.power_limit`), while the `/power` read is pmo-only; the CLI reflects that split (`node-power` is pmo-only, `set-node-power-limit` needs the setup key). This integration now exposes the node limit as a number + no-limit switch pair reading the setup's `power_limit` key (`writes.set_node_power_limit`, live-verified on the BOX endpoint only); the node-level acceptance of `"0"` and the endpoint's echo stay `**[unverified]**` (no pmo hardware in any live round) `[live 2026-09-27]` `[code session.py:884-902, 904-921]`.
5. **Away `enabled`/`forced` server-side meaning** — `enabled` is tolerated-but-not-required (RESOLVED 2026-09-27 for the write path); who may set `enabled`/`forced` and what `enabled: false` does to an away write remain unconfirmed; the current app hardcodes `enabled: true` / `forced: false` on every away write `[doc api-ws-user.md §2.3]`, so nothing in the apps exercises the other values. ANSWERED for `forced` (2026-10-18): the SERVER sets `forced: true` on an away write and the posted `false` does not stick (live: POSTed `{away: true, enabled: true, forced: false}` → the pushed `/mgr/away_status` frame carried `forced: true`, and the same on the away-off write; this also explains the unexplained false→true flip seen 2026-09-27/29 — no write had to carry it, the server derives it) `[live 2026-10-18]`. What `forced: true` MEANS remains unprobed. Observed 2026-11-20 on the boxless guest home: `forced` was already `true` before the first toggle of the session and stayed `true` through the away-off push, so it does not track the `away` state `[live 2026-11-20]`.
6. **`should_sync` trigger** — never fired in two outage variants (mains cycle and internet-only cut); "box reconnect" and "warm server reconnect" hypotheses both rejected; trigger unknown. The debounce behavior is code-verified only — 1000 ms in both current apps too `[doc api-ws-user.md §1.2/§5]` `[live 2026-10-02]` `[code ws_user.py:525-541]`.
7. **htr_mod write semantics** — htr_mod nodes carry `on`/`selected_temp`/`comfort_temp`/`eco_offset` in status `[code models.py:180-186]`. In manual/preset paths the integration treats `selected_temp` as the active profile; in AUTO it derives the target from the current programme slot (`0=OFF,1=ICE,2=ECO,3=COMFORT`), matching the app's `getAutoModeTemp`, and AUTO uses the global 5-28 °C bounds. A manual `selected_temp: "off"` is OFF and a setpoint from it enters eco/comfort instead of raising. The `getPower` source is `setup.power` while active/heating, and the settings list only offers name, priority and heater power. The current mobile app's htr_mod manager sends `{on: true, mode: "manual", selected_temp, comfort_temp, eco_offset}` for a setpoint and `{on, mode, selected_temp}` for a profile, so the integration's mixed-category body is app-shaped `[app mobile 2.41.0 vendors.js@5035003, @5048320]`; the current 2.54.2 builder still omits `units` `[app mobile 2.54.2 chunk-vendors.f809b153.js@5729165]`. A 2026-11-17 Haverland report did not apply a body with `units` carrying a temperature outside the active profile's range, while a setpoint whose temperature sat inside the range was applied in the same round; the range is the leading explanation, but the two variables are not isolated and `units` is neither confirmed nor excluded on this route `**[unverified]**` `[live 2026-11-17, issue #66]`. Live acceptance of the exact no-`units` body with an in-range value remains `**[unverified]**`; the apps route `htr_mod` status frames but exclude it from the version-frame regex `[doc api-ws-user.md §1.4]`.

   **htr_mod setpoint contract (2.54.2).** `HaverlandHtrModController` hardcodes the family's grid and bounds: `MIN_COMFORT 18 °C`, `MAX_COMFORT 28 °C`, `MAX_ECO 18 °C`, `MIN_ECO_OFFSET 4 °C`, `MAX_ECO_OFFSET 8 °C` (so `MIN_ECO` derives to 10 °C), `FALLBACK_ICE 5 °C`, `SET_TEMP_STEP`/`PROG_TEMP_STEP 1 °C` `[app mobile 2.54.2 chunk-vendors.f809b153.js@5707070]`. The app dispatches this controller on the node's product code, `version.pid.substring(2,4).toUpperCase()`: `0A` (heater module) and `FF` (legacy heater) are the two codes that select it, and it is the only `htr_mod` controller in the bundle `[app mobile 2.54.2 chunk-vendors.f809b153.js@4688397, @4487398]`. There is no firmware or app-version gate — the app keys the limits on the product code alone. The setpoint builder sends `mode: "manual"` on every setpoint (a setpoint leaves the programme), `selected_temp: "comfort"` above 18 °C with `comfort_temp` as requested; at or below 18 °C it sends `selected_temp: "eco"`, reusing the stored comfort when the gap lands in 4-8 °C and otherwise walking `comfort_temp` in 1 °C steps inside 18-28 °C until it does `[app mobile 2.54.2 new_vendors.decoded.js@5803935]`. The integration advertises the union of both ranges while either profile is active, so the 18 °C boundary is crossable from either one. The UI consumes the same constants as its slider bounds and step: comfort 18-28, eco 10-18, ice fixed to the slot's `ice_temp`, programme temps at the same 1 °C step `[app mobile 2.54.2 new_vendors.decoded.js@5798033, @5783462]`. No status or setup field carries the comfort floor or the step, so the constants are the only source. `units` is not foreign to the family: the app's programme-temp writer posts `{comfort_temp, eco_offset, units}` with whole-degree values `[app mobile 2.54.2 new_vendors.decoded.js@5790684]`; only the setpoint body above omits it.

   Two integration-side normalizations follow from that model. The stored `comfort_temp` is snapped to a whole degree before the offset walk, because the app compares the raw value for exact equality and posts a body without a profile when it does not fit `[app mobile 2.54.2 new_vendors.decoded.js@5796200]`. A target outside the active profile's range is reported at the nearest bound; the app clamps its eco display the same way (`getTempValue(ECO)` returns `min(MAX_ECO_TEMP, comfort_temp - eco_offset)`) `[app mobile 2.54.2 new_vendors.decoded.js@5796100]`.
8. **PMO cadence and samples** — the pmo sample shape `{t, counter, max, min}` differs from htr `{t, counter, temp}` `[code models.py:277-297]`, but pmo push cadence, `pmo_system` frames and power-limit behavior have no live observations (no pmo hardware in any live round); the current apps' inventory adds no cadence facts `[doc api-ws-user.md §2.2]` `**[unverified]**`.
9. **Refresh-token server-side TTL** — never captured; the rotation facts (§2.2) make it irrelevant for availability `[live 2026-09-26]` (rotation confirmed by the current apps `[doc api-ws-user.md §1.7]`).
10. **`extra_options` replace-vs-merge on boost-capable units** — unobservable on the boost-disabled family probed so far `[live 2026-09-26]`. The current apps drive such units through `/boost_mode` (2.40.4) and legacy `/boost` `[doc api-ws-user.md §2.3]`; the integration now follows the app's dispatch (1 fixed-time and 2 set-temp-and-time use the status/setup path; 3 FULL posts `/boost_mode`), with live acceptance still open for a full-boost unit.
11. **Backend-vs-physical divergence during rapid probing** — cause unknown; needs a `tools/live_probe.py ws` capture of a recurrence (setpoint off by 2–3° with the user at the heater; phantom `active=True duty=100` for minutes) `[live 2026-09-28]`.
12. **Idle lost-flip push** — RESOLVED 2026-10-03: the server pushes the bare-lost frame proactively to a connected ws_user socket even for an IDLE node (power cut → Unavailable, flip ~6m43s — a sample inside the ~4–11 min window, a window not a constant; recovery ~5 s) `[live 2026-10-03]`. Evidence: the 2.6.0 soak TEST B; this summary is the durable record.
13. **Per-host ws_user deployment** — ANSWERED at app level for most: the 2026-11-15 audit found eleven SPA apps on `users-api.helki.com`, Elnur on `api-elnur.helki.com`, and EHC on legacy `socket.io` at `api-ehc.helki.com`. ws_user is live-verified on `users-api` (SmartControl, 2026-10-18); Elnur is app-evidenced and route-probed, not live-account tested; other tenant deployments remain unproven on the wire.
14. **socket_io wire URL shape** — NOT PURSUED: the transport is retired for this integration and the vendor apps (Appendix A.1). The engineio-dialed URL was never pinned by packet capture; only the library-side construction is known `**[unverified]**`.
15. **`/htr_system/setup` frames** — DECIDED (2026-10-18): route the frame's `power_limit` key into the same numeric slot as `/htr_system/power_limit` (the evidence: the "0" clear pushes ONLY this frame, §5.3), ignoring the frame's other keys (`refresh_period`, `extra_nrg_conf` — unread); a keyless frame is a no-op. The old lib coerced it into the same slot too. Implemented in this integration's device-frame routing `[live 2026-10-18]`.
16. **`max_stemp_limit` sentinel and 4-key prog-temp body** — ANSWERED (2026-10-20, live, htr fw 1.9): POSTing `max_stemp_limit: "0.0"` was accepted, echoed in the pushed setup frame, and persisted in the REST readback; restoring `"24.0"` did the same. The app-shaped 4-key `/status` body `{ice_temp, eco_temp, comf_temp, units}` was accepted, echoed all three temperatures, and left `stemp`/`mode` unchanged; the original values were restored. The sentinel's enforcement was then tested on the same node: with the ceiling at `"24.0"`, `stemp: "24.5"` was clamped to `"24.0"`; with the ceiling at `"0.0"`, the same `"24.5"` was accepted and echoed. The dedicated `/prog_temps` endpoint itself remains unprobed `[live 2026-10-20]`.
17. **Boxless box power-limit capability** — PARTIAL (2026-11-17): a single-heater HJM parent without a Smartbox rejected `GET`/`POST /htr_system/power_limit` with 404 and showed no box power limit in the engine (`power_limit: null`); the reporter's app shows no BOX power-limit control for it (the per-heater duty cap on the same screen is a different feature, §5.7) `[live 2026-11-17, user-supplied diagnostics; 2026-11-20 snapshot]`. The 2.54.2 app has no boxless distinction: it reads `htr_system.power_limit.power_limit` with a `0` default and writes the endpoint `[app mobile 2.54.2 new_app.decoded.js@523628, new_vendors.decoded.js@1597425]`. The integration probes `GET /htr_system/power_limit` during discovery. A rejected probe or a 2xx without a `power_limit` key is confirmed against snapshot-shaped `dev_data`; absence is trusted only when that payload has `nodes` and `away_status` (the keys REST does serve — §3.2 E46) and its `htr_system` carries no power-limit configuration. A boxless HJM serves `"htr_system": {"power_limit": {}}`, a non-empty placeholder with no `setup.power_limit`, so the check tests the limit rather than the subtree's emptiness `[live 2026-11-20, user-supplied snapshot on issue #56]`. A box whose limit was cleared keeps `setup.power_limit: 0` and therefore the entities (§5.3). Auth and transport failures keep the existing surface. The boxed GET is live-verified `[live 2026-09-29]`; the boxless GET returns 404 `[live 2026-11-18, user-supplied probe output]`. The same boxless home served `/dev_data` over REST WITHOUT `connected` on 2026-11-20 — the ws snapshot carries the key, REST serves it only at `/devs/{id}/connected` (§3.2 E46) — so a confirmation that requires `connected` in the REST payload can never trust absence there; `nodes` and `away_status` are the keys REST does serve `[live 2026-11-20]`.
18. **Status `locked` type by node family** — ANSWERED (2026-11-17): an htr heater served `locked: false` (JSON boolean) while an acm accumulator in the same account served `locked: 0` (integer) `[live 2026-11-17, user report on issue #54]`. The `1` value is inferred from the integer field; no `locked: 1` sample has been recorded. The integration normalizes both to a boolean for the `locked` attribute; the write field remains a boolean (E26).

19. **Accumulator sample shape (`counters`, `valley`)** — PARTIAL (2026-11-18): an acm accumulator's sample row is `{t, counters: [int, ...], valley: bool, temp}`, while a heater's is `{t, counter: int, temp}` `[live 2026-11-17, user report on issue #54]`. Raw counters are Wh; the 2.54.2 app reads `counters[0]` as `TOTAL_CONSUMPTION` and `counters[1]` as `E2_CONSUMPTION` (its consumption series is `counters[0] - counters[1]`), never reads further entries, and negates both `counter` and every entry when `setup.reverse` is set `[app mobile 2.54.2 new_vendors.decoded.js@3548649, @3554031, @1623933]`. `valley` marks off-peak samples and drives the app's load/valley split `[app mobile 2.54.2 new_vendors.decoded.js@3555199]`; the app never reads `counter_offsets`. The integration's total-consumption sensor reads `counters[0]` and keeps the scalar `counter` for heaters, reporting Wh. Whether a multi-entry `counters[0]` is the tariff total (rather than one tariff) and whether accumulators set `reverse` remain `**[unverified]**`; the four Pico/Valle/Llano/Solar columns in the vendor CSV are a server-side export, not the raw list.

20. **`acm` lock surface and write payload** — PARTIAL (2026-11-20, static decode of `net.evsa.haverland` 2.54.2 plus a maintainer report): the SmartControl app shows no lock control on the maintainer's htr, while the integration's child-lock switch works on it `[live 2026-11-20, maintainer report]`. The app has two separate UI gates and neither covers those families. The temperature-screen button renders only when `HEATING_LOCK_AVAILABLE` is active `[app mobile 2.54.2 new_app.decoded.js@1156367]`, and only the Haverland HTR and htr_mod controllers advertise that capability `[new_vendors.decoded.js@5339541, @5789895]`; the Elnur and Helki storage lists and the Helki/Lhz heater chain do not `[@3676019, @1549843, @5061985]`. The controller settings list still carries `keyboard_locked` and `super_locked` `[@3569662]`, available for a storage heater while it is installed and not lost `[@3681001, E2 variant @1142896]`, but the settings screen renders the keyboard row only for EvConfort clients (`appConfig.clientName === "evconfort"`) `[new_app.decoded.js@1000962, @1483259]` and has no super-lock row at all, so other brands, including Elnur and Lhz/SmartControl, show no lock UI on these families. Values are read from `status.locked` against `UNBLOCKED = 0`, `LOCALLY_BLOCKED = 1`, `REMOTELY_BLOCKED = 2` `[@5769387]`: keyboard lock is `1`, super lock is `2`. The app writes `{locked: 1}` / `{locked: 0}` for keyboard lock on storage heaters `[@1534968, @3490028]` and `{locked: 2}` / `{locked: 0}` for super lock `[@4647607]`, while the htr quick-lock path posts a boolean `[@4647542, @4647796]` (an htr with `super_lock_available` uses `1`/`0` `[@5063588]`). The integration's `ChildLockSwitch` is created for `acm` (ACM is in `HEATER_NODE_TYPES`) and posts a boolean, and it works on the SmartControl htr `[live 2026-11-20, maintainer report]`; whether an `acm` accepts that boolean is `**[unverified]**` — the recorded acm status is the integer `0` (item 18) and no acm lock write has been observed. Because exposure is a brand/UI decision and `locked` is a device fact (item 18), the entity stays gated on node type rather than on the app UI. Live 2026-11-22 on the HJM guest home (pid `0132`, fw 1.8.0): `POST /status {"locked": 1}` echoed `1`, `{"locked": 2}` echoed `2`, and `{"locked": 0}` cleared either; `{"locked": true}` returned HTTP success but left `locked: 0`, so the boolean form is a silent no-op on that controller. The HJM app renders the same lock icon at `1` and `2` and has no child-lock row, so it distinguishes locked from unlocked but not local from remote; the wire enum is the contract. The integration now exposes the three states as one Off/Local lock/Remote lock select on integer-lock families and keeps the boolean switch for the base ElectricHeater/Haverland path `[live 2026-11-22]`. The shape follows the controller payload: Helki/Lhz heater nodes take the integer select only when `factory_options.super_lock_available` is set (the app's `getKeyboardLockPayload`), Haverland `1D` switches at fw 1.7, and storage, WiFi/ATC and code `47` write integers unconditionally.

21. **Node duty cap (`extra_options.user_duty_limit`)** — SUFFICIENT FOR PURPOSE (2026-11-20): the field's meaning, the app's preset mapping, the acceptance of arbitrary 0–100 values and the apply-at-next-start rule are live-confirmed (§5.7) `[live 2026-11-20]`. Three behaviours are deliberately not probed because no consumer depends on them: the firmware's lowest honoured value (an entity mirroring the app writes only 0/25/50), the value's persistence across a node power cycle (the integration renders whatever `setup` serves after reconnect, as the app does), and the cap's behaviour under boost (the setting is consulted at a start, and parity with the app is the goal). A future free-number entity would need all three before it could claim enforcement. `factory_options.duty_limit` (0 on the probed unit) is a separate field whose meaning is unprobed.

---

## 8. Evidence appendix

One line per observation: date, what, where the detail came from. The raw diary is not shipped; each line carries the durable claim.

- E1 (auth capture): web-app devtools / HTTPS-proxy recipes for the Basic credential, `x-serialid`, `x-referer` — the capture recipe is not shipped.
- E2 (2026-09-26, live, SmartControl account, 5×htr fw 1.9): payload family, boost-disabled semantics, `extra_options` probes, ws side-effects, T1–T3 + multi-key finding, echo frames — the pre-contract notes "Observed behaviour (2026-09-26)"; raw fixtures in `tests/fixtures/live/`.
- E3 (2026-09-26, live): password change does not revoke tokens; stale-password reauth fails fast — the pre-contract notes "Auth behaviour observations".
- E4 (2026-09-27, live): prog GET/POST shapes + 400-on-array + settle-lag rule; away `{away}`-alone honored; setup model widening; management-surface reads (discovery, rtc `n=8`, geo_data, extra_data 404, presence config); error-mapping hardening; pmo asymmetry flagged — the pre-contract notes "2.6.0 alignment round".
- E5 (2026-09-28, live, one-off A/B probes): A/B setpoint-in-auto probe — stored AND driving; 0 s follow-up applies; `modified_auto` explicit-only, no self-revert ≤60 s; 0.5 °C floor quantization; OPEN backend divergence — the pre-contract notes "Setpoint-in-auto A/B probe".
- E6 (2026-09-29, integration round): open questions routed (RTC month, power_limit 0, away enabled/forced) — the pre-contract notes "Box-device round".
- E7 (2026-09-30, live): node-reachability probes — `/connected` stays true; bare-lost status; stale-ok ≥3.5 min / flip ~11 min; 67 s/95 s pushes under cadence; silent write acceptance (~50–60 ms); no-op writes push nothing; recovery ~5 s + garbage boot frames — the pre-contract notes "Node reachability".
- E8 (2026-10-01, live + app decodes): engineio 85 s abort math to the ms (HA log); ws_user protocol from the web app (6 s all_data watchdog); tcpdump proxy PING every 30.000 s; Android `com.technotherm.lhzapp` 1.417.1 (2025-08) on socket_io EIO=3 with the 20 s app ping — the pre-contract notes "Transports" + "Observation log".
- E9 (2026-10-01/02, live, dedicated probe): ws_user frames (single `all_data`, nested `update` payload, handler path set); idle cadence variability; 4 h idle run (133 updates, max gap 448.9 s, zero closes); token lifecycle (JWT claims, exp-validated handshake, cut at exp with close 1006 ~4 s outage, rotating + reusable refresh tokens, byte-identical parallel logins); client-side outage invisibility + half-open model; heartbeat=60 full Wi-Fi-toggle cycle (13 retriable failures, recovery 2 s); reseller support matrix — the pre-contract notes "Transports" sections.
- E10 (2026-10-02, live, three outage variants): box outage cycle (49 s/44 s detection, double announce, cold-burst composition, warm reconnect silent self-correction ~67 s); offline-box write semantics (applied-or-errored, never queued); `should_sync` never fired — the pre-contract notes "Box outage + write semantics".
- E11 (2026-10-04, live probe round): token path + `username` field + `start`/`end` corrections; node-entry key shape; setup-write 3-frame burst with NO version frame; case C off-mode setpoint; push-latency and presence-artifact block — the parked edits in the pre-contract notes (left in place there; normative content absorbed here).
- E12 (2026-10-03, live, ws_user soak): idle-node power cut on a stable ws_user socket — server pushes the bare-lost frame proactively (flip ~6m43s, window not constant; recovery ~5 s) — from the 2.6.0 soak's own capture, which is not mirrored in the pre-contract notes; this summary is the record.
- E13 (2026-10-14, vendor-app review, read-only): third-implementation calibration against the decoded Helki SPA — calibration 3/3 passed (token POST `/client/token`, form-encoded `username` key; samples `start`/`end` epoch seconds; `x-serialid` on every call incl. the token POST, `x-referer` where set), then app-side pins: setpoint quantization is validate-and-reject (not pre-rounding), `power_limit` string body with `"0"` as the shipped unlimited, °F integer grid with offset-snapping converters and node-units writes, away `{away, enabled: true, forced: false}` (the server owns `forced`), and the exact ws_user reconnect constants — sources: the decoded web build (`app.helki.com.app.611c74a6`), the decoded mobile build, `webapi-spec.md`; no app-vs-`[live]` contradictions found. CAVEAT (lazy chunks): the web bundle's API-manager/node layer sits in lazy chunks absent from the single-file web decode — mobile-build excerpts fill those gaps, and every excerpt is flagged per source bundle; all excerpts were bounded-window extracted from the single-line bundles.
- E14 (2026-10-16, live, SmartControl account, 5×htr fw 1.9, `dedicated probe`): `devData` snapshot shape captured — top-level keys `away_status`/`connected`/`discovery`/`geoData`/ `geo_data`/`htr_system`/`nodes`/`pmo_system`; `nodes[]` entries with `type`/`addr`/`installed`/`lost`/`name` + `status`/`setup`/`prog`/ `version` slots and no per-node power key on the htr family; the box power limit in TWO copies (`power_limit.power_limit` digit string, `setup.power_limit` int) whose object copy EMPTIES when the limit is cleared; plus a controlled write (3600 -> 3000 from the vendor app with a socket listening) showing the server pushes BOTH `/htr_system/setup` (int) and `/htr_system/power_limit` (string). No `/mgr/nodes` frame in the 5 min window (idle cadence only). Closes E46's shape gap, adjudicates the §9 snapshot-read row, and largely answers §7 item 3; detail in §4.1 and §5.3.
- E15 (2026-10-20, live, SmartControl account, htr fw 1.9, `htr:3`): `max_stemp_limit` sentinel round-trip — `"0.0"` accepted, echoed in the pushed setup frame and REST readback, then `"24.0"` restored and echoed; and the 4-key `/status` prog-temp body — all three temperatures accepted/echoed, `stemp`/`mode` unchanged, originals restored. The sentinel's ceiling-enforcement semantics and the dedicated `/prog_temps` endpoint were not exercised. Detail in §5.2, §7 item 16.
- E16 (2026-10-20, live, SmartControl account, htr fw 1.9, `htr:3`): max-limit correction round-trip — lowering the ceiling from `"24.0"` to `"19.0"` pushed the corrected profile frame `{ice: "18.0", eco: "18.5", comf: "19.0", units: "C"}`, then the setup frame carried `"19.0"`; restoring `"24.0"` and the original `18.5/19.5/20.5` profiles was echoed cleanly. The status frame (`stemp: "20.0"`, mode `manual`) stayed unchanged.
- E17 (2026-10-20, live, SmartControl account, htr fw 1.9, `htr:3`): `max_stemp_limit` enforcement — with the ceiling at `"24.0"`, a `stemp: "24.5"` write was clamped and echoed `"24.0"`; with the ceiling at `"0.0"`, the same `"24.5"` write was accepted and echoed `"24.5"`. Setpoint and limit were then restored (`20.0` / `"24.0"`); `active` stayed false and `duty` 0 throughout.
- E18 (2026-11-20, live, guest account on the boxless reporter's home — product 0132, fw 1.8.0, one `htr:2`, `owner: false`): away round trip driven from the vendor app — `/mgr/away_status` pushes with `away` toggled, `forced` already `true` and staying `true` through away-off, `stemp` following a manually set `setup.away_offset` of 2.0 (`"19.0"` <-> `"17.0"`), `setup.away_mode` left at `0`, and an unchanged status frame after each toggle; the duty cap — app writes of 0/25/50 and arbitrary values 40/60/35, all stored and echoed ~1.3 s after the POST returned, 60 and 35 honoured as `duty`/`act_duty` at the next start, 60 displayed as a blank selection in the app; REST `/dev_data` without `connected` on this account and on the maintainer's boxed unit; `setup.power` rewritten from the status value on every setup write; and the invite shape — `GET /devs` returning an empty `devs` with the home under `invited_to`, the grouped-device read serving the unit. The app's heater screen also has no radiator-priority row, because `isWifiHeater` hides it on a device with no Smartbox; the integration mirrors that gate. Detail in §3.2, §5.2, §5.3, §5.4, §5.7, §6.2, §9 and §7 items 5/17/21.
- E18a (2026-11-21, live, SmartControl account, five LHZ/EHC htr nodes): every node reported `control_mode: 5`, which the app displayed as PID (15 min. period); a full app cycle on the Obývačka node confirmed PID 15 → `5`, PID 30 → `6`, Hysteresis 0.2/0.3/0.5/0.7 → `0`/`1`/`2`/`3`, and the node was restored to `5`. Detail in §1.9.
- E18b (2026-11-21, static decode, Ducaheat portal): `https://ducaheat.termoweb.net/` serves the Tevolve SPA build with serial `15`, theme `ducasa`, the same Basic credential, `users-api.helki.com` and the `ws_user` client; the bundle contains no `socket.io` string. A live login remains `**[unverified]**`. Detail in §1.1.
- E18c (2026-11-22, live, Obývačka htr, control-mode soak build): with the balcony and the window above the heater open at 0 °C outside, the heater kept heating during the initial drop from 19.5 °C to 17.2 °C, then its own `window_open` status flipped `true` at 01:07:00.368Z. The same frame's `active`/`hvac_action` became idle and the room fell to 16.4 °C. Closing the window flipped `window_open` `false` at 01:08:10.427Z; `active`/`hvac_action` resumed heating and the room recovered to 16.9 °C. This confirms the flag is the heater's detection event, not an external contact, and that it suspends heating. Detail in §3.4.

---

## 9. Known implementation divergences

Cases where the two implementations of this wire contract disagree: the 2.7 library and the 3.0 `smartbox.protocol` package. Code citations use the `[file:line]` tag and resolve against the installed library. Rows marked code-TBD need a fix IN THE CODE (or a probe to adjudicate); this file records, it does not adjudicate.

| Wire question | Old lib | This integration | Status |
|---|---|---|---|
| Token-endpoint 429 semantics | `APIUnavailableError` — rate limit treated as transient and retried `[code session.py:428-435]` | 429 → `TransportUnavailableError` (transient: retried by the grant ladder, never triggers the refresh->password fallback — a password re-grant while rate-limited would amplify the limit); other 4xx → `AuthError` `[auth.py:293-303]` `[2026-10-05]` | wire semantics `**[unverified]**`; resolved-deliberate — 429 reclassified transient `[2026-10-05]` |
| `power_limit` POST body shape | stringified `{"power_limit": "<int as string>"}` `[code session.py:913]` | stringified `{"power_limit": str(value)}` at BOTH endpoints — the box `htr_system` one `[writes.py:445-464]` and the node's own `/{type}/{addr}/power_limit` `[writes.py:466-487]` — lib, app and this integration now agree `[2026-10-05]`; GET side unchanged (raw passthrough) | resolved-deliberate (lib and app agree on the string form — `powerLimit.toFixed(0)`, both the htr_system and node/PMO endpoints `[app mobile app.js@5159281, @5159154]`; the box `"0"` clear is live-verified 2026-10-18; node-level `"0"` remains `**[unverified]**` — no pmo hardware) |
| Non-JWT / empty token classification | non-JWT → `WsUserUnsupportedError` (terminal unsupported → socket_io fallback); empty token → `SmartboxError` `[code ws_user.py:109-133]` | both → `AuthError` from `jwt_user_id` — unusable credentials, terminal for the supervise ladder (no restart loop) and recovered through the reauth flow; messages stay token-free `[jwt.py:54-71]` `[2026-10-05]` | resolved (classification is deliberate: a token the client cannot decode is a credential problem, not a capability one) |
| 2xx with an undecodable JSON body | `SmartboxError` `[code session.py:622-624]` | 204 / empty / non-JSON content-type → `{}` (unchanged); a JSON content-type with an undecodable body → `SmartboxError`, message bounded to status/content-type/length — never the body `[client.py:352-375]` `[2026-10-05]` | resolved |
| ws_user reconnect attempts | 10 connect attempts/loop `[code ws_user.py:67]` | 10 attempts/loop — matches; jitter ALIGNED to the vendor's one-sided `+0–20 %` form: `uniform(1.0, 1.2)`, cap 30 s unchanged `[ws_user.py:548-557]` `[2026-10-05]` | resolved (deliberate LIB policy; the vendor web-app reference says 5 attempts / 30 s total with ONE-SIDED `+0–20 %` jitter `[app helki.js@1209876]` — §4.1; the ± vs + nuance is no longer a divergence `[2026-10-05]`) |
| Off-grid setpoint quantization | posts the raw value (no client quantization) `[code session.py:977-991]`; the server floors whatever reaches it `[live 2026-09-28]` | pre-quantizes client-side before POST: Celsius floors onto the 0.5 grid, Fahrenheit rounds half-up to whole degrees in the app's `.1f` body form (the server 400s a decimal-less F body, live 2026-10-18) `[values.py:108-125]` `[live 2026-10-18]` | four postures on one wire: the vendor app validates-and-REJECTS off-grid input client-side (C: `temp % 0.5 !== 0`; F: integer-only) and posts only `.toFixed(1)` values `[app mobile app.js@506258, @497729]` / the server FLOORS `[live 2026-09-28]` / the lib posts raw / this integration pre-quantizes — floor matches the verified server behavior on C; on F the whole-degree `.1f` body is accepted and the server's own fractional-input quantization is half-up (live 2026-10-18) — `**[unverified]**` |
| GET retry constants | 5 attempts, backoff factor 0.1 `[code session.py:626-657]` | 3 attempts, factor 1.0 `[client.py:47-49, 219-244]` | resolved (client policy; the wire semantics — GET-only retry, shared 30 s budget, skipped overrunning backoff — match) |
| Away write body | 2-key `{away, enabled}` with client-side `enabled: true` defaulting `[code session.py:810-827]` | the app's 3-key body `{away, enabled: true, forced: false}` `[writes.py:426-443]` | resolved-deliberate (adopted the app shape; the server sets `forced: true` regardless `[live 2026-10-18]`) |
| `get_power_limit` return form | `int(resp["power_limit"])` | raw payload passthrough `[client.py:203-205]` | resolved (client-side extraction; the E17/E33 pmo asymmetry is flagged in both) |
| Snapshot power-limit read and frame-slot type | jq dev_data `.htr_system.setup.power_limit`, frames `.body.power_limit`, both wrapped in `int()` `[code update_manager.py:368-380, session.py:880-921]` | read flat `htr_system["power_limit"]` — the OBJECT, not the value — and stored frame bodies uncoerced, so a digit-string body landed in the int slot (engine.py before 2026-10-16) | ADJUDICATED 2026-10-16 and FIXED: the live snapshot carries both copies (`power_limit.power_limit` digit string, `setup.power_limit` int), the server keeps them in step on a write, clearing the limit serves an EMPTY object copy with `setup.power_limit: 0`, and the write pushes both paths with different TYPES — this integration now reads the object first with the `setup` fallback (`_snapshot_power_limit`) and coerces every path to int (`coerce_power_limit`) `[merging.py:110-158]`; the old lib's `setup` read is current on this family and its `int()` already absorbs the string frame body |
| `max_stemp_limit` unit | raw pass-through: the value was treated as the node's unit and remembered/converted in that scale `[code session.py 2.6.x models.py:552-559, 736-744]` | converts both ways around the wire's Celsius: reads convert into the node's unit for the entities, writes convert back and snap to the 0.5 °C grid `[values.py:80-108]` `[adapter.py:429-447]` `[writes.py:345-389]` | resolved-deliberate (app-decoded: the vendor app converts this field both ways and displays 75.0 °F for a stored 24.0; live probe 76.0 °F -> `"24.5"` `[live 2026-10-18]`) |

### App-vs-integration checks

These are UI/behaviour differences around the same wire, not different endpoints or bodies:

- **`max_stemp_limit` re-enable value.** The app's `MAX_STEMP_LIMIT_ENABLED` path writes the global maximum when enabling and `"0.0"` when disabling `[app mobile 2.41.0 vendors.js@2843163]`. This integration writes `"0.0"` to disable and restores the **last non-zero limit** on enable (`switch.py` max-temperature switch; documented in README). The wire body is app-shaped; the enable value is a deliberate UX choice.
- **Programme-temperature sanitization.** The app clamps each changed profile temperature to its per-profile limits, enforces the ice/eco/comfort ordering with the profile offset, and after lowering `max_stemp_limit` posts corrected profile temperatures when one exceeds the new ceiling `[app mobile 2.41.0 vendors.js@2845899, @2853563, @2854733]`. The integration now performs the same clamp in `merging.sanitize_prog_temps` (with the changed profile exempt from the neighbour pass) and `engine.set_max_stemp_limit` posts the corrected 4-key status body when a profile sits above the new ceiling; the correction was live-verified 2026-10-20. The low-level `WriteApi.set_prog_temps` remains a transport primitive and does not sanitize by itself.
- **4-key body on the protocol API.** The app's UI path posts all three temperatures plus `units` to `/status` (`applyProgTemps`). The integration's HA callers also pass all three, so the live body matches; the low-level `WriteApi.set_prog_temps` still permits omitting a key (`None`), which the app's UI path does not.
- **Per-heater duty cap (select).** The app's "power limit factor" select offers OFF / LST / Baby Care and writes `extra_options.user_duty_limit` as `0` / `lst_value` / `bbc_value` (§5.7); the wire accepts any 0–100 and the heater honours it, after which the app renders an empty selection for the unmapped value. The integration exposes the same three choices as one select per heater, gated the way the app gates it, and reports an unmapped stored value as unknown rather than as a percentage `[2026-11-20]`. It deliberately offers no free number: a value the app cannot map would leave the vendor app's heater screen with no selection shown.
- **Radiator priority (boxed devices only).** The app renders the priority row only for a node whose device is not a WiFi heater — `!isWifiHeater`, where `getDevProductType(pid) === WIFI_HTR` marks a heater that is its own device with no Smartbox to arbitrate the load — and hides it for a multi-node selection `[app mobile 2.54.2 new_app.decoded.js@1004213, @1035579]`; the controllers below that still list `PRIORITY` among their settings `[app mobile 2.54.2 new_vendors.decoded.js@3569505]`. The integration gates the select on the device's box capability instead of the product code and prunes the registry entry older builds created on boxless homes `[2026-11-20]`.

---

## Appendix A — retired: `socket_io` and the old lib's dispatch

Kept for the record. `socket_io` is the legacy per-device transport: the vendor apps no longer use it, and this integration dropped it in the three-layer rewrite. The dispatch notes describe the old library's machinery. Nothing below is implemented or planned; the `[code ...]` citations name the `smartbox` library's source, kept as the origin of these facts.

### A.1 `socket_io` — the retired per-device transport

- URL/namespace (C2 resolution): the engineio client dials `{api_host}/?token=<enc>&dev_id={dev_id}` — token and dev_id go on the BASE URL query; `/api/v2/socket_io` is the socket.io NAMESPACE (a routing name), NOT an HTTP path `[code socket.py:20, 376-378]`. [superseded: pre-contract notes transports table "`wss://<host>:443/api/v2/socket_io?token=…&dev_id=…`" -> conflates namespace with URL path; the dialed URL is the base host plus query.] The exact engineio wire URL (with EIO query params the client appends) should be pinned by a packet capture before quoting it externally `**[unverified]**` (only the library-side URL construction is code-pinned).
- Protocol: socket.io over engine.io — EIO=4 from Python, EIO=3 from the mobile app; the endpoint accepts both `[live 2026-10-01]`.
- Per-device session; transport `["websocket"]` only `[code socket.py:224-232]`.
- `dev_data` emit: sent right after a successful connect; PLUS a fallback emit inside `on_update` when no server message has arrived yet (the emit races the server drop — that race is why the gate exists); `update` frames are ignored until the first `dev_data` is received `[code socket.py:85-98, 197-203, 276-286]`. [superseded: "On successful connection, the client should emit a dev_data event" (pre-contract notes) -> code also re-emits on the first update if nothing was received `[code socket.py:88-97]`.]
- Keepalive: app-level `"ping"` string every 20 s (skipped while disconnected); NEVER answered by the server `[code socket.py:205-223]` `[live 2026-10-01]`. It is a faithful copy of the vendor mobile client's behavior, not a lib invention `[live 2026-10-01]`.
- Failure mode on server silence: the engineio read loop aborts after exactly `pingInterval + pingTimeout` (25 + 60 = 85 s) — engineio behavior, not a library constant — and recurs every 3–7 min while a half-open TCP connection lingers `[live 2026-10-01]`.
- Reconnect: 10 attempts, backoff `1.0 · 2^n` cap 30 s → fall through to the REST token refresh (itself retried with capped backoff; only `InvalidAuthError` ends the loop); exit flag checked BEFORE the REST refresh `[code socket.py:23-30, 360-429, 431-443]`.
- Teardown: bounded (5 s) single idempotent disconnect shared by cancel/shutdown; SIGINT handler install/remove; dangling cleanup tasks awaited (bounded) `[code socket.py:26-30, 310-345, 472-505]`. [superseded: the five teardown defects recorded 2026-09-26 (hang, double disconnect, REST detour, dangling tasks, log misuse) -> all fixed in code `[code socket.py:26-30, 310-345, 472-505]`; see pre-contract notes "E2 round — lib-side teardown residuals" (closure recorded in its 2.6.0 alignment round) — NOT §8/E2, which is the same-date live payload round.]
- The engineio client is bound to the REST session's websession (`external_http=True`) — disconnect never closes the shared session `[code socket.py:184-195]`.

### A.2 The retired fallback doctrine

- ONLY deterministic handshake rejections signal "endpoint unsupported": the probe maps 401/403/404/410 on a fresh token; the run loop retries everything except 404/410 (mid-run 401/403 also mean expired-but-refreshable) `[code ws_user.py:54-57, 720-760]`.
- Transient transport errors are retried and NEVER treated as unsupported `[code error.py:10-21]`.
- Per-host ws_user deployment is verified only for api-lhz; a released consumer must probe, not assume (§7) `[live 2026-10-02]`.

### A.3 The old lib's update dispatch (`UpdateManager`)

- Subscriptions: `dev_data` jq subscriptions (fast path for single-level keys); `update` subscriptions = path regex + body jq, regex named groups passed as kwargs; user callbacks are exception-isolated `[code update_manager.py:46-165]`.
- Node-key subscriptions pair the dev_data jq `(.nodes[] | {addr, type, <key>})?` with the update regex `^/(?P<node_type>[^/]+)/(?P<addr>\d+)/<key>$` for status/setup/version/prog `[code update_manager.py:366-431]`.
- Node availability: ok status/setup frame → Available (cancels timers); `sync_status: "lost"` → 6 s grace timer (`unavailable_delay`, default 6.0) → Unavailable; unknown `sync_status` values change nothing `[code update_manager.py:28-33, 532-560, 602-613]` `[live 2026-09-30 shapes]`.
- Write confirmation `expect_write_confirmation(node, written, kind)`: 6 s window (`write_confirm_timeout`, default 6.0), then one-shot discriminating GET (status or setup; unknown `kind` → `ValueError`); a bare-lost payload in typed mode fails validation and the carried `payload` IS the Unavailable verdict; `_written_applied` tolerates numeric-string precision differences (1e-6) and treats an explicit `sync_status: "lost"` payload as never-applied `[code update_manager.py:28-33, 459-521, 615-697, 740-773]`.
- ws_user mode: the manager registers its device on a shared `WsUserSocketSession` and `run()` parks until `cancel()`; the shared socket's run loop is owned by the caller `[code update_manager.py:168-273]`.

---

