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
    CONF_OPENDTU_INVERTER_SWITCH,
    CONF_OPENDTU_OUTPUT_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_BATTERY_CAPACITY_WH,
    CONF_BATTERY_MIN_LIMIT_PCT,
    CONF_EXCLUDED_POWER_SENSORS,
    CONF_PRIORITIZED_EXCESS_CONSUMERS,
)

class SmartBatteryOptimizerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Battery Optimizer."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Basic validation could go here
            return self.async_create_entry(title="Smart Battery Optimizer", data=user_input)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_TIBBER_API_TOKEN): str,
                vol.Required(CONF_TIBBER_PRICE_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_TIBBER_CONSUMPTION_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Required(CONF_TIBBER_EXPORT_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Required(CONF_BATTERY_LEVEL_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="battery")
                ),
                vol.Required(CONF_SOLAR_POWER_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Required(CONF_OPENDTU_INVERTER_SWITCH): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="switch")
                ),
                vol.Required(CONF_OPENDTU_OUTPUT_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power")
                ),
                vol.Required(CONF_WEATHER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather")
                ),
                vol.Required(CONF_BATTERY_CAPACITY_WH, default=5000): int,
                vol.Required(CONF_BATTERY_MIN_LIMIT_PCT, default=10): vol.All(int, vol.Range(min=0, max=100)),
                vol.Optional(CONF_EXCLUDED_POWER_SENSORS): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="power", multiple=True)
                ),
                vol.Optional(CONF_PRIORITIZED_EXCESS_CONSUMERS): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="switch", multiple=True)
                ),
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )
