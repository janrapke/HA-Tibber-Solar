"""Coordinator to handle the core control logic for Smart Battery Optimizer."""
import logging
from datetime import timedelta, datetime
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
    CONF_BALCONY_POWER_SENSOR,
    CONF_OPENDTU_DPL_SWITCH,
    CONF_OPENDTU_TURN_ON_BUTTON,
    CONF_OPENDTU_TURN_OFF_BUTTON,
    CONF_OPENDTU_PRODUCING_SENSOR,
    CONF_OPENDTU_OUTPUT_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_BATTERY_CAPACITY_WH,
    CONF_BATTERY_MIN_LIMIT_PCT,
    CONF_SOLAR_CHARGE_STATE_SENSOR,
    CONF_BATTERY_EFFICIENCY_PCT,
    CONF_EXCLUDED_POWER_SENSORS,
    CONF_PRIMARY_EXCESS_CONSUMERS,
    CONF_SECONDARY_EXCESS_CONSUMERS,
    CONF_EXTREME_PRICE_THRESHOLD,
    CONF_MAX_INVERTER_POWER_W,
    CONF_EARLY_EXCESS_CONSUMERS,
    CONF_EARLY_EXCESS_MIN_BATTERY_PCT,
    CONF_EARLY_EXCESS_EXPECTED_POWER_W,
    CONF_PERSON_ENTITIES,
    CONF_ABSENCE_CALENDARS,
    CONF_SPONTANEOUS_ABSENCE_MINUTES,
    CONF_SOLAR_PEAK_W,
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
        self.learning_engine = LearningEngine(hass, config)

        # Internal state
        self.is_enabled = True
        self.manual_zero_export = False
        self.tibber_prices = []
        self.last_tibber_fetch = None

        # Number entity states
        self.extreme_price_threshold = config.get(CONF_EXTREME_PRICE_THRESHOLD, 0.40)
        self.extreme_price_factor = 0.0

        # Number entity states initialized via fallbacks, actual states managed by NumberEntities
        self.extreme_price_threshold = config.get(CONF_EXTREME_PRICE_THRESHOLD, 0.40)
        self.extreme_price_factor = 0.0
        self.primary_excess_on = 95.0
        self.primary_excess_off = 90.0
        self.secondary_excess_on = 98.0
        self.secondary_excess_off = 95.0

        # Switch entity states
        self.primary_excess_auto = True
        self.secondary_excess_auto = True
        self.early_excess_auto = True

        # Output values for sensors
        self.calculated_house_consumption = 0.0
        self.predicted_remaining_solar = 0.0
        self.predicted_remaining_consumption = 0.0
        self.current_operating_mode = "Initializing"
        self.hourly_plan = [] # Renamed visually but keeping attr name for compatibility

        self._current_inverter_state = "unknown"
        self._low_solar_minutes = 0
        self._battery_recovery_mode = False

        self._spontaneous_absence_end: dict[str, datetime] = {}

        # Intraday Solar Correction tracking
        self._intraday_solar_expected_acc = 0.0
        self._intraday_solar_actual_acc = 0.0
        self._intraday_solar_factor = 1.0

    @property
    def is_learning_mode_active(self) -> bool:
        """Check if learning mode is active (within 7 days of start)."""
        end_time_str = self.learning_engine.data.get("learning_mode_end_time")
        if not end_time_str:
            return False
        try:
            end_time = datetime.fromisoformat(end_time_str)
            return datetime.now() < end_time
        except ValueError:
            return False

    async def async_start_learning_mode(self):
        """Start the fast learning mode."""
        self.learning_engine.reset_learning_counters()
        await self.learning_engine.async_save()

    async def async_stop_learning_mode(self):
        """Stop the fast learning mode manually."""
        self.learning_engine.data["learning_mode_end_time"] = datetime.now().isoformat()
        await self.learning_engine.async_save()

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
            state = self.hass.states.get(weather_entity)
            if state and "forecast" in state.attributes:
                for f in state.attributes["forecast"]:
                    dt_str = f.get("datetime")
                    if dt_str:
                        dt = dt_util.parse_datetime(dt_str)
                        if dt:
                            cloud_cover = f.get("cloud_coverage", 50)
                            forecasts.append({"datetime": dt, "cloud_cover": cloud_cover})
            elif state is not None:
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
                            if dt:
                                cloud_cover = f.get("cloud_coverage", 50)
                                forecasts.append({"datetime": dt, "cloud_cover": cloud_cover})

        except Exception as e:
            _LOGGER.warning("Could not fetch weather forecast: %s", e)

        return forecasts

    def _get_current_price(self) -> float | None:
        """Get the current live Tibber price from the HA sensor."""
        price_entity = self.config.get(CONF_TIBBER_PRICE_SENSOR)
        state = self.hass.states.get(price_entity)
        if state and state.state not in ("unknown", "unavailable"):
            try:
                return float(state.state)
            except ValueError:
                pass

        now = dt_util.now()
        for price_data in self.tibber_prices:
            dt = price_data.get("datetime")
            if dt and dt <= now < dt + timedelta(minutes=15):
                return float(price_data.get("total", 0.0))

        return None

    def _get_quarter_index(self, dt) -> int:
        return (dt.hour * 4) + (dt.minute // 15)

    async def _async_update_data(self):
        """Update data and apply logic."""
        if not self.is_enabled:
            return None

        await self._fetch_tibber_prices()

        tibber_cons = self._get_float_state(self.config[CONF_TIBBER_CONSUMPTION_SENSOR])
        tibber_exp = self._get_float_state(self.config[CONF_TIBBER_EXPORT_SENSOR])
        opendtu_output = self._get_float_state(self.config[CONF_OPENDTU_OUTPUT_SENSOR])

        excluded_power = 0.0
        for entity_id in self.config.get(CONF_EXCLUDED_POWER_SENSORS, []):
            excluded_power += self._get_float_state(entity_id)

        current_balcony = 0.0
        balcony_sensor = self.config.get(CONF_BALCONY_POWER_SENSOR)
        if balcony_sensor:
            current_balcony = self._get_float_state(balcony_sensor)

        self.calculated_house_consumption = max(0, tibber_cons - tibber_exp + opendtu_output - excluded_power + current_balcony)

        now = dt_util.now()
        current_quarter = self._get_quarter_index(now)

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

        charge_state_sensor = self.config.get(CONF_SOLAR_CHARGE_STATE_SENSOR)
        charge_state = None
        if charge_state_sensor:
            charge_state_obj = self.hass.states.get(charge_state_sensor)
            if charge_state_obj:
                charge_state = charge_state_obj.state

        # Determine active persons and spontaneous absences
        persons = self.config.get(CONF_PERSON_ENTITIES, [])
        if isinstance(persons, str):
            persons = [persons]

        active_persons = []
        spontaneous_minutes = int(self.config.get(CONF_SPONTANEOUS_ABSENCE_MINUTES, 120))

        for person_id in persons:
            state_obj = self.hass.states.get(person_id)
            if state_obj and state_obj.state == "home":
                active_persons.append(person_id)
                # If they are home, clear any spontaneous absence timer
                if person_id in self._spontaneous_absence_end:
                    del self._spontaneous_absence_end[person_id]
            else:
                # They are NOT home. Check if they just left (no timer yet)
                if person_id not in self._spontaneous_absence_end:
                    self._spontaneous_absence_end[person_id] = now + timedelta(minutes=spontaneous_minutes)

        # Pre-fetch calendar events for absence planning (up to 35 hours)
        calendars = self.config.get(CONF_ABSENCE_CALENDARS, [])
        if isinstance(calendars, str):
            calendars = [calendars]

        planned_absences = []
        horizon_end = now + timedelta(hours=35)
        for cal_id in calendars:
            try:
                response = await self.hass.services.async_call(
                    "calendar",
                    "get_events",
                    {
                        "entity_id": cal_id,
                        "start_date_time": now.isoformat(),
                        "end_date_time": horizon_end.isoformat()
                    },
                    blocking=True,
                    return_response=True
                )
                if response and cal_id in response:
                    events = response[cal_id].get("events", [])
                    for e in events:
                        start_str = e.get("start")
                        end_str = e.get("end")
                        if start_str and end_str:
                            start_dt = dt_util.parse_datetime(start_str)
                            end_dt = dt_util.parse_datetime(end_str)
                            # Handle all-day events (date strings instead of datetime)
                            if not start_dt:
                                start_dt = dt_util.parse_date(start_str)
                                if start_dt:
                                    start_dt = dt_util.start_of_local_day(datetime.combine(start_dt, datetime.min.time()))
                            if not end_dt:
                                end_dt = dt_util.parse_date(end_str)
                                if end_dt:
                                    # All day events technically end at midnight the next day
                                    end_dt = dt_util.start_of_local_day(datetime.combine(end_dt, datetime.min.time()))

                            if start_dt and end_dt:
                                planned_absences.append((start_dt, end_dt))
            except Exception as e:
                _LOGGER.warning("Could not fetch calendar events for %s: %s", cal_id, e)

        self._planned_absences = planned_absences
        self._active_persons_now = active_persons
        self._all_tracked_persons = persons

        await self.learning_engine.record_consumption(current_quarter, self.calculated_house_consumption, active_persons)
        await self.learning_engine.record_solar(current_quarter, current_solar, cloud_cover, charge_state)
        await self.learning_engine.record_balcony(current_quarter, current_balcony, cloud_cover)

        is_weekend = now.weekday() >= 5

        if current_quarter != self.learning_engine._last_quarter_processed and self.learning_engine._last_quarter_processed != -1:
            # Clean up old intraday tracking if it's a new day
            if current_quarter < self.learning_engine._last_quarter_processed:
                self._intraday_solar_expected_acc = 0.0
                self._intraday_solar_actual_acc = 0.0
                self._intraday_solar_factor = 1.0
                self.pessimistic_solar_factor = 1.0
                self.pessimistic_consumption_factor = 1.0

            actual_cons_wh, actual_solar_wh = await self.learning_engine.finalize_quarter(self.learning_engine._last_quarter_processed, cloud_cover, is_weekend)

            # Intraday Solar Correction logic
            pred_solar_wh = self.learning_engine.predict_solar_for_quarter(self.learning_engine._last_quarter_processed, cloud_cover)

            is_absorption = charge_state and charge_state.lower() in ("absorption", "float", "ausgleichsladung", "equalization")
            if not is_absorption:
                self._intraday_solar_expected_acc += pred_solar_wh
                self._intraday_solar_actual_acc += actual_solar_wh

                # Keep rolling window manageable (e.g. max 8 quarters / 2 hours of memory)
                if self._intraday_solar_expected_acc > 1000:
                    self._intraday_solar_expected_acc *= 0.5
                    self._intraday_solar_actual_acc *= 0.5

                if self._intraday_solar_expected_acc > 10:
                    raw_factor = self._intraday_solar_actual_acc / self._intraday_solar_expected_acc
                    # Clamp the factor to prevent crazy spikes, say between 0.2 and 2.0
                    self._intraday_solar_factor = max(0.2, min(2.0, raw_factor))

            # Predict consumption using active persons to establish pessimistic factor
            pred_cons_wh = self.learning_engine.predict_consumption_for_quarter(self.learning_engine._last_quarter_processed, is_weekend, getattr(self.learning_engine, "_current_quarter_active_persons", []))

            # Balcony reduces predicted consumption. It does NOT charge the battery.
            if self.config.get(CONF_BALCONY_POWER_SENSOR):
                pred_balcony = self.learning_engine.predict_balcony_for_quarter(self.learning_engine._last_quarter_processed, cloud_cover)
                pred_cons_wh = max(0.0, pred_cons_wh - pred_balcony)

            # If the charge state is absorption/float, true solar potential is hidden.
            # Do not use this quarter to penalize solar prediction.
            is_absorption = charge_state and charge_state.lower() in ("absorption", "float", "ausgleichsladung", "equalization")
            if is_absorption or actual_solar_wh >= pred_solar_wh or pred_solar_wh == 0:
                self.pessimistic_solar_factor = 1.0
            else:
                self.pessimistic_solar_factor = actual_solar_wh / pred_solar_wh

            if actual_cons_wh <= pred_cons_wh or pred_cons_wh == 0:
                self.pessimistic_consumption_factor = 1.0
            else:
                self.pessimistic_consumption_factor = actual_cons_wh / pred_cons_wh

            await self.learning_engine.async_save()

        self.learning_engine._last_quarter_processed = current_quarter

        # Ensure factors exist if first run
        if not hasattr(self, 'pessimistic_solar_factor'):
            self.pessimistic_solar_factor = 1.0
        if not hasattr(self, 'pessimistic_consumption_factor'):
            self.pessimistic_consumption_factor = 1.0

        hourly_forecasts = await self._get_hourly_forecasts()

        # Temporary sum for sensors
        self.predicted_remaining_solar = 0.0
        self.predicted_remaining_consumption = 0.0

        # Forecast and Planning
        current_price = self._get_current_price()

        batt_level_pct = self._get_float_state(self.config[CONF_BATTERY_LEVEL_SENSOR])
        batt_cap_wh = self.config.get(CONF_BATTERY_CAPACITY_WH, 5000)
        batt_min_pct = self.config.get(CONF_BATTERY_MIN_LIMIT_PCT, 10)

        price_threshold = self._simulate_optimal_threshold(now, hourly_forecasts, batt_level_pct, batt_cap_wh, batt_min_pct)
        self._build_forecast_plan(now, hourly_forecasts, batt_level_pct, batt_cap_wh, price_threshold)

        available_batt_capacity_wh = batt_cap_wh * (1.0 - (batt_level_pct / 100.0))

        # Re-calc 24h sum and check for intermediate overfill
        battery_will_overfill = False
        temp_simulated_wh = batt_cap_wh * (batt_level_pct / 100.0)

        for block in self.hourly_plan:
            self.predicted_remaining_solar += block["solar_wh"]
            self.predicted_remaining_consumption += block["consumption_wh"]

            # Check if at any point during the plan the battery hits 100% capacity
            temp_simulated_wh += block["solar_wh"] - block["consumption_wh"]
            if temp_simulated_wh > batt_cap_wh:
                battery_will_overfill = True

            # Clamp to not artificially inflate future capacity
            if temp_simulated_wh > batt_cap_wh:
                 temp_simulated_wh = batt_cap_wh
            if temp_simulated_wh < 0:
                 temp_simulated_wh = 0

        turn_on_inverter = True

        async def set_switches(entities: list, turn_on: bool):
            for switch_entity in entities:
                state = self.hass.states.get(switch_entity)
                if state is None:
                    continue
                is_on = state and state.state == "on"
                if turn_on and not is_on:
                    await self.hass.services.async_call("switch", "turn_on", {"entity_id": switch_entity}, blocking=False)
                elif not turn_on and is_on:
                    await self.hass.services.async_call("switch", "turn_off", {"entity_id": switch_entity}, blocking=False)

        is_absorption = charge_state and charge_state.lower() in ("absorption", "float", "ausgleichsladung", "equalization")
        virtual_batt_pct = 100.0 if is_absorption else batt_level_pct
        has_excess_power = (current_solar > self.calculated_house_consumption) or is_absorption

        # Timer logic for cloud tolerance
        if not has_excess_power:
            self._low_solar_minutes += 1
        else:
            self._low_solar_minutes = 0

        cloud_tolerance_mins = getattr(self, "excess_cloud_tolerance_mins", 5.0)
        cloud_override_off = self._low_solar_minutes >= cloud_tolerance_mins

        # For excess logic, ensure it triggers if the battery is practically full now
        # (virtual_batt_pct >= 99) regardless of the future lookahead,
        # or if the lookahead predicts it will overfill.
        excess_condition_met = battery_will_overfill or virtual_batt_pct >= 99.0

        # Primary Hysteresis Logic
        if self.primary_excess_auto:
            turn_on_primary = False
            if virtual_batt_pct >= getattr(self, "primary_excess_on", 95.0) and has_excess_power and excess_condition_met:
                turn_on_primary = True
            elif virtual_batt_pct <= getattr(self, "primary_excess_off", 90.0) or cloud_override_off:
                turn_on_primary = False
            else:
                # Maintain current state if in hysteresis zone
                turn_on_primary = any(self.hass.states.get(e) and self.hass.states.get(e).state == "on" for e in self.config.get(CONF_PRIMARY_EXCESS_CONSUMERS, []))

            await set_switches(self.config.get(CONF_PRIMARY_EXCESS_CONSUMERS, []), turn_on_primary)

        # Secondary Hysteresis Logic
        if self.secondary_excess_auto:
            turn_on_secondary = False
            if virtual_batt_pct >= getattr(self, "secondary_excess_on", 98.0) and has_excess_power and excess_condition_met:
                turn_on_secondary = True
            elif virtual_batt_pct <= getattr(self, "secondary_excess_off", 95.0) or cloud_override_off:
                turn_on_secondary = False
            else:
                turn_on_secondary = any(self.hass.states.get(e) and self.hass.states.get(e).state == "on" for e in self.config.get(CONF_SECONDARY_EXCESS_CONSUMERS, []))

            await set_switches(self.config.get(CONF_SECONDARY_EXCESS_CONSUMERS, []), turn_on_secondary)


        # Early Excess Logic
        if getattr(self, "early_excess_auto", True):
            early_excess_min_batt = float(self.config.get(CONF_EARLY_EXCESS_MIN_BATTERY_PCT, 30.0))
            if virtual_batt_pct < early_excess_min_batt:
                turn_on_early = False
            elif not has_excess_power and cloud_override_off:
                turn_on_early = False
            else:
                early_expected_w = float(self.config.get(CONF_EARLY_EXCESS_EXPECTED_POWER_W, 400.0))
                # Will battery overfill even if this device is ON?
                will_overfill_with_early = self._simulate_early_excess_overfill(now, hourly_forecasts, virtual_batt_pct, batt_cap_wh, early_expected_w)

                # Simple ON/OFF logic based on simulation. No explicit hysteresis since simulation recalculates
                # remaining capacity which acts as a dynamic threshold.
                # However, to prevent rapid toggling, we check current state.
                currently_on = any(self.hass.states.get(e) and self.hass.states.get(e).state == "on" for e in self.config.get(CONF_EARLY_EXCESS_CONSUMERS, []))

                if currently_on:
                    # Keep it on unless simulation says we definitely won't overfill anymore
                    turn_on_early = will_overfill_with_early
                else:
                    # Only turn on if simulation says we will still overfill
                    turn_on_early = will_overfill_with_early

            await set_switches(self.config.get(CONF_EARLY_EXCESS_CONSUMERS, []), turn_on_early)


        # Update recovery mode
        if batt_level_pct <= batt_min_pct and not is_absorption:
            self._battery_recovery_mode = True
        elif batt_level_pct >= (batt_min_pct + 2.0) or is_absorption:
            self._battery_recovery_mode = False

        if self.manual_zero_export:
            self.current_operating_mode = "Manueller Modus: Nulleinspeisung erzwungen"
            turn_on_inverter = True

        elif self._battery_recovery_mode:
            self.current_operating_mode = "Batterie am Minimum: OpenDTU aus (Netzbezug)"
            turn_on_inverter = False

        elif battery_will_overfill or is_absorption:
            self.current_operating_mode = "Batterie wird voll/Absorption: Überschussvermeidung aktiv"
            turn_on_inverter = True

        elif current_price is not None and current_price <= price_threshold:
            self.current_operating_mode = f"Strom günstig (<{round(price_threshold,3)}€): DTU aus (Akku wird gespart)"
            turn_on_inverter = False

        else:
            self.current_operating_mode = "Preis hoch: DTU an (Nulleinspeisung aktiv)"
            turn_on_inverter = True

        producing_sensor = self.config.get(CONF_OPENDTU_PRODUCING_SENSOR)
        if producing_sensor:
            producing_state = self.hass.states.get(producing_sensor)
            if producing_state:
                self._current_inverter_state = producing_state.state

        try:
            dpl_switch = self.config.get(CONF_OPENDTU_DPL_SWITCH)
            turn_on_btn = self.config.get(CONF_OPENDTU_TURN_ON_BUTTON)
            turn_off_btn = self.config.get(CONF_OPENDTU_TURN_OFF_BUTTON)

            dpl_value_to_set = None
            if dpl_switch:
                dpl_state_obj = self.hass.states.get(dpl_switch)
                if dpl_state_obj:
                    current_dpl = dpl_state_obj.state
                    target_dpl = "0" if turn_on_inverter else "1"
                    if current_dpl != target_dpl:
                        domain = dpl_switch.split(".")[0]
                        if domain == "select":
                            await self.hass.services.async_call("select", "select_option", {"entity_id": dpl_switch, "option": target_dpl}, blocking=False)
                        elif domain == "number":
                            # Ensure we pass a float/int if the entity expects it
                            val_to_set = float(target_dpl)
                            await self.hass.services.async_call("number", "set_value", {"entity_id": dpl_switch, "value": val_to_set}, blocking=False)
                        elif domain == "text":
                            await self.hass.services.async_call("text", "set_value", {"entity_id": dpl_switch, "value": target_dpl}, blocking=False)

                        # Once we set DPL, we assume it's enforced
                        if target_dpl == "1":
                            self._current_inverter_state = "off"
                        else:
                            self._current_inverter_state = "on"

            # Always check if we need to press buttons (as a fallback or safety measure)
            # but only if they are configured
            if turn_on_inverter and self._current_inverter_state != "on":
                if turn_on_btn and self.hass.states.get(turn_on_btn) is not None:
                    await self.hass.services.async_call("button", "press", {"entity_id": turn_on_btn}, blocking=False)
                    self._current_inverter_state = "on"
            elif not turn_on_inverter and self._current_inverter_state != "off":
                if turn_off_btn and self.hass.states.get(turn_off_btn) is not None:
                    await self.hass.services.async_call("button", "press", {"entity_id": turn_off_btn}, blocking=False)
                    self._current_inverter_state = "off"
        except Exception as e:
            _LOGGER.error("Failed to control OpenDTU: %s", e)

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

    def _simulate_early_excess_overfill(self, now, hourly_forecasts: list[dict], current_batt_pct: float, batt_cap_wh: float, early_expected_w: float) -> bool:
        """Simulates if the battery will reach 100% using pessimistic data, even if the early excess device runs."""
        future_blocks = self._build_future_blocks_pessimistic(now, hourly_forecasts)

        simulated_batt_wh = batt_cap_wh * (current_batt_pct / 100.0)
        batt_eff = float(self.config.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0

        early_expected_wh_per_15min = early_expected_w / 4.0
        max_inverter_power_w = float(self.config.get(CONF_MAX_INVERTER_POWER_W, 800))
        max_discharge_wh_per_15min = max_inverter_power_w / 4.0

        for fb in future_blocks:
            # We assume the early consumer runs constantly until battery hits 100%.
            # The early consumer is a house load, so it adds to 'cons'.
            # However, since it is an 'excess' consumer, it should primarily eat solar.
            # Does it matter? From a battery sum perspective, we just add solar and subtract consumption.
            pred_solar = fb["solar"] * batt_eff
            # Total consumption is house cons + early cons
            pred_cons = fb["cons"] + early_expected_wh_per_15min

            # The system can only discharge at the inverter limit, but the early consumer
            # is a physical load on the house. The DTU attempts to cover it.
            # Regardless of the DTU limit, we are simulating if the battery *charges* to 100%.
            # The battery charges with whatever solar is left after house + early consumer.
            # But the house + early consumer can only consume solar directly UP TO the DTU limit + whatever the panels directly feed (balcony).
            # To keep it simple: Battery change = Solar - (House + Early)
            # But bounded by DTU discharge limits if Solar < House + Early.

            # If solar > cons, battery charges.
            if pred_solar > pred_cons:
                simulated_batt_wh += (pred_solar - pred_cons)
            else:
                # Discharging is capped by DTU
                actual_discharge = min(pred_cons - pred_solar, max_discharge_wh_per_15min)
                simulated_batt_wh -= actual_discharge

            if simulated_batt_wh >= batt_cap_wh:
                return True

        return False

    def _simulate_optimal_threshold(self, now, hourly_forecasts: list[dict], current_batt_pct: float, batt_cap_wh: float, batt_min_pct: float) -> float:
        """Simulates the future to find the optimal price threshold to discharge the battery."""
        future_blocks = self._build_future_blocks(now, hourly_forecasts)

        max_inverter_power_w = float(self.config.get(CONF_MAX_INVERTER_POWER_W, 800))
        max_discharge_wh_per_15min = max_inverter_power_w / 4.0

        low, high = -0.5, 1.0
        best_threshold = -0.5

        batt_eff = float(self.config.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0

        for _ in range(15):
            mid_threshold = (low + high) / 2.0
            simulated_batt_wh = batt_cap_wh * (current_batt_pct / 100.0)
            min_batt_wh = batt_cap_wh * (batt_min_pct / 100.0)

            failed = False
            for fb in future_blocks:
                # Add solar with efficiency loss
                simulated_batt_wh += fb["solar"] * batt_eff

                # Decide if we can discharge
                # Apply 2% hysteresis logic in simulation as well
                is_recovery = simulated_batt_wh <= min_batt_wh
                if not is_recovery and simulated_batt_wh < (min_batt_wh + (batt_cap_wh * 0.02)):
                    # If we are in the 2% band, use the previous state. For a simple forward simulation,
                    # if we were below min_batt_wh we don't discharge until we hit +2%.
                    # But to keep it simple and robust, let's just say we don't discharge if we are below min_batt_wh
                    # The goal is to see if we fail due to *this* threshold.
                    pass

                # Discharge if price > threshold and we have enough battery, OR if battery is full
                if (fb["price"] > mid_threshold or simulated_batt_wh >= (batt_cap_wh * 0.99)) and simulated_batt_wh > min_batt_wh:
                    # Battery can only discharge at the max inverter output limit
                    actual_discharge = min(fb["cons"], max_discharge_wh_per_15min)
                    simulated_batt_wh -= actual_discharge

                    if simulated_batt_wh < min_batt_wh:
                        failed = True
                        break

                simulated_batt_wh = min(batt_cap_wh, simulated_batt_wh)

            if failed:
                low = mid_threshold
            else:
                best_threshold = mid_threshold
                high = mid_threshold

        return best_threshold

    def _build_forecast_plan(self, now, hourly_forecasts: list[dict], current_batt_pct: float, batt_cap_wh: float, price_threshold: float):
        """Generate a forecast plan for the dynamic horizon using the dynamic threshold."""
        plan = []
        simulated_batt_wh = batt_cap_wh * (current_batt_pct / 100.0)
        future_blocks = self._build_future_blocks(now, hourly_forecasts)

        batt_eff = float(self.config.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0

        max_inverter_power_w = float(self.config.get(CONF_MAX_INVERTER_POWER_W, 800))
        max_discharge_wh_per_15min = max_inverter_power_w / 4.0

        min_batt_pct = self.config.get(CONF_BATTERY_MIN_LIMIT_PCT, 10)
        min_batt_wh = batt_cap_wh * (min_batt_pct / 100.0)
        recovery_mode = getattr(self, "_battery_recovery_mode", False)

        for i, fb in enumerate(future_blocks):
            pred_solar = fb["solar"] * batt_eff
            pred_cons = fb["cons"]
            price = fb["price"]

            # Update recovery mode logic for forecast
            if simulated_batt_wh <= min_batt_wh:
                recovery_mode = True
            elif simulated_batt_wh >= (min_batt_wh + (batt_cap_wh * 0.02)):
                recovery_mode = False

            # The actual battery discharge is capped by the inverter
            actual_discharge = min(pred_cons, max_discharge_wh_per_15min)

            # Will overfill check (basic lookahead sum, capped to inverter limit)
            rem_solar = sum(b["solar"] for b in future_blocks[i:])
            rem_cons = sum(min(b["cons"], max_discharge_wh_per_15min) for b in future_blocks[i:])
            available_cap = batt_cap_wh - simulated_batt_wh

            # Use a more accurate intermediate overfill check
            will_overfill = False
            temp_simulated = simulated_batt_wh
            for b in future_blocks[i:]:
                temp_simulated += (b["solar"] * batt_eff) - min(b["cons"], max_discharge_wh_per_15min)
                if temp_simulated > batt_cap_wh:
                    will_overfill = True
                    break
                temp_simulated = min(batt_cap_wh, max(0, temp_simulated))

            if recovery_mode:
                action = "Batterie am Minimum (DTU Aus)"
                simulated_batt_wh += pred_solar
            elif will_overfill:
                action = "Überschussvermeidung (DTU An)"
                simulated_batt_wh += pred_solar - actual_discharge
            elif simulated_batt_wh >= (batt_cap_wh * 0.99):
                # Force zero-export if the battery is basically full, regardless of price
                action = "Batterie voll (DTU An)"
                simulated_batt_wh += pred_solar - actual_discharge
            elif price <= price_threshold:
                action = f"Netzbezug (Akku sparen für >{round(price_threshold,3)}€)"
                simulated_batt_wh += pred_solar
            else:
                action = "Nulleinspeisung (DTU An)"
                simulated_batt_wh += pred_solar - actual_discharge

            simulated_batt_wh = max(0, min(batt_cap_wh, simulated_batt_wh))

            plan.append({
                "hour": fb["dt"].strftime("%H:%M"),
                "price": round(price, 4),
                "solar_wh": round(pred_solar),
                "consumption_wh": round(pred_cons),
                "house_wh": round(fb["house_cons"]),
                "balcony_wh": round(fb["balcony"]),
                "battery_pct_end": round((simulated_batt_wh / batt_cap_wh) * 100),
                "planned_action": action
            })

        self.hourly_plan = plan

    def _build_future_blocks_pessimistic(self, now, hourly_forecasts: list[dict]) -> list[dict]:
        """Builds a pessimistic list of 15-min blocks applying the current daily deviation factors."""
        blocks = self._build_future_blocks(now, hourly_forecasts)

        # Determine end of today for the pessimistic daily boundary
        end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        # Apply the factors only for blocks belonging to today
        solar_factor = min(1.0, getattr(self, "pessimistic_solar_factor", 1.0))
        cons_factor = max(1.0, getattr(self, "pessimistic_consumption_factor", 1.0))

        for b in blocks:
            if b["dt"] <= end_of_today:
                b["solar"] = b["solar"] * solar_factor
                b["cons"] = b["cons"] * cons_factor

        return blocks

    def _build_future_blocks(self, now, hourly_forecasts: list[dict]) -> list[dict]:
        """Builds a list of 15-min blocks until the end of available prices."""
        # Find maximum time we have prices for
        max_dt = now
        for p in self.tibber_prices:
            p_dt = p.get("datetime")
            if p_dt and p_dt > max_dt:
                max_dt = p_dt

        # Extend max_dt to end of the last block
        max_dt += timedelta(minutes=15)

        # We want to always simulate at least 24h into the future to ensure we
        # don't run out of battery during the night/next morning.
        min_required_horizon = now + timedelta(hours=24)
        if max_dt < min_required_horizon:
             max_dt = min_required_horizon

        blocks = []
        eval_dt = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)

        while eval_dt < max_dt:
            hr = eval_dt.hour
            q = self._get_quarter_index(eval_dt)

            # Find cloud cover for this hour
            cc = 50.0
            for f in hourly_forecasts:
                if f["datetime"].date() == eval_dt.date() and f["datetime"].hour == hr:
                    cc = f["cloud_cover"]
                    break

            pred_solar = self.learning_engine.predict_solar_for_quarter(q, cc)

            # Apply intraday solar correction if it's for today
            end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=999999)
            if eval_dt <= end_of_today:
                pred_solar *= getattr(self, "_intraday_solar_factor", 1.0)
                # Cap to physical limit
                solar_peak_w = float(self.config.get(CONF_SOLAR_PEAK_W, 6000))
                solar_peak_wh_15min = solar_peak_w / 4.0
                if pred_solar > solar_peak_wh_15min:
                    pred_solar = solar_peak_wh_15min

            # Project presence for this future block
            projected_active_persons = []

            # Is there a calendar event (planned absence) covering this block?
            # We assume a global calendar approach: if an absence calendar event is active,
            # we assume nobody is home UNLESS their live tracker overrides it.
            is_planned_absence = False
            for start_dt, end_dt in getattr(self, "_planned_absences", []):
                if start_dt <= eval_dt < end_dt:
                    is_planned_absence = True
                    break

            active_now = getattr(self, "_active_persons_now", [])
            spontaneous_ends = getattr(self, "_spontaneous_absence_end", {})
            all_persons = getattr(self, "_all_tracked_persons", [])

            for person_id in all_persons:
                if person_id in active_now:
                    # Live tracker says they are home -> Overrides calendar and everything else
                    projected_active_persons.append(person_id)
                elif is_planned_absence:
                    # Not home, and calendar says they shouldn't be -> They are away
                    pass
                elif person_id in spontaneous_ends:
                    # Not home, no calendar event -> Are they within the spontaneous absence window?
                    if eval_dt < spontaneous_ends[person_id]:
                        # Still in the window -> Project away
                        pass
                    else:
                        # Spontaneous window expired, assume they return (pessimistic planning)
                        projected_active_persons.append(person_id)
                else:
                    # Edge case fallback (e.g. no tracker data at all), default to present for safety
                    projected_active_persons.append(person_id)

            house_cons = self.learning_engine.predict_consumption_for_quarter(q, eval_dt.weekday() >= 5, projected_active_persons)
            pred_balcony = 0.0

            # Only apply predicted balcony if a sensor is configured
            if self.config.get(CONF_BALCONY_POWER_SENSOR):
                pred_balcony = self.learning_engine.predict_balcony_for_quarter(q, cc)
                if eval_dt <= end_of_today:
                    pred_balcony *= getattr(self, "_intraday_solar_factor", 1.0)

            # Balcony production reduces future consumption from the grid/battery point of view.
            # It does NOT add to pred_solar because it cannot charge the battery.
            # Excess goes to the grid.
            pred_cons = max(0.0, house_cons - pred_balcony)

            # Find price for this 15 min block
            price = None
            for p in self.tibber_prices:
                p_dt = p.get("datetime")
                if p_dt and p_dt <= eval_dt < p_dt + timedelta(minutes=15):
                    price = float(p.get("total", 0.0))
                    break

            # Fallback logic: if Tibber hasn't published tomorrow's prices yet,
            # use the price from exactly 24 hours ago.
            if price is None:
                fallback_dt = eval_dt - timedelta(hours=24)
                for p in self.tibber_prices:
                    p_dt = p.get("datetime")
                    if p_dt and p_dt <= fallback_dt < p_dt + timedelta(minutes=15):
                        price = float(p.get("total", 0.0))
                        break

            # Last resort fallback if still no price found
            if price is None:
                price = 0.20

            # Extreme Price Reserve Logic
            if price > self.extreme_price_threshold:
                pred_solar = pred_solar * self.extreme_price_factor

            blocks.append({
                "dt": eval_dt,
                "solar": pred_solar,
                "cons": pred_cons,
                "house_cons": house_cons,
                "balcony": pred_balcony,
                "price": price
            })

            eval_dt += timedelta(minutes=15)

        return blocks
