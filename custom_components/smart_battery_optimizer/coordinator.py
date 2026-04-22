"""Coordinator to handle the core control logic for Smart Battery Optimizer."""
import logging
from datetime import timedelta
import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util
from .tibber import fetch_tibber_prices

from .const import (
    DOMAIN,
    CONF_TIBBER_API_TOKEN,
    CONF_TIBBER_PRICE_SENSOR,
    CONF_TIBBER_CONSUMPTION_SENSOR,
    CONF_TIBBER_EXPORT_SENSOR,
    CONF_BATTERY_LEVEL_SENSOR,
    CONF_SOLAR_POWER_SENSOR,
    CONF_OPENDTU_TURN_ON_BUTTON,
    CONF_OPENDTU_TURN_OFF_BUTTON,
    CONF_OPENDTU_PRODUCING_SENSOR,
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
        self.tibber_prices = []
        self.last_tibber_fetch = None

        # Output values for sensors
        self.calculated_house_consumption = 0.0
        self.predicted_remaining_solar = 0.0
        self.predicted_remaining_consumption = 0.0
        self.current_operating_mode = "Initializing"
        self.hourly_plan = []

        self._current_inverter_state = "unknown"

    async def _async_setup(self):
        """Set up the coordinator."""
        await self.learning_engine.async_load()
        await self._fetch_tibber_prices()

    async def _fetch_tibber_prices(self):
        """Fetch prices from Tibber API."""
        api_token = self.config.get(CONF_TIBBER_API_TOKEN)
        if not api_token:
            return

        now = dt_util.now()
        if self.last_tibber_fetch and (now - self.last_tibber_fetch).total_seconds() < 3600:
            return # Only fetch once per hour

        prices = await fetch_tibber_prices(self.hass, api_token)
        if prices:
            self.tibber_prices = prices
            self.last_tibber_fetch = now

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

    def _get_current_price(self) -> float | None:
        """Get the current live Tibber price from the HA sensor."""
        # Always use the live HA sensor to support 15-minute price updates
        price_entity = self.config.get(CONF_TIBBER_PRICE_SENSOR)
        state = self.hass.states.get(price_entity)
        if state and state.state not in ("unknown", "unavailable"):
            try:
                return float(state.state)
            except ValueError:
                pass

        # Fallback to hourly API if sensor failed
        now = dt_util.now()
        for price_data in self.tibber_prices:
            dt = price_data.get("datetime")
            if dt and dt.date() == now.date() and dt.hour == now.hour:
                return float(price_data.get("total", 0.0))

        return None

    def _is_price_cheap(self, current_price: float) -> bool:
        """Determine if price is cheap."""
        if current_price is None:
            return False

        # Hardcoded threshold for now, could be made configurable.
        # Anything below 18 cents is generally considered cheap in DE.
        return current_price < 0.18

    async def _async_update_data(self):
        """Update data and apply logic."""
        if not self.is_enabled:
            return None

        await self._fetch_tibber_prices()

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

        current_solar = 0.0
        solar_sensors = self.config.get(CONF_SOLAR_POWER_SENSOR, [])
        if isinstance(solar_sensors, str):
            solar_sensors = [solar_sensors]
        for entity_id in solar_sensors:
            current_solar += self._get_float_state(entity_id)

        await self.learning_engine.record_consumption(current_hour, self.calculated_house_consumption)
        await self.learning_engine.record_solar(current_hour, current_solar, cloud_cover)

        # Finalize and save at the end of the hour or if the hour changed
        if current_hour != self.learning_engine._last_hour_processed and self.learning_engine._last_hour_processed != -1:
            # We transitioned to a new hour
            await self.learning_engine.finalize_hour(self.learning_engine._last_hour_processed, cloud_cover)
            await self.learning_engine.async_save()

        self.learning_engine._last_hour_processed = current_hour

        # 3. Forecast and Planning
        hourly_forecasts = await self._get_hourly_forecasts()
        self.predicted_remaining_solar = self.learning_engine.predict_remaining_solar(current_hour, hourly_forecasts)
        self.predicted_remaining_consumption = self.learning_engine.predict_remaining_consumption(current_hour)

        current_price = self._get_current_price()

        # 4. Control Logic (Dynamic Threshold Simulation)
        batt_level_pct = self._get_float_state(self.config[CONF_BATTERY_LEVEL_SENSOR])
        batt_cap_wh = self.config.get(CONF_BATTERY_CAPACITY_WH, 5000)
        batt_min_pct = self.config.get(CONF_BATTERY_MIN_LIMIT_PCT, 10)

        # Build hourly plan to determine the dynamic price threshold
        price_threshold = self._simulate_optimal_threshold(current_hour, hourly_forecasts, batt_level_pct, batt_cap_wh, batt_min_pct)
        self._build_hourly_plan(current_hour, hourly_forecasts, batt_level_pct, batt_cap_wh, price_threshold)

        available_batt_capacity_wh = batt_cap_wh * (1.0 - (batt_level_pct / 100.0))

        # Determine if battery will overfill today
        battery_will_overfill = self.predicted_remaining_solar > (self.predicted_remaining_consumption + available_batt_capacity_wh)

        turn_on_inverter = True # True = Producing (Nulleinspeisung by DTU), False = Off (Grid consumption)

        async def set_excess_switches(turn_on: bool):
            for switch_entity in self.config.get(CONF_PRIORITIZED_EXCESS_CONSUMERS, []):
                state = self.hass.states.get(switch_entity)
                is_on = state and state.state == "on"
                if turn_on and not is_on:
                    await self.hass.services.async_call("switch", "turn_on", {"entity_id": switch_entity}, blocking=False)
                elif not turn_on and is_on:
                    await self.hass.services.async_call("switch", "turn_off", {"entity_id": switch_entity}, blocking=False)

        if self.manual_zero_export:
            # Force zero export mode
            self.current_operating_mode = "Manueller Modus: Nulleinspeisung erzwungen"
            turn_on_inverter = True
            await set_excess_switches(False)

        elif batt_level_pct <= batt_min_pct:
            self.current_operating_mode = "Batterie am Minimum: OpenDTU aus (Netzbezug)"
            turn_on_inverter = False
            await set_excess_switches(False)

        elif battery_will_overfill:
            # We must use the energy now, regardless of price!
            self.current_operating_mode = "Batterie wird voll: DTU an zur Vermeidung von Überschuss"
            turn_on_inverter = True

            # Manage excess consumers if battery is very full
            if batt_level_pct >= 95 and (current_solar > self.calculated_house_consumption):
                self.current_operating_mode = "Batterie voll: Priorisierte Verbraucher aktiviert"
                await set_excess_switches(True)
            else:
                await set_excess_switches(False)

        elif current_price is not None and current_price <= price_threshold:
            # Price is cheap enough compared to the threshold needed for upcoming expensive hours
            self.current_operating_mode = f"Strom günstig (<{round(price_threshold,3)}€): DTU aus (Akku wird gespart)"
            turn_on_inverter = False
            await set_excess_switches(False)

        else:
            # Normal operation: Price is above threshold, use battery.
            self.current_operating_mode = "Preis hoch: DTU an (Nulleinspeisung aktiv)"
            turn_on_inverter = True
            await set_excess_switches(False)

        # Verify current state using the Producing binary sensor
        producing_sensor = self.config.get(CONF_OPENDTU_PRODUCING_SENSOR)
        if producing_sensor:
            producing_state = self.hass.states.get(producing_sensor)
            if producing_state:
                # "on" means it's producing, "off" means it's not
                self._current_inverter_state = producing_state.state

        # Apply Switch state to OpenDTU Inverter Buttons
        try:
            turn_on_btn = self.config.get(CONF_OPENDTU_TURN_ON_BUTTON)
            turn_off_btn = self.config.get(CONF_OPENDTU_TURN_OFF_BUTTON)

            if turn_on_inverter and self._current_inverter_state != "on":
                if turn_on_btn:
                    await self.hass.services.async_call("button", "press", {"entity_id": turn_on_btn}, blocking=False)
                    # We optimistically update our internal state so we don't spam the button before HA updates the sensor
                    self._current_inverter_state = "on"
                    _LOGGER.debug("Pressed OpenDTU Turn ON Button (Producing)")
            elif not turn_on_inverter and self._current_inverter_state != "off":
                if turn_off_btn:
                    await self.hass.services.async_call("button", "press", {"entity_id": turn_off_btn}, blocking=False)
                    self._current_inverter_state = "off"
                    _LOGGER.debug("Pressed OpenDTU Turn OFF Button (Not Producing)")
        except Exception as e:
            _LOGGER.error("Failed to press OpenDTU button: %s", e)

        return {
            "calculated_house_consumption": self.calculated_house_consumption,
            "predicted_remaining_solar": self.predicted_remaining_solar,
            "predicted_remaining_consumption": self.predicted_remaining_consumption,
            "battery_will_overfill": battery_will_overfill,
            "current_operating_mode": self.current_operating_mode,
            "hourly_plan": self.hourly_plan,
            "current_price": current_price,
            "current_battery": batt_level_pct,
            "current_solar": current_solar,
            "current_consumption": tibber_cons,
        }

    def _simulate_optimal_threshold(self, start_hour: int, hourly_forecasts: list[dict], current_batt_pct: float, batt_cap_wh: float, batt_min_pct: float) -> float:
        """Simulates the future to find the optimal price threshold to discharge the battery."""
        forecast_dict = {f["hour"]: f["cloud_cover"] for f in hourly_forecasts}
        now = dt_util.now()

        # Gather data for the next 24 hours (today and tomorrow morning)
        future_hours = []
        for hr_offset in range(24):
            eval_time = now + timedelta(hours=hr_offset)
            hr = eval_time.hour
            cc = forecast_dict.get(hr, 50.0) if eval_time.date() == now.date() else 50.0 # simplified next day cloud

            pred_solar = self.learning_engine.predict_solar_for_hour(hr, cc)
            pred_cons = self.learning_engine.predict_consumption_for_hour(hr)

            price = 0.0
            for p in self.tibber_prices:
                dt = p.get("datetime")
                if dt and dt.date() == eval_time.date() and dt.hour == hr:
                    price = float(p.get("total", 0.0))
                    break

            future_hours.append({
                "solar": pred_solar,
                "cons": pred_cons,
                "price": price
            })

        # Binary search for the perfect threshold (between 0.0 and 1.0 EUR)
        low, high = -0.5, 1.0
        best_threshold = -0.5

        for _ in range(15): # 15 iterations is plenty for precision
            mid_threshold = (low + high) / 2.0
            simulated_batt_wh = batt_cap_wh * (current_batt_pct / 100.0)
            min_batt_wh = batt_cap_wh * (batt_min_pct / 100.0)

            failed = False
            for fh in future_hours:
                # Add solar
                simulated_batt_wh += fh["solar"]

                # If price is above threshold, we use battery to cover consumption
                if fh["price"] > mid_threshold:
                    simulated_batt_wh -= fh["cons"]

                # Clamp battery
                simulated_batt_wh = min(batt_cap_wh, simulated_batt_wh)

                # Did we run out of battery during an expensive hour?
                if simulated_batt_wh < min_batt_wh:
                    failed = True
                    break

            if failed:
                # Threshold was too low (we discharged too often and ran out of battery)
                # We need a higher threshold (discharge less often)
                low = mid_threshold
            else:
                # We survived! This threshold works. Try to find a lower one (use battery more)
                best_threshold = mid_threshold
                high = mid_threshold

        return best_threshold

    def _build_hourly_plan(self, start_hour: int, hourly_forecasts: list[dict], current_batt_pct: float, batt_cap_wh: float, price_threshold: float):
        """Generate a forecast plan for the remaining hours of the day using the dynamic threshold."""
        plan = []
        simulated_batt_wh = batt_cap_wh * (current_batt_pct / 100.0)

        forecast_dict = {f["hour"]: f["cloud_cover"] for f in hourly_forecasts}
        now = dt_util.now()

        for hr in range(start_hour, 24):
            cc = forecast_dict.get(hr, 50.0)

            # Predict values
            pred_solar = self.learning_engine.predict_solar_for_hour(hr, cc)
            pred_cons = self.learning_engine.predict_consumption_for_hour(hr)

            # Get price
            price = 0.0
            for p in self.tibber_prices:
                dt = p.get("datetime")
                if dt and dt.date() == now.date() and dt.hour == hr:
                    price = float(p.get("total", 0.0))
                    break

            # Simulate logic with dynamic threshold
            action = "Nulleinspeisung"
            available_cap = batt_cap_wh - simulated_batt_wh
            will_overfill = (pred_solar * (24 - hr)) > (pred_cons * (24 - hr) + available_cap)

            if will_overfill:
                action = "Überschussvermeidung (DTU An)"
                simulated_batt_wh += pred_solar - pred_cons
            elif price <= price_threshold:
                action = f"Netzbezug (Akku sparen für >{round(price_threshold,3)}€)"
                simulated_batt_wh += pred_solar # battery only charges, no discharge
            else:
                action = "Nulleinspeisung (DTU An)"
                simulated_batt_wh += pred_solar - pred_cons

            # Clamp battery simulation
            simulated_batt_wh = max(0, min(batt_cap_wh, simulated_batt_wh))

            plan.append({
                "hour": f"{hr:02d}:00",
                "price": round(price, 4),
                "solar_wh": round(pred_solar),
                "consumption_wh": round(pred_cons),
                "battery_pct_end": round((simulated_batt_wh / batt_cap_wh) * 100),
                "planned_action": action
            })

        self.hourly_plan = plan
