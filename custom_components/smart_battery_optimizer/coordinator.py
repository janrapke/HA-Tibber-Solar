"""Coordinator to handle the core control logic for Smart Battery Optimizer."""
import logging
from datetime import timedelta, datetime
import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .tibber import fetch_tibber_prices

from .const import (
    CONF_MIN_SWITCH_INTERVAL_MINUTES,
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
    CONF_PRESUNNY_SOLAR_MARGIN_PCT,
)
from .learning import LearningEngine
from .appliance_manager import SmartApplianceManager, ApplianceStateMachine, ApplianceState, ProposalCalculator

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
        self.appliance_entities = {'button': [], 'select': [], 'sensor': [], 'text': [], 'number': [], 'switch': []}

        # Internal state
        self.is_enabled = True
        self.manual_zero_export = False
        self._excess_devices_last_turned_on = {}
        self.tibber_prices = []
        self.last_tibber_fetch = None
        self._last_inverter_command_time = None
        # Inverter debounce uses the same min_switch_interval_minutes setting

        # Open-Meteo GHI cache
        self._open_meteo_ghi_data = []  # list of {datetime, ghi}
        self._last_open_meteo_fetch = None

        # Bidirectional switch lock — prevents any device (consumer or inverter) from switching
        # more often than the configured min_switch_interval_minutes
        self._last_switch_time: dict = {}  # entity_id -> datetime of last state change

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

        # Overfill emergency tracking
        self._overfill_emergency_active = False
        self._overfill_emergency_start = None
        self._overfill_emergency_batt_wh_start = 0.0

        # Block-by-block dispatch plan: [{dt, discharge_wh, price}]
        self._dispatch_plan: list[dict] = []
        self.overfill_absorption_last_wh = 0.0

        # Laderaum-Vorbereitung (proactive night discharge before sunny day)
        self.presunny_discharge_enabled = False
        self.target_morning_soc_pct: float | None = None
        self.tomorrow_net_solar_wh: float = 0.0
        self._last_morning_soc_calc: datetime | None = None

        # Climate device state — keyed by device_id, populated by climate_entities on init
        # Each entry: {enabled, device_type, setpoint, manual_w, power_sensor}
        self.climate_device_states: dict = {}

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
        self.appliance_entities = {'button': [], 'select': [], 'sensor': [], 'text': [], 'number': [], 'switch': []}

        # Init state machines for configured devices
        smart_devices_str = self.config.get(CONF_SMART_DEVICES, "")
        if smart_devices_str:
            devices = [d.strip() for d in smart_devices_str.split(",") if d.strip()]
            for dev in devices:
                self.appliance_state_machines[dev] = ApplianceStateMachine(dev, self.appliance_manager)

                from .appliance_entities import (
                    ApplianceProposalSelect, ApplianceConfirmButton, ApplianceCancelButton,
                    ApplianceStatusSensor, ApplianceTimerSensor, ApplianceProgramNameText,
                    ApplianceScheduleSelect, ApplianceEarliestStartNumber, ApplianceLatestEndNumber,
                )

                prop_sel = ApplianceProposalSelect(self, self.config_entry.entry_id, dev)

                self.appliance_entities['select'].extend([
                    prop_sel,
                    ApplianceScheduleSelect(self, self.config_entry.entry_id, dev),
                ])
                self.appliance_entities['number'].extend([
                    ApplianceEarliestStartNumber(self, self.config_entry.entry_id, dev),
                    ApplianceLatestEndNumber(self, self.config_entry.entry_id, dev),
                ])
                self.appliance_entities['button'].extend([
                    ApplianceConfirmButton(self, self.config_entry.entry_id, dev, prop_sel),
                    ApplianceCancelButton(self, self.config_entry.entry_id, dev),
                ])
                self.appliance_entities['sensor'].extend([
                    ApplianceStatusSensor(self, self.config_entry.entry_id, dev),
                    ApplianceTimerSensor(self, self.config_entry.entry_id, dev),
                ])
                self.appliance_entities['text'].append(
                    ApplianceProgramNameText(self, self.config_entry.entry_id, dev)
                )

        # Auto-create state machines for excluded power sensors (e.g. EV charger) so that
        # when they are active, their remaining load is included in the forward plan and
        # the early-excess logic reacts correctly — no separate smart_devices entry needed.
        for entity_id in self.config.get(CONF_EXCLUDED_POWER_SENSORS, []):
            if entity_id not in self.appliance_state_machines:
                self.appliance_state_machines[entity_id] = ApplianceStateMachine(entity_id, self.appliance_manager)

        # Create per-device entities for configured climate devices
        from .climate_entities import (
            ClimateDeviceEnabledSwitch, ClimateDeviceTypeSelect,
            ClimateManualWNumber, ClimateSolltemperaturNumber, ClimatePowerSensorText,
        )
        from .const import CONF_CLIMATE_DEVICES
        climate_devices = self.config.get(CONF_CLIMATE_DEVICES, [])
        for device_config in climate_devices:
            eid = self.config_entry.entry_id
            self.appliance_entities['switch'].append(ClimateDeviceEnabledSwitch(self, eid, device_config))
            self.appliance_entities['select'].append(ClimateDeviceTypeSelect(self, eid, device_config))
            self.appliance_entities['number'].append(ClimateManualWNumber(self, eid, device_config))
            self.appliance_entities['number'].append(ClimateSolltemperaturNumber(self, eid, device_config))
            self.appliance_entities['text'].append(ClimatePowerSensorText(self, eid, device_config))

        await self._fetch_tibber_prices()
        await self._fetch_open_meteo_ghi()

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

    async def _fetch_open_meteo_ghi(self):
        """Fetch hourly GHI and outdoor temperature from Open-Meteo (free, no API key)."""
        now = dt_util.now()
        if self._last_open_meteo_fetch and (now - self._last_open_meteo_fetch).total_seconds() < 3600:
            return

        lat = self.hass.config.latitude
        lon = self.hass.config.longitude
        if lat is None or lon is None:
            return

        try:
            session = async_get_clientsession(self.hass)
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&hourly=shortwave_radiation,temperature_2m"
                f"&forecast_days=3&timezone=auto"
            )
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    hourly = data.get("hourly", {})
                    times = hourly.get("time", [])
                    ghis = hourly.get("shortwave_radiation", [])
                    temps = hourly.get("temperature_2m", [])
                    ghi_data = []
                    for t_str, g, temp in zip(times, ghis, temps):
                        dt = dt_util.parse_datetime(t_str)
                        if dt:
                            ghi_data.append({
                                "datetime": dt,
                                "ghi": float(g or 0.0),
                                "temp": float(temp) if temp is not None else None,
                            })
                    self._open_meteo_ghi_data = ghi_data
                    self._last_open_meteo_fetch = now
                    _LOGGER.debug("Fetched %d Open-Meteo entries (GHI + temp)", len(ghi_data))
        except Exception as e:
            _LOGGER.warning("Could not fetch Open-Meteo data: %s", e)

    def _get_ghi_for_dt(self, target_dt) -> float:
        """Return GHI (W/m²) for the hour containing target_dt, or 0 if unavailable."""
        local_target = dt_util.as_local(target_dt)
        for item in self._open_meteo_ghi_data:
            item_local = dt_util.as_local(item["datetime"])
            if item_local.date() == local_target.date() and item_local.hour == local_target.hour:
                return item["ghi"]
        return 0.0

    def _get_outdoor_temp_for_dt(self, target_dt) -> float | None:
        """Return forecast outdoor temperature (°C) for the hour containing target_dt."""
        local_target = dt_util.as_local(target_dt)
        for item in self._open_meteo_ghi_data:
            item_local = dt_util.as_local(item["datetime"])
            if item_local.date() == local_target.date() and item_local.hour == local_target.hour:
                return item.get("temp")
        return None

    def _get_current_outdoor_temp(self) -> float:
        """Return current outdoor temperature from the most recent Open-Meteo forecast hour.

        Falls back to the HA weather entity if Open-Meteo data is unavailable.
        Returns 15.0 as a neutral fallback (no heating/cooling demand expected).
        """
        now = dt_util.now()
        temp = self._get_outdoor_temp_for_dt(now)
        if temp is not None:
            return temp

        # Fallback: HA weather entity
        weather_entity = self.config.get(CONF_WEATHER_ENTITY)
        if weather_entity:
            state = self.hass.states.get(weather_entity)
            if state and state.attributes.get("temperature") is not None:
                try:
                    return float(state.attributes["temperature"])
                except (ValueError, TypeError):
                    pass
        return 15.0

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
        await self._fetch_open_meteo_ghi()

        # Stündliche Neuberechnung des Morgen-SOC-Ziels (Laderaum-Vorbereitung)
        if self._last_morning_soc_calc is None or (now - self._last_morning_soc_calc).total_seconds() >= 3600:
            self.target_morning_soc_pct = self._calculate_target_morning_soc(now)
            self._last_morning_soc_calc = now

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

        # Record climate device power (smart-plug sensor or 0 if not configured)
        for device_id, state in self.climate_device_states.items():
            if not state.get("enabled", False):
                continue
            sensor_entity_id = state.get("power_sensor", "")
            power_w = 0.0
            if sensor_entity_id:
                sensor_state = self.hass.states.get(sensor_entity_id)
                if sensor_state and sensor_state.state not in ("unavailable", "unknown", None):
                    try:
                        power_w = float(sensor_state.state)
                    except (ValueError, TypeError):
                        pass
            await self.learning_engine.record_climate_power(device_id, power_w)

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
            current_ghi = self._get_ghi_for_dt(now)
            actual_cons_wh, actual_solar_wh = await self.learning_engine.finalize_quarter(
                self.learning_engine._last_quarter_processed, cloud_cover, c_price,
                ghi=current_ghi, dow=now.weekday()
            )

            # Finalize climate quarters for active devices
            current_outdoor_temp = self._get_current_outdoor_temp()
            for device_id, state in self.climate_device_states.items():
                if not state.get("enabled", False):
                    continue
                device_type = state.get("device_type", "heating")
                setpoint = state.get("setpoint", 20.0)
                await self.learning_engine.finalize_climate_quarter(
                    self.learning_engine._last_quarter_processed,
                    device_id, device_type, current_outdoor_temp, setpoint,
                    dow=now.weekday(),
                )

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

            # Intraday Solar Factor — corrects for day-specific deviations (dust, haze, etc.)
            # Pass GHI so the GHI-ratio model is preferred when available
            last_q_ghi = self._get_ghi_for_dt(now)
            target_cc = self.implicit_cloud_cover if getattr(self, 'implicit_cloud_cover', None) is not None else cloud_cover
            target_pred = self.learning_engine.predict_solar_for_quarter(self.learning_engine._last_quarter_processed, target_cc, ghi=last_q_ghi)
            if self.config.get(CONF_BALCONY_POWER_SENSOR):
                target_pred += self.learning_engine.predict_balcony_for_quarter(self.learning_engine._last_quarter_processed, target_cc, ghi=last_q_ghi)

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

        # Temporary sum for sensors
        self.predicted_remaining_solar = 0.0
        self.predicted_remaining_consumption = 0.0

        # Forecast and Planning
        current_price = self._get_current_price()

        batt_level_pct = self._get_float_state(self.config[CONF_BATTERY_LEVEL_SENSOR])
        batt_cap_wh = self.config.get(CONF_BATTERY_CAPACITY_WH, 5000)
        batt_min_pct = self.config.get(CONF_BATTERY_MIN_LIMIT_PCT, 10)
        max_batt_pct = float(self.config.get(CONF_BATTERY_MAX_LIMIT_PCT, self.config.get(CONF_EARLY_EXCESS_MAX_BATTERY_PCT, 100.0)))

        is_absorption = charge_state and charge_state.lower() in ("absorption", "float", "ausgleichsladung", "equalization")
        virtual_batt_pct = 100.0 if is_absorption else batt_level_pct

        # Simulations run every minute for accurate trend tracking.
        # Device switching is controlled separately by min_switch_interval_minutes.
        hourly_forecasts = await self._get_hourly_forecasts()

        self._dispatch_plan, price_threshold = self._calculate_optimal_dispatch(now, hourly_forecasts, batt_level_pct, batt_cap_wh, batt_min_pct)
        charge_price_threshold = self._simulate_optimal_charge_threshold(now, hourly_forecasts, batt_level_pct, batt_cap_wh)

        self._build_forecast_plan(now, hourly_forecasts, batt_level_pct, batt_cap_wh, price_threshold, charge_price_threshold)

        # Re-calc 24h sum
        for block in self.hourly_plan:
            self.predicted_remaining_solar += block["solar_wh"]
            self.predicted_remaining_consumption += block["consumption_wh"]

        # Forward simulation — will battery hit max? (use real solar forecast, no extreme_price_factor)
        battery_will_overfill = False
        _real_blocks_overfill = self._build_future_blocks_pessimistic(now, hourly_forecasts, apply_extreme_price_factor=False)
        _batt_eff_check = float(self.config.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0
        _max_dis_wh_check = float(self.config.get(CONF_MAX_INVERTER_POWER_W, 800)) / 4.0
        _min_batt_wh_check = batt_cap_wh * (batt_min_pct / 100.0)
        _max_batt_wh_check = batt_cap_wh * (max_batt_pct / 100.0)
        _temp_batt_wh_check = batt_cap_wh * (batt_level_pct / 100.0)
        for _rb in _real_blocks_overfill:
            _sol = _rb["solar"] * _batt_eff_check
            _con = max(0.0, _rb["house_wh"] - _rb.get("balcony", 0.0))
            if _sol > _con:
                _temp_batt_wh_check += (_sol - _con)
            else:
                _temp_batt_wh_check -= min(_con - _sol, _max_dis_wh_check)
            _temp_batt_wh_check = max(_min_batt_wh_check, _temp_batt_wh_check)
            if _temp_batt_wh_check >= _max_batt_wh_check:
                battery_will_overfill = True
                break
            if _temp_batt_wh_check <= _min_batt_wh_check:
                break

        # Evaluate grid charging
        is_grid_charging = False
        if current_price is not None and charge_price_threshold is not None:
            if current_price <= charge_price_threshold:
                is_grid_charging = self._check_grid_charge_safety(now, current_price, virtual_batt_pct, batt_cap_wh, hourly_forecasts)

        turn_on_inverter = True

        async def set_switches(entities: list, turn_on: bool):
            min_interval = int(self.config.get(CONF_MIN_SWITCH_INTERVAL_MINUTES, 15))
            current_time = dt_util.now()

            for switch_entity in entities:
                state = self.hass.states.get(switch_entity)
                if state is None:
                    continue
                is_on = state.state == "on"

                if is_on == turn_on:
                    continue  # already in desired state

                # Bidirectional lock: don't switch if last switch was too recent
                last_switch = self._last_switch_time.get(switch_entity)
                if last_switch:
                    elapsed = (current_time - last_switch).total_seconds() / 60.0
                    if elapsed < min_interval:
                        _LOGGER.debug(
                            "Switch %s locked for %.1f more min (interval=%d min)",
                            switch_entity, min_interval - elapsed, min_interval
                        )
                        continue

                if turn_on:
                    await self.hass.services.async_call("switch", "turn_on", {"entity_id": switch_entity}, blocking=False)
                else:
                    await self.hass.services.async_call("switch", "turn_off", {"entity_id": switch_entity}, blocking=False)
                self._last_switch_time[switch_entity] = current_time
                self._excess_devices_last_turned_on[switch_entity] = current_time if turn_on else None

        # If a switch was manually turned off, clear the tracking so the min-run-time resets
        _min_run_time = int(self.config.get(CONF_EXCESS_MIN_RUN_TIME_MINUTES, 10))
        for entity_list in [self.config.get(CONF_PRIMARY_EXCESS_CONSUMERS, []), self.config.get(CONF_SECONDARY_EXCESS_CONSUMERS, []), self.config.get(CONF_EARLY_EXCESS_CONSUMERS, [])]:
            if not isinstance(entity_list, list):
                continue
            for switch_entity in entity_list:
                state = self.hass.states.get(switch_entity)
                if state and state.state == "off" and switch_entity in self._excess_devices_last_turned_on:
                    self._excess_devices_last_turned_on.pop(switch_entity, None)

        # is_absorption and virtual_batt_pct already computed above before quarter-gate

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

        # Primary Hysteresis Logic — pure battery % thresholds, no solar/cloud dependency
        primary_entities = self.config.get(CONF_PRIMARY_EXCESS_CONSUMERS, [])
        primary_is_external = _any_external(primary_entities)

        if self.primary_excess_auto:
            if is_negative_price:
                turn_on_primary = True
            else:
                on_thr = getattr(self, "primary_excess_on", 95.0)
                off_thr = getattr(self, "primary_excess_off", 90.0)
                currently_on = any(self.hass.states.get(e) and self.hass.states.get(e).state == "on" for e in primary_entities)
                if virtual_batt_pct >= on_thr:
                    turn_on_primary = True
                elif virtual_batt_pct <= off_thr:
                    turn_on_primary = False
                else:
                    turn_on_primary = currently_on  # hold current state (hysteresis)

            await set_switches(primary_entities, turn_on_primary)

        # Secondary Hysteresis Logic — pure battery % thresholds
        secondary_entities = self.config.get(CONF_SECONDARY_EXCESS_CONSUMERS, [])
        secondary_is_external = _any_external(secondary_entities)

        if self.secondary_excess_auto:
            if is_negative_price:
                turn_on_secondary = True
            else:
                on_thr = getattr(self, "secondary_excess_on", 98.0)
                off_thr = getattr(self, "secondary_excess_off", 95.0)
                currently_on = any(self.hass.states.get(e) and self.hass.states.get(e).state == "on" for e in secondary_entities)
                if virtual_batt_pct >= on_thr:
                    turn_on_secondary = True
                elif virtual_batt_pct <= off_thr:
                    turn_on_secondary = False
                else:
                    turn_on_secondary = currently_on  # hold current state (hysteresis)

            await set_switches(secondary_entities, turn_on_secondary)

        # Early Excess Logic — prediction-based, no cloud/solar override to prevent toggling
        # Turns ON when simulation predicts overfill; stays ON until simulation says it's safe to turn OFF.
        early_entities = self.config.get(CONF_EARLY_EXCESS_CONSUMERS, [])

        if getattr(self, "early_excess_auto", True):
            if is_negative_price:
                turn_on_early = True
            else:
                early_excess_min_batt = float(self.config.get(CONF_EARLY_EXCESS_MIN_BATTERY_PCT, 30.0))
                if virtual_batt_pct < early_excess_min_batt:
                    turn_on_early = False
                else:
                    turn_on_early = self._simulate_early_excess_overfill(
                        now, hourly_forecasts, virtual_batt_pct, batt_cap_wh
                    )

            await set_switches(early_entities, turn_on_early)

        # Überfüll-Notfall: wenn Simulation Überfüllung zeigt, ALLE Verbraucher sofort einschalten
        # unabhängig von individuellen Hysterese-Schwellen — verhindert Solarabschneidung
        if battery_will_overfill and not is_negative_price:
            emergency_entities = (
                (primary_entities if self.primary_excess_auto else []) +
                (secondary_entities if self.secondary_excess_auto else []) +
                (early_entities if getattr(self, "early_excess_auto", True) else [])
            )
            if emergency_entities:
                await set_switches(emergency_entities, True)

        # Absorption-Tracking: misst ob Verbraucher die Überfüllung tatsächlich verhindern
        _current_batt_wh = batt_cap_wh * (batt_level_pct / 100.0)
        if battery_will_overfill:
            if not self._overfill_emergency_active:
                self._overfill_emergency_start = now
                self._overfill_emergency_batt_wh_start = _current_batt_wh
            self._overfill_emergency_active = True
        else:
            if self._overfill_emergency_active and self._overfill_emergency_start is not None:
                elapsed_min = (now - self._overfill_emergency_start).total_seconds() / 60.0
                if elapsed_min >= 1.0:
                    # Positive = Batterie gefallen = Verbraucher haben absorbiert (gut)
                    # Negativ = Batterie trotzdem gestiegen = Verbraucher reichten nicht aus
                    self.overfill_absorption_last_wh = self._overfill_emergency_batt_wh_start - _current_batt_wh
            self._overfill_emergency_active = False

        # Update recovery mode
        if batt_level_pct <= batt_min_pct and not is_absorption:
            self._battery_recovery_mode = True
        elif batt_level_pct >= (batt_min_pct + 2.0) or is_absorption:
            self._battery_recovery_mode = False

        if virtual_batt_pct < max_batt_pct - 5.0:
            self._battery_full_hysteresis = False

        # Dispatch lookup: check if current 15-min block should discharge
        _now_ts = now
        _current_dispatch_wh = 0.0
        _next_dispatch_info = ""
        for _dp in self._dispatch_plan:
            _dp_dt = _dp["dt"]
            if _dp_dt <= _now_ts < _dp_dt + timedelta(minutes=15):
                _current_dispatch_wh = _dp["discharge_wh"]
            elif _dp_dt > _now_ts and _dp["discharge_wh"] > 0.5 and not _next_dispatch_info:
                _next_dispatch_info = f" (nächste Entladung ~{dt_util.as_local(_dp_dt).strftime('%H:%M')}, {round(_dp['price'], 3)}€)"
        should_discharge_now = _current_dispatch_wh > 0.5

        if is_grid_charging:
            self.current_operating_mode = f"Netzladen aktiv (<={round(charge_price_threshold, 3)}€): DTU aus"
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

        elif (
            getattr(self, "presunny_discharge_enabled", False)
            and self.target_morning_soc_pct is not None
            and batt_level_pct > self.target_morning_soc_pct + 5.0
            and (dt_util.as_local(now).hour >= 20 or dt_util.as_local(now).hour < 6)
        ):
            self.current_operating_mode = (
                f"Laderaum vorbereiten: Ziel {self.target_morning_soc_pct:.0f}% "
                f"(aktuell {batt_level_pct:.0f}%) — DTU An"
            )
            turn_on_inverter = True

        elif not should_discharge_now:
            self.current_operating_mode = f"Akku sparen{_next_dispatch_info}: DTU aus"
            turn_on_inverter = False

        else:
            self.current_operating_mode = f"Dispatch aktiv ({round(current_price, 3) if current_price else '?'}€): DTU an"
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

            min_inverter_interval = int(self.config.get(CONF_MIN_SWITCH_INTERVAL_MINUTES, 15))
            debounce_active = False
            if self._last_inverter_command_time is not None:
                elapsed_mins = (now - self._last_inverter_command_time).total_seconds() / 60.0
                if elapsed_mins < min_inverter_interval:
                    debounce_active = True
                    _LOGGER.debug(
                        "Inverter locked for %.1f more min (interval=%d min)",
                        min_inverter_interval - elapsed_mins, min_inverter_interval
                    )

            if not debounce_active:
                if turn_on_inverter and (not inverter_is_on or self._current_inverter_state != "on"):
                    if turn_on_btn and self.hass.states.get(turn_on_btn) is not None:
                        await self.hass.services.async_call("button", "press", {"entity_id": turn_on_btn}, blocking=False)
                    await _set_dpl_mode(0.0)
                    self._current_inverter_state = "on"
                    self._last_inverter_command_time = now
                elif not turn_on_inverter and (inverter_is_on or self._current_inverter_state != "off"):
                    if turn_off_btn and self.hass.states.get(turn_off_btn) is not None:
                        await self.hass.services.async_call("button", "press", {"entity_id": turn_off_btn}, blocking=False)
                    await _set_dpl_mode(1.0)
                    self._current_inverter_state = "off"
                    self._last_inverter_command_time = now
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

    def _simulate_optimal_charge_threshold(self, now, hourly_forecasts: list[dict], current_batt_pct: float, batt_cap_wh: float) -> float | None:
        """Simulates the future to find the most expensive shortages and matches them with the cheapest charging blocks."""
        if not getattr(self, "grid_charge_enabled", False):
            return None

        grid_charger_switch = self.config.get("grid_charger_switch")
        if not grid_charger_switch:
            return None

        future_blocks = self._build_future_blocks_pessimistic(now, hourly_forecasts)
        if not future_blocks:
            return None

        grid_charge_eff = getattr(self, "grid_charge_efficiency", 80) / 100.0
        batt_eff = float(self.config.get("battery_efficiency_pct", 90)) / 100.0

        max_inverter_power_w = float(self.config.get("max_inverter_power_w", 800))
        max_discharge_wh_per_15min = max_inverter_power_w / 4.0

        charger_power_w = float(self.config.get("grid_charger_power_w", 1000))
        max_charge_wh_per_15min = (charger_power_w / 4.0) * grid_charge_eff

        # Simulate natural battery flow to identify shortages
        simulated_batt_wh = batt_cap_wh * (current_batt_pct / 100.0)
        temp_batt_wh = simulated_batt_wh
        max_batt_wh = batt_cap_wh * (float(self.config.get("battery_max_limit_pct", 100.0)) / 100.0)

        shortages = []
        for i, fb in enumerate(future_blocks):
            f_solar = fb["solar"] * batt_eff
            f_cons = max(0.0, fb["house_wh"] - fb["balcony"])

            if f_solar > f_cons:
                temp_batt_wh += (f_solar - f_cons)
            else:
                needed = f_cons - f_solar
                available_from_batt = min(temp_batt_wh, max_discharge_wh_per_15min)

                # If we need more than the battery has, that's a shortage we might want to prevent by charging earlier
                if needed > available_from_batt:
                    uncovered = needed - available_from_batt
                    shortages.append({
                        "index": i,
                        "price": fb["price"],
                        "wh": min(uncovered, max_discharge_wh_per_15min - available_from_batt) # We can only discharge up to inverter limit anyway
                    })

                temp_batt_wh -= min(needed, max_discharge_wh_per_15min)

            temp_batt_wh = max(0.0, min(max_batt_wh, temp_batt_wh))

        if not shortages:
            return None

        # Sort shortages by price descending (we want to cover the most expensive ones first)
        shortages.sort(key=lambda x: x["price"], reverse=True)

        # Sort potential charging blocks by price ascending
        charge_candidates = [{"index": i, "price": fb["price"], "used_wh": 0.0} for i, fb in enumerate(future_blocks)]
        charge_candidates.sort(key=lambda x: x["price"])

        highest_approved_charge_price = -0.5
        grid_charge_margin_eur = getattr(self, "grid_charge_margin", 2.0) / 100.0

        # Try to match the most expensive shortages with the cheapest charging blocks that occur *before* them
        for shortage in shortages:
            shortage_wh_remaining = shortage["wh"]

            for candidate in charge_candidates:
                if shortage_wh_remaining <= 0:
                    break

                # We can only charge BEFORE the shortage occurs
                if candidate["index"] >= shortage["index"]:
                    continue

                # Is it profitable?
                # (cost to charge now + round trip losses) vs (cost to buy from grid during shortage)
                required_shortage_price = candidate["price"] / (grid_charge_eff * batt_eff)

                # Must be profitable enough to cover the configured minimum profit margin
                if shortage["price"] > (required_shortage_price + grid_charge_margin_eur):
                    # Profit! Let's schedule this candidate block to charge
                    available_charge_capacity = max_charge_wh_per_15min - candidate["used_wh"]
                    if available_charge_capacity > 0:
                        charge_amount = min(shortage_wh_remaining, available_charge_capacity)
                        candidate["used_wh"] += charge_amount
                        shortage_wh_remaining -= charge_amount

                        if candidate["price"] > highest_approved_charge_price:
                            highest_approved_charge_price = candidate["price"]

        if highest_approved_charge_price > -0.5:
            return highest_approved_charge_price

        return None

    def _check_grid_charge_safety(self, now, current_price: float, current_batt_pct: float, batt_cap_wh: float, hourly_forecasts: list[dict]) -> bool:
        """Runs the upper limit and buffer safety checks before allowing a grid charge."""
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

        future_blocks = self._build_future_blocks_pessimistic(now, hourly_forecasts, apply_extreme_price_factor=False)
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
        future_blocks = self._build_future_blocks_pessimistic(now, hourly_forecasts, apply_extreme_price_factor=False)

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

    def _calculate_optimal_dispatch(self, now, hourly_forecasts: list[dict], current_batt_pct: float, batt_cap_wh: float, batt_min_pct: float) -> tuple[list[dict], float]:
        """Greedy zwei-Pass Dispatch: weist Batterie-Entladung den teuersten Blöcken zuerst zu.

        Pass 1: Vorwärtssimulation ohne strategische Entladung — erfasst Batterie-Verlauf
                und erzwingt Entladung nur wenn Überfüllung droht.
        Pass 2: Greedy-Zuteilung — verarbeitet Blöcke nach Preis absteigend und weist
                verfügbare Batteriekapazität dem teuersten verbleibenden Verbrauch zu.

        Gibt zurück: (dispatch_plan, effective_threshold)
          dispatch_plan: Liste von {dt, discharge_wh, price} pro 15-min-Block
          effective_threshold: niedrigster Preis bei dem entladen wird (für Rückwärts-Kompatibilität)
        """
        real_blocks = self._build_future_blocks_pessimistic(now, hourly_forecasts, apply_extreme_price_factor=False)

        if not real_blocks:
            return [], -0.5

        batt_eff = float(self.config.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0
        max_discharge_wh = float(self.config.get(CONF_MAX_INVERTER_POWER_W, 800)) / 4.0
        min_batt_wh = batt_cap_wh * (batt_min_pct / 100.0)
        max_batt_pct_val = float(self.config.get(CONF_BATTERY_MAX_LIMIT_PCT, self.config.get(CONF_EARLY_EXCESS_MAX_BATTERY_PCT, 100.0)))
        max_batt_wh = batt_cap_wh * (max_batt_pct_val / 100.0)

        n = len(real_blocks)
        discharge_decisions = [0.0] * n

        # Pass 1: Vorwärtssimulation — nur erzwungene Entladung (Überfüll-Schutz)
        running_batt_wh = [0.0] * n
        temp_batt = batt_cap_wh * (current_batt_pct / 100.0)
        for i, rb in enumerate(real_blocks):
            running_batt_wh[i] = temp_batt
            sol = rb["solar"] * batt_eff
            con = max(0.0, rb["house_wh"] - rb.get("balcony", 0.0))
            temp_batt += sol
            if temp_batt > max_batt_wh:
                forced = min(con, max_discharge_wh, temp_batt - max_batt_wh)
                discharge_decisions[i] += forced
                temp_batt -= forced
            temp_batt = max(min_batt_wh, min(max_batt_wh, temp_batt))

        # Pass 2: Greedy-Zuteilung — teuerste Blöcke zuerst
        sortable = [(i, rb["price"]) for i, rb in enumerate(real_blocks) if rb["price"] > 0]
        sortable.sort(key=lambda x: x[1], reverse=True)

        for idx, price in sortable:
            rb = real_blocks[idx]
            sol = rb["solar"] * batt_eff
            con = max(0.0, rb["house_wh"] - rb.get("balcony", 0.0))

            # Wieviel Verbrauch ist noch ungedeckt (über erzwungene Entladung hinaus)?
            cons_uncovered = max(0.0, con - sol - discharge_decisions[idx])
            max_additional = min(cons_uncovered, max_discharge_wh - discharge_decisions[idx])
            if max_additional <= 0:
                continue

            # Wieviel Batterie ist zu diesem Zeitpunkt verfügbar?
            # running_batt_wh[idx] = Akkustand VOR diesem Block (inkl. Kaskaden aus früheren Zuteilungen)
            # + sol: Solar lädt den Akku in diesem Block auf, bevor wir entladen
            # - discharge_decisions[idx]: bereits reservierte Entladung (Pass-1-Zwangsentladung)
            available = max(0.0, running_batt_wh[idx] + sol - min_batt_wh - discharge_decisions[idx])
            extra = min(max_additional, available)
            if extra <= 0:
                continue

            discharge_decisions[idx] += extra

            # Kaskade: Batterie-Stand für alle nachfolgenden Blöcke reduzieren
            for j in range(idx + 1, n):
                running_batt_wh[j] = max(min_batt_wh, running_batt_wh[j] - extra)

        # Ergebnis-Liste aufbauen
        dispatch_plan = [
            {"dt": rb["dt"], "discharge_wh": discharge_decisions[i], "price": rb["price"]}
            for i, rb in enumerate(real_blocks)
        ]

        # Effektiver Schwellenwert für Rückwärts-Kompatibilität (z.B. Plandarstellung)
        dispatch_prices = [d["price"] for d in dispatch_plan if d["discharge_wh"] > 0.5]
        effective_threshold = min(dispatch_prices) if dispatch_prices else -0.5

        return dispatch_plan, effective_threshold

    def _build_future_blocks_pessimistic(self, now, hourly_forecasts: list[dict], apply_extreme_price_factor: bool = True) -> list[dict]:
        """Builds a list of 15-min blocks applying the implicit cloud cover correction."""
        blocks = self._build_future_blocks(now, hourly_forecasts, apply_extreme_price_factor=apply_extreme_price_factor)

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
                    b_ghi = b.get("ghi")
                    b["solar"] = self.learning_engine.predict_solar_for_quarter(q, implicit_cloud, ghi=b_ghi)
                    if self.config.get(CONF_BALCONY_POWER_SENSOR):
                        b["balcony"] = self.learning_engine.predict_balcony_for_quarter(q, implicit_cloud, ghi=b_ghi)

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

    def _build_forecast_plan(self, now, hourly_forecasts: list[dict], current_batt_pct: float, batt_cap_wh: float, price_threshold: float, charge_price_threshold: float | None = None):
        """Generate a forecast plan for the dynamic horizon using the dynamic threshold."""
        plan = []
        simulated_batt_wh = batt_cap_wh * (current_batt_pct / 100.0)
        future_blocks = self._build_future_blocks_pessimistic(now, hourly_forecasts)
        # Real blocks for overfill detection: solar not zeroed by extreme_price_factor
        real_blocks = self._build_future_blocks_pessimistic(now, hourly_forecasts, apply_extreme_price_factor=False)

        batt_eff = float(self.config.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0

        max_inverter_power_w = float(self.config.get(CONF_MAX_INVERTER_POWER_W, 800))
        max_discharge_wh_per_15min = max_inverter_power_w / 4.0

        min_batt_pct = self.config.get(CONF_BATTERY_MIN_LIMIT_PCT, 10)
        min_batt_wh = batt_cap_wh * (min_batt_pct / 100.0)

        max_batt_pct = float(self.config.get(CONF_BATTERY_MAX_LIMIT_PCT, self.config.get(CONF_EARLY_EXCESS_MAX_BATTERY_PCT, 100.0)))
        max_batt_wh = batt_cap_wh * (max_batt_pct / 100.0)

        charger_power_w = float(self.config.get("grid_charger_power_w", 1000))
        grid_charge_eff = getattr(self, "grid_charge_efficiency", 80) / 100.0
        max_charge_wh_per_15min = (charger_power_w / 4.0) * grid_charge_eff

        for i, fb in enumerate(future_blocks):
            pred_solar = fb["solar"] * batt_eff
            pred_balcony = fb["balcony"]
            raw_house_wh = fb["house_wh"]
            cc = fb["cc"]
            price = fb["price"]

            # The actual consumption the battery sees is house minus balcony
            pred_cons = max(0.0, raw_house_wh - pred_balcony)

            available_discharge = max(0.0, simulated_batt_wh - min_batt_wh)
            available_discharge = min(available_discharge, max_discharge_wh_per_15min)

            # Use dispatch plan for this block: how much should we discharge?
            dispatch_wh = 0.0
            if i < len(self._dispatch_plan):
                dispatch_wh = self._dispatch_plan[i]["discharge_wh"]
            sim_discharge = min(dispatch_wh, available_discharge)
            dispatching_this_block = dispatch_wh > 0.5

            # Will overfill check: use real_blocks so solar during expensive hours is not zeroed
            will_overfill = False
            temp_batt_wh = simulated_batt_wh
            for rb_future in real_blocks[i:]:
                f_solar = rb_future["solar"] * batt_eff
                f_cons = max(0.0, rb_future["house_wh"] - rb_future["balcony"])
                f_actual_discharge = min(f_cons, max_discharge_wh_per_15min, max(0.0, temp_batt_wh - min_batt_wh))
                temp_batt_wh += f_solar - f_actual_discharge

                if temp_batt_wh >= max_batt_wh:
                    will_overfill = True
                    break
                if temp_batt_wh <= min_batt_wh:
                    break

            # Determine if this block would trigger grid charging
            is_charging_block = False
            if charge_price_threshold is not None and price <= charge_price_threshold:
                current_temp_pct = (simulated_batt_wh / batt_cap_wh) * 100.0
                is_charging_block = self._check_grid_charge_safety(now, price, current_temp_pct, batt_cap_wh, hourly_forecasts[i:])

            if is_charging_block:
                action = f"Netzladen (LAD) für <={round(charge_price_threshold,3)}€"
                simulated_batt_wh += pred_solar + max_charge_wh_per_15min
            elif price < 0.0:
                action = f"Negativer Preis ({round(price,3)}€): DTU aus"
                simulated_batt_wh += pred_solar
            elif simulated_batt_wh >= max_batt_wh:
                action = f"Batterie {int(max_batt_pct)}% voll (DTU An)"
                simulated_batt_wh += pred_solar - sim_discharge
            elif will_overfill:
                action = "Überschussvermeidung (DTU An)"
                simulated_batt_wh += pred_solar - sim_discharge
            elif simulated_batt_wh <= min_batt_wh:
                action = "Batterie am Minimum (DTU Aus)"
                simulated_batt_wh += pred_solar
            elif not dispatching_this_block:
                action = "Akku sparen: DTU aus"
                simulated_batt_wh += pred_solar
            else:
                action = f"Dispatch ({round(price,3)}€): DTU an"
                simulated_batt_wh += pred_solar - sim_discharge

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

    def _calculate_target_morning_soc(self, now) -> float | None:
        """Berechnet den Ziel-SOC für den nächsten Morgen um Platz für Solar zu schaffen.

        Berücksichtigt:
        - Erwarteten Solar-Nettoüberschuss morgen (Solar minus Verbrauch)
        - Morgenpreis-Reserve: Batterie-Kapazität für teure Morgenstunden (6-10 Uhr)
        - Solar-Sicherheitspuffer (konfigurierbar)
        - Gestrige Tibber-Preise als Fallback wenn heutige Morgenpreise noch nicht bekannt

        Gibt None zurück wenn keine GHI-Daten für morgen verfügbar.
        """
        import homeassistant.util.dt as dt_util_local
        local_now = dt_util_local.as_local(now)
        tomorrow = (local_now + timedelta(days=1)).date()

        batt_cap_wh = float(self.config.get(CONF_BATTERY_CAPACITY_WH, 5000))
        batt_min_pct = float(self.config.get(CONF_BATTERY_MIN_LIMIT_PCT, 10))
        max_batt_pct = float(self.config.get(CONF_BATTERY_MAX_LIMIT_PCT, 100.0))
        batt_eff = float(self.config.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0
        solar_margin_pct = float(self.config.get(CONF_PRESUNNY_SOLAR_MARGIN_PCT, 20)) / 100.0

        # Schritt 1: Erwarteter Netto-Solar morgen (Solar minus Verbrauch, 6-18 Uhr)
        tomorrow_net_solar_wh = 0.0
        has_ghi_tomorrow = False
        for hour in range(6, 19):
            for minute_offset in [0, 15, 30, 45]:
                q = hour * 4 + (minute_offset // 15)
                block_dt = dt_util_local.as_utc(
                    dt_util_local.as_local(now).replace(
                        year=tomorrow.year, month=tomorrow.month, day=tomorrow.day,
                        hour=hour, minute=minute_offset, second=0, microsecond=0
                    )
                )
                ghi = self._get_ghi_for_dt(block_dt)
                if ghi > 0:
                    has_ghi_tomorrow = True
                pred_solar = self.learning_engine.predict_solar_for_quarter(q, cloud_cover=50.0, ghi=ghi)
                if self.config.get(CONF_BALCONY_POWER_SENSOR):
                    pred_solar += self.learning_engine.predict_balcony_for_quarter(q, cloud_cover=50.0, ghi=ghi)
                pred_solar_effective = pred_solar * batt_eff
                pred_cons = self.learning_engine.predict_consumption_for_quarter(q, dow=tomorrow.weekday())
                net = pred_solar_effective - pred_cons
                if net > 0:
                    tomorrow_net_solar_wh += net

        if not has_ghi_tomorrow:
            return None

        # Sicherheitspuffer anwenden (20% Puffer = nutze nur 80% des Forecasts)
        tomorrow_net_solar_wh_safe = tomorrow_net_solar_wh * (1.0 - solar_margin_pct)
        self.tomorrow_net_solar_wh = tomorrow_net_solar_wh_safe

        # Schritt 2: Morgenpreis-Reserve (6-10 Uhr)
        morning_reserve_wh = 0.0
        avg_morning_price = 0.0
        morning_price_count = 0
        for hour in range(6, 11):
            for minute_offset in [0, 15, 30, 45]:
                block_dt_local = dt_util_local.as_local(now).replace(
                    year=tomorrow.year, month=tomorrow.month, day=tomorrow.day,
                    hour=hour, minute=minute_offset, second=0, microsecond=0
                )
                block_dt_utc = dt_util_local.as_utc(block_dt_local)
                # Tibber-Preis für diesen Block finden (Fallback: gestrig)
                block_price = None
                for p in self.tibber_prices:
                    p_dt = p.get("datetime")
                    if p_dt and p_dt <= block_dt_utc < p_dt + timedelta(minutes=15):
                        block_price = float(p.get("total", 0.0))
                        break
                if block_price is None:
                    # Fallback: gleicher Block gestern
                    fallback_dt = block_dt_utc - timedelta(hours=24)
                    for p in self.tibber_prices:
                        p_dt = p.get("datetime")
                        if p_dt and p_dt <= fallback_dt < p_dt + timedelta(minutes=15):
                            block_price = float(p.get("total", 0.0))
                            break
                if block_price is not None:
                    avg_morning_price += block_price
                    morning_price_count += 1

        if morning_price_count > 0:
            avg_morning_price /= morning_price_count

        # Morgenverbrauch (6-10 Uhr) — Anteil aus Batterie (Solar noch gering in diesen Stunden)
        for hour in range(6, 11):
            for minute_offset in [0, 15, 30, 45]:
                q = hour * 4 + (minute_offset // 15)
                block_dt = dt_util_local.as_utc(
                    dt_util_local.as_local(now).replace(
                        year=tomorrow.year, month=tomorrow.month, day=tomorrow.day,
                        hour=hour, minute=minute_offset, second=0, microsecond=0
                    )
                )
                ghi = self._get_ghi_for_dt(block_dt)
                pred_solar = self.learning_engine.predict_solar_for_quarter(q, cloud_cover=50.0, ghi=ghi) * batt_eff
                pred_cons = self.learning_engine.predict_consumption_for_quarter(q, dow=tomorrow.weekday())
                # Wie viel kommt aus Batterie? (Verbrauch minus Solar, mindestens 0)
                from_battery = max(0.0, pred_cons - pred_solar)
                morning_reserve_wh += from_battery

        # Schritt 3: Ziel-SOC berechnen
        # Batterie muss morgen früh haben: Morgenreserve + Mindest-Puffer
        min_batt_wh = batt_cap_wh * (batt_min_pct / 100.0)
        max_batt_wh = batt_cap_wh * (max_batt_pct / 100.0)

        target_wh = morning_reserve_wh + min_batt_wh
        # Sicherstellen: target + Solar passt in Batterie
        if target_wh + tomorrow_net_solar_wh_safe > max_batt_wh:
            target_wh = max(min_batt_wh, max_batt_wh - tomorrow_net_solar_wh_safe)

        target_pct = (target_wh / batt_cap_wh) * 100.0
        # Clamp: sinnvoller Bereich
        target_pct = max(batt_min_pct + 5.0, min(max_batt_pct - 10.0, target_pct))

        return target_pct

    def _build_future_blocks(self, now, hourly_forecasts: list[dict], apply_extreme_price_factor: bool = True) -> list[dict]:
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

            ghi = self._get_ghi_for_dt(eval_dt)
            pred_solar = self.learning_engine.predict_solar_for_quarter(q, cc, ghi=ghi)
            pred_cons = self.learning_engine.predict_consumption_for_quarter(q, dow=eval_dt.weekday())

            pred_balcony = 0.0
            # Only add predicted balcony if a sensor is configured
            if self.config.get(CONF_BALCONY_POWER_SENSOR):
                pred_balcony = self.learning_engine.predict_balcony_for_quarter(q, cc, ghi=ghi)

            # Add climate device load prediction per active device
            forecast_temp = self._get_outdoor_temp_for_dt(eval_dt)
            if forecast_temp is None:
                forecast_temp = self._get_current_outdoor_temp()
            for device_id, state in self.climate_device_states.items():
                if not state.get("enabled", False):
                    continue
                device_type = state.get("device_type", "heating")
                pred_cons += self.learning_engine.predict_climate_for_quarter(
                    slot_id=device_id,
                    device_type=device_type,
                    quarter=q,
                    outdoor_temp=forecast_temp,
                    setpoint=state.get("setpoint", 20.0),
                    dow=eval_dt.weekday(),
                    manual_w=state.get("manual_w", 0.0),
                    device_enabled=True,
                )

            # Add load from currently running or scheduled appliances (e.g. EV charging)
            pred_cons += self._get_appliance_load_wh_for_block(eval_dt, now)

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

            # Extreme Price Reserve Logic (only for price arbitrage simulations, not physical overfill checks)
            if apply_extreme_price_factor and price > self.extreme_price_threshold:
                pred_solar = pred_solar * self.extreme_price_factor
                pred_balcony = pred_balcony * self.extreme_price_factor

            blocks.append({
                "dt": eval_dt,
                "solar": pred_solar,
                "balcony": pred_balcony,
                "house_wh": pred_cons,
                "cc": cc,
                "ghi": ghi,
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

    def _get_appliance_load_wh_for_block(self, block_start: datetime, now: datetime) -> float:
        """Returns extra Wh load for a 15-min block from all running or scheduled appliances.

        Covers two cases:
        - RUNNING_SPONTANEOUS / RUNNING_SCHEDULED: device is active now, estimate remaining run time
        - WAITING_FOR_START: device has a scheduled start time in the future
        """
        if not self.appliance_manager:
            return 0.0
        block_end = block_start + timedelta(minutes=15)
        total_wh = 0.0

        for sensor_id, sm in self.appliance_state_machines.items():
            if sm.state in (ApplianceState.RUNNING_SCHEDULED, ApplianceState.RUNNING_SPONTANEOUS):
                # While running: detected program (confidence >50%) wins over what was planned
                if sm.active_program_id:
                    prog = self.appliance_manager.get_program_by_id(sensor_id, sm.active_program_id)
                elif sm.planned_run and sm.planned_run.program_id:
                    prog = self.appliance_manager.get_program_by_id(sensor_id, sm.planned_run.program_id)
                else:
                    prog = self.appliance_manager.get_program(sensor_id)
            elif sm.planned_run and sm.planned_run.program_id:
                # Waiting to start: use the scheduled program
                prog = self.appliance_manager.get_program_by_id(sensor_id, sm.planned_run.program_id)
            else:
                prog = self.appliance_manager.get_program(sensor_id)

            if not prog or prog.effective_power_w <= 0 or prog.duration_minutes <= 0:
                continue

            run_start: datetime | None = None
            run_end: datetime | None = None

            if sm.state in (ApplianceState.RUNNING_SPONTANEOUS, ApplianceState.RUNNING_SCHEDULED):
                if sm._run_start_time is not None:
                    elapsed_minutes = (now - sm._run_start_time).total_seconds() / 60
                    remaining_minutes = max(0.0, prog.duration_minutes - elapsed_minutes)
                    if remaining_minutes > 0:
                        run_start = now
                        run_end = now + timedelta(minutes=remaining_minutes)
            elif sm.state == ApplianceState.WAITING_FOR_START and sm.planned_run:
                run_start = sm.planned_run.scheduled_start
                run_end = run_start + timedelta(minutes=prog.duration_minutes)

            if run_start is None or run_end is None:
                continue

            overlap_start = max(block_start, run_start)
            overlap_end = min(block_end, run_end)
            overlap_minutes = (overlap_end - overlap_start).total_seconds() / 60
            if overlap_minutes > 0:
                total_wh += (prog.effective_power_w / 60.0) * overlap_minutes

        return total_wh
