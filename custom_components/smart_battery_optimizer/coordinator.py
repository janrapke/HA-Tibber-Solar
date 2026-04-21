"""Coordinator to handle the core control logic for Smart Battery Optimizer."""
import logging
from datetime import timedelta
import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util

from .const import (
    DOMAIN,
    CONF_TIBBER_PRICE_SENSOR,
    CONF_TIBBER_CONSUMPTION_SENSOR,
    CONF_TIBBER_EXPORT_SENSOR,
    CONF_BATTERY_LEVEL_SENSOR,
    CONF_SOLAR_POWER_SENSOR,
    CONF_OPENDTU_LIMIT_NUMBER,
    CONF_OPENDTU_OUTPUT_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_BATTERY_CAPACITY_WH,
    CONF_BATTERY_MIN_LIMIT_PCT,
    CONF_EXCLUDED_POWER_SENSORS,
    CONF_PRIORITIZED_EXCESS_CONSUMERS,
)
from .learning import LearningEngine

_LOGGER = logging.getLogger(__name__)

class SmartBatteryOptimizerCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data and applying control logic."""

    def __init__(self, hass: HomeAssistant, config: dict):
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=1),
        )
        self.config = config
        self.learning_engine = LearningEngine(hass)

        # Internal state
        self.is_enabled = True
        self.manual_zero_export = False

        # Output values for sensors
        self.calculated_house_consumption = 0.0
        self.predicted_remaining_solar = 0.0
        self.predicted_remaining_consumption = 0.0

    async def _async_setup(self):
        """Set up the coordinator."""
        await self.learning_engine.async_load()

    def _get_float_state(self, entity_id: str, default: float = 0.0) -> float:
        """Helper to safely get float state from an entity."""
        state = self.hass.states.get(entity_id)
        if state and state.state not in ("unknown", "unavailable"):
            try:
                return float(state.state)
            except ValueError:
                pass
        return default

    async def _get_hourly_forecasts(self) -> list[dict]:
        """Fetch hourly weather forecast to get cloud coverage."""
        weather_entity = self.config.get(CONF_WEATHER_ENTITY)
        forecasts = []

        if not weather_entity:
            return forecasts

        try:
            # Depending on HA version, weather forecasts are retrieved via service call
            # For simplicity in this logic, we attempt to read 'forecast' attribute
            # Modern HA uses weather.get_forecasts service, let's try direct attribute first,
            # as it's a fallback.
            state = self.hass.states.get(weather_entity)
            if state and "forecast" in state.attributes:
                for f in state.attributes["forecast"]:
                    # Try to extract datetime and cloud coverage
                    dt_str = f.get("datetime")
                    if dt_str:
                        dt = dt_util.parse_datetime(dt_str)
                        if dt and dt.date() == dt_util.now().date():
                            cloud_cover = f.get("cloud_coverage", 50) # default to 50 if missing
                            forecasts.append({"hour": dt.hour, "cloud_cover": cloud_cover})
            else:
                # Use service call for modern HA
                response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {"entity_id": weather_entity, "type": "hourly"},
                    blocking=True,
                    return_response=True,
                )
                if response and weather_entity in response:
                    for f in response[weather_entity].get("forecast", []):
                        dt_str = f.get("datetime")
                        if dt_str:
                            dt = dt_util.parse_datetime(dt_str)
                            if dt and dt.date() == dt_util.now().date():
                                cloud_cover = f.get("cloud_coverage", 50)
                                forecasts.append({"hour": dt.hour, "cloud_cover": cloud_cover})

        except Exception as e:
            _LOGGER.warning("Could not fetch weather forecast: %s", e)

        return forecasts

    def _is_price_cheap(self) -> bool:
        """Check if current Tibber price is considered cheap."""
        price_entity = self.config.get(CONF_TIBBER_PRICE_SENSOR)
        state = self.hass.states.get(price_entity)
        if not state:
            return False

        current_price = self._get_float_state(price_entity)

        # Tibber sensors often have price levels in attributes (e.g., 'price_level': 'CHEAP')
        # or daily max/min. Here we check attributes.
        price_level = state.attributes.get("price_level", "NORMAL")
        if price_level in ("CHEAP", "VERY_CHEAP"):
            return True

        # Fallback heuristic: below 15 cents
        if current_price < 0.15:
            return True

        return False

    async def _async_update_data(self):
        """Update data and apply logic."""
        if not self.is_enabled:
            return None

        # 1. Read Sensors
        tibber_cons = self._get_float_state(self.config[CONF_TIBBER_CONSUMPTION_SENSOR])
        tibber_exp = self._get_float_state(self.config[CONF_TIBBER_EXPORT_SENSOR])
        opendtu_output = self._get_float_state(self.config[CONF_OPENDTU_OUTPUT_SENSOR])

        # Calculate excluded power
        excluded_power = 0.0
        for entity_id in self.config.get(CONF_EXCLUDED_POWER_SENSORS, []):
            excluded_power += self._get_float_state(entity_id)

        # True house consumption:
        # What we buy + what inverter provides - what we export - excluded heavy loads
        self.calculated_house_consumption = max(0, tibber_cons - tibber_exp + opendtu_output - excluded_power)

        # 2. Update Learning Engine (only once per hour or minute? We do it smoothly)
        now = dt_util.now()
        current_hour = now.hour

        # Get current cloud cover from weather
        cloud_cover = 50.0
        weather_entity = self.config.get(CONF_WEATHER_ENTITY)
        weather_state = self.hass.states.get(weather_entity)
        if weather_state and "cloud_coverage" in weather_state.attributes:
            try:
                cloud_cover = float(weather_state.attributes["cloud_coverage"])
            except ValueError:
                pass

        current_solar = self._get_float_state(self.config[CONF_SOLAR_POWER_SENSOR])

        await self.learning_engine.record_consumption(current_hour, self.calculated_house_consumption)
        await self.learning_engine.record_solar(current_hour, current_solar, cloud_cover)

        # Finalize and save at the end of the hour or if the hour changed
        if current_hour != self.learning_engine._last_hour_processed and self.learning_engine._last_hour_processed != -1:
            # We transitioned to a new hour
            await self.learning_engine.finalize_hour(self.learning_engine._last_hour_processed, cloud_cover)
            await self.learning_engine.async_save()

        self.learning_engine._last_hour_processed = current_hour

        # 3. Forecast
        hourly_forecasts = await self._get_hourly_forecasts()
        self.predicted_remaining_solar = self.learning_engine.predict_remaining_solar(current_hour, hourly_forecasts)
        self.predicted_remaining_consumption = self.learning_engine.predict_remaining_consumption(current_hour)

        # 4. Control Logic
        batt_level_pct = self._get_float_state(self.config[CONF_BATTERY_LEVEL_SENSOR])
        batt_cap_wh = self.config.get(CONF_BATTERY_CAPACITY_WH, 5000)
        batt_min_pct = self.config.get(CONF_BATTERY_MIN_LIMIT_PCT, 10)

        available_batt_capacity_wh = batt_cap_wh * (1.0 - (batt_level_pct / 100.0))

        # Determine if battery will overfill
        # If expected solar > expected consumption + available battery space
        battery_will_overfill = self.predicted_remaining_solar > (self.predicted_remaining_consumption + available_batt_capacity_wh)

        target_opendtu_limit = 0.0

        async def set_switches(turn_on: bool):
            for switch_entity in self.config.get(CONF_PRIORITIZED_EXCESS_CONSUMERS, []):
                state = self.hass.states.get(switch_entity)
                is_on = state and state.state == "on"
                if turn_on and not is_on:
                    await self.hass.services.async_call("switch", "turn_on", {"entity_id": switch_entity}, blocking=False)
                elif not turn_on and is_on:
                    await self.hass.services.async_call("switch", "turn_off", {"entity_id": switch_entity}, blocking=False)

        if self.manual_zero_export:
            # Force zero export mode
            target_opendtu_limit = self.calculated_house_consumption
            await set_switches(False)

        elif battery_will_overfill:
            # We must use the energy now, regardless of price!
            target_opendtu_limit = self.calculated_house_consumption

            # Manage excess consumers if battery is very full
            if batt_level_pct >= 95 and (current_solar > self.calculated_house_consumption):
                await set_switches(True)
            else:
                await set_switches(False)

        elif self._is_price_cheap():
            # Price is cheap and battery won't overfill -> turn off inverter, use grid, save solar
            target_opendtu_limit = 0.0
            await set_switches(False)

        else:
            # Normal operation: Price is not cheap, zero export.
            # Only do this if battery is above minimum limit.
            if batt_level_pct > batt_min_pct:
                target_opendtu_limit = self.calculated_house_consumption
            else:
                target_opendtu_limit = 0.0

            await set_switches(False)

        # Apply limit to OpenDTU
        try:
            # We add a small buffer or cap it based on max inverter capability.
            # Assuming OpenDTU number entity accepts Watts.
            target_w = round(max(0, target_opendtu_limit))

            await self.hass.services.async_call(
                "number",
                "set_value",
                {"entity_id": self.config[CONF_OPENDTU_LIMIT_NUMBER], "value": target_w},
                blocking=False
            )
            _LOGGER.debug("Set OpenDTU limit to %s W", target_w)
        except Exception as e:
            _LOGGER.error("Failed to set OpenDTU limit: %s", e)

        return {
            "calculated_house_consumption": self.calculated_house_consumption,
            "predicted_remaining_solar": self.predicted_remaining_solar,
            "predicted_remaining_consumption": self.predicted_remaining_consumption,
            "battery_will_overfill": battery_will_overfill,
            "target_limit": target_opendtu_limit,
        }
