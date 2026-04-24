"""Constants for the Smart Battery Optimizer integration."""

DOMAIN = "smart_battery_optimizer"
VERSION = "1.9.0"

# Configuration keys
CONF_TIBBER_API_TOKEN = "tibber_api_token"
CONF_TIBBER_PRICE_SENSOR = "tibber_price_sensor"
CONF_TIBBER_CONSUMPTION_SENSOR = "tibber_consumption_sensor"
CONF_TIBBER_EXPORT_SENSOR = "tibber_export_sensor"
CONF_BATTERY_LEVEL_SENSOR = "battery_level_sensor"
CONF_SOLAR_POWER_SENSOR = "solar_power_sensor"
CONF_BALCONY_POWER_SENSOR = "balcony_power_sensor"
CONF_OPENDTU_DPL_SWITCH = "opendtu_dpl_switch"
CONF_OPENDTU_TURN_ON_BUTTON = "opendtu_turn_on_button"
CONF_OPENDTU_TURN_OFF_BUTTON = "opendtu_turn_off_button"
CONF_OPENDTU_PRODUCING_SENSOR = "opendtu_producing_sensor"
CONF_OPENDTU_OUTPUT_SENSOR = "opendtu_output_sensor"
CONF_WEATHER_ENTITY = "weather_entity"
CONF_SOLAR_CHARGE_STATE_SENSOR = "solar_charge_state_sensor"

CONF_BATTERY_CAPACITY_WH = "battery_capacity_wh"
CONF_BATTERY_MIN_LIMIT_PCT = "battery_min_limit_pct"
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

CONF_SMART_DEVICES = "smart_devices"
