# hass-smartbox
![hassfest](https://github.com/ajtudela/hass-smartbox/workflows/Validate%20with%20hassfest/badge.svg) [![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration) [![codecov](https://codecov.io/gh/ajtudela/hass-smartbox/branch/main/graph/badge.svg?token=C6J448TUQ8)](https://codecov.io/gh/ajtudela/hass-smartbox) [![Total downloads](https://img.shields.io/github/downloads/ajtudela/hass-smartbox/total)](https://github.com/ajtudela/hass-smartbox/releases) [![Downloads of latest version (latest by SemVer)](https://img.shields.io/github/downloads/ajtudela/hass-smartbox/latest/total?sort=semver)](https://github.com/ajtudela/hass-smartbox/releases/latest) [![Current version](https://img.shields.io/github/v/release/ajtudela/hass-smartbox)](https://github.com/ajtudela/hass-smartbox/releases/latest)


Home Assistant integration for Haverland (and other brands) heating smartboxes.


## Installation

### Using HACS (Recommended)

1. Add this repository to your custom repositories
1. Search for and install "Smartbox" in HACS.
1. Restart Home Assistant.


### Manually Copy Files

1. Using the tool of choice open the directory (folder) for your HA configuration (where you find `configuration.yaml`).
1. If you do not have a `custom_components` directory (folder) there, you need to create it.
1. In the `custom_components` directory (folder) create a new folder called `smartbox`.
1. Download _all_ the files from the `custom_components/smartbox/` directory (folder) in this repository.
1. Place the files you downloaded in the new directory (folder) you created.
1. Restart Home Assistant


### Finally

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=smartbox)


## Configuration

You will need the following items of information:
* Name of your reseller
* Your username and password used for the mobile app/web app.

If there is an issue during the process or authentication, the errors will be displayed.

### Additional Options
You can also specify the following options (although they have reasonable defaults)

#### Consumption history options
We are currently getting the [consumption](#consumption) of device throuw the API and we inject it in statistics and TotalConsumption sensor
* `start` : we will get the last 3 years of consumption and set the option to auto.
* `auto` : every hour, we get the last 24 hours.
* `off` : stop the automatic collect. We will still update the sensor every hour.

#### Reseller logo
By default, each sensor has in own icon depends on the type of the sensor.
You can activate this option to display the logo of the reseller instead.

#### Timedelta between update power
If you have a [Dedicated energy monitor](#dedicated-energy-monitor), we get the current power each 60 seconds by default.
You can update this time with this option.
> [!NOTE]
> Be carefull with this option, reduce the number little by little to see if any instability occurs.

## Features

### Dedicated energy monitor
The PMO devices are available including the power limit entity.

### Heaters Supported Node types
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

### Consumption
The smartbox API is only giving the hourly consumption from a start and an end period of time.
You can't have real time consumption of a device and this consumption is always increasing.

At every beginning of an hour, during around 15/20 minutes, the API do not provide data for the current hour.
So we always get a period of two hour to have at least some data, and get the most recent one to not have drop of consumption.

Every 15 minutes, we are updating data sensor with the most recent data. You are able to see the consumption directly into the history graph of the sensor.

But to be sure we ensure the right data to the right hour, we also get the last 24 hours and upsert these data into statistics to avoid time difference and some data drop.
> [!TIP]
> If you don't want to upsert these 24 hours, you have to set the [option](#consumption-history-options) to `off`.

#### History
The first time we create a config entry (or when the [option](#consumption-history-options) of the config entry is set to `start`) we get the last 3 years of consumption.
As it is not possible to add it directly to the sensor data, we insert it into the statistics of the sensor.
So it let the energy dashboard working with the current and back history.


> [!TIP]
> If you want to reset all the data, you have to set the [option](#consumption-history-options) to `start`.

## FAQ
#### There is negative consumption in the energy dashboard
There might be a huge negative consumption in your energy dashboard. The consumption [history](#history) should deal with it. But sometimes it didn't work.
You have two options:
* Settings the [option](#consumption-history-options) to `start` : it will force load all data.
* Go to [![Open your Home Assistant instance and show your statistics developer tools.](https://my.home-assistant.io/badges/developer_statistics.svg)](https://my.home-assistant.io/redirect/developer_statistics/), select the total consumption entity, outliers and patch the negative value with 0.

#### My Reseailer is not present in the list
If you can't see you reseller which is using a smartbox you have to do an [Reseller Github issue].

## Debugging

### Diagnostics

If there is an error and raise an issue in [Github issue], please attach the diagnostics of the entry :
* Go on [![Open your Home Assistant instance and show an integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=smartbox)
* Click on the three dots of the config entry
* Download diagnostics

### System health

You can see if all the smartbox component are available here [![Open your Home Assistant instance and show information about your system.](https://my.home-assistant.io/badges/system_health.svg)](https://my.home-assistant.io/redirect/system_health/)

### Logs
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

> [!WARNING]
> Currently logs might include credentials, so please be careful when
sharing excerpts from logs

See the [Home Assistant logger docs] for how to view the actual logs. Please
file a [Github issue] with any problems.


> [!NOTE]
> The initial version of this integration was made by [graham33](https://github.com/graham33) but it was not maintained.

# Support
Ajtudela [![Buy a coffee to ajtudela][buymeacoffee-shield]][buymeacoffee-ajtudela]

Delmael [![Buy a coffee to delmael][buymeacoffee-shield]][buymeacoffee-delmael]

[buymeacoffee-ajtudela]: https://www.buymeacoffee.com/ajtudela
[buymeacoffee-delmael]: https://www.buymeacoffee.com/delmael

[buymeacoffee-shield]: https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png
[custom repository]: https://hacs.xyz/docs/faq/custom_repositories
[Github issue]: https://github.com/ajtudela/hass-smartbox/issues
[Reseller Github issue]: https://github.com/ajtudela/smartbox/issues/new?template=new-reseller.md
[Home Assistant integration docs]: https://developers.home-assistant.io/docs/creating_integration_file_structure/#where-home-assistant-looks-for-integrations
[Home Assistant logger docs]: https://www.home-assistant.io/integrations/logger/#viewing-logs
[Home Assistant secrets management]: https://www.home-assistant.io/docs/configuration/secrets/
[smartbox]: https://github.com/ajtudela/smartbox
