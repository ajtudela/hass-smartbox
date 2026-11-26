<!-- record:begin -->
> **Maintained here.** This is the endpoint catalogue the repository cites (`docs/api-endpoints.md`); new paths and status changes are recorded in this file.
<!-- record:end -->

# Endpoint catalogue

One row per endpoint, or one row for a GET/POST pair that shares a path. This
is the index; accepted request/response shapes and probe evidence are in
`api-contract.md` §3.2, and current app behavior and write sequencing are in
`api-ws-user.md`.

**IDs.** `E#` rows are the contract's endpoint rows; `S#` rows are app-surface
endpoints with no contract row; `X#` rows are account/session endpoints. The
number is a stable key, not a category. The path is the semantic identity, and
the section grouping is the category.

**Consumer.** `library+HA` = a library method exists and the Home Assistant adapter
uses it; `library` = a library method exists but the adapter does not use it;
`app only` = current vendor app surface with no client implementation here;
`—` = declared in vendor code but with no current caller.

**Status.** `accepted` = live-probed and recorded in the contract; `current` =
observed in the current app builds but not accepted live; `unverified` = declared
but neither seen in a current build nor probed; `closed` = investigated and not
pursued.

**Last confirmed.** The current decoded builds are Helki 2.55.0, Elnur 3.7.0 and
Tevolve 2.8.8, all fetched 2026-11-25. A live date in the Status column is the
last accepted wire observation.

Account and app endpoints (`/api/v2/user/*`, `/api/v2/users/*`, `/client/*`,
`/client-data/*`) are indexed in §7; their app behavior is in
`api-ws-user.md` §2.1/§2.4. They are not device-control rows.

## 1. Auth and health

| ID | Method and path | What it does | Consumer | Status | Last confirmed | Details |
|---|---|---|---|---|---|---|
| E1 | `GET /health_check` | host health | library+HA | unverified | — | contract §3.2 E1 |
| E2 | `GET /version` | API version | library+HA | accepted 2026-09-26 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E2 |
| E3 | `POST /client/token` | password/refresh grant | library+HA | accepted 2026-10-04 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §2.1, §3.2 E3 |

## 2. Homes and guests

| ID | Method and path | What it does | Consumer | Status | Last confirmed | Details |
|---|---|---|---|---|---|---|
| E5 | `GET /api/v2/grouped_devs` | homes with their devices | library+HA | accepted 2026-09-27 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E5 |
| E6 | `GET /api/v2/groups/{home_id}/guest_users` | guest list | library | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E6 |
| E35 | `GET /api/v2/groups/{id}/geo_data` | home geolocation | app only | accepted 2026-09-27 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E35 |
| E37 | `GET /api/v2/groups/{id}/extra_data` | home extra data | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E37 |
| S6 | `POST /api/v2/groups/{id}/geo_data`; `POST .../extra_data` | write home geo/extra data | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.3 |
| S1 | `GET /api/v2/groups/{id}/name` | home name read | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | — |
| E36 | `POST /api/v2/groups/{id}/name` | rename home | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E36 |
| E38 | `POST /api/v2/users/{uid}/invite` | invite a guest | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E38 |
| E39 | `DELETE /api/v2/users/{uid}/invite` | revoke an invite | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E39 |
| E40 | `POST /api/v2/users/{uid}/invite_confirmation` | invite confirmation (unauthenticated) | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E40 |

## 3. Devices

| ID | Method and path | What it does | Consumer | Status | Last confirmed | Details |
|---|---|---|---|---|---|---|
| E4 | `GET /api/v2/devs` | device list | library+HA | accepted 2026-09-27 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E4 |
| E7 | `GET /api/v2/devs/{dev_id}/mgr/nodes` | node list | library+HA | accepted 2026-10-04 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E7 |
| E8 | `GET /api/v2/devs/{dev_id}/connected` | device connectivity | library | accepted 2026-09-27/30 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E8 |
| E9 | `GET /api/v2/devs/{dev_id}/mgr/away_status` | away status | library+HA | accepted 2026-09-27 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E9 |
| E10 | `POST /api/v2/devs/{dev_id}/mgr/away_status` | set away status | library+HA | accepted 2026-09-27 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E10 |
| E11 | `GET /api/v2/devs/{dev_id}/mgr/discovery` | discovery state | library | accepted 2026-09-27 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E11 |
| E12 | `POST /api/v2/devs/{dev_id}/mgr/discovery` | start/stop discovery | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E12 |
| E13 | `GET /api/v2/devs/{dev_id}/mgr/rtc/time` | box clock | library+HA | accepted 2026-09-27 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E13 |
| E14 | `POST /api/v2/devs/{dev_id}/name` | rename device | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E14 |
| E15 | `DELETE /api/v2/devs/{dev_id}` | remove device | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E15 |
| E16 | `POST /api/v2/devs/{dev_id}/group` | move device between homes | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E16 |
| S5 | `POST /api/v2/devs` | add device | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.3 |
| E17 | `GET /api/v2/devs/{dev_id}/htr_system/power_limit` | box power limit | library+HA | accepted 2026-09-29 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E17 |
| E18 | `POST /api/v2/devs/{dev_id}/htr_system/power_limit` | set box power limit | library+HA | accepted 2026-09-29 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E18 |
| E47 | `POST .../{type}/{addr}/power_limit` | set node power limit | library+HA | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E47 |
| E46 | `GET /api/v2/devs/{dev_id}/dev_data` | full device snapshot | library+HA | accepted 2026-11-20 | live 2026-11-20 | contract §3.2 E46 |
| S2 | `POST /api/v2/devs/{dev_id}/pmo_system` | PMO configuration | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | — |

## 4. Nodes

`{type}` is one of `htr`, `htr_mod`, `thm`, `acm`, `pmo`; `{addr}` is the node
address. The per-node base path is `/api/v2/devs/{dev_id}/{type}/{addr}`.

| ID | Method and path | What it does | Consumer | Status | Last confirmed | Details |
|---|---|---|---|---|---|---|
| S3 | `GET /api/v2/devs/{dev_id}/{type}/{addr}` | full node data | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | — |
| E20 | `GET .../{type}/{addr}/status` | status read | library+HA | accepted 2026-09-26 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E20 |
| E21 | `POST .../{type}/{addr}/status` | status write | library+HA | accepted 2026-09-26/28 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E21, §5.1 |
| E22 | `GET .../{type}/{addr}/setup` | setup read | library+HA | accepted 2026-09-27 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E22 |
| E23 | `POST .../{type}/{addr}/setup` | merged setup write | library+HA | accepted 2026-10-04 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E23 |
| E24 | `GET .../{type}/{addr}/version` | version read | library+HA | accepted 2026-09-26 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E24 |
| E25 | `POST .../{type}/{addr}/mode` | dedicated mode write | library | unverified | — | contract §3.2 E25 |
| E26 | `POST .../{type}/{addr}/lock` | lock write | library+HA | accepted 2026-11-22 (status path) | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E26 |
| E27 | `POST .../{type}/{addr}/boost` | boost write | library+HA | accepted 2026-09-26 (status path) | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E27 |
| S4 | `POST .../{type}/{addr}/boost_mode` | full-boost activation | library+HA | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.3 |
| E28 | `POST .../{type}/{addr}/prog_temps` | dedicated profile-temp write | — | unverified | — | contract §3.2 E28 |
| E29 | `GET .../{type}/{addr}/prog` | programme read | library+HA | accepted 2026-09-27 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E29 |
| E30 | `POST .../{type}/{addr}/prog` | programme write | library+HA | accepted 2026-09-27 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E30 |
| E19 | `GET .../{type}/{addr}/samples?start=&end=` | samples read | library+HA | accepted 2026-10-04 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E19 |
| E31 | `POST .../{type}/{addr}/name` | rename node | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E31 |
| E32 | `POST .../{type}/{addr}/select` | select firmware/version | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E32 |
| E33 | `GET .../{type}/{addr}/power` | node power | library+HA | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E33 |
| E34 | `DELETE /api/v2/devs/{dev_id}/{type}/{addr}` | remove node | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E34 |

## 5. Notifications

| ID | Method and path | What it does | Consumer | Status | Last confirmed | Details |
|---|---|---|---|---|---|---|
| E41 | `GET /api/notifications/v1/{gid}/presence/config` | quiet-home config | app only | accepted 2026-09-27 | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E41 |
| E42 | `POST /api/notifications/v1/{gid}/presence/config` | set quiet-home config | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E42 |
| E43 | `POST /api/notifications/v1/{gid}/test` | send test notification | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E43 |

## 6. Provisioning and location

| ID | Method and path | What it does | Consumer | Status | Last confirmed | Details |
|---|---|---|---|---|---|---|
| E44 | `GET /api/v2/encrypted_wifi_credentials?ssid=&pass=` | encrypt Wi-Fi credentials | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E44 |
| E45 | `GET /api/location/v1/coordinates?country=&state=&city=&zip=` | geocode a place | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | contract §3.2 E45 |

## 7. Account and app

These are the current app's account/session surface. The app constructions and
sequencing are in `api-ws-user.md` §2.1/§2.4; none is device control.

| ID | Method and path | What it does | Consumer | Status | Last confirmed | Details |
|---|---|---|---|---|---|---|
| X1 | `GET /api/v2/device_data` | per-serial device metadata | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.2 |
| X2 | `GET /client/sessions`; `DELETE /client/sessions/{id}`; `DELETE /client/sessions` | list or revoke sessions | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §1.6, §2.1 |
| X3 | `POST /client/logout` | log out | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.1 |
| X4 | `POST /api/v2/events` | analytics events | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.4 |
| X5 | `GET/POST /api/v2/user/preferences` | app preferences | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.4 |
| X6 | `GET /api/v2/user/feature-flags` | floor-plan and chatbot flags | app only | current | 2.55.0 (2026-11-25) | api-ws-user.md §2.4 |
| X7 | `POST /api/v2/users/new_pass`; `POST /api/v2/users/new_pass/confirm` | password reset | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.4 |
| X8 | `POST /api/v2/users/change_email`; `POST /api/v2/users/change_email/confirm` | email change | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.4 |
| X9 | `POST /api/v2/users/confirm` | account confirmation | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.4 |
| X10 | `GET /client/code` | OAuth device linking | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.4 |
| X11 | `GET /client-data/{serial}/*.json` | per-serial statics | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.5 |
| X12 | `POST /api/v2/fcm_token/` | push registration | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.4 |
| X13 | `POST /api/v2/user/change_pass` | change password | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.4 |
| X14 | `GET /api/v2/user/integrations`; `GET .../integrations/{id}` | account integrations | app only | current | 2.55.0, 3.7.0, 2.8.8 (2026-11-25) | api-ws-user.md §2.4 |
| X15 | `GET /api/v2/client_invitations/{id}` | read a client invitation | app only | current | 2.55.0, 3.7.0 (2026-11-25) | api-ws-user.md §2.4 |
| X16 | `POST /api/v2/client_invitations/{id}/accept` | accept a client invitation | app only | current | 2.55.0, 3.7.0 (2026-11-25) | api-ws-user.md §2.4 |
| X17 | `POST /api/v2/users` | sign up | app only | current | 2.8.8 (2026-11-25) | api-ws-user.md §2.4 |
| X18 | `GET/POST /api/v2/users/{id}/privacy` | privacy consent | app only | current | 2.8.8 (2026-11-25) | api-ws-user.md §2.4 |
| X19 | `DELETE /api/v2/users/{id}` | delete account | app only | current | 2.8.8 (2026-11-25) | api-ws-user.md §2.4 |

## 8. Local box

The local-LAN API is closed and has no vendor client. It is recorded in
`api-contract.md` Appendix — Local box API; it is not part of the cloud endpoint surface.
