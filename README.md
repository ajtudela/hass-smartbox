# hass-smartbox ![hassfest](https://github.com/graham33/hass-smartbox/workflows/Validate%20with%20hassfest/badge.svg) [![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration) [![codecov](https://codecov.io/gh/graham33/hass-smartbox/branch/main/graph/badge.svg?token=F3VFCU9WPA)](https://codecov.io/gh/graham33/hass-smartbox)
Home Assistant integration for heating smartboxes.

## Installation
This integration uses the [smartbox] Python module, so make sure to install that
into your Home Assistant python environment first. If you are using hass.io, the
module should be automatically installed from github via the reference in the
`manifest.json` file.

Then, install the `custom_components/smartbox` directory in this repo to your
Home Assistant `custom_components` directory. See the [Home Assistant
integration docs] for details.

### HACS
Initial support is available for installation via HACS, as a [custom
repository].

## Configuration
**Note: currently only YAML-based configuration is supported, the UI-based
config flow is not yet implemented.**

Add a section like the following to your Home Assistant config.yaml:

```
smartbox:
  accounts:
  - api_name: <api name>
    username: <api username>
    password: <api password>
    device_ids:
    - <device id>
  basic_auth_creds: <basic auth credentials>
```

You will need the following items of information:
* The API name for your heater vendor. This is visible in the 'API Host' entry
  in the 'Version' menu item in the mobile app/web app. If the host name is of
  the form `api-foo.xxxx` or `api.xxxx` use the values `api-foo` or `api`
  respectively.
* Your username and password used for the mobile app/web app.
* Your smartbox device ID, which is shows as the 'Device ID Code' under My
  Devices in the Home section of the mobile app/web app.
* Basic auth credentials: this is an HTTP Basic Auth credential used to do
  initial authentication with the server. Use the base64 encoded string
  directly. See 'Basic Auth Credential' section below for more details.

It's recommended that you store credentials using the built-in [Home Assistant
secrets management].

### Basic Auth Credential
Initial authentication to the smartbox REST API is protected by HTTP Basic Auth,
in addition to the user's username and password which are then used to obtain an
access token. In order not to undermine the security layer it provides, and also
because it might change over time or vary between implementations, **the token
is not provided here and system owners need to find it themselves**.

### Additional Options
You can also specify the following options (although they have reasonable defaults):

```
smartbox:
  accounts:
  - ...
    session_retry_attempts: 8 # how many times to retry session REST operations
    session_backoff_factor: 0.1 # how much to backoff between REST retries
    socket_reconnect_attempts: 3 # how many times to try reconnecting the socket.io socket
    socket_backoff_factor: 0.1 # how much to backoff between initial socket connect attempts
```

### Use in energy dashboard

To use the values in the Energy dashboard of Home Assistant, you have to
aggregate the power sensors into energy sensors via an integration
(https://www.home-assistant.io/integrations/integration/#energy)

*WARNING*: currently the power sensors created by this component seem to
misreport power usage quite significantly. This is because they record power
used when the status of the device is 'active', but status updates are very
infrequent and so this won't be accurate.

```
sensor:
  - platform: integration
    source: sensor.living_room
    name: energy_living_room
    unit_prefix: k
    round: 2
    method: left
```

The resulting sensor 'sensor.energy_living_room' can then be added to the Energy dashboard as soon as it has values.

## Supported Node types

### Heaters
These are modelled as Home Assistant Climate entities.

* `htr` and `acm` (accumulator) nodes
  * Supported modes: 'manual' and 'auto'
  * Supported presets: 'home and 'away'
* `htr_mod`
  * Supported modes: 'manual', 'auto', 'self_learn' and 'presence'
  * Supported presets: 'away', 'comfort', 'eco', 'ice' and 'away'

The modes and presets for htr_mod heaters are mapped as follows:

| htr\_mod mode | htr\_mod selected_temp | HA HVAC mode | HA preset   |
|---------------|------------------------|--------------|-------------|
| manual        | comfort                | HEAT         | COMFORT     |
|               | eco                    | HEAT         | ECO         |
|               | ice                    | HEAT         | ICE         |
| auto          | *                      | AUTO         | SCHEDULE    |
| self\_learn   | *                      | AUTO         | SELF\_LEARN |
| presence      | *                      | AUTO         | ACTIVITY    |

## Debugging

Debug logging can be enabled by increasing the log level for the smartbox custom
component and the underlying [smartbox] python module in the Home Assistant
`configuration.yaml`:

```
 logger:
   ...
   logs:
     custom_components.smartbox: debug
     smartbox: debug
   ...
```

**Warning: currently logs might include credentials, so please be careful when
sharing excerpts from logs**

See the [Home Assistant logger docs] for how to view the actual logs. Please
file a [Github issue] with any problems.

## TODO
* config_flow (only configured via yaml atm)
* Handle adding and removing entities properly
* Graceful cleanup/shutdown of update task

[custom repository]: https://hacs.xyz/docs/faq/custom_repositories
[Github issue]: https://github.com/graham33/hass-smartbox/issues
[Home Assistant integration docs]: https://developers.home-assistant.io/docs/creating_integration_file_structure/#where-home-assistant-looks-for-integrations
[Home Assistant logger docs]: https://www.home-assistant.io/integrations/logger/#viewing-logs
[Home Assistant secrets management]: https://www.home-assistant.io/docs/configuration/secrets/
[smartbox]: https://github.com/graham33/smartbox
