"""Constants for the Smartbox integration."""

from enum import Enum, StrEnum

from smartbox import SmartboxNodeType

DOMAIN = "smartbox"

CONF_API_NAME = "api_name"
CONF_DISPLAY_ENTITY_PICTURES = "resailer_entity"
CONF_TIMEDELTA_POWER = "timedelta_update_power"

DEFAULT_TIMEDELTA_POWER = 60
DEFAULT_BOOST_TIME = 60
DEFAULT_BOOST_TEMP = 21.0
GITHUB_ISSUES_URL = "https://github.com/ajtudela/hass-smartbox/issues"

HEATER_NODE_TYPES = [
    SmartboxNodeType.ACM,
    SmartboxNodeType.HTR,
    SmartboxNodeType.HTR_MOD,
]


PRESET_FROST = "frost"
PRESET_SCHEDULE = "schedule"
PRESET_SELF_LEARN = "self_learn"
PRESET_BOOST = "boost"

SMARTBOX_DEVICES = "smartbox_devices"
SMARTBOX_NODES = "smartbox_nodes"
SMARTBOX_SESSIONS = "smartbox_sessions"

CONF_HISTORY_CONSUMPTION = "history_consumption"


class HistoryConsumptionStatus(StrEnum):
    """Config Consumption History Status."""

    START = "start"
    AUTO = "auto"
    OFF = "off"


class BoostConfig(Enum):
    """Boost configuration."""

    UNSUPPORTED = 0
    BASIC = 1
    FULL = 2
