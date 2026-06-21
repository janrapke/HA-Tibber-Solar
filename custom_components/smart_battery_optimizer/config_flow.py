"""Config flow for Smart Battery Optimizer integration."""
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

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
    CONF_OPENDTU_PRODUCING_SENSOR,
    CONF_OPENDTU_OUTPUT_SENSOR,
    CONF_OPENDTU_DPL_MODE_SELECT,
    CONF_WEATHER_ENTITY,
    CONF_SOLAR_CHARGE_STATE_SENSOR,
    CONF_BATTERY_CAPACITY_WH,
    CONF_BATTERY_MIN_LIMIT_PCT,
    CONF_BATTERY_MAX_LIMIT_PCT,
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
    CONF_EARLY_EXCESS_MAX_BATTERY_PCT,
    CONF_EXCESS_MIN_RUN_TIME_MINUTES,
    CONF_EXCESS_EXTERNAL_INVERTER,
    CONF_SMART_DEVICES,
    CONF_GRID_CHARGER_SWITCH,
    CONF_GRID_CHARGER_POWER_W,
    CONF_CLIMATE_DEVICES,
    CLIMATE_TYPE_HEATING,
    CLIMATE_TYPE_COOLING,
    CLIMATE_TYPE_HEAT_PUMP,
    CLIMATE_LABEL_TO_INTERNAL,
)


def _get(key: str, d: dict, fallback=vol.UNDEFINED):
    val = d.get(key, fallback)
    return fallback if isinstance(val, bool) else val


def _schema_tibber(d: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_TIBBER_API_TOKEN, default=_get(CONF_TIBBER_API_TOKEN, d, "")): str,
        vol.Required(CONF_TIBBER_PRICE_SENSOR, default=_get(CONF_TIBBER_PRICE_SENSOR, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
        vol.Required(CONF_TIBBER_CONSUMPTION_SENSOR, default=_get(CONF_TIBBER_CONSUMPTION_SENSOR, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="power")
        ),
        vol.Required(CONF_TIBBER_EXPORT_SENSOR, default=_get(CONF_TIBBER_EXPORT_SENSOR, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="power")
        ),
    })


def _schema_battery(d: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_BATTERY_LEVEL_SENSOR, default=_get(CONF_BATTERY_LEVEL_SENSOR, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="battery")
        ),
        vol.Required(CONF_BATTERY_CAPACITY_WH, default=_get(CONF_BATTERY_CAPACITY_WH, d, 5000)): int,
        vol.Required(CONF_BATTERY_MIN_LIMIT_PCT, default=_get(CONF_BATTERY_MIN_LIMIT_PCT, d, 10)): vol.All(int, vol.Range(min=0, max=100)),
        vol.Required(CONF_BATTERY_MAX_LIMIT_PCT, default=_get(CONF_BATTERY_MAX_LIMIT_PCT, d, d.get(CONF_EARLY_EXCESS_MAX_BATTERY_PCT, 99))): vol.All(int, vol.Range(min=0, max=100)),
        vol.Required(CONF_BATTERY_EFFICIENCY_PCT, default=_get(CONF_BATTERY_EFFICIENCY_PCT, d, 90)): vol.All(int, vol.Range(min=1, max=100)),
        vol.Optional(CONF_SOLAR_CHARGE_STATE_SENSOR, default=_get(CONF_SOLAR_CHARGE_STATE_SENSOR, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor")
        ),
    })


def _schema_solar_inverter(d: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_SOLAR_POWER_SENSOR, default=_get(CONF_SOLAR_POWER_SENSOR, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="power", multiple=True)
        ),
        vol.Optional(CONF_BALCONY_POWER_SENSOR, default=_get(CONF_BALCONY_POWER_SENSOR, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="power")
        ),
        vol.Required(CONF_OPENDTU_TURN_ON_BUTTON, default=_get(CONF_OPENDTU_TURN_ON_BUTTON, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="button")
        ),
        vol.Required(CONF_OPENDTU_TURN_OFF_BUTTON, default=_get(CONF_OPENDTU_TURN_OFF_BUTTON, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="button")
        ),
        vol.Required(CONF_OPENDTU_PRODUCING_SENSOR, default=_get(CONF_OPENDTU_PRODUCING_SENSOR, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor")
        ),
        vol.Required(CONF_OPENDTU_OUTPUT_SENSOR, default=_get(CONF_OPENDTU_OUTPUT_SENSOR, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="power")
        ),
        vol.Optional(CONF_OPENDTU_DPL_MODE_SELECT, default=_get(CONF_OPENDTU_DPL_MODE_SELECT, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["select", "number", "input_number", "input_select"])
        ),
        vol.Required(CONF_MAX_INVERTER_POWER_W, default=_get(CONF_MAX_INVERTER_POWER_W, d, 800)): int,
    })


def _schema_system(d: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_WEATHER_ENTITY, default=_get(CONF_WEATHER_ENTITY, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather")
        ),
        vol.Required(CONF_BASE_LOAD_W, default=_get(CONF_BASE_LOAD_W, d, 250)): int,
        vol.Required(CONF_SOLAR_PEAK_W, default=_get(CONF_SOLAR_PEAK_W, d, 6000)): int,
        vol.Required(CONF_EXTREME_PRICE_THRESHOLD, default=_get(CONF_EXTREME_PRICE_THRESHOLD, d, 0.40)): vol.Coerce(float),
        vol.Optional(CONF_EXCLUDED_POWER_SENSORS, default=_get(CONF_EXCLUDED_POWER_SENSORS, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", device_class="power", multiple=True)
        ),
    })


def _schema_excess(d: dict) -> vol.Schema:
    ext_inv_default = d.get(CONF_EXCESS_EXTERNAL_INVERTER, vol.UNDEFINED)
    if isinstance(ext_inv_default, bool):
        ext_inv_default = vol.UNDEFINED

    return vol.Schema({
        vol.Optional(CONF_PRIMARY_EXCESS_CONSUMERS, default=_get(CONF_PRIMARY_EXCESS_CONSUMERS, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch", multiple=True)
        ),
        vol.Optional(CONF_SECONDARY_EXCESS_CONSUMERS, default=_get(CONF_SECONDARY_EXCESS_CONSUMERS, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch", multiple=True)
        ),
        vol.Optional(CONF_EARLY_EXCESS_CONSUMERS, default=_get(CONF_EARLY_EXCESS_CONSUMERS, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch", multiple=True)
        ),
        vol.Required(CONF_EARLY_EXCESS_EXPECTED_POWER_W, default=_get(CONF_EARLY_EXCESS_EXPECTED_POWER_W, d, 400)): int,
        vol.Required(CONF_EARLY_EXCESS_MIN_BATTERY_PCT, default=_get(CONF_EARLY_EXCESS_MIN_BATTERY_PCT, d, 30)): vol.All(int, vol.Range(min=0, max=100)),
        vol.Required(CONF_EXCESS_MIN_RUN_TIME_MINUTES, default=_get(CONF_EXCESS_MIN_RUN_TIME_MINUTES, d, 10)): vol.All(int, vol.Range(min=0, max=120)),
        vol.Optional(CONF_EXCESS_EXTERNAL_INVERTER, default=ext_inv_default): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch", multiple=True)
        ),
        vol.Optional(CONF_SMART_DEVICES, default=_get(CONF_SMART_DEVICES, d, "")): str,
    })


def _schema_grid_charger(d: dict) -> vol.Schema:
    return vol.Schema({
        vol.Optional(CONF_GRID_CHARGER_SWITCH, default=_get(CONF_GRID_CHARGER_SWITCH, d)): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch")
        ),
        vol.Required(CONF_GRID_CHARGER_POWER_W, default=_get(CONF_GRID_CHARGER_POWER_W, d, 1000)): int,
    })


class SmartBatteryOptimizerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Battery Optimizer."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return SmartBatteryOptimizerOptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_battery()
        return self.async_show_form(step_id="user", data_schema=_schema_tibber(self._data))

    async def async_step_battery(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_solar_inverter()
        return self.async_show_form(step_id="battery", data_schema=_schema_battery(self._data))

    async def async_step_solar_inverter(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_system()
        return self.async_show_form(step_id="solar_inverter", data_schema=_schema_solar_inverter(self._data))

    async def async_step_system(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_excess()
        return self.async_show_form(step_id="system", data_schema=_schema_system(self._data))

    async def async_step_excess(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_grid_charger()
        return self.async_show_form(step_id="excess", data_schema=_schema_excess(self._data))

    async def async_step_grid_charger(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title="Smart Battery Optimizer", data=self._data)
        return self.async_show_form(step_id="grid_charger", data_schema=_schema_grid_charger(self._data))


class SmartBatteryOptimizerOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Smart Battery Optimizer."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        pass  # config_entry is managed by the base class

    def _current(self) -> dict:
        data = dict(self.config_entry.data)
        data.update(self.config_entry.options)
        return data

    def _save_section(self, user_input: dict) -> FlowResult:
        data = self._current()
        data.update(user_input)
        return self.async_create_entry(title="", data=data)

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["tibber", "battery", "solar_inverter", "system", "excess", "grid_charger", "climate_devices"],
        )

    async def async_step_tibber(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self._save_section(user_input)
        return self.async_show_form(step_id="tibber", data_schema=_schema_tibber(self._current()))

    async def async_step_battery(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self._save_section(user_input)
        return self.async_show_form(step_id="battery", data_schema=_schema_battery(self._current()))

    async def async_step_solar_inverter(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self._save_section(user_input)
        return self.async_show_form(step_id="solar_inverter", data_schema=_schema_solar_inverter(self._current()))

    async def async_step_system(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self._save_section(user_input)
        return self.async_show_form(step_id="system", data_schema=_schema_system(self._current()))

    async def async_step_excess(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self._save_section(user_input)
        return self.async_show_form(step_id="excess", data_schema=_schema_excess(self._current()))

    async def async_step_grid_charger(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self._save_section(user_input)
        return self.async_show_form(step_id="grid_charger", data_schema=_schema_grid_charger(self._current()))

    async def async_step_climate_devices(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(
            step_id="climate_devices",
            menu_options=["climate_add", "climate_remove"],
        )

    async def async_step_climate_add(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            import uuid
            data = self._current()
            devices = list(data.get(CONF_CLIMATE_DEVICES, []))
            devices.append({
                "id": uuid.uuid4().hex[:8],
                "name": user_input["name"],
                "device_type": CLIMATE_LABEL_TO_INTERNAL.get(user_input["device_type"], "heating"),
                "setpoint": float(user_input.get("setpoint", 20.0)),
                "manual_w": float(user_input.get("manual_w", 0.0)),
                "power_sensor": user_input.get("power_sensor", ""),
            })
            data[CONF_CLIMATE_DEVICES] = devices
            return self.async_create_entry(title="", data=data)

        schema = vol.Schema({
            vol.Required("name"): str,
            vol.Required("device_type", default=CLIMATE_TYPE_HEATING): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[CLIMATE_TYPE_HEATING, CLIMATE_TYPE_COOLING, CLIMATE_TYPE_HEAT_PUMP],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional("setpoint", default=20.0): selector.NumberSelector(
                selector.NumberSelectorConfig(min=10, max=35, step=0.5, mode=selector.NumberSelectorMode.SLIDER)
            ),
            vol.Optional("manual_w", default=0.0): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=10000, step=50, mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional("power_sensor", default=""): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
        })
        return self.async_show_form(step_id="climate_add", data_schema=schema)

    async def async_step_climate_remove(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        data = self._current()
        devices = data.get(CONF_CLIMATE_DEVICES, [])

        if not devices:
            return await self.async_step_climate_devices()

        if user_input is not None:
            remove_id = user_input.get("device_id")
            data[CONF_CLIMATE_DEVICES] = [d for d in devices if d["id"] != remove_id]
            return self.async_create_entry(title="", data=data)

        options = [{"label": d["name"], "value": d["id"]} for d in devices]
        schema = vol.Schema({
            vol.Required("device_id"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        })
        return self.async_show_form(step_id="climate_remove", data_schema=schema)
