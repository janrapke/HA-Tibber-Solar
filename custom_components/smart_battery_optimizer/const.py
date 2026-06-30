"""Constants for the Smart Battery Optimizer integration."""

DOMAIN = "smart_battery_optimizer"
VERSION = "1.8.4"

# Configuration keys
CONF_TIBBER_API_TOKEN = "tibber_api_token"
CONF_TIBBER_PRICE_SENSOR = "tibber_price_sensor"
CONF_TIBBER_CONSUMPTION_SENSOR = "tibber_consumption_sensor"
CONF_TIBBER_EXPORT_SENSOR = "tibber_export_sensor"
CONF_BATTERY_LEVEL_SENSOR = "battery_level_sensor"
CONF_SOLAR_POWER_SENSOR = "solar_power_sensor"
CONF_BALCONY_POWER_SENSOR = "balcony_power_sensor"
CONF_OPENDTU_TURN_ON_BUTTON = "opendtu_turn_on_button"
CONF_OPENDTU_TURN_OFF_BUTTON = "opendtu_turn_off_button"
CONF_OPENDTU_PRODUCING_SENSOR = "opendtu_producing_sensor"
CONF_OPENDTU_OUTPUT_SENSOR = "opendtu_output_sensor"
CONF_OPENDTU_DPL_MODE_SELECT = "opendtu_dpl_mode_select"

# Wechselrichter-Profil
CONF_INVERTER_PROFILE = "inverter_profile"
INVERTER_PROFILE_OPENDTU = "opendtu"
INVERTER_PROFILE_POWERSTATION = "powerstation"
INVERTER_PROFILE_GENERIC = "generic_switch"

# Powerstation-Profil (EcoFlow, Anker, Bluetti, Victron All-in-One ...)
CONF_PS_DISCHARGE_POWER_ENTITY = "ps_discharge_power_entity"
CONF_PS_CHARGE_POWER_ENTITY = "ps_charge_power_entity"
CONF_PS_AC_OUTPUT_SWITCH = "ps_ac_output_switch"
CONF_PS_OUTPUT_SENSOR = "ps_output_sensor"

# Generisches Schalter-Profil
CONF_GENERIC_INVERTER_SWITCH = "generic_inverter_switch"
CONF_GENERIC_OUTPUT_SENSOR = "generic_output_sensor"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_SOLAR_CHARGE_STATE_SENSOR = "solar_charge_state_sensor"

CONF_BATTERY_CAPACITY_WH = "battery_capacity_wh"
CONF_BATTERY_MIN_LIMIT_PCT = "battery_min_limit_pct"
CONF_BATTERY_MAX_LIMIT_PCT = "battery_max_limit_pct"
CONF_BATTERY_EFFICIENCY_PCT = "battery_efficiency_pct"

CONF_BASE_LOAD_W = "base_load_w"
CONF_SOLAR_PEAK_W = "solar_peak_w"
CONF_EXTREME_PRICE_THRESHOLD = "extreme_price_threshold"
CONF_MAX_INVERTER_POWER_W = "max_inverter_power_w"

CONF_EXCLUDED_POWER_SENSORS = "excluded_power_sensors"
CONF_PRIMARY_EXCESS_CONSUMERS = "primary_excess_consumers"
CONF_SECONDARY_EXCESS_CONSUMERS = "secondary_excess_consumers"

CONF_EARLY_EXCESS_CONSUMERS = "early_excess_consumers"
CONF_EARLY_EXCESS_EXPECTED_POWER_W = "early_excess_expected_power_w"
CONF_EARLY_EXCESS_MIN_BATTERY_PCT = "early_excess_min_battery_pct"
CONF_EARLY_EXCESS_MAX_BATTERY_PCT = "early_excess_max_battery_pct" # Deprecated, use CONF_BATTERY_MAX_LIMIT_PCT

CONF_EXCESS_MIN_RUN_TIME_MINUTES = "excess_min_run_time_minutes"
CONF_MIN_SWITCH_INTERVAL_MINUTES = "min_switch_interval_minutes"  # Min time between any ON/OFF state change (1-15 min)

CONF_EXCESS_EXTERNAL_INVERTER = "excess_external_inverter"
CONF_SMART_DEVICES = "smart_devices"

CONF_GRID_CHARGER_SWITCH = "grid_charger_switch"
CONF_GRID_CHARGER_POWER_W = "grid_charger_power_w"

# Proactive night-discharge feature
CONF_PRESUNNY_SOLAR_MARGIN_PCT = "presunny_solar_margin_pct"

# Climate device learning
CONF_CLIMATE_DEVICES = "climate_devices"   # list of device dicts stored in options

# Number of degrees assumed for initial W/°C bootstrap (prior when no real data yet)
CLIMATE_BOOTSTRAP_ASSUMED_DELTA = 10.0

# Climate device type values (stored per device in CONF_CLIMATE_DEVICES)
CLIMATE_TYPE_HEATING = "Nur Heizen"
CLIMATE_TYPE_COOLING = "Nur Kühlen"
CLIMATE_TYPE_HEAT_PUMP = "Wärmepumpe (Heizen+Kühlen)"

# Internal type strings used by the learning engine
CLIMATE_INTERNAL_HEATING = "heating"
CLIMATE_INTERNAL_COOLING = "cooling"
CLIMATE_INTERNAL_HEAT_PUMP = "heat_pump"

# Map display labels → internal strings
CLIMATE_LABEL_TO_INTERNAL = {
    CLIMATE_TYPE_HEATING: CLIMATE_INTERNAL_HEATING,
    CLIMATE_TYPE_COOLING: CLIMATE_INTERNAL_COOLING,
    CLIMATE_TYPE_HEAT_PUMP: CLIMATE_INTERNAL_HEAT_PUMP,
}
CLIMATE_INTERNAL_TO_LABEL = {v: k for k, v in CLIMATE_LABEL_TO_INTERNAL.items()}
