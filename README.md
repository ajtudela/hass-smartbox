# hass-smartbox
![hassfest](https://github.com/ajtudela/hass-smartbox/workflows/Validate%20with%20hassfest/badge.svg)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![codecov](https://codecov.io/gh/ajtudela/hass-smartbox/branch/main/graph/badge.svg)](https://codecov.io/gh/ajtudela/hass-smartbox)
[![Total downloads](https://img.shields.io/github/downloads/ajtudela/hass-smartbox/total)](https://github.com/ajtudela/hass-smartbox/releases)
[![Downloads of latest version (latest by SemVer)](https://img.shields.io/github/downloads/ajtudela/hass-smartbox/latest/total?sort=semver)](https://github.com/ajtudela/hass-smartbox/releases/latest)
[![Current version](https://img.shields.io/github/v/release/ajtudela/hass-smartbox)](https://github.com/ajtudela/hass-smartbox/releases/latest)


Home Assistant integration for Haverland (and other brands) heating smartboxes.

## Requirements

* Home Assistant **2026.5.0 or newer**.
* A Smartbox account with one of the [supported resellers](#supported-resellers).
* [HACS](https://hacs.xyz/) for the recommended installation; the manual route works without it.

## Supported resellers

The setup flow offers these providers. The link identifies the brand/account; most providers dial the shared API host (`users-api.helki.com`), Elnur dials `api-elnur.helki.com`, and the legacy EHC app stays on its own host, so the link is not the integration's endpoint. Several names share a tenant/backend with another brand, as noted.

| Brand | Provider link | Notes |
|---|---|---|
| Helki | https://app.helki.com/ | Generic Helki/Smartbox account |
| Lucht LHZ | https://smartcontrol.lucht-lhz.de/ | |
| Electric Heating Company | https://control.electric-heatingcompany.co.uk/ | Legacy socket.io web app on `api-ehc.helki.com` (2025-11-28; 3.x unverified) |
| Climastar | https://avantwifi.climastar.es/ | |
| Perry | https://te.perryhome.it/ | Selectable; live login unverified |
| Elnur | https://remotecontrol.elnur.es/ | Own API host `api-elnur.helki.com`; ws_user app (2026-10-30) |
| Valderoma | https://wifi.valderoma.fr/ | Shares the Climastar tenant |
| iHeat Control | https://app.iheatcontrol.com/ | Shares the Electrorad tenant |
| HJM | https://api.calorhjm.com/ | Configured brand link |
| Electrorad | https://app.controls-electrorad.co.uk/ | ws_user app; migrated from socket.io (2026-11-06) |
| Haverland | https://i2control.haverland.com/ | |
| Tevolve | https://tevolve.termoweb.net/ | Selectable; live login unverified |
| Technotherm | https://ttiapp.technotherm.com/ | Shares the Lucht LHZ tenant |
| SmartControl | https://app.smart-control.eu/ | Shares the Lucht LHZ tenant |

Accounts whose backend only serves the retired `socket_io` transport need 2.6.x; the 3.0 line requires `ws_user`. The public web-app check above is a hint: twelve provider apps ship the `ws_user` client, while the EHC public app remains legacy `socket.io` (2026-11-15 audit). The setup probe is authoritative for an individual account — if it reports `unsupported`, stay on 2.6.x. See the websocket warning below.

## Installation

### Using HACS (Recommended)

1. Add this repository to your HACS [custom repositories] as an **Integration**.
1. Search for and install "Smartbox" in HACS.
1. Restart Home Assistant.


### Manually Copy Files

1. Open your Home Assistant configuration directory (where `configuration.yaml` lives).
1. Create `custom_components/` if it does not exist.
1. Copy the **whole** `custom_components/smartbox/` directory from this repository (or the release archive) into it, preserving `brand/`, `translations/`, `frontend/`, and the JSON/YAML files. The `frontend/` directory carries the bundled program card; the integration serves it automatically, so there is no separate Lovelace resource or HACS plugin to install.
1. Install the matching `smartbox` library in the Home Assistant Python environment: `pip install "smartbox>=3.2.0,<3.3"`. HACS/HA installs this requirement automatically; manual installs need it explicitly.
1. Restart Home Assistant
1. Hard-refresh the browser (`Ctrl+Shift+R` / `Cmd+Shift+R`) after the restart or an upgrade, so the new frontend module is loaded.


### Finally

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=smartbox)


### Removal

1. Remove the Smartbox entry in **Settings → Devices & services**.
1. Remove the custom repository and the integration in HACS (if installed that way), or delete `custom_components/smartbox/` for a manual install.
1. Restart Home Assistant.


## Configuration

You will need the following items of information:
* Name of your reseller
* Your username and password used for the mobile app/web app.

If there is an issue during the process or authentication, the errors will be displayed.

### Additional Options
You can also specify the following options (although they have reasonable defaults)

#### Consumption history options
We are currently getting the [consumption](#consumption) of device through the API and we inject it in statistics and TotalConsumption sensor
* `start` : we will get the last 3 years of consumption and set the option to auto.
* `auto` : every 15 minutes, we get the last 24 hours.
* `off` : stop the automatic collect. We will still update the sensor's value every 15 minutes.

#### Reseller logo
By default, each entity has its own icon based on its type. You can activate this option to display the logo of the reseller instead.

#### Timedelta between update power
If you have a [Dedicated energy monitor](#dedicated-energy-monitor), we get the current power each 60 seconds by default. You can update this time with this option (minimum 1 second).
> [!NOTE]
> Be careful with this option; reduce the number little by little to see if any instability occurs.

### Architecture

Since `3.1.0` the integration runs on the separate `smartbox` library:

* `smartbox.protocol` — auth (token manager), the REST client, the `ws_user` websocket transport and the named write operations.
* `smartbox.core` — the versioned state store + engine: snapshot ingestion, websocket-frame merging, availability tracking and write-confirmation windows.
* `custom_components/smartbox` — the HA adapter: config flow, entity platforms, diagnostics.

`manifest.json` requires the `smartbox` package from PyPI, so HACS/HA installs it. The 3.0.0 release vendored the same layers; the split is the 3.1 line.

See [architecture.md](docs/architecture.md) for the layer rules, the repository map, and the read and write flows.

### Session persistence

The integration stores the vendor token bundle in Home Assistant's private
`.storage` and resumes it after a restart. The refresh grant continues the
existing vendor session, so HA restarts no longer create a new login row for
each start; the rotated refresh token is rewritten after every grant. Reauth,
reconfigure and entry removal clear the stored bundle. Diagnostics expose
`runtime.token_origin` as `restored` or `granted` so the path taken is visible.

### Websocket transport

Status updates arrive over the `ws_user` channel (the vendor web apps' transport): ONE connection for the whole account. A client-side 60-second heartbeat, alongside the proxy's own PINGs, supervises the session — the periodic "Server has stopped communicating" aborts of the legacy per-device `socket_io` transport disappear. The server cuts the socket at the access token's 4-hour expiry (~4-second outage while it reconnects with a fresh token); that is the steady state, by design.

> [!WARNING]
> **BREAKING:** the socket_io fallback is GONE. Most reseller keys dial the shared host (`users-api.helki.com`); Elnur dials `api-elnur.helki.com`. The key selects the tenant's credentials, serial id, referer and API host. The setup probes `ws_user` once with a fresh token, and a host that does not serve it cannot work with this version (the config entry fails with an "unsupported" error). `ws_user` is live-verified on the shared host; tenant deployments other than the probed one are unverified, not known-bad. Accounts whose backend only serves socket_io must stay on 2.6.x; the legacy EHC public app is the known example (2026-11-15 audit). Electrorad migrated to `ws_user` on 2026-11-06. Transient probe trouble (a 5xx-ing or unreachable host) retries via the normal `ConfigEntryNotReady` retry — a transport verdict is never inferred from transient trouble.

The transport is visible in Settings → Devices → diagnostics as `runtime.transport`.

### Write confirmation

Writes to a node arm a confirmation window: the write is applied optimistically in Home Assistant, and a confirming frame from the node within the window confirms it. If no frame arrives, the engine reads the written slot once. A lost-marked read marks the node Unavailable; any data is reconciled into the store; an API failure changes nothing. Availability otherwise follows lost/ok frames and the feed. A node that accepted the write but ignored it stays available, and the ignored write is logged.

## Features

### Devices

Each Smartbox box (the gateway the heaters talk through) is a Home Assistant device, and every heater is its own device linked to that box. Open the box device to see its heaters under **Connected devices**; each heater page shows **Connected via** the box.

### Heaters (climate)
Heater node types (`htr`, `acm` and `htr_mod`) are modelled as Home Assistant Climate entities.

* `htr` and `acm` (accumulator) nodes
  * Supported modes: 'manual' and 'auto'
  * Supported presets: 'home' and 'away'
* `htr_mod`
  * Supported modes: 'manual', 'auto', 'self_learn' and 'presence'
  * Supported presets: 'comfort', 'eco', 'frost', 'schedule', 'self_learn' and 'activity'

Every heater also exposes the 'away' and 'boost' presets (boost where the device supports it) and the `OFF` HVAC mode. The modes and presets for `htr_mod` heaters are mapped as follows:

| htr\_mod mode | htr\_mod selected_temp | HA HVAC mode | HA preset   |
|---------------|------------------------|--------------|-------------|
| manual        | comfort                | HEAT         | COMFORT     |
|               | eco                    | HEAT         | ECO         |
|               | ice                    | HEAT         | FROST       |
| auto          | *                      | AUTO         | SCHEDULE    |
| self\_learn   | *                      | AUTO         | SELF\_LEARN |
| presence      | *                      | AUTO         | ACTIVITY    |

On `htr_mod` nodes, the climate entity also exposes the device's live
`selected_temp` profile (`comfort`, `eco`, `ice` or `off`) as a read-only
state attribute when the status carries it. This is the active profile; the
Schedule sensor's state remains the programme's expected profile.

The target temperature step is 0.5 °C / 1.0 °F on `htr` and `acm` nodes. On `htr_mod` nodes the vendor app's setpoint contract applies: a 1 °C step, the comfort and eco ranges of 18–28 °C and 10–18 °C, and the ice profile fixed to the device's own `ice_temp`. A target at or below 18 °C selects the `eco` profile and moves `comfort_temp`/`eco_offset` the way the app does - the stored comfort stays unless the 4–8 °C gap cannot reach the target - snapping an off-grid stored comfort to a whole degree; above 18 °C it selects `comfort` and keeps the stored offset. While comfort or eco is active the advertised range is their union (10–28 °C), so the slider and service calls can cross 18 °C without a preset change; `ice` keeps its fixed value. Every htr_mod setpoint write carries `mode: manual`, as the app's builder does, so setting a temperature leaves the programme it was running. These bounds come from the app's htr_mod controller, in whole degrees on Fahrenheit nodes too, and are applied to the Haverland product codes it dispatches on (`pid[2:4]` = `0A` or `FF`), plus nodes whose version has not been read yet. Other htr_mod hardware keeps the previous temperature arithmetic and the generic grid; the `mode: manual` key is sent on every htr_mod setpoint. On the fw-1.9 `htr` family, the maximum target temperature is clamped to the node's `max_stemp_limit` when the device advertises one.

If a heater or box is paired to the account after the integration has set up, the snapshot triggers a fresh REST identity read and the entity builders add the new device and node entities in place. A lagging identity is retried with a short backoff; if it still fails, a repair issue names the pending nodes and asks for a reload.

When the account no longer lists a heater or box, the integration confirms the absence with a fresh full account read after a short grace period, then removes the device and its entities. Recorder history and long-term statistics are kept, and pairing the heater again restores the same device and entity IDs (with their area, name and history). A member the integration cannot confirm, for example while the vendor API is unreachable, can still be deleted from its device page.

**Boost:** the Boost switch/preset follows the vendor app's `boost_config` dispatch. Configuration `1` and `2` use the status/setup path; configuration `3` (full) posts `/boost_mode` (`{active, mode, temp?, time?}`). The full path is app-evidenced; live acceptance on a full-boost unit is still pending.

On a full-boost node, **Runback** and **Advanced boost** (boost until the next schedule change) appear as climate presets when the node's status advertises them. Runback uses its own lower/upper temperature limits while active. The boost/runback temperature and duration number entities are **defaults** used at activation; while a mode is running, the climate target is its active temperature, and the climate entity's `boost_mode`, `boost_end_time`, and `boost_remaining_minutes` attributes show the active mode and time left. Use `smartbox.set_boost_mode` to edit a running mode's duration or to select advanced-slot/runback explicitly.

### Program card

The integration ships a Lovelace card for a heater's weekly program. It is served by the integration and loaded automatically; install the integration and the card appears in the card picker as **Smartbox program**. No separate HACS dashboard item or Lovelace resource is needed.

![Smartbox program card](docs/images/program-card.png)

```yaml
type: custom:smartbox-program-card
entity: sensor.living_room_schedule
# optional:
# title: Living room
# show_legend: false
```

The card takes the heater's Schedule sensor as its entity; the visual editor lists the available Smartbox schedule sensors. The legend shows the profile names with their current Frost/Eco/Comfort values; the **Legend** switch is on by default and hides the whole legend when turned off. The rows run from 00:00 at the top to 24:00 at the bottom; open the editor for the exact slot times.

The card presents the week as a seven-column grid — days across, time slots down — starting on the Home Assistant user's configured first day of the week. It has a color legend for the profiles and is deliberately plan-only: the heater's mode, current target and overrides belong to the climate card, and the card does not derive a "now" or "next" state from the programme. Click the week grid to open the editor; on desktop the grid shows an **Edit program** overlay on hover or keyboard focus, and clicking a cell starts at that day and time. The editor has profile brushes, drag painting with a mouse, tap-to-apply on touch, undo, reset, and copy-day to weekdays, the weekend, or the whole week. Tap a day header to choose the copy source; the copy row names the selected day, and one instruction line above the grid covers painting and copying. Apply sends only the days you changed through `smartbox.set_schedule`. The dialog closes from its X button or Escape; a week with unsaved changes asks before discarding it. The editor warns when the program changed elsewhere, and it keeps unknown program formats read-only.

![Smartbox program card editor](docs/images/program-card-editor.png)

The card preview merges equal neighbouring slots into continuous bands, marks 00, 06, 12 and 18 in a 30px left gutter, and draws rules across the plot at 06, 12 and 18. The editor uses the same header, flush gutter and hour guides, and renders the full week inside the dialog; when the chart does not fit, the dialog body scrolls, so there is at most one scrollbar. On phone widths the grid lines thicken and gain contrast so they stay visible on high-density screens. The band colors mix with the card surface to follow the active theme; override `--smartbox-frost-color`, `--smartbox-eco-color` or `--smartbox-comfort-color` to replace a fill.

The card reads the schedule sensor's `schedule` attribute. The legend's temperatures come from the corresponding number entities; tapping one opens its more-info dialog. Hide the legend to leave only the chart. See [Services](#services) for the underlying write call.

## Use cases

Pick the real entity IDs from **Settings → Devices & services → Smartbox**; the examples below use placeholder names.

### Run a bounded warm-up boost before guests arrive
Boost heats for a fixed time and then returns to the programme:

```yaml
actions:
  - action: smartbox.set_boost_mode
    target:
      entity_id: climate.living_room
    data:
      mode: temp_and_time
      temperature: 22
      duration: 60
```

On a full-boost node, `smartbox.set_boost_mode` also covers `runback` and `advanced_slot`, and the climate presets expose them.

### Notice a heater that lost power
The climate and sensor entities turn Unavailable when a node stops answering, so a state trigger can warn you before the room gets cold:

```yaml
alias: "Heating: warn about an unreachable heater"
triggers:
  - trigger: state
    entity_id: climate.living_room
    to: "unavailable"
    for: "00:15:00"
actions:
  - action: notify.send_message
    target:
      entity_id: notify.mobile_app
    data:
      message: "The living-room heater has been unreachable for 15 minutes."
```

See [Node availability](#node-availability) for what the Unavailable state means.

## Known limitations

These are the boundaries of what the integration can do; the linked sections explain the details.

* **Cloud-only.** State and commands travel through the vendor cloud; there is no local or LAN control. Without internet the entities keep their last values until the feed is declared stale and they turn Unavailable. The 4-hour token refresh also cuts the socket for a few seconds (see [Websocket transport](#websocket-transport)).
* **`ws_user` accounts only.** Accounts whose backend only serves the retired `socket_io` transport must stay on 2.6.x. The setup probe decides; the remaining known example is the legacy EHC public app. Electrorad migrated to `ws_user` (2026-11-06; see [Supported resellers](#supported-resellers)).
* **No real-time consumption.** The API exposes hourly counters with a 15–20 minute blind window at the top of each hour, so the total consumption sensor can lag by about an hour and never updates in real time (see [Consumption](#consumption)).
* **Coarse polling where the wire is silent.** PMO power polls every 60 seconds (configurable), the RTC drift every 5 minutes, and consumption every 15 minutes. These are the only values that do not arrive by push.
* **Writes are confirmed, not guaranteed.** A write is applied optimistically and the node turns Unavailable if it does not confirm within the window; the vendor app behaves the same way. The server read-through can also lag several seconds after a successful POST (see [Write confirmation](#write-confirmation)).
* **Capability-gated features.** Full-boost/Runback/Advanced boost, `htr_mod` presets, window mode, true radiant and PMO power limits exist only on units that advertise them. The full-boost write path is taken from the vendor app bundle and still awaits live acceptance on a full-boost unit (see [Boost](#heaters-climate)).
* **Membership cleanup needs confirmation.** A heater or box disappears from Home Assistant only after the account reports it gone in two authoritative reads. A box removed from the account can therefore remain until the next full snapshot (the 4-hour reconnect bound) unless a frame reports it. A member the integration cannot confirm stays until you delete it from its device page. A node that merely lost power is never removed.
* **Pairing stays in the vendor app.** Heaters are added, removed and renamed in the Smartbox app; the integration mirrors the account and does not manage the vendor side.
* **Tevolve is unverified.** The tenant is selectable, but a live login has not been validated yet.
* **Home Assistant floor.** 2026.5.0 is the supported minimum; older releases are not covered.

## Services

The integration registers three domain services, also available under **Developer tools → Actions**.

### `smartbox.set_schedule`

Target a schedule sensor (entity, device, or area). The `schedule` field is a day-keyed object (`"0"`..`"6"`, Sunday..Saturday); each value is that day's list of profile slots (`0` = frost, `1` = eco, `2` = comfort). Omitted days keep their current programme.

```yaml
action: smartbox.set_schedule
target:
  entity_id: sensor.living_room_schedule
data:
  schedule:
    "0": [0, 0, 1, 1, 2, 2, 2, 2, 1, 1, 0, 0,
          0, 0, 1, 1, 2, 2, 2, 2, 1, 1, 0, 0]
```

### `smartbox.set_boost_mode`

Target a Smartbox climate entity. Activates one of the full-boost modes (`temp_and_time`, `max_temp_fixed_time`, `advanced_slot`, `runback`) or deactivates the running mode. `temperature` is in the climate entity's unit; `duration` is in minutes. This is the service path for editing a running boost/runback duration and for modes the presets do not cover.

```yaml
action: smartbox.set_boost_mode
target:
  entity_id: climate.living_room
data:
  mode: runback
  temperature: 20.0
  duration: 60
```

### `smartbox.set_boost_params`

Target a boost temperature or boost duration number entity. Both fields are optional, but at least one is required; the temperature is in the target entity's unit.

```yaml
action: smartbox.set_boost_params
target:
  entity_id: number.living_room_boost_temperature
data:
  temperature: 21.5
  duration: 120
```

### Node availability
Heaters the box reports as unreachable (node-level `sync_status: "lost"`) show as Unavailable on all of their entities, so a heater whose power is cut is visible at a glance. Entities come back as Available shortly after the node answers again.

### Sensors
* Temperature (current)
* Power (including PMO dedicated energy monitors)
* Duty cycle (`htr` nodes; disabled by default)
* Total consumption (energy, with recorder statistics import - see [Consumption](#consumption))
* Charge level (`acm` nodes)
* Boost end time (timestamp)
* Schedule (enum, per heater node): the state is the profile the programme asks for now; the `schedule` attribute holds the full week
* RTC clock drift (box-level, refreshed every 5 minutes; disabled by default)

### Binary sensor
One box-level connectivity sensor (per-node connectivity is shown through the climate entities' availability instead).

### Switches
* Box level: away mode; "no power limit" only on devices that support a box power limit
* Per node: window mode, true radiant, boost, child lock (keypad lockout), maximum temperature limit (remembers the last non-zero limit across Home Assistant restarts), "no node power limit" (on the PMO nodes whose setup carries the key)

### Numbers
* Box power limit on devices that support it (0 = no limit, max 60000)
* Per node: node power limit (PMO nodes whose setup carries the key), boost temperature, boost duration, away offset, maximum temperature, and programme temperatures (frost / eco / comfort, where the device reports them)

### Selects
* Per node: radiator priority (low / medium / high, fw-1.9 family only) — the device takes it into account itself when enforcing the box power limit

Boxless heaters (heaters not paired with a Smartbox controller) keep the parent device, away mode, connectivity and their node entities, but do not get the box power-limit number or no-limit switch.

### Dedicated energy monitor
The PMO devices are available including the power limit entity.

### Consumption
The smartbox API is only giving the hourly consumption from a start and an end period of time. You can't have real time consumption of a device and this consumption is always increasing.

At every beginning of an hour, during around 15/20 minutes, the API do not provide data for the current hour. So we always ask for the last three hours and report the most recent counter in that window, so a missing hour cannot make the consumption drop.

Every 15 minutes, we are updating data sensor with the most recent data. You are able to see the consumption directly into the history graph of the sensor.

But to be sure we ensure the right data to the right hour, we also get the last 24 hours and upsert these data into statistics to avoid time difference and some data drop.
> [!TIP]
> If you don't want to upsert these 24 hours, you have to set the [option](#consumption-history-options) to `off`.

#### History
The first time we create a config entry (or when the [option](#consumption-history-options) of the config entry is set to `start`) we get the last 3 years of consumption. As it is not possible to add it directly to the sensor data, we insert it into the statistics of the sensor. So it let the energy dashboard working with the current and back history.


> [!TIP]
> If you want to reset all the data, you have to set the [option](#consumption-history-options) to `start`.

## FAQ
#### There is negative consumption in the energy dashboard
There might be a huge negative consumption in your energy dashboard. The consumption [history](#history) should deal with it. But sometimes it didn't work. You have two options:
* Settings the [option](#consumption-history-options) to `start` : it will force load all data.
* Go to [![Open your Home Assistant instance and show your statistics developer tools.](https://my.home-assistant.io/badges/developer_statistics.svg)](https://my.home-assistant.io/redirect/developer_statistics/), select the total consumption entity, outliers and patch the negative value with 0.

#### My Reseller is not present in the list
If your reseller (which uses a smartbox) is not in the list, open a [Reseller Github issue] on this integration's tracker.

## Debugging

### Manual connection overrides

For testing a vendor-side host or tenant change before it lands in a release,
edit `custom_components/smartbox/reseller.py` and add an entry to
`RESELLER_OVERRIDES`, keyed by the config entry's API name. The fields are
`api_host` (hostname or `https://` URL), `basic_auth`, `serial_id` and
`web_url` (the referer); these values replace the built-in row. Leave the
table empty for normal operation. The edit is overwritten by an integration
update; use it only for a controlled test. The `smartbox` CLI exposes the
same overrides plus `--api-host`.

### Diagnostics

If there is an error and raise an issue in [Github issue], please attach the diagnostics of the entry :
* Go on [![Open your Home Assistant instance and show an integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=smartbox)
* Click on the three dots of the config entry
* Download diagnostics

The diagnostics file is grouped by the question it answers:

| Block | What it tells you |
| --- | --- |
| `entry` | The redacted config entry: reseller key, with username, password, title, unique ID and device IDs removed. |
| `runtime.transport` | Whether the websocket is running, the short-cycle and auth-rejection streaks, and the last close code/reason. |
| `runtime.supervision` | Whether the failure is terminal (`unsupported`, `auth_failed`, `terminal_error`) or still retrying. |
| `runtime.token_expiry` | When the current access token expires. |
| `runtime.token_origin` | Whether setup resumed the stored bundle (`restored`) or minted a fresh login (`granted`). |
| `runtime.feed` | Whether values are flowing, the reason and duration of a gap, the outage count, and the grace window. |
| `runtime.membership` | Nodes still awaiting materialization and whether the reload repair issue is raised. |
| `runtime.statistics_import` | Which first-setup imports are pending or completed, and the failure count, last error and remaining backoff. |
| `runtime.engine` | Raw device and node slots (device IDs replaced by `device_N` labels), plus the armed deadlines: the direct answer to “why is this entity unavailable?”. |
| `ws_user_recovery` | Whether the websocket repair issue is raised. |
| `hass_devices` / `hass_entities` | Home Assistant’s registry view: entity IDs and registry state, with device identifiers, serials, deep links and unique IDs redacted. |

A `runtime: null` means the entry is failing or retrying setup: there is no live client to inspect. Start with `entry`, `ws_user_recovery` and the Home Assistant logs in that case.

### System health

You can see if all the smartbox component are available here [![Open your Home Assistant instance and show information about your system.](https://my.home-assistant.io/badges/system_health.svg)](https://my.home-assistant.io/redirect/system_health/)

### Logs

Enable debug logging from the integration page: **Settings** > **Devices & services** > **Smartbox** > the three-dot menu > **Enable debug logging**. The manifest declares the `smartbox` library logger, so this enables debug logging for the integration modules and the library's `smartbox.protocol` and `smartbox.core` layers.

On Home Assistant 2026.11 and later, the debug banner on the integration page has a **Show logs** button. It opens the Smartbox Live log, filtered to the integration and its library lines.

Debug logging can also be set in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.smartbox: debug
    smartbox: debug
```

See the [Home Assistant logger docs] for how to view the actual logs. Please file a [Github issue] with any problems.

## Development / live probing

For wire investigation against the real hosts (without a live HA deploy), `tools/live_probe.py` drives the installed `smartbox.protocol` layer from the CLI. It needs YOUR OWN `.env` file at the repo root (gitignored) with:

```
SMARTBOX_API_NAME=...
SMARTBOX_USERNAME=...
SMARTBOX_PASSWORD=...
```

(The reseller table supplies the Basic-auth/serial-id/referer values, so no extra key is needed.) Tokens, credentials and device IDs are masked in the output; a token is never printed - only its length, expiry and claim facts are.

Commands:

- `uv run python tools/live_probe.py --token-cache PATH <command>` —
  reuse one vendor session across runs (`auth` ignores the cache: it
  exists to exercise fresh grants). The record is stamped with the
  reseller/account and effective API host, so a shared path cannot restore
  another account's session; the file is `0600`, written atomically, and
  holds a live refresh token.
- `uv run python tools/live_probe.py auth` — password grant + one
  refresh grant (rotation check).
- `... probe` — ws_user capability probe verdict.
- `... reads [--dev DEV_ID] [--node TYPE:ADDR]` — grouped devices, then per node the raw status/setup/version JSON (and `--samples-from/--samples-to` for the samples window).
- `... ws [--duration 60] [--heartbeat 60]` — subscribe the ws_user transport; event counts, bounded update previews, transport facts.
- `... write --dev DEV_ID --node TYPE:ADDR --i-know-this-writes-to-my-heater <op> ...` — the CLI's WriteApi ops (`set_mode`, `set_setpoint`, `engage_modified_auto`, `set_lock`, `set_boost`, `set_boost_mode`, `set_prog_temps`, `set_window_mode`, `set_true_radiant`, `set_away_offset`, `set_max_stemp_limit`, `set_priority`, `set_away`, `set_power_limit`, `set_node_power_limit`, `set_prog`); prints the exact body before sending, then GETs the affected resource (the server's read-through may lag several seconds and show the old value).
- `... all` — probe + reads (the daily sanity menu).
> [!NOTE]
> The initial version of this integration was made by [graham33](https://github.com/graham33) but it was not maintained.

# Support
[![Buy a coffee to ajtudela][buymeacoffee-shield]][buymeacoffee-ajtudela]

[buymeacoffee-ajtudela]: https://www.buymeacoffee.com/ajtudela

[buymeacoffee-shield]: https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png
[custom repository]: https://hacs.xyz/docs/faq/custom_repositories
[Github issue]: https://github.com/ajtudela/hass-smartbox/issues
[Reseller Github issue]: https://github.com/ajtudela/hass-smartbox/issues/new?labels=reseller&title=New%20reseller
[Home Assistant integration docs]: https://developers.home-assistant.io/docs/creating_integration_file_structure/#where-home-assistant-looks-for-integrations
[Home Assistant logger docs]: https://www.home-assistant.io/integrations/logger/#viewing-logs
[Home Assistant secrets management]: https://www.home-assistant.io/docs/configuration/secrets/
