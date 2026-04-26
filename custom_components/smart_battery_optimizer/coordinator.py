"""Coordinator to handle the core control logic for Smart Battery Optimizer."""
import logging
from datetime import timedelta, datetime
import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util
from .tibber import fetch_tibber_prices

from .device_manager import SmartDeviceManager

from .const import (
    CONF_SMART_DEVICES,
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
    CONF_EXCESS_EXTERNAL_INVERTER,
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
        self.device_manager = SmartDeviceManager(hass)

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
        await self.device_manager.async_load()
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

        # Smart Devices logic
        now = dt_util.now()
        smart_devices_str = self.config.get(CONF_SMART_DEVICES, "")
        if smart_devices_str:
            smart_devices = [s.strip() for s in smart_devices_str.split(",") if s.strip()]
            for device_id in smart_devices:
                power = self._get_float_state(device_id)

                # Auto-Detect, Auto-Start, Auto-Cancel, and Continuous Learning
                await self.device_manager.update_live_device_states(device_id, power)

                if device_id in self.device_manager.learning_states:
                    self.device_manager.record_power(device_id, power)
                    if self.device_manager.learning_states[device_id]["zero_power_minutes"] > 10:
                        await self.device_manager.stop_learning(device_id)

                excluded_power += power

        current_balcony = 0.0
        balcony_sensor = self.config.get(CONF_BALCONY_POWER_SENSOR)
        if balcony_sensor:
            current_balcony = self._get_float_state(balcony_sensor)

        # Ensure all inputs are valid floats before math operations to prevent TypeErrors
        try:
            self.calculated_house_consumption = max(
                0.0,
                float(tibber_cons) - float(tibber_exp) + float(opendtu_output) - float(excluded_power) + float(current_balcony)
            )
        except (ValueError, TypeError) as e:
            _LOGGER.warning("Could not calculate house consumption due to invalid sensor states: %s", e)
            # Fallback to last known or 0
            self.calculated_house_consumption = getattr(self, 'calculated_house_consumption', 0.0)

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

        await self.learning_engine.record_consumption(current_quarter, self.calculated_house_consumption)
        await self.learning_engine.record_solar(current_quarter, current_solar, cloud_cover, charge_state)
        await self.learning_engine.record_balcony(current_quarter, current_balcony, cloud_cover)
        await self.learning_engine.record_dtu_output(current_quarter, opendtu_output)
        await self.learning_engine.record_grid_import(current_quarter, tibber_cons)

        if current_quarter != self.learning_engine._last_quarter_processed and self.learning_engine._last_quarter_processed != -1:
            # Get price from that quarter to pass to finalize_quarter
            old_price = 0.0
            if self.hourly_plan:
                # Find the plan for the exact time
                for p in self.hourly_plan:
                    if p.get("hour") == now.strftime("%H:%M"):
                        old_price = p.get("price", 0.0)
                        break

            c_price = old_price
            actual_cons_wh, actual_solar_wh, actual_dtu_wh, actual_grid_import_wh = await self.learning_engine.finalize_quarter(self.learning_engine._last_quarter_processed, cloud_cover, c_price)

            # Savings calculations
            c_price_for_savings = max(0.0, c_price) / 1000.0  # EUR per Wh, ignore negative prices

            # 1. Total Battery Savings
            self.learning_engine.data["savings"]["total_battery_savings"] += actual_dtu_wh * c_price_for_savings

            # 2. Total Battery Savings vs No Battery
            sim_rest_wh = max(0.0, actual_cons_wh - actual_solar_wh)
            sim_cost = sim_rest_wh * c_price_for_savings
            actual_cost = actual_grid_import_wh * c_price_for_savings

            savings_diff = sim_cost - actual_cost
            self.learning_engine.data["savings"]["total_battery_savings_vs_no_battery"] += savings_diff

            # Calculate pessimistic factors
            pred_solar_wh = self.learning_engine.predict_solar_for_quarter(self.learning_engine._last_quarter_processed, cloud_cover)
            pred_cons_wh = self.learning_engine.predict_consumption_for_quarter(self.learning_engine._last_quarter_processed)

            # Balcony gets added to predicted solar just like in _build_future_blocks
            if self.config.get(CONF_BALCONY_POWER_SENSOR):
                pred_balcony = self.learning_engine.predict_balcony_for_quarter(self.learning_engine._last_quarter_processed, cloud_cover)
                pred_solar_wh += pred_balcony

            # If the charge state is absorption/float, true solar potential is hidden.
            # Do not use this quarter to penalize solar prediction.
            is_absorption = charge_state and charge_state.lower() in ("absorption", "float", "ausgleichsladung", "equalization")
            if is_absorption or actual_solar_wh >= pred_solar_wh or pred_solar_wh == 0:
                self.pessimistic_solar_factor = 1.0
            else:
                self.pessimistic_solar_factor = actual_solar_wh / pred_solar_wh

            # Removed pessimistic consumption factor as spikes (e.g. cooking) ruin the daily forecast
            self.pessimistic_consumption_factor = 1.0

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

        # Re-calc 24h sum
        for block in self.hourly_plan:
            self.predicted_remaining_solar += block["solar_wh"]
            self.predicted_remaining_consumption += block["consumption_wh"]

        battery_will_overfill = self.predicted_remaining_solar > (self.predicted_remaining_consumption + available_batt_capacity_wh)

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

        # --- Negative Price Override ---
        # When price is negative, we get paid to consume energy.
        # Force all excess consumers ON and ensure OpenDTU (inverter) remains OFF.
        is_negative_price = current_price is not None and current_price < 0.0

# --- Negative Price Override ---
        # When price is negative, we get paid to consume energy.
        # Force all excess consumers ON and ensure OpenDTU (inverter) remains OFF.
        is_negative_price = current_price is not None and current_price < 0.0

        # Helper for checking if consumers are on external inverter
        external_inverters = self.config.get(CONF_EXCESS_EXTERNAL_INVERTER, [])
        if isinstance(external_inverters, bool):
            external_inverters = []
        def _any_external(entities):
            return any(e in external_inverters for e in entities)

        # Primary Hysteresis Logic
        primary_entities = self.config.get(CONF_PRIMARY_EXCESS_CONSUMERS, [])
        primary_is_external = _any_external(primary_entities)

        if self.primary_excess_auto:
            if is_negative_price:
                turn_on_primary = True
            else:
                turn_on_primary = False
                if primary_is_external:
                    # External inverter: strict battery percentage logic, ignore solar/cloud
                    if virtual_batt_pct >= getattr(self, "primary_excess_on", 95.0):
                        turn_on_primary = True
                    elif virtual_batt_pct <= getattr(self, "primary_excess_off", 90.0):
                        turn_on_primary = False
                    else:
                        turn_on_primary = any(self.hass.states.get(e) and self.hass.states.get(e).state == "on" for e in primary_entities)
                else:
                    if virtual_batt_pct >= getattr(self, "primary_excess_on", 95.0) and has_excess_power and battery_will_overfill:
                        turn_on_primary = True
                    elif virtual_batt_pct <= getattr(self, "primary_excess_off", 90.0) or cloud_override_off:
                        turn_on_primary = False
                    else:
                        turn_on_primary = any(self.hass.states.get(e) and self.hass.states.get(e).state == "on" for e in primary_entities)

            await set_switches(primary_entities, turn_on_primary)

        # Secondary Hysteresis Logic
        secondary_entities = self.config.get(CONF_SECONDARY_EXCESS_CONSUMERS, [])
        secondary_is_external = _any_external(secondary_entities)

        if self.secondary_excess_auto:
            if is_negative_price:
                turn_on_secondary = True
            else:
                turn_on_secondary = False
                if secondary_is_external:
                    if virtual_batt_pct >= getattr(self, "secondary_excess_on", 98.0):
                        turn_on_secondary = True
                    elif virtual_batt_pct <= getattr(self, "secondary_excess_off", 95.0):
                        turn_on_secondary = False
                    else:
                        turn_on_secondary = any(self.hass.states.get(e) and self.hass.states.get(e).state == "on" for e in secondary_entities)
                else:
                    if virtual_batt_pct >= getattr(self, "secondary_excess_on", 98.0) and has_excess_power and battery_will_overfill:
                        turn_on_secondary = True
                    elif virtual_batt_pct <= getattr(self, "secondary_excess_off", 95.0) or cloud_override_off:
                        turn_on_secondary = False
                    else:
                        turn_on_secondary = any(self.hass.states.get(e) and self.hass.states.get(e).state == "on" for e in secondary_entities)

            await set_switches(secondary_entities, turn_on_secondary)

        # Early Excess Logic
        early_entities = self.config.get(CONF_EARLY_EXCESS_CONSUMERS, [])
        early_is_external = _any_external(early_entities)

        if getattr(self, "early_excess_auto", True):
            if is_negative_price:
                turn_on_early = True
            else:
                early_excess_min_batt = float(self.config.get(CONF_EARLY_EXCESS_MIN_BATTERY_PCT, 30.0))
                if virtual_batt_pct < early_excess_min_batt:
                    turn_on_early = False
                elif not has_excess_power and cloud_override_off and not early_is_external:
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


        # Update recovery mode
        if batt_level_pct <= batt_min_pct and not is_absorption:
            self._battery_recovery_mode = True
        elif batt_level_pct >= (batt_min_pct + 2.0) or is_absorption:
            self._battery_recovery_mode = False

        if is_negative_price:
            self.current_operating_mode = f"Negativer Preis ({round(current_price,3)}€): DTU aus (Netzbezug maximieren)"
            turn_on_inverter = False

        elif self.manual_zero_export:
            self.current_operating_mode = "Manueller Modus: Nulleinspeisung erzwungen"
            turn_on_inverter = True

        elif self._battery_recovery_mode:
            self.current_operating_mode = "Batterie am Minimum: OpenDTU aus (Netzbezug)"
            turn_on_inverter = False

        elif battery_will_overfill or is_absorption:
            self.current_operating_mode = "Batterie wird voll/Absorption: Überschussvermeidung aktiv"
            turn_on_inverter = True

        elif virtual_batt_pct >= 100.0:
            self.current_operating_mode = "Batterie 100% voll: DTU an (Nulleinspeisung aktiv)"
            turn_on_inverter = True

        elif current_price is not None and current_price <= price_threshold:
            self.current_operating_mode = f"Strom günstig (<{round(price_threshold,3)}€): DTU aus (Akku wird gespart)"
            turn_on_inverter = False

        else:
            self.current_operating_mode = "Preis hoch: DTU an (Nulleinspeisung aktiv)"
            turn_on_inverter = True

        producing_sensor = self.config.get(CONF_OPENDTU_PRODUCING_SENSOR)
        inverter_is_on = True # Default to True to force off if unknown
        if producing_sensor:
            producing_state = self.hass.states.get(producing_sensor)
            if producing_state:
                # OpenDTU producing sensor is a binary_sensor. It is 'on' when producing, 'off' when not.
                # However, during initialization or errors, it might be 'unavailable' or 'unknown'.
                # We also want to fire the button if our logical state changes, even if the sensor hasn't updated yet.
                inverter_is_on = producing_state.state == "on"

                # Check if the state is something other than "on" or "off" (e.g. string state from older config)
                if producing_state.state not in ("on", "off"):
                    # If it's a string like "producing", we map it.
                    inverter_is_on = str(producing_state.state).lower() in ("on", "true", "1", "producing")

        try:
            turn_on_btn = self.config.get(CONF_OPENDTU_TURN_ON_BUTTON)
            turn_off_btn = self.config.get(CONF_OPENDTU_TURN_OFF_BUTTON)
            dpl_mode_select = self.config.get(CONF_OPENDTU_DPL_MODE_SELECT)

            dpl_mode_current = None
            if dpl_mode_select:
                dpl_state_obj = self.hass.states.get(dpl_mode_select)
                if dpl_state_obj:
                    # Keep as string for comparison
                    dpl_mode_current = str(dpl_state_obj.state)

            # Fire the button if the requested state differs from what we *think* the current state is.
            # We also fire if our internal requested state changed since last time, just to be sure.
            # For negative prices, we force the OFF button every time to ensure DPL is definitely set.
            requested_state_str = "on" if turn_on_inverter else "off"

            async def set_dpl_mode(entity_id: str, value: str):
                domain = entity_id.split('.')[0]
                if domain in ("select", "input_select"):
                    await self.hass.services.async_call(domain, "select_option", {"entity_id": entity_id, "option": value}, blocking=False)
                elif domain in ("number", "input_number"):
                    try:
                        await self.hass.services.async_call(domain, "set_value", {"entity_id": entity_id, "value": float(value)}, blocking=False)
                    except ValueError:
                        pass

            dpl_mismatch_on = dpl_mode_select and dpl_mode_current not in ("0", "0.0")
            dpl_mismatch_off = dpl_mode_select and dpl_mode_current not in ("1", "1.0")

            if turn_on_inverter and (not inverter_is_on or self._current_inverter_state != "on" or dpl_mismatch_on):
                if turn_on_btn and self.hass.states.get(turn_on_btn) is not None:
                    await self.hass.services.async_call("button", "press", {"entity_id": turn_on_btn}, blocking=False)
                if dpl_mode_select and self.hass.states.get(dpl_mode_select) is not None:
                    await set_dpl_mode(dpl_mode_select, "0")
                self._current_inverter_state = "on"
            elif not turn_on_inverter and (inverter_is_on or self._current_inverter_state != "off" or is_negative_price or dpl_mismatch_off):
                if turn_off_btn and self.hass.states.get(turn_off_btn) is not None:
                    await self.hass.services.async_call("button", "press", {"entity_id": turn_off_btn}, blocking=False)
                if dpl_mode_select and self.hass.states.get(dpl_mode_select) is not None:
                    await set_dpl_mode(dpl_mode_select, "1")
                self._current_inverter_state = "off"
        except Exception as e:
            _LOGGER.error("Failed to press OpenDTU button or set DPL Mode: %s", e)

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

        external_inverters = self.config.get(CONF_EXCESS_EXTERNAL_INVERTER, [])
        if isinstance(external_inverters, bool):
            external_inverters = []
        early_entities = self.config.get(CONF_EARLY_EXCESS_CONSUMERS, [])
        early_is_external = any(e in external_inverters for e in early_entities)

        for fb in future_blocks:
            pred_solar = fb["solar"] * batt_eff

            # Real dynamic consumption
            base_cons = max(0.0, fb["house_wh"] - fb["balcony"])

            if early_is_external:
                # If external, early cons is drawn directly from battery, independent of DTU
                pred_cons = base_cons
                if pred_solar > pred_cons:
                    simulated_batt_wh += (pred_solar - pred_cons)
                else:
                    actual_discharge = min(pred_cons - pred_solar, max_discharge_wh_per_15min)
                    simulated_batt_wh -= actual_discharge

                # Early consumer unconditionally drains from battery
                simulated_batt_wh -= early_expected_wh_per_15min
            else:
                pred_cons = base_cons + early_expected_wh_per_15min
                if pred_solar > pred_cons:
                    simulated_batt_wh += (pred_solar - pred_cons)
                else:
                    actual_discharge = min(pred_cons - pred_solar, max_discharge_wh_per_15min)
                    simulated_batt_wh -= actual_discharge

            if simulated_batt_wh >= batt_cap_wh:
                return True

        return False

    def _simulate_optimal_threshold(self, now, hourly_forecasts: list[dict], current_batt_pct: float, batt_cap_wh: float, batt_min_pct: float) -> float:
        """Simulates the future to find the optimal price threshold to discharge the battery."""
        # Use pessimistic blocks for the threshold calculation to ensure safe predictions
        future_blocks = self._build_future_blocks_pessimistic(now, hourly_forecasts)

        max_inverter_power_w = float(self.config.get(CONF_MAX_INVERTER_POWER_W, 800))
        max_discharge_wh_per_15min = max_inverter_power_w / 4.0

        low, high = -0.5, 1.0
        best_threshold = -0.5

        batt_eff = float(self.config.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0

        for _ in range(15):
            mid_threshold = (low + high) / 2.0
            simulated_batt_wh = batt_cap_wh * (current_batt_pct / 100.0)
            min_batt_wh = batt_cap_wh * (batt_min_pct / 100.0)

            failed_due_to_empty = False
            failed_due_to_full = False

            for fb in future_blocks:
                # Add solar with efficiency loss
                simulated_batt_wh += fb["solar"] * batt_eff

                pred_cons = max(0.0, fb["house_wh"] - fb["balcony"])

                # Discharge if price > threshold and we have enough battery.
                # HOWEVER, if price is negative, we explicitly do NOT discharge, because we want to consume grid power.
                if fb["price"] > mid_threshold and simulated_batt_wh > min_batt_wh and fb["price"] >= 0.0:
                    # Battery can only discharge at the max inverter output limit (for the house load)
                    actual_discharge = min(pred_cons, max_discharge_wh_per_15min)
                    simulated_batt_wh -= actual_discharge

                    if simulated_batt_wh < min_batt_wh:
                        failed_due_to_empty = True
                        break

                # The primary directive: 100% is bad. If we reach ~99%, the threshold is too high (too much saving).
                if simulated_batt_wh >= (batt_cap_wh * 0.99):
                    failed_due_to_full = True
                    break



            if failed_due_to_full:
                # We need to discharge more, lower the threshold so it discharges at cheaper prices
                high = mid_threshold


            elif failed_due_to_empty:
                # We discharged too much, raise the threshold
                low = mid_threshold
            else:
                # Neither empty nor full, this is a valid threshold.
                # Try to lower the threshold further to maximize battery usage (offset more grid import)
                best_threshold = mid_threshold
                high = mid_threshold

        return best_threshold

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
                b["balcony"] = b["balcony"] * solar_factor
                b["house_wh"] = b["house_wh"] * cons_factor

        return blocks

    def _build_forecast_plan(self, now, hourly_forecasts: list[dict], current_batt_pct: float, batt_cap_wh: float, price_threshold: float):
        """Generate a forecast plan for the dynamic horizon using the dynamic threshold."""
        plan = []
        simulated_batt_wh = batt_cap_wh * (current_batt_pct / 100.0)
        future_blocks = self._build_future_blocks_pessimistic(now, hourly_forecasts)

        batt_eff = float(self.config.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0

        max_inverter_power_w = float(self.config.get(CONF_MAX_INVERTER_POWER_W, 800))
        max_discharge_wh_per_15min = max_inverter_power_w / 4.0

        min_batt_pct = self.config.get(CONF_BATTERY_MIN_LIMIT_PCT, 10)
        min_batt_wh = batt_cap_wh * (min_batt_pct / 100.0)
        recovery_mode = getattr(self, "_battery_recovery_mode", False)

        for i, fb in enumerate(future_blocks):
            pred_solar = fb["solar"] * batt_eff
            pred_balcony = fb["balcony"]
            raw_house_wh = fb["house_wh"]
            cc = fb["cc"]
            price = fb["price"]

            # The actual consumption the battery sees is house minus balcony
            pred_cons = max(0.0, raw_house_wh - pred_balcony)

            # Update recovery mode logic for forecast
            if simulated_batt_wh <= min_batt_wh:
                recovery_mode = True
            elif simulated_batt_wh >= (min_batt_wh + (batt_cap_wh * 0.02)):
                recovery_mode = False

            # The actual battery discharge is capped by the inverter
            actual_discharge = min(pred_cons, max_discharge_wh_per_15min)

            # Will overfill check (basic lookahead sum, capped to inverter limit)
            rem_solar = sum(b["solar"] for b in future_blocks[i:])

            # Recalculate remaining dynamic consumption
            rem_cons = sum(min(max(0.0, b["house_wh"] - b["balcony"]), max_discharge_wh_per_15min) for b in future_blocks[i:])

            available_cap = batt_cap_wh - simulated_batt_wh
            will_overfill = rem_solar > (rem_cons + available_cap)

            if price < 0.0:
                action = f"Negativer Preis ({round(price,3)}€): DTU aus (Netzbezug)"
                simulated_batt_wh += pred_solar
            elif simulated_batt_wh >= batt_cap_wh:
                action = "Batterie 100% voll (DTU An)"
                simulated_batt_wh += pred_solar - actual_discharge
            elif recovery_mode:
                action = "Batterie am Minimum (DTU Aus)"
                simulated_batt_wh += pred_solar
            elif will_overfill:
                action = "Überschussvermeidung (DTU An)"
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
                "balcony_wh": round(pred_balcony),
                "house_wh": round(raw_house_wh),
                "consumption_wh": round(pred_cons),
                "cloud_cover": round(cc),
                "battery_pct_end": round((simulated_batt_wh / batt_cap_wh) * 100),
                "planned_action": action
            })

        self.hourly_plan = plan

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

        # Ensure we always simulate at least 24 hours into the future,
        # but if we have prices up to midnight tomorrow, we go up to that max_dt.
        minimum_end_dt = now + timedelta(hours=24)
        if max_dt < minimum_end_dt:
            max_dt = minimum_end_dt

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
            pred_cons = self.learning_engine.predict_consumption_for_quarter(q)

            planned_device_power_w_sum = 0.0
            for minute_offset in range(15):
                dt_minute = eval_dt + timedelta(minutes=minute_offset)
                planned_device_power_w_sum += self.device_manager.get_planned_power_at_time(dt_minute)

            planned_device_wh = planned_device_power_w_sum / 60.0
            pred_cons += planned_device_wh

            pred_balcony = 0.0
            # Only add predicted balcony if a sensor is configured
            if self.config.get(CONF_BALCONY_POWER_SENSOR):
                pred_balcony = self.learning_engine.predict_balcony_for_quarter(q, cc)

            # Find price for this 15 min block
            price = None
            for p in self.tibber_prices:
                p_dt = p.get("datetime")
                if p_dt and p_dt <= eval_dt < p_dt + timedelta(minutes=15):
                    price = float(p.get("total", 0.0))
                    break

            # Fallback: if we don't have the price (e.g. tomorrow's prices aren't published yet),
            # copy the price from exactly 24 hours ago.
            if price is None:
                fallback_dt = eval_dt - timedelta(hours=24)
                for p in self.tibber_prices:
                    p_dt = p.get("datetime")
                    if p_dt and p_dt <= fallback_dt < p_dt + timedelta(minutes=15):
                        price = float(p.get("total", 0.0))
                        break

            # If still None (which shouldn't happen unless we have no prices at all), fallback to 0.0
            if price is None:
                price = 0.0

            # Extreme Price Reserve Logic
            if price > self.extreme_price_threshold:
                pred_solar = pred_solar * self.extreme_price_factor
                pred_balcony = pred_balcony * self.extreme_price_factor

            blocks.append({
                "dt": eval_dt,
                "solar": pred_solar,
                "balcony": pred_balcony,
                "house_wh": pred_cons,
                "cc": cc,
                "price": price
            })

            eval_dt += timedelta(minutes=15)

        return blocks

    async def async_calculate_optimal_start_times(self, device_id: str, program_name: str, max_hours: int = 24) -> list[tuple[datetime, float]]:
        """Find the top 3 optimal start times and costs, spaced at least 60 minutes apart."""
        profile = self.device_manager.get_program_profile(device_id, program_name)
        if not profile:
            return [(dt_util.now(), 0.0)]

        now = dt_util.now()
        start_eval = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0) + timedelta(minutes=15)
        end_eval = start_eval + timedelta(hours=max_hours)

        hourly_forecasts = await self._get_hourly_forecasts()

        results = []

        original_plan = dict(self.device_manager.planned_devices)

        eval_dt = start_eval
        while eval_dt < end_eval:
            if device_id in self.device_manager.planned_devices:
                del self.device_manager.planned_devices[device_id]
            blocks_without = self._build_future_blocks(now, hourly_forecasts)

            self.device_manager.set_planned_device(device_id, program_name, eval_dt, 0.0)
            blocks_with = self._build_future_blocks(now, hourly_forecasts)

            cost_without = self._simulate_grid_cost(blocks_without)
            cost_with = self._simulate_grid_cost(blocks_with)

            marginal_cost = max(0.0, cost_with - cost_without)
            results.append((eval_dt, marginal_cost))

            eval_dt += timedelta(minutes=15)

        self.device_manager.planned_devices = original_plan

        # Sort by lowest cost
        results.sort(key=lambda x: x[1])

        # Filter top 3 ensuring at least 60 mins apart
        top_results = []
        for res in results:
            dt, cost = res
            too_close = False
            for top_dt, _ in top_results:
                if abs((dt - top_dt).total_seconds()) < 3600:
                    too_close = True
                    break
            if not too_close:
                top_results.append(res)
            if len(top_results) >= 3:
                break

        if not top_results:
            top_results = [(dt_util.now(), 0.0)]

        return top_results

    def _simulate_grid_cost(self, blocks: list[dict]) -> float:
        """Simulate total grid cost for a set of blocks, considering battery."""
        batt_cap_wh = self.config.get(CONF_BATTERY_CAPACITY_WH, 5000)
        batt_pct = self._get_float_state(self.config.get(CONF_BATTERY_LEVEL_SENSOR))
        simulated_batt_wh = batt_cap_wh * (batt_pct / 100.0)

        batt_eff = float(self.config.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0
        max_inverter_power_w = float(self.config.get(CONF_MAX_INVERTER_POWER_W, 800))
        max_discharge_wh_per_15min = max_inverter_power_w / 4.0

        min_batt_pct = self.config.get(CONF_BATTERY_MIN_LIMIT_PCT, 10)
        min_batt_wh = batt_cap_wh * (min_batt_pct / 100.0)

        total_cost = 0.0

        for b in blocks:
            pred_solar = b["solar"] * batt_eff
            pred_cons = b.get("house_wh", 0.0)
            pred_cons -= b.get("balcony", 0.0)
            if pred_cons < 0:
                pred_cons = 0

            price = b["price"]

            simulated_batt_wh += pred_solar

            if simulated_batt_wh > min_batt_wh:
                available_discharge = simulated_batt_wh - min_batt_wh
                actual_discharge = min(pred_cons, max_discharge_wh_per_15min, available_discharge)
                simulated_batt_wh -= actual_discharge
                grid_import_wh = pred_cons - actual_discharge
            else:
                grid_import_wh = pred_cons

            simulated_batt_wh = min(batt_cap_wh, simulated_batt_wh)

            cost = (grid_import_wh / 1000.0) * price
            total_cost += cost

        return total_cost
