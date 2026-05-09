"""Coordinator to handle the core control logic for Smart Battery Optimizer."""
import logging
from datetime import timedelta, datetime
import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util
from .tibber import fetch_tibber_prices

from .const import (
    CONF_SOLAR_PEAK_W,
    CONF_OPENDTU_DPL_MODE_SELECT,
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
    CONF_EARLY_EXCESS_MAX_BATTERY_PCT,
    CONF_BATTERY_MAX_LIMIT_PCT,
    CONF_EXCESS_MIN_RUN_TIME_MINUTES,
)
from .learning import LearningEngine
from .appliance_manager import SmartApplianceManager, ApplianceStateMachine, ProposalCalculator

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

        # Smart Appliances
        from .appliance_manager import SmartApplianceManager, ProposalCalculator
        # Note: at __init__, config_entry might not be fully attached in this architecture.
        # But if it is passed in config, or we can just initialize them dynamically in _async_setup
        self.appliance_manager = None
        self.appliance_state_machines = {}
        self.proposal_calculator = ProposalCalculator(self)
        self.appliance_entities = {'button': [], 'select': [], 'sensor': [], 'text': []}

        # Internal state
        self.is_enabled = True
        self.manual_zero_export = False
        self._excess_devices_last_turned_on = {}
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
        """Check if learning mode is active."""
        return self.learning_engine.data.get("learning_mode_active", False)

    @property
    def learning_rate_factor(self) -> float:
        """Get the custom learning rate factor."""
        return self.learning_engine.data.get("learning_rate_factor", 0.7)

    @learning_rate_factor.setter
    def learning_rate_factor(self, value: float):
        """Set the custom learning rate factor."""
        self.learning_engine.set_learning_rate(value)
        self.hass.async_create_task(self.learning_engine.async_save())

    async def async_start_learning_mode(self):
        """Start the learning mode manually."""
        self.learning_engine.set_learning_mode(True)
        await self.learning_engine.async_save()

    async def async_stop_learning_mode(self):
        """Stop the learning mode manually."""
        self.learning_engine.set_learning_mode(False)
        await self.learning_engine.async_save()

    async def _async_setup(self):
        # Initialize appliance manager with entry_id if not done yet
        if not self.appliance_manager:
            from .appliance_manager import SmartApplianceManager
            self.appliance_manager = SmartApplianceManager(self.hass, self.config_entry.entry_id)

        """Set up the coordinator."""
        await self.learning_engine.async_load()
        await self.appliance_manager.async_load()

        # Clear any existing entities to prevent double-initialization on reload
        self.appliance_state_machines.clear()
        self.appliance_entities = {'button': [], 'select': [], 'sensor': [], 'text': []}

        # Init state machines for configured devices
        smart_devices_str = self.config.get(CONF_SMART_DEVICES, "")
        if smart_devices_str:
            devices = [d.strip() for d in smart_devices_str.split(",") if d.strip()]
            for dev in devices:
                self.appliance_state_machines[dev] = ApplianceStateMachine(dev, self.appliance_manager)

                # Instantiate UI Entities
                from .appliance_entities import (
                    ApplianceRecordButton, ApplianceProgramSelect, ApplianceProposalSelect,
                    ApplianceConfirmButton, ApplianceDeleteButton, ApplianceRenameText, ApplianceStatusSensor, ApplianceTimerSensor,
                    ApplianceProfileSensor, ApplianceManualTimeText
                )

                prog_sel = ApplianceProgramSelect(self, self.config_entry.entry_id, dev)
                prop_sel = ApplianceProposalSelect(self, self.config_entry.entry_id, dev, prog_sel)
                manual_time_txt = ApplianceManualTimeText(self, self.config_entry.entry_id, dev)

                self.appliance_entities['select'].extend([prog_sel, prop_sel])
                self.appliance_entities['button'].extend([
                    ApplianceRecordButton(self, self.config_entry.entry_id, dev),
                    ApplianceDeleteButton(self, self.config_entry.entry_id, dev, prog_sel),
                    ApplianceConfirmButton(self, self.config_entry.entry_id, dev, prog_sel, prop_sel, manual_time_txt)
                ])
                self.appliance_entities['text'].append(ApplianceRenameText(self, self.config_entry.entry_id, dev, prog_sel))
                self.appliance_entities['text'].append(manual_time_txt)
                self.appliance_entities['sensor'].append(ApplianceStatusSensor(self, self.config_entry.entry_id, dev))
                self.appliance_entities['sensor'].append(ApplianceTimerSensor(self, self.config_entry.entry_id, dev))
                self.appliance_entities['sensor'].append(ApplianceProfileSensor(self, self.config_entry.entry_id, dev, prog_sel))

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
        now = datetime.now()
        await self._tick_appliances(now)
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
            actual_cons_wh, actual_solar_wh = await self.learning_engine.finalize_quarter(self.learning_engine._last_quarter_processed, cloud_cover, c_price)

            # Implicit Cloud Cover Correction
            pred_solar_clear = self.learning_engine.predict_solar_for_quarter(self.learning_engine._last_quarter_processed, 0.0)
            pred_solar_partly = self.learning_engine.predict_solar_for_quarter(self.learning_engine._last_quarter_processed, 50.0)
            pred_solar_cloudy = self.learning_engine.predict_solar_for_quarter(self.learning_engine._last_quarter_processed, 100.0)

            if self.config.get(CONF_BALCONY_POWER_SENSOR):
                pred_solar_clear += self.learning_engine.predict_balcony_for_quarter(self.learning_engine._last_quarter_processed, 0.0)
                pred_solar_partly += self.learning_engine.predict_balcony_for_quarter(self.learning_engine._last_quarter_processed, 50.0)
                pred_solar_cloudy += self.learning_engine.predict_balcony_for_quarter(self.learning_engine._last_quarter_processed, 100.0)

            # Determine the implied cloud cover based on actual production
            is_absorption = charge_state and charge_state.lower() in ("absorption", "float", "ausgleichsladung", "equalization")
            implied_cloud_cover = None

            if not is_absorption and pred_solar_clear > 0:
                # Fallback to physical peak if the learned clear value is too low or corrupted.
                # This prevents "good" days from being marked cloudy just because the EMA hasn't caught up.
                peak_w = float(self.config.get(CONF_SOLAR_PEAK_W, 6000.0))
                # Max Wh possible in 15 mins based on physical peak
                peak_wh_15min = peak_w / 4.0

                effective_clear_target = max(pred_solar_clear, peak_wh_15min * 0.7)

                # Calculate differences to the three categories
                diff_clear = abs(actual_solar_wh - pred_solar_clear)
                diff_partly = abs(actual_solar_wh - pred_solar_partly)
                diff_cloudy = abs(actual_solar_wh - pred_solar_cloudy)

                # Find the closest match
                min_diff = min(diff_clear, diff_partly, diff_cloudy)

                # Absolute check against physical potential to override corrupted learning
                if actual_solar_wh >= peak_wh_15min * 0.6:
                    implied_cloud_cover = 0.0 # Definitely clear if >60% of physical max
                elif actual_solar_wh >= peak_wh_15min * 0.3:
                    implied_cloud_cover = min(50.0, implied_cloud_cover) if implied_cloud_cover is not None else 50.0

                # If production is significantly higher than cloudy, it's not cloudy.
                # If production is significantly higher than expected, assume clearer skies.
                elif actual_solar_wh > pred_solar_partly and actual_solar_wh > effective_clear_target * 0.8:
                    implied_cloud_cover = 0.0 # Clear
                elif actual_solar_wh < pred_solar_partly * 0.5:
                    implied_cloud_cover = 100.0 # Cloudy
                elif min_diff == diff_clear:
                    implied_cloud_cover = 0.0
                elif min_diff == diff_partly:
                    implied_cloud_cover = 50.0
                else:
                    implied_cloud_cover = 100.0

            if implied_cloud_cover is not None:
                # Smooth the transition of implicit cloud cover
                if not hasattr(self, 'implicit_cloud_cover') or self.implicit_cloud_cover is None:
                    self.implicit_cloud_cover = implied_cloud_cover
                else:
                    # Exponential moving average for cloud cover correction (alpha = 0.3)
                    self.implicit_cloud_cover = (0.3 * implied_cloud_cover) + (0.7 * self.implicit_cloud_cover)

            # Intraday Solar Factor
            # We track how much better/worse actual yield is compared to what we predict for the *deduced* cloud cover.
            target_cc = self.implicit_cloud_cover if getattr(self, 'implicit_cloud_cover', None) is not None else cloud_cover
            target_pred = self.learning_engine.predict_solar_for_quarter(self.learning_engine._last_quarter_processed, target_cc)
            if self.config.get(CONF_BALCONY_POWER_SENSOR):
                target_pred += self.learning_engine.predict_balcony_for_quarter(self.learning_engine._last_quarter_processed, target_cc)

            if target_pred > 0 and actual_solar_wh > 0:
                raw_factor = actual_solar_wh / target_pred
                # Bound the factor to prevent absurdly huge scaling when target_pred is tiny
                raw_factor = max(0.5, min(raw_factor, 3.0))

                if not hasattr(self, 'intraday_solar_factor') or getattr(self, 'intraday_solar_factor') is None:
                    self.intraday_solar_factor = raw_factor
                else:
                    self.intraday_solar_factor = (0.3 * raw_factor) + (0.7 * getattr(self, 'intraday_solar_factor', 1.0))

            self.pessimistic_consumption_factor = 1.0

            await self.learning_engine.async_save()

        self.learning_engine._last_quarter_processed = current_quarter

        # Ensure attributes exist
        if not hasattr(self, 'implicit_cloud_cover'):
            self.implicit_cloud_cover = None
        if not hasattr(self, 'intraday_solar_factor'):
            self.intraday_solar_factor = 1.0
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
        max_batt_pct = float(self.config.get(CONF_BATTERY_MAX_LIMIT_PCT, self.config.get(CONF_EARLY_EXCESS_MAX_BATTERY_PCT, 100.0)))

        price_threshold = self._simulate_optimal_threshold(now, hourly_forecasts, batt_level_pct, batt_cap_wh, batt_min_pct)
        self._build_forecast_plan(now, hourly_forecasts, batt_level_pct, batt_cap_wh, price_threshold)

        available_batt_capacity_wh = batt_cap_wh * (1.0 - (batt_level_pct / 100.0))

        # Re-calc 24h sum
        for block in self.hourly_plan:
            self.predicted_remaining_solar += block["solar_wh"]
            self.predicted_remaining_consumption += block["consumption_wh"]

        # Forward simulation to accurately detect if battery will hit max limit before emptying
        battery_will_overfill = False
        temp_batt_pct = batt_level_pct
        for block in self.hourly_plan:
            temp_batt_pct = block.get("battery_pct_end", temp_batt_pct)
            if temp_batt_pct >= max_batt_pct:
                battery_will_overfill = True
                break
            if temp_batt_pct <= batt_min_pct:
                break

        turn_on_inverter = True

        async def set_switches(entities: list, turn_on: bool):
            min_run_time = int(self.config.get(CONF_EXCESS_MIN_RUN_TIME_MINUTES, 10))
            current_time = dt_util.now()

            for switch_entity in entities:
                state = self.hass.states.get(switch_entity)
                if state is None:
                    continue
                is_on = state and state.state == "on"

                if turn_on:
                    if not is_on:
                        await self.hass.services.async_call("switch", "turn_on", {"entity_id": switch_entity}, blocking=False)
                        self._excess_devices_last_turned_on[switch_entity] = current_time
                else:
                    if is_on:
                        last_turned_on = self._excess_devices_last_turned_on.get(switch_entity)
                        if last_turned_on:
                            elapsed = (current_time - last_turned_on).total_seconds() / 60.0
                            if elapsed < min_run_time:
                                _LOGGER.debug("Switch %s must remain on. Min run time not reached. Elapsed: %.1f, Min: %d", switch_entity, elapsed, min_run_time)
                                continue
                        await self.hass.services.async_call("switch", "turn_off", {"entity_id": switch_entity}, blocking=False)
                        self._excess_devices_last_turned_on.pop(switch_entity, None)

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
                    # Will battery overfill if this device is OFF?
                    will_overfill_without_early = self._simulate_early_excess_overfill(now, hourly_forecasts, virtual_batt_pct, batt_cap_wh)

                    # Simple ON/OFF logic based on simulation. Minimum run time prevents rapid toggling.
                    turn_on_early = will_overfill_without_early

            await set_switches(early_entities, turn_on_early)


        # Update recovery mode
        if batt_level_pct <= batt_min_pct and not is_absorption:
            self._battery_recovery_mode = True
        elif batt_level_pct >= (batt_min_pct + 2.0) or is_absorption:
            self._battery_recovery_mode = False

        if virtual_batt_pct < max_batt_pct - 5.0:
            self._battery_full_hysteresis = False

        # Evaluate grid charging
        is_grid_charging = False
        if current_price is not None:
            is_grid_charging = self._should_grid_charge(now, current_price, virtual_batt_pct, batt_cap_wh, hourly_forecasts)

        if is_grid_charging:
            self.current_operating_mode = "Netzladen aktiv: DTU aus"
            turn_on_inverter = False
        elif is_negative_price:
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

        elif virtual_batt_pct >= max_batt_pct - 1.0:
            self._battery_full_hysteresis = True
            self.current_operating_mode = "Batterie voll (DTU An)"
            turn_on_inverter = True

        elif getattr(self, "_battery_full_hysteresis", False) and virtual_batt_pct >= max_batt_pct - 5.0:
            self.current_operating_mode = "Batterie voll (Hysterese, DTU An)"
            turn_on_inverter = True

        elif current_price is not None and current_price <= price_threshold:
            self.current_operating_mode = f"Strom günstig (<{round(price_threshold,3)}€): DTU aus (Akku wird gespart)"
            turn_on_inverter = False

        else:
            self.current_operating_mode = "Preis hoch: DTU an (Nulleinspeisung aktiv)"
            turn_on_inverter = True

        # Toggle grid charger switch
        grid_charger_switch = self.config.get("grid_charger_switch")
        if grid_charger_switch:
            charger_state = self.hass.states.get(grid_charger_switch)
            if charger_state:
                if is_grid_charging and charger_state.state != "on":
                    await self.hass.services.async_call("switch", "turn_on", {"entity_id": grid_charger_switch}, blocking=False)
                elif not is_grid_charging and charger_state.state != "off":
                    await self.hass.services.async_call("switch", "turn_off", {"entity_id": grid_charger_switch}, blocking=False)

        dpl_entity = self.config.get(CONF_OPENDTU_DPL_MODE_SELECT)

        inverter_is_on = True # Default to True to force off if unknown

        if dpl_entity:
            # DPL configured: Use DPL state to check if inverter is "on" (0/0.0) or "off" (1/1.0)
            dpl_state = self.hass.states.get(dpl_entity)
            if dpl_state and dpl_state.state not in ("unknown", "unavailable"):
                state_str = str(dpl_state.state).strip()
                if state_str.startswith("0"):
                    inverter_is_on = True
                elif state_str.startswith("1"):
                    inverter_is_on = False
                else:
                    try:
                        dpl_val = float(state_str)
                        # DPL mode 0 means DPL is active (inverter is ON and follows DPL limits)
                        # DPL mode 1 means OFF
                        inverter_is_on = (dpl_val == 0.0)
                    except ValueError:
                        pass
        else:
            # Fallback to producing sensor
            producing_sensor = self.config.get(CONF_OPENDTU_PRODUCING_SENSOR)
            if producing_sensor:
                producing_state = self.hass.states.get(producing_sensor)
                if producing_state:
                    inverter_is_on = producing_state.state == "on"
                    if producing_state.state not in ("on", "off"):
                        inverter_is_on = str(producing_state.state).lower() in ("on", "true", "1", "producing")

        try:
            turn_on_btn = self.config.get(CONF_OPENDTU_TURN_ON_BUTTON)
            turn_off_btn = self.config.get(CONF_OPENDTU_TURN_OFF_BUTTON)

            # Fire the button if the requested state differs from what we *think* the current state is.
            # We also fire if our internal requested state changed since last time, just to be sure.
            # For negative prices, we force the OFF button every time to ensure DPL is definitely set.
            requested_state_str = "on" if turn_on_inverter else "off"

            async def _set_dpl_mode(mode_val: float):
                if not dpl_entity:
                    return
                entity_state = self.hass.states.get(dpl_entity)
                if not entity_state:
                    return
                domain = dpl_entity.split(".")[0]
                if domain in ("number", "input_number"):
                    await self.hass.services.async_call(domain, "set_value", {"entity_id": dpl_entity, "value": mode_val}, blocking=False)
                elif domain in ("select", "input_select"):
                    target_prefix = str(int(mode_val))
                    options = entity_state.attributes.get("options", [])

                    target_option = None
                    for opt in options:
                        if str(opt).strip().startswith(target_prefix):
                            target_option = str(opt)
                            break

                    if target_option is None:
                        target_option = target_prefix

                    await self.hass.services.async_call(domain, "select_option", {"entity_id": dpl_entity, "option": target_option}, blocking=False)

            if turn_on_inverter and (not inverter_is_on or self._current_inverter_state != "on"):
                if turn_on_btn and self.hass.states.get(turn_on_btn) is not None:
                    await self.hass.services.async_call("button", "press", {"entity_id": turn_on_btn}, blocking=False)
                await _set_dpl_mode(0.0)
                self._current_inverter_state = "on"
            elif not turn_on_inverter and (inverter_is_on or self._current_inverter_state != "off" or is_negative_price):
                if turn_off_btn and self.hass.states.get(turn_off_btn) is not None:
                    await self.hass.services.async_call("button", "press", {"entity_id": turn_off_btn}, blocking=False)
                await _set_dpl_mode(1.0)
                self._current_inverter_state = "off"
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

    def _should_grid_charge(self, now, current_price: float, current_batt_pct: float, batt_cap_wh: float, hourly_forecasts: list[dict]) -> bool:
        """Determines if we should charge from the grid right now."""
        if not getattr(self, "grid_charge_enabled", False):
            return False

        grid_charger_switch = self.config.get("grid_charger_switch")
        if not grid_charger_switch:
            return False

        grid_charge_eff = getattr(self, "grid_charge_efficiency", 80) / 100.0
        grid_charge_buffer_pct = getattr(self, "grid_charge_buffer", 25)

        # 1. Upper Limit Check
        # Calculate the absolute maximum allowed battery percentage for grid charging.
        # It must be strictly below the lowest "turn-off" threshold of the primary/secondary excess consumers
        # AND it must leave at least the configured 'grid_charge_buffer_pct' empty.
        primary_off = getattr(self, "primary_excess_off", 90.0)
        secondary_off = getattr(self, "secondary_excess_off", 95.0)
        lowest_excess_off = min(primary_off, secondary_off)

        # Max allowed by excess devices (with 2% safety margin)
        max_allowed_by_excess = lowest_excess_off - 2.0

        # Max allowed by safety buffer (e.g., 100 - 20 = 80%)
        # Note: We cap it against the general battery_max_limit_pct
        global_max_pct = float(self.config.get("battery_max_limit_pct", 100.0))
        max_allowed_by_buffer = global_max_pct - grid_charge_buffer_pct

        target_max_charge_pct = min(max_allowed_by_excess, max_allowed_by_buffer)

        if current_batt_pct >= target_max_charge_pct:
            return False

        future_blocks = self._build_future_blocks_pessimistic(now, hourly_forecasts)
        if not future_blocks:
            return False

        simulated_batt_wh = batt_cap_wh * (current_batt_pct / 100.0)
        batt_eff = float(self.config.get("battery_efficiency_pct", 90)) / 100.0

        max_inverter_power_w = float(self.config.get("max_inverter_power_w", 800))
        max_discharge_wh_per_15min = max_inverter_power_w / 4.0

        # This is the physical maximum the battery can hold, used for general bounds
        max_batt_wh = batt_cap_wh * (global_max_pct / 100.0)

        # This is the maximum we are allowed to reach during simulation to approve grid charging
        target_max_charge_wh = batt_cap_wh * (target_max_charge_pct / 100.0)

        # First pass: Check if charging is profitable by finding future high prices
        # We need future prices to be > current_price / (grid_charge_eff * batt_eff)
        # to cover round-trip losses (grid -> charger -> battery -> inverter -> house)
        required_price = current_price / (grid_charge_eff * batt_eff)
        profitable_wh = 0.0

        temp_batt_wh = simulated_batt_wh
        for fb in future_blocks:
            f_solar = fb["solar"] * batt_eff
            f_cons = max(0.0, fb["house_wh"] - fb["balcony"])
            f_price = fb["price"]

            # If we predict we would import from grid at a high price
            if f_price > required_price:
                # How much would we need from battery/grid?
                uncovered = max(0.0, f_cons - f_solar)
                if uncovered > 0:
                    profitable_wh += min(uncovered, max_discharge_wh_per_15min)

            # Update temp battery assuming NO grid charging
            if f_solar > f_cons:
                temp_batt_wh += (f_solar - f_cons)
            else:
                f_actual_discharge = min(f_cons - f_solar, max_discharge_wh_per_15min)
                temp_batt_wh -= f_actual_discharge
            temp_batt_wh = max(0.0, min(max_batt_wh, temp_batt_wh))

        # Need at least one 15min block worth of profitable discharge (e.g. 50 Wh)
        if profitable_wh < 50.0:
            return False

        # Second pass: Forward Simulation Check
        # Will the natural solar forecast push the battery beyond our strict target limit?
        temp_batt_wh_future = simulated_batt_wh

        # Assume we add one block of grid charge right now
        charger_power_w = float(self.config.get("grid_charger_power_w", 1000))
        charge_added_wh = (charger_power_w / 4.0) * grid_charge_eff
        temp_batt_wh_future += charge_added_wh

        # We immediately check if this single charge block puts us over the limit
        if temp_batt_wh_future >= target_max_charge_wh:
            return False

        for fb in future_blocks:
            f_solar = fb["solar"] * batt_eff
            f_cons = max(0.0, fb["house_wh"] - fb["balcony"])

            if f_solar > f_cons:
                temp_batt_wh_future += (f_solar - f_cons)
            else:
                f_actual_discharge = min(f_cons - f_solar, max_discharge_wh_per_15min)
                temp_batt_wh_future -= f_actual_discharge

            # If at any point the simulated future (including our 1 grid charge block now)
            # exceeds our strict buffer limit, we abort charging.
            if temp_batt_wh_future >= target_max_charge_wh:
                return False

        return True

    def _simulate_early_excess_overfill(self, now, hourly_forecasts: list[dict], current_batt_pct: float, batt_cap_wh: float) -> bool:
        """Simulates if the battery will reach the configured limit using pessimistic data, assuming the early excess device is OFF."""
        future_blocks = self._build_future_blocks_pessimistic(now, hourly_forecasts)

        simulated_batt_wh = batt_cap_wh * (current_batt_pct / 100.0)
        batt_eff = float(self.config.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0

        max_inverter_power_w = float(self.config.get(CONF_MAX_INVERTER_POWER_W, 800))
        max_discharge_wh_per_15min = max_inverter_power_w / 4.0

        max_batt_pct = float(self.config.get(CONF_BATTERY_MAX_LIMIT_PCT, self.config.get(CONF_EARLY_EXCESS_MAX_BATTERY_PCT, 100.0)))
        max_batt_wh = batt_cap_wh * (max_batt_pct / 100.0)

        for fb in future_blocks:
            pred_solar = fb["solar"] * batt_eff

            # Real dynamic consumption
            pred_cons = max(0.0, fb["house_wh"] - fb["balcony"])

            if pred_solar > pred_cons:
                simulated_batt_wh += (pred_solar - pred_cons)
            else:
                actual_discharge = min(pred_cons - pred_solar, max_discharge_wh_per_15min)
                simulated_batt_wh -= actual_discharge

            if simulated_batt_wh >= max_batt_wh:
                return True

        return False

    def _simulate_optimal_threshold(self, now, hourly_forecasts: list[dict], current_batt_pct: float, batt_cap_wh: float, batt_min_pct: float) -> float:
        """Simulates the future to find the optimal price threshold by minimizing total electricity cost."""
        # Use pessimistic blocks for the threshold calculation to ensure safe predictions
        future_blocks = self._build_future_blocks_pessimistic(now, hourly_forecasts)

        if not future_blocks:
            return -0.5

        max_inverter_power_w = float(self.config.get(CONF_MAX_INVERTER_POWER_W, 800))
        max_discharge_wh_per_15min = max_inverter_power_w / 4.0

        batt_eff = float(self.config.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0

        # Unique prices sorted
        unique_prices = sorted(list(set(b["price"] for b in future_blocks)))
        lowest_actual_price = unique_prices[0] if unique_prices else 0.0
        # Add a fallback threshold that discharges everything
        unique_prices.insert(0, -0.5)

        best_threshold = -0.5
        min_total_cost = float('inf')

        max_batt_pct = float(self.config.get(CONF_BATTERY_MAX_LIMIT_PCT, self.config.get(CONF_EARLY_EXCESS_MAX_BATTERY_PCT, 100.0)))
        max_batt_wh = batt_cap_wh * (max_batt_pct / 100.0)

        for candidate_threshold in unique_prices:
            simulated_batt_wh = batt_cap_wh * (current_batt_pct / 100.0)
            min_batt_wh = batt_cap_wh * (batt_min_pct / 100.0)

            total_cost = 0.0
            overfill_penalty = 0.0

            for i, fb in enumerate(future_blocks):
                price = fb["price"]
                pred_solar = fb["solar"] * batt_eff
                pred_cons = max(0.0, fb["house_wh"] - fb["balcony"])

                # Will overfill check: forward simulation to detect if we hit max limit before emptying
                will_overfill = False
                temp_batt_wh = simulated_batt_wh
                for fb_future in future_blocks[i:]:
                    f_solar = fb_future["solar"] * batt_eff
                    f_cons = max(0.0, fb_future["house_wh"] - fb_future["balcony"])
                    f_actual_discharge = min(f_cons, max_discharge_wh_per_15min, max(0.0, temp_batt_wh - min_batt_wh))
                    temp_batt_wh += f_solar - f_actual_discharge

                    if temp_batt_wh >= max_batt_wh:
                        will_overfill = True
                        break
                    if temp_batt_wh <= min_batt_wh:
                        break

                simulated_batt_wh += pred_solar

                grid_import_wh = pred_cons

                # If the battery is destined to overfill today, we MUST discharge to make room,
                # ignoring the candidate threshold.
                if will_overfill and simulated_batt_wh > min_batt_wh:
                    available_discharge = simulated_batt_wh - min_batt_wh
                    actual_discharge = min(pred_cons, max_discharge_wh_per_15min, available_discharge)
                    simulated_batt_wh -= actual_discharge
                    grid_import_wh -= actual_discharge
                # If battery has enough energy and price is >= candidate threshold, we discharge.
                # This ensures we strictly prioritize the most expensive blocks top-down.
                # Do NOT discharge if price is negative.
                elif price >= candidate_threshold and simulated_batt_wh > min_batt_wh and price >= 0.0:
                    available_discharge = simulated_batt_wh - min_batt_wh
                    actual_discharge = min(pred_cons, max_discharge_wh_per_15min, available_discharge)
                    simulated_batt_wh -= actual_discharge
                    grid_import_wh -= actual_discharge

                # Add to total cost (Wh -> kWh * price per kWh)
                total_cost += (grid_import_wh / 1000.0) * price

                # Strictly penalize hitting max battery limit to prevent wasting solar energy
                if simulated_batt_wh >= max_batt_wh:
                    # Heavy penalty for every time block we are full, proportional to wasted potential
                    overfill_penalty += 1000.0
                    simulated_batt_wh = max_batt_wh

            total_cost += overfill_penalty

            # If we end up with unused battery at the end of the simulation horizon,
            # we value it at the lowest available price to force the optimizer to use the stored energy
            # during the available price horizon at the most efficient times, rather than
            # keeping it completely full forever if prices are low.
            if simulated_batt_wh > min_batt_wh:
                safe_residual_price = max(0.0, min(candidate_threshold, lowest_actual_price))
                residual_value = ((simulated_batt_wh - min_batt_wh) / 1000.0) * safe_residual_price
                total_cost -= residual_value

            # Using <= ensures that if two thresholds yield the exact same cost, we prefer the higher threshold
            # to be more conservative about discharging.
            if total_cost <= min_total_cost:
                min_total_cost = total_cost
                best_threshold = candidate_threshold

        return best_threshold

    def _build_future_blocks_pessimistic(self, now, hourly_forecasts: list[dict]) -> list[dict]:
        """Builds a list of 15-min blocks applying the implicit cloud cover correction."""
        blocks = self._build_future_blocks(now, hourly_forecasts)

        # Determine end of today for the daily boundary
        end_of_today = now.replace(hour=23, minute=59, second=59, microsecond=999999)

        cons_factor = max(1.0, getattr(self, "pessimistic_consumption_factor", 1.0))
        implicit_cloud = getattr(self, "implicit_cloud_cover", None)
        solar_factor = getattr(self, "intraday_solar_factor", 1.0)

        peak_w = float(self.config.get(CONF_SOLAR_PEAK_W, 6000.0))
        peak_wh_15min = peak_w / 4.0

        for b in blocks:
            if b["dt"] <= end_of_today:
                if implicit_cloud is not None:
                    q = self._get_quarter_index(b["dt"])
                    b["solar"] = self.learning_engine.predict_solar_for_quarter(q, implicit_cloud)
                    if self.config.get(CONF_BALCONY_POWER_SENSOR):
                        b["balcony"] = self.learning_engine.predict_balcony_for_quarter(q, implicit_cloud)

                    # Store original cloud cover for reference, but use implicit for calculation
                    b["original_cc"] = b["cc"]
                    b["cc"] = implicit_cloud

                # Apply intraday scale factor to adapt to conditions faster than the EMA learns
                b["solar"] = b["solar"] * solar_factor
                # Hard limit to physical peak just in case factor + clear prediction explodes
                if b["solar"] > peak_wh_15min:
                    b["solar"] = peak_wh_15min

                if self.config.get(CONF_BALCONY_POWER_SENSOR) and "balcony" in b:
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

        max_batt_pct = float(self.config.get(CONF_BATTERY_MAX_LIMIT_PCT, self.config.get(CONF_EARLY_EXCESS_MAX_BATTERY_PCT, 100.0)))
        max_batt_wh = batt_cap_wh * (max_batt_pct / 100.0)

        for i, fb in enumerate(future_blocks):
            pred_solar = fb["solar"] * batt_eff
            pred_balcony = fb["balcony"]
            raw_house_wh = fb["house_wh"]
            cc = fb["cc"]
            price = fb["price"]

            # The actual consumption the battery sees is house minus balcony
            pred_cons = max(0.0, raw_house_wh - pred_balcony)

            available_discharge = max(0.0, simulated_batt_wh - min_batt_wh)
            actual_discharge = min(pred_cons, max_discharge_wh_per_15min, available_discharge)

            # Will overfill check: forward simulation to detect if we hit 100% before emptying
            will_overfill = False
            temp_batt_wh = simulated_batt_wh
            for fb_future in future_blocks[i:]:
                f_solar = fb_future["solar"] * batt_eff
                f_cons = max(0.0, fb_future["house_wh"] - fb_future["balcony"])
                f_actual_discharge = min(f_cons, max_discharge_wh_per_15min, max(0.0, temp_batt_wh - min_batt_wh))
                temp_batt_wh += f_solar - f_actual_discharge

                if temp_batt_wh >= max_batt_wh:
                    will_overfill = True
                    break
                if temp_batt_wh <= min_batt_wh:
                    # Battery empties before overfilling, so no overfill risk for this energy
                    break

            # New precise discharging logic mirroring the threshold calculation
            if price < 0.0:
                action = f"Negativer Preis ({round(price,3)}€): DTU aus"
                simulated_batt_wh += pred_solar
            elif simulated_batt_wh >= max_batt_wh:
                action = f"Batterie {int(max_batt_pct)}% voll (DTU An)"
                # To prevent forecasting drops, we calculate as if solar goes into battery, then cap it
                simulated_batt_wh += pred_solar
            elif will_overfill:
                # If we know the battery will hit the max limit today, never save battery via grid import
                # Instead, act as Nulleinspeisung (DTU An) to make room for the solar.
                action = "Überschussvermeidung (DTU An)"
                simulated_batt_wh += pred_solar - actual_discharge
            elif price < price_threshold:
                action = f"Netzbezug (Akku sparen für >={round(price_threshold,3)}€)"
                simulated_batt_wh += pred_solar
            elif simulated_batt_wh <= min_batt_wh:
                action = "Batterie am Minimum (DTU Aus)"
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

        # Ensure we always simulate at least 24 hours into the future.
        # If Tibber prices end tonight at 23:45, this forces the simulation to project into tomorrow.
        min_max_dt = now + timedelta(hours=24)
        if max_dt < min_max_dt:
            max_dt = min_max_dt

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

            # Fallback: if no price found (e.g. tomorrow before 13:00), use the price from exactly 24h prior
            if price is None:
                fallback_dt = eval_dt - timedelta(hours=24)
                for p in self.tibber_prices:
                    p_dt = p.get("datetime")
                    if p_dt and p_dt <= fallback_dt < p_dt + timedelta(minutes=15):
                        price = float(p.get("total", 0.0))
                        break

            # Secondary fallback just in case
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

    async def _tick_appliances(self, now):
        for sensor_id, sm in self.appliance_state_machines.items():
            state = self.hass.states.get(sensor_id)
            if state and state.state not in ('unknown', 'unavailable'):
                try:
                    power_w = float(state.state)
                    await sm.async_process_power_reading(power_w, now)
                except ValueError:
                    pass

    def get_scheduled_appliance_load_for_minute(self, target_time) -> float:
        total_w = 0.0
        for sm in self.appliance_state_machines.values():
            if sm.planned_run:
                start = sm.planned_run.scheduled_start
                prog = self.appliance_manager.get_program_by_id(sm.sensor_id, sm.planned_run.program_id)
                if prog and prog.power_profile:
                    duration = len(prog.power_profile)
                    end = start + timedelta(minutes=duration)
                    if start <= target_time < end:
                        min_idx = int((target_time - start).total_seconds() / 60)
                        if min_idx < len(prog.power_profile):
                            total_w += prog.power_profile[min_idx]
        return total_w
