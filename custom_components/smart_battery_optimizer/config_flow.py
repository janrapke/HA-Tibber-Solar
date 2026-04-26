"""Config flow for Smart Battery Optimizer integration."""
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_TIBBER_API_TOKEN,
    CONF_TIBBER_PRICE_SENSOR,
    CONF_TIBBER_CONSUMPTION_SENSOR,
    CONF_TIBBER_EXPORT_SENSOR,
    CONF_BATTERY_LEVEL_SENSOR,
    CONF_SOLAR_POWER_SENSOR,
    CONF_BALCONY_POWER_SENSOR,
    CONF_OPENDTU_TURN_ON_BUTTON,
    CONF_OPENDTU_TURN_OFF_BUTTON,
    CONF_OPENDTU_DPL_MODE_SELECT,
    CONF_OPENDTU_PRODUCING_SENSOR,
    CONF_OPENDTU_OUTPUT_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_SOLAR_CHARGE_STATE_SENSOR,
    CONF_BATTERY_CAPACITY_WH,
    CONF_BATTERY_MIN_LIMIT_PCT,
    CONF_BATTERY_EFFICIENCY_PCT,
    CONF_BASE_LOAD_W,
    CONF_SOLAR_PEAK_W,
    CONF_EXTREME_PRICE_THRESHOLD,
    CONF_MAX_INVERTER_POWER_W,
    CONF_EXCLUDED_POWER_SENSORS,
    CONF_PRIMARY_EXCESS_CONSUMERS,
    CONF_SECONDARY_EXCESS_CONSUMERS,
    CONF_EARLY_EXCESS_CONSUMERS,
    CONF_EARLY_EXCESS_EXPECTED_POWER_W,
    CONF_EARLY_EXCESS_MIN_BATTERY_PCT,
    CONF_EXCESS_EXTERNAL_INVERTER,
    CONF_SMART_DEVICES,
)

def get_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the schema with default values populated."""
    if defaults is None:
        defaults = {}

    ext_inv_default = defaults.get(CONF_EXCESS_EXTERNAL_INVERTER, vol.UNDEFINED)
    if isinstance(ext_inv_default, bool):
        ext_inv_default = vol.UNDEFINED

    return vol.Schema(
        {
            vol.Required(CONF_TIBBER_API_TOKEN, default=defaults.get(CONF_TIBBER_API_TOKEN, "")): str,
            vol.Required(CONF_TIBBER_PRICE_SENSOR, default=defaults.get(CONF_TIBBER_PRICE_SENSOR, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_TIBBER_CONSUMPTION_SENSOR, default=defaults.get(CONF_TIBBER_CONSUMPTION_SENSOR, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            vol.Required(CONF_TIBBER_EXPORT_SENSOR, default=defaults.get(CONF_TIBBER_EXPORT_SENSOR, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            vol.Required(CONF_BATTERY_LEVEL_SENSOR, default=defaults.get(CONF_BATTERY_LEVEL_SENSOR, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="battery")
            ),
            vol.Required(CONF_SOLAR_POWER_SENSOR, default=defaults.get(CONF_SOLAR_POWER_SENSOR, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power", multiple=True)
            ),
            vol.Optional(CONF_BALCONY_POWER_SENSOR, default=defaults.get(CONF_BALCONY_POWER_SENSOR, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            vol.Required(CONF_OPENDTU_TURN_ON_BUTTON, default=defaults.get(CONF_OPENDTU_TURN_ON_BUTTON, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="button")
            ),
            vol.Required(CONF_OPENDTU_TURN_OFF_BUTTON, default=defaults.get(CONF_OPENDTU_TURN_OFF_BUTTON, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="button")
            ),
            vol.Optional(CONF_OPENDTU_DPL_MODE_SELECT, default=defaults.get(CONF_OPENDTU_DPL_MODE_SELECT, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["select", "number", "input_select", "input_number"])
            ),
            vol.Required(CONF_OPENDTU_PRODUCING_SENSOR, default=defaults.get(CONF_OPENDTU_PRODUCING_SENSOR, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
            vol.Required(CONF_OPENDTU_OUTPUT_SENSOR, default=defaults.get(CONF_OPENDTU_OUTPUT_SENSOR, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")
            ),
            vol.Required(CONF_WEATHER_ENTITY, default=defaults.get(CONF_WEATHER_ENTITY, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="weather")
            ),
            vol.Optional(CONF_SOLAR_CHARGE_STATE_SENSOR, default=defaults.get(CONF_SOLAR_CHARGE_STATE_SENSOR, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_BATTERY_CAPACITY_WH, default=defaults.get(CONF_BATTERY_CAPACITY_WH, 5000)): int,
            vol.Required(CONF_BATTERY_MIN_LIMIT_PCT, default=defaults.get(CONF_BATTERY_MIN_LIMIT_PCT, 10)): vol.All(int, vol.Range(min=0, max=100)),
            vol.Required(CONF_BATTERY_EFFICIENCY_PCT, default=defaults.get(CONF_BATTERY_EFFICIENCY_PCT, 90)): vol.All(int, vol.Range(min=1, max=100)),
            vol.Required(CONF_BASE_LOAD_W, default=defaults.get(CONF_BASE_LOAD_W, 250)): int,
            vol.Required(CONF_SOLAR_PEAK_W, default=defaults.get(CONF_SOLAR_PEAK_W, 6000)): int,
            vol.Required(CONF_MAX_INVERTER_POWER_W, default=defaults.get(CONF_MAX_INVERTER_POWER_W, 800)): int,
            vol.Required(CONF_EXTREME_PRICE_THRESHOLD, default=defaults.get(CONF_EXTREME_PRICE_THRESHOLD, 0.40)): vol.Coerce(float),
            vol.Optional(CONF_EXCLUDED_POWER_SENSORS, default=defaults.get(CONF_EXCLUDED_POWER_SENSORS, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power", multiple=True)
            ),
            vol.Optional(CONF_PRIMARY_EXCESS_CONSUMERS, default=defaults.get(CONF_PRIMARY_EXCESS_CONSUMERS, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch", multiple=True)
            ),
            vol.Optional(CONF_SECONDARY_EXCESS_CONSUMERS, default=defaults.get(CONF_SECONDARY_EXCESS_CONSUMERS, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch", multiple=True)
            ),
            vol.Optional(CONF_EARLY_EXCESS_CONSUMERS, default=defaults.get(CONF_EARLY_EXCESS_CONSUMERS, vol.UNDEFINED)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch", multiple=True)
            ),
            vol.Required(CONF_EARLY_EXCESS_EXPECTED_POWER_W, default=defaults.get(CONF_EARLY_EXCESS_EXPECTED_POWER_W, 400)): int,
            vol.Required(CONF_EARLY_EXCESS_MIN_BATTERY_PCT, default=defaults.get(CONF_EARLY_EXCESS_MIN_BATTERY_PCT, 30)): vol.All(int, vol.Range(min=0, max=100)),
            vol.Optional(CONF_EXCESS_EXTERNAL_INVERTER, default=ext_inv_default): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="switch", multiple=True)
            ),
            vol.Optional(CONF_SMART_DEVICES, default=defaults.get(CONF_SMART_DEVICES, "")): str,
        }
    )

class SmartBatteryOptimizerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Battery Optimizer."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return SmartBatteryOptimizerOptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Basic validation could go here
            return self.async_create_entry(title="Smart Battery Optimizer", data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=get_schema(), errors=errors
        )

class SmartBatteryOptimizerOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Smart Battery Optimizer."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        pass  # config_entry is now managed by the base class

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Combine existing config and options (options take precedence)
        current_config = dict(self.config_entry.data)
        current_config.update(self.config_entry.options)

        return self.async_show_form(
            step_id="init",
            data_schema=get_schema(current_config),
            errors=errors,
        )
