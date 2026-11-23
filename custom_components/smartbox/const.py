"""Constants for the Smartbox integration."""

from enum import StrEnum

from smartbox.core.engine import UNAVAILABLE_DELAY, WRITE_CONFIRM_TIMEOUT

DOMAIN = "smartbox"


class SmartboxNodeType(StrEnum):
    """Node type (wire values; pinned by tests/test_lib_contract.py)."""

    HTR = "htr"
    THM = "thm"
    HTR_MOD = "htr_mod"
    ACM = "acm"
    PMO = "pmo"


# Product codes (``version.pid[2:4]``) whose controller has different
# telemetry semantics (docs/api-contract.md §3.4).
PRODUCT_CODE_ELNUR_STEPPED_STORAGE = "14"
PRODUCT_CODE_WATER_STORAGE = "2A"
WATER_READY_LEVEL = 50
WATER_FULLY_READY_LEVEL = 100

ATTR_DURATION = "duration"
SERVICE_SET_BOOST_PARAMS = "set_boost_params"
SERVICE_SET_BOOST_MODE = "set_boost_mode"
SERVICE_SET_SCHEDULE = "set_schedule"
SERVICE_SET_CHARGING_SCHEDULE = "set_charging_schedule"
SERVICE_SET_THERMOSTAT_SCHEDULE = "set_thermostat_schedule"
FIELD_SCHEDULE = "schedule"
FIELD_ACTIVE_DAYS = "active_days"
FIELD_SLOT_1 = "slot_1"
FIELD_SLOT_2 = "slot_2"
# Schedule profile slot values; day keys are "0".."6" (Sunday..Saturday).
DAY_KEYS = ("0", "1", "2", "3", "4", "5", "6")
# Base ElectricHeater programme catalogue, in the vendor app's sorted order
# (docs/webapi-spec.md §5): 0=ICE, 1=ECO, 2=COMF. Per-controller catalogues
# (the four-profile Helki/Haverland families and the off-first htr_mod order)
# live in controller.py; this tuple is the default used by the sensor when a
# node has no decoded controller-specific list.
PROG_PROFILE_NAMES = ("frost", "eco", "comfort")
CONF_API_NAME = "api_name"
CONF_DISPLAY_ENTITY_PICTURES = "reseller_entity"
CONF_TIMEDELTA_POWER = "timedelta_update_power"

DEFAULT_TIMEDELTA_POWER = 60
DEFAULT_BOOST_TIME = 60
DEFAULT_BOOST_TEMP = 21.0
BOOST_TIME_MIN = 60
BOOST_TIME_STEP = 60
RUNBACK_TIME_DEFAULT = 30
RUNBACK_TIME_STEP = 30
RUNBACK_TIME_MAX = 1440
# Restore value when the maximum temperature limit is re-enabled with no
# remembered value (the wire erases max_stemp_limit on toggle-off); matches
# the number entity's Celsius slider cap.
MAX_TEMP_LIMIT_DEFAULT = 30.0
# Node-availability windows (seconds): the vendor app flags a node
# unreachable ~5-6 s after a command whose confirming frame never arrives, and
# gives a bare lost frame ~6 s to be followed by an ok frame
# (docs/api-contract.md §6.3). The engine owns these as constructor defaults;
# these names re-export them for the adapter and the test fixtures.
SMARTBOX_UNAVAILABLE_DELAY = UNAVAILABLE_DELAY
SMARTBOX_WRITE_CONFIRM_TIMEOUT = WRITE_CONFIRM_TIMEOUT
GITHUB_ISSUES_URL = "https://github.com/ajtudela/hass-smartbox/issues"

HEATER_NODE_TYPES = [
    SmartboxNodeType.ACM,
    SmartboxNodeType.HTR,
    SmartboxNodeType.HTR_MOD,
]

# The API's node-type enum. Product/UI categories in the per-tenant
# client-data/<serial>/capabilities.json (towel rails, WiFi heater variants,
# floor thermostats, water/solar storage, timers) are not wire node types
# (docs/webapi-spec.md §3.2, docs/api-contract.md §3).
WIRE_NODE_TYPES = frozenset(SmartboxNodeType)
# Node types that carry power and sample data. THM is excluded: the app's
# thermostat controller has no getPower()/status.power, and its per-node
# energy view sets showsKwh=false and shows usage time instead of kWh
# (docs/api-contract.md §5.2). Its samples still feed the home-level totals.
ENERGY_NODE_TYPES = frozenset(
    {
        SmartboxNodeType.HTR,
        SmartboxNodeType.HTR_MOD,
        SmartboxNodeType.ACM,
        SmartboxNodeType.PMO,
    }
)


PRESET_FROST = "frost"
PRESET_SCHEDULE = "schedule"
PRESET_SELF_LEARN = "self_learn"
PRESET_RUNBACK = "runback"
PRESET_ADVANCED_SLOT = "advanced_slot"

CONF_HISTORY_CONSUMPTION = "history_consumption"


class HistoryConsumptionStatus(StrEnum):
    """Config Consumption History Status."""

    START = "start"
    AUTO = "auto"
    OFF = "off"
