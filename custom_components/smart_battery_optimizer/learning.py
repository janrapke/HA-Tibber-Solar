"""Learning and Prediction Engine for Smart Battery Optimizer."""
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
import math

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_BASE_LOAD_W, CONF_SOLAR_PEAK_W,
    CLIMATE_BOOTSTRAP_ASSUMED_DELTA,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "smart_battery_optimizer.learning_data_v2"
STORAGE_VERSION = 1

class LearningEngine:
    """Class to manage learning of consumption and solar generation in 15-min intervals."""

    def __init__(self, hass: HomeAssistant, config: dict):
        """Initialize the learning engine."""
        self.hass = hass
        self.config = config
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: Dict[str, Any] = {
            "consumption": {},  # Format: {"quarter": average_consumption_wh_per_15min}
            "solar": {},        # Format: {"quarter": {"cloud_cover_range": average_solar_wh_per_15min}}
            "balcony": {},      # Format: {"quarter": {"cloud_cover_range": average_balcony_wh_per_15min}}
            "counters": {
                "consumption": {},
                "solar": {},
                "balcony": {}
            },
            "learning_mode_active": False, "learning_rate_factor": 0.7
        }

        self._current_quarter_consumption_acc = 0.0
        self._current_quarter_consumption_count = 0
        self._current_quarter_solar_acc = 0.0
        self._current_quarter_solar_count = 0
        self._current_quarter_solar_throttled = False
        self._current_quarter_balcony_acc = 0.0
        self._current_quarter_balcony_count = 0
        self._last_quarter_processed = -1

        # Initialize default structures for 96 quarters (24h * 4)
        for q in range(96):
            self.data["consumption"][str(q)] = 0.0
            self.data["counters"]["consumption"][str(q)] = 6  # Default to normal alpha

            self.data["solar"][str(q)] = {
                "clear": 0.0,    # 0-33% cloud cover
                "partly": 0.0,   # 34-66% cloud cover
                "cloudy": 0.0,   # 67-100% cloud cover
            }
            self.data["counters"]["solar"][str(q)] = {
                "clear": 6,
                "partly": 6,
                "cloudy": 6,
            }

            self.data["balcony"][str(q)] = {
                "clear": 0.0,
                "partly": 0.0,
                "cloudy": 0.0,
            }
            self.data["counters"]["balcony"][str(q)] = {
                "clear": 6,
                "partly": 6,
                "cloudy": 6,
            }

        # Climate device learning — device-ID-keyed, temperature-delta model.
        # Devices are registered dynamically via register_climate_device().
        # Per device, separate W/°C models for heating and cooling so a heat pump
        # can learn both independently.
        self.data.setdefault("climate", {})
        self.data.setdefault("vacation_mode_active", False)

        # Accumulators for current quarter (climate), keyed by device_id
        self._climate_acc: dict = {}

        # GHI-ratio based solar prediction (site-specific efficiency per time slot)
        # ratio[slot] = actual_production_wh / GHI_W_per_m2
        # Works for any system type (tracker/fixed) by learning the real relationship
        self.data.setdefault("solar_ghi_ratio", {str(q): 0.0 for q in range(96)})
        self.data.setdefault("solar_ghi_ratio_counters", {str(q): 0 for q in range(96)})
        self.data.setdefault("balcony_ghi_ratio", {str(q): 0.0 for q in range(96)})
        self.data.setdefault("balcony_ghi_ratio_counters", {str(q): 0 for q in range(96)})

        # Day-of-week consumption model (0=Monday … 6=Sunday), 7×96 slots
        # Used for prediction when learning_mode_active=False (switch once enough data exists)
        # Always learned in the background regardless of learning mode
        self.data.setdefault("consumption_dow", {
            str(dow): {str(q): 0.0 for q in range(96)} for dow in range(7)
        })
        self.data.setdefault("consumption_dow_counters", {
            str(dow): {str(q): 0 for q in range(96)} for dow in range(7)
        })

    async def async_load(self):
        """Load historical data from storage or initialize priors."""
        stored_data = await self._store.async_load()
        if stored_data:
            # Simple merge to ensure new keys exist if schema changes
            if "consumption" in stored_data:
                for q, val in stored_data["consumption"].items():
                    if q in self.data["consumption"]:
                        self.data["consumption"][q] = val
            if "solar" in stored_data:
                for q, conditions in stored_data["solar"].items():
                    if q in self.data["solar"]:
                        for condition, val in conditions.items():
                            if condition in self.data["solar"][q]:
                                self.data["solar"][q][condition] = val
            if "balcony" in stored_data:
                for q, conditions in stored_data["balcony"].items():
                    if q in self.data["balcony"]:
                        for condition, val in conditions.items():
                            if condition in self.data["balcony"][q]:
                                self.data["balcony"][q][condition] = val

            if "counters" in stored_data:
                for category in ["consumption", "solar", "balcony"]:
                    if category in stored_data["counters"]:
                        for q, val in stored_data["counters"][category].items():
                            if q in self.data["counters"][category]:
                                if isinstance(val, dict):
                                    for cond, c_val in val.items():
                                        if cond in self.data["counters"][category][q]:
                                            self.data["counters"][category][q][cond] = c_val
                                else:
                                    self.data["counters"][category][q] = val

            for ghi_key in ("solar_ghi_ratio", "solar_ghi_ratio_counters", "balcony_ghi_ratio", "balcony_ghi_ratio_counters"):
                if ghi_key in stored_data:
                    for q, val in stored_data[ghi_key].items():
                        if q in self.data[ghi_key]:
                            self.data[ghi_key][q] = val

            for dow_key in ("consumption_dow", "consumption_dow_counters"):
                if dow_key in stored_data:
                    for dow, slots in stored_data[dow_key].items():
                        if dow in self.data[dow_key]:
                            for q, val in slots.items():
                                if q in self.data[dow_key][dow]:
                                    self.data[dow_key][dow][q] = val

            if "learning_mode_active" in stored_data:
                self.data["learning_mode_active"] = stored_data["learning_mode_active"]
            if "learning_rate_factor" in stored_data:
                self.data["learning_rate_factor"] = stored_data["learning_rate_factor"]
            if "vacation_mode_active" in stored_data:
                self.data["vacation_mode_active"] = stored_data["vacation_mode_active"]

            if "climate" in stored_data:
                for device_id, src in stored_data["climate"].items():
                    # Restore any stored device — new devices get lazy-created via register_climate_device
                    dst = self._ensure_climate_slot(device_id)
                    for scalar_key in ("wpc_heating", "wpc_heating_counter", "wpc_cooling", "wpc_cooling_counter"):
                        if scalar_key in src:
                            cast = float if "counter" not in scalar_key else int
                            dst[scalar_key] = cast(src[scalar_key])
                    for dow_key in ("occupancy_dow", "occupancy_dow_counters"):
                        if dow_key in src:
                            for dow, q_map in src[dow_key].items():
                                if dow in dst[dow_key]:
                                    for q, val in q_map.items():
                                        if q in dst[dow_key][dow]:
                                            dst[dow_key][dow][q] = val

            _LOGGER.debug("Loaded learning data: %s", self.data)
        else:
            _LOGGER.info("No learning data found, initializing with priors.")
            self._initialize_priors()
            await self.async_save()

    def _initialize_priors(self):
        """Initialize learning data with base load and solar peak assumptions."""
        base_load_w = float(self.config.get(CONF_BASE_LOAD_W, 250))
        solar_peak_w = float(self.config.get(CONF_SOLAR_PEAK_W, 6000))

        # Base load in Wh per 15 minutes
        base_load_wh_15min = base_load_w / 4.0

        # Assumption for balcony solar peak if not explicitly configured
        balcony_peak_w = 600.0

        for q in range(96):
            self.data["consumption"][str(q)] = base_load_wh_15min
            # Seed DOW model with same base load so it works from day 1
            for dow in range(7):
                self.data["consumption_dow"][str(dow)][str(q)] = base_load_wh_15min
                # Keep counters at 0 so DOW alpha starts high (learns fast)

            # Calculate time of day (0.0 to 24.0)
            hour_of_day = q / 4.0

            # Simple bell curve for solar between 6am and 6pm (12h duration)
            solar_wh_15min = 0.0
            balcony_wh_15min = 0.0
            if 6.0 <= hour_of_day <= 18.0:
                # Map 6..18 to -pi/2 .. pi/2 for cosine curve
                normalized_time = ((hour_of_day - 6.0) / 12.0) * math.pi - (math.pi / 2)
                # Cosine curve scaled to peak power, divided by 4 for 15-min Wh
                solar_wh_15min = math.cos(normalized_time) * (solar_peak_w / 4.0)
                balcony_wh_15min = math.cos(normalized_time) * (balcony_peak_w / 4.0)

            self.data["solar"][str(q)]["clear"] = solar_wh_15min
            self.data["solar"][str(q)]["partly"] = solar_wh_15min * 0.5
            self.data["solar"][str(q)]["cloudy"] = solar_wh_15min * 0.2

            self.data["balcony"][str(q)]["clear"] = balcony_wh_15min
            self.data["balcony"][str(q)]["partly"] = balcony_wh_15min * 0.5
            self.data["balcony"][str(q)]["cloudy"] = balcony_wh_15min * 0.2

    async def hard_reset_data(self):
        """Hard reset the learning data completely and reinitialize with priors."""
        self.data = {
            "consumption": {},
            "solar": {str(q): {} for q in range(96)},
            "balcony": {str(q): {} for q in range(96)},
            "counters": {
                "consumption": {str(q): 0 for q in range(96)},
                "solar": {str(q): {"clear": 0, "partly": 0, "cloudy": 0} for q in range(96)},
                "balcony": {str(q): {"clear": 0, "partly": 0, "cloudy": 0} for q in range(96)}
            },
            "solar_ghi_ratio": {str(q): 0.0 for q in range(96)},
            "solar_ghi_ratio_counters": {str(q): 0 for q in range(96)},
            "balcony_ghi_ratio": {str(q): 0.0 for q in range(96)},
            "balcony_ghi_ratio_counters": {str(q): 0 for q in range(96)},
            "consumption_dow": {str(dow): {str(q): 0.0 for q in range(96)} for dow in range(7)},
            "consumption_dow_counters": {str(dow): {str(q): 0 for q in range(96)} for dow in range(7)},
            "learning_mode_active": False, "learning_rate_factor": 0.7,
            "vacation_mode_active": False,
            "climate": {},
        }
        self._climate_acc = {}
        self._initialize_priors()
        await self.async_save()
        _LOGGER.info("Learning data has been hard-reset to priors.")

    def reset_learning_counters(self):
        pass

    def set_learning_mode(self, active: bool):
        self.data["learning_mode_active"] = active
        _LOGGER.info("Learning mode set to %s", active)

    def set_learning_rate(self, rate: float):
        self.data["learning_rate_factor"] = rate

    def set_vacation_mode(self, active: bool):
        self.data["vacation_mode_active"] = active
        _LOGGER.info("Vacation mode set to %s — learning %s", active, "paused" if active else "resumed")

    def is_vacation_mode_active(self) -> bool:
        return bool(self.data.get("vacation_mode_active", False))

    # ------------------------------------------------------------------
    # Climate device learning
    # ------------------------------------------------------------------

    def _ensure_climate_slot(self, device_id: str) -> dict:
        """Return the climate data dict for device_id, creating it if absent."""
        if device_id not in self.data["climate"]:
            self.data["climate"][device_id] = {
                "wpc_heating": 0.0, "wpc_heating_counter": 0,
                "wpc_cooling": 0.0, "wpc_cooling_counter": 0,
                "occupancy_dow": {
                    str(dow): {str(q): 0.0 for q in range(96)} for dow in range(7)
                },
                "occupancy_dow_counters": {
                    str(dow): {str(q): 0 for q in range(96)} for dow in range(7)
                },
            }
        if device_id not in self._climate_acc:
            self._climate_acc[device_id] = {"power": 0.0, "n": 0}
        return self.data["climate"][device_id]

    def register_climate_device(self, device_id: str, manual_w: float, device_type: str):
        """Ensure storage exists for device_id and run bootstrap if not yet seeded."""
        self._ensure_climate_slot(device_id)
        if manual_w > 0:
            self.bootstrap_climate_slot(device_id, manual_w, device_type)

    def bootstrap_climate_slot(self, slot_id: str, manual_w: float, device_type: str):
        """Seed the W/°C coefficient from a manual wattage estimate.

        Uses CLIMATE_BOOTSTRAP_ASSUMED_DELTA as the typical operating delta so
        predictions work immediately without waiting for real data.
        Counter is set to 1 so actual measurements quickly take over.
        device_type must be one of: "heating", "cooling", "heat_pump".
        """
        if manual_w <= 0:
            return
        slot = self._ensure_climate_slot(slot_id)
        wpc_init = manual_w / CLIMATE_BOOTSTRAP_ASSUMED_DELTA
        if device_type in ("heating", "heat_pump") and slot["wpc_heating_counter"] == 0:
            slot["wpc_heating"] = wpc_init
            slot["wpc_heating_counter"] = 1
            _LOGGER.info("Bootstrapped %s heating W/°C = %.1f from manual %.0f W", slot_id, wpc_init, manual_w)
        if device_type in ("cooling", "heat_pump") and slot["wpc_cooling_counter"] == 0:
            slot["wpc_cooling"] = wpc_init
            slot["wpc_cooling_counter"] = 1
            _LOGGER.info("Bootstrapped %s cooling W/°C = %.1f from manual %.0f W", slot_id, wpc_init, manual_w)

    async def record_climate_power(self, slot_id: str, power_w: float):
        """Accumulate smart-plug power reading during a 15-min quarter."""
        if power_w < 0:
            return
        self._ensure_climate_slot(slot_id)
        self._climate_acc[slot_id]["power"] += power_w
        self._climate_acc[slot_id]["n"] += 1

    async def finalize_climate_quarter(
        self,
        quarter: int,
        slot_id: str,
        device_type: str,
        outdoor_temp: float,
        setpoint: float,
        dow: int,
    ):
        """Finalize a quarter for a climate device slot — update W/°C and occupancy.

        device_type: "heating" | "cooling" | "heat_pump"
        setpoint: single target temperature (Solltemperatur).
          - Heating runs when outdoor < setpoint
          - Cooling runs when outdoor > setpoint
        Learning is skipped when vacation mode is active.
        """
        if self.data.get("vacation_mode_active"):
            self._climate_acc[slot_id] = {"power": 0.0, "n": 0}
            return

        acc = self._climate_acc.get(slot_id, {"power": 0.0, "n": 0})
        n = acc["n"]
        avg_power_w = (acc["power"] / n) if n > 0 else 0.0
        self._climate_acc[slot_id] = {"power": 0.0, "n": 0}

        slot = self._ensure_climate_slot(slot_id)
        q_str = str(quarter)
        d_str = str(dow)

        device_was_running = avg_power_w > 20.0

        # Occupancy update
        occ_counter = slot["occupancy_dow_counters"][d_str][q_str]
        occ_alpha = self._calculate_alpha(occ_counter)
        running_signal = 1.0 if device_was_running else 0.0
        slot["occupancy_dow"][d_str][q_str] = (
            occ_alpha * running_signal + (1 - occ_alpha) * slot["occupancy_dow"][d_str][q_str]
        )
        if occ_counter < 6:
            slot["occupancy_dow_counters"][d_str][q_str] = occ_counter + 1

        if not device_was_running:
            return

        temp_delta = setpoint - outdoor_temp  # positive = it's colder than setpoint → heating
        # For cooling: outdoor > setpoint → temp_delta negative → flip sign
        heating_delta = max(0.0, temp_delta)
        cooling_delta = max(0.0, -temp_delta)

        if device_type in ("heating", "heat_pump") and heating_delta > 1.0:
            observed_wpc = avg_power_w / heating_delta
            cnt = slot["wpc_heating_counter"]
            alpha = self._calculate_alpha(cnt)
            slot["wpc_heating"] = (
                observed_wpc if slot["wpc_heating"] == 0.0
                else alpha * observed_wpc + (1 - alpha) * slot["wpc_heating"]
            )
            if cnt < 6:
                slot["wpc_heating_counter"] = cnt + 1

        if device_type in ("cooling", "heat_pump") and cooling_delta > 1.0:
            observed_wpc = avg_power_w / cooling_delta
            cnt = slot["wpc_cooling_counter"]
            alpha = self._calculate_alpha(cnt)
            slot["wpc_cooling"] = (
                observed_wpc if slot["wpc_cooling"] == 0.0
                else alpha * observed_wpc + (1 - alpha) * slot["wpc_cooling"]
            )
            if cnt < 6:
                slot["wpc_cooling_counter"] = cnt + 1

    def predict_climate_for_quarter(
        self,
        slot_id: str,
        device_type: str,
        quarter: int,
        outdoor_temp: float,
        setpoint: float,
        dow: int = None,
        manual_w: float = 0.0,
        device_enabled: bool = True,
    ) -> float:
        """Predict climate device consumption (Wh) for a 15-min quarter.

        device_type: "heating" | "cooling" | "heat_pump"
        setpoint: single Solltemperatur in °C.
        Returns 0 when disabled, no temperature demand, or no model and no manual fallback.
        """
        if not device_enabled or device_type == "disabled":
            return 0.0

        slot = self.data["climate"].get(slot_id, {})

        heating_delta = max(0.0, setpoint - outdoor_temp)   # > 0 when cold outside
        cooling_delta = max(0.0, outdoor_temp - setpoint)   # > 0 when hot outside

        # A heat pump cannot heat and cool simultaneously — dominant delta wins
        if heating_delta > 0 and cooling_delta > 0:
            if heating_delta >= cooling_delta:
                cooling_delta = 0.0
            else:
                heating_delta = 0.0

        power_w = 0.0

        if device_type in ("heating", "heat_pump") and heating_delta > 0:
            wpc = float(slot.get("wpc_heating", 0.0))
            cnt = int(slot.get("wpc_heating_counter", 0))
            if cnt > 0 and wpc > 0.0:
                power_w = wpc * heating_delta
            elif manual_w > 0.0:
                power_w = manual_w * min(1.0, heating_delta / CLIMATE_BOOTSTRAP_ASSUMED_DELTA)

        if device_type in ("cooling", "heat_pump") and cooling_delta > 0 and power_w == 0.0:
            wpc = float(slot.get("wpc_cooling", 0.0))
            cnt = int(slot.get("wpc_cooling_counter", 0))
            if cnt > 0 and wpc > 0.0:
                power_w = wpc * cooling_delta
            elif manual_w > 0.0:
                power_w = manual_w * min(1.0, cooling_delta / CLIMATE_BOOTSTRAP_ASSUMED_DELTA)

        if power_w <= 0:
            return 0.0

        # Apply learned occupancy probability
        occ = 1.0
        if dow is not None:
            d_str = str(dow)
            q_str = str(quarter)
            occ_val = slot.get("occupancy_dow", {}).get(d_str, {}).get(q_str, -1.0)
            occ_cnt = slot.get("occupancy_dow_counters", {}).get(d_str, {}).get(q_str, 0)
            if occ_cnt >= 2 and occ_val >= 0.0:
                occ = float(occ_val)

        return (power_w * occ) / 4.0  # W → Wh per 15 min

    # ------------------------------------------------------------------
    # Learning coverage hint
    # ------------------------------------------------------------------

    def get_learning_coverage(self) -> dict:
        """Return learning coverage info for the hint sensor.

        Counts DOW/quarter slots with ≥ 6 observations (= stable alpha = 0.1).
        Total possible slots: 7 days × 96 quarters = 672.
        Estimated weeks remaining assumes ~96 new observations per week.
        """
        total = 7 * 96
        mature = 0
        for dow in range(7):
            d_str = str(dow)
            for q in range(96):
                q_str = str(q)
                cnt = self.data.get("consumption_dow_counters", {}).get(d_str, {}).get(q_str, 0)
                if cnt >= 6:
                    mature += 1

        pct = round((mature / total) * 100, 1)
        # Each week adds roughly 96 observations per DOW × 1 DOW = 96 mature slots
        remaining_slots = total - mature
        weeks_remaining = max(0, math.ceil(remaining_slots / 96))
        return {
            "coverage_pct": pct,
            "mature_slots": mature,
            "total_slots": total,
            "estimated_weeks_remaining": weeks_remaining,
        }

    async def async_save(self):
        """Save historical data to storage."""
        await self._store.async_save(self.data)

    def _get_cloud_category(self, cloud_cover: float) -> str:
        """Categorize cloud cover percentage into clear/partly/cloudy."""
        if cloud_cover < 33:
            return "clear"
        elif cloud_cover < 67:
            return "partly"
        else:
            return "cloudy"

    async def record_consumption(self, current_quarter: int, power_w: float):
        """Accumulate real house consumption data for the current 15-min interval."""
        if power_w < 0:
            return

        self._current_quarter_consumption_acc += power_w
        self._current_quarter_consumption_count += 1

    async def record_solar(self, current_quarter: int, power_w: float, cloud_cover: float, charge_state: str = None):
        """Accumulate solar production data for the current 15-min interval."""
        if power_w < 0:
            return

        # Do not record solar if the battery is in a state that throttles solar production
        if charge_state and charge_state.lower() in ("absorption", "float", "ausgleichsladung", "equalization"):
            _LOGGER.debug("Skipping solar learning because charge controller is in state: %s", charge_state)
            self._current_quarter_solar_throttled = True
            return

        self._current_quarter_solar_acc += power_w
        self._current_quarter_solar_count += 1

    async def record_balcony(self, current_quarter: int, power_w: float, cloud_cover: float):
        """Accumulate balcony solar production data for the current 15-min interval."""
        if power_w < 0:
            return

        self._current_quarter_balcony_acc += power_w
        self._current_quarter_balcony_count += 1

    def _calculate_alpha(self, counter: int) -> float:
        """Calculate dynamic alpha based on counter or fixed if learning mode active."""
        if self.data.get("learning_mode_active"):
            return float(self.data.get("learning_rate_factor", 0.7))
        if counter >= 6:
            return 0.1
        return max(0.7 - (0.1 * counter), 0.1)

    async def finalize_quarter(self, quarter: int, cloud_cover: float, price: float = 0.0, ghi: float = None, balcony_ghi: float = None, dow: int = None):
        """Called once at the end of a 15-min interval to calculate the average Wh and apply EMA."""
        q_str = str(quarter)

        # For pessimistic tracking in the coordinator
        actual_consumption_wh = 0.0
        actual_solar_wh = 0.0

        # Vacation mode: skip all consumption/solar learning but still return accumulators flushed
        if self.data.get("vacation_mode_active"):
            self._current_quarter_consumption_acc = 0.0
            self._current_quarter_consumption_count = 0
            self._current_quarter_solar_acc = 0.0
            self._current_quarter_solar_count = 0
            self._current_quarter_solar_throttled = False
            self._current_quarter_balcony_acc = 0.0
            self._current_quarter_balcony_count = 0
            _LOGGER.debug("finalize_quarter: skipped — vacation mode active")
            return actual_consumption_wh, actual_solar_wh

        # Do not learn consumption data if prices are negative
        skip_consumption = False
        try:
            if price is not None and float(price) < 0.0:
                skip_consumption = True
        except (ValueError, TypeError):
            pass

        # Finalize consumption
        if self._current_quarter_consumption_count > 0 and not skip_consumption:
            counter = self.data["counters"]["consumption"][q_str]
            alpha = self._calculate_alpha(counter)

            # Average power in W over 15 min equals energy in Wh for that 15 min if divided by 4
            avg_power = self._current_quarter_consumption_acc / self._current_quarter_consumption_count
            actual_consumption_wh = avg_power / 4.0
            current_avg = self.data["consumption"][q_str]
            self.data["consumption"][q_str] = (alpha * actual_consumption_wh) + ((1 - alpha) * current_avg)

            if counter < 6:
                self.data["counters"]["consumption"][q_str] = counter + 1

            # Day-of-week model — always learned in background regardless of learning mode
            if dow is not None:
                d_str = str(dow)
                dow_counter = self.data["consumption_dow_counters"][d_str][q_str]
                dow_alpha = self._calculate_alpha(dow_counter)
                current_dow = self.data["consumption_dow"][d_str][q_str]
                if current_dow == 0.0:
                    self.data["consumption_dow"][d_str][q_str] = actual_consumption_wh
                else:
                    self.data["consumption_dow"][d_str][q_str] = (dow_alpha * actual_consumption_wh) + ((1 - dow_alpha) * current_dow)
                if dow_counter < 6:
                    self.data["consumption_dow_counters"][d_str][q_str] = dow_counter + 1

            self._current_quarter_consumption_acc = 0.0
            self._current_quarter_consumption_count = 0

        # Finalize solar
        if self._current_quarter_solar_count > 0 and not self._current_quarter_solar_throttled:
            condition = self._get_cloud_category(cloud_cover)
            counter = self.data["counters"]["solar"][q_str][condition]
            alpha = self._calculate_alpha(counter)

            avg_power = self._current_quarter_solar_acc / self._current_quarter_solar_count
            actual_solar_wh = avg_power / 4.0
            current_avg = self.data["solar"][q_str][condition]

            self.data["solar"][q_str][condition] = (alpha * actual_solar_wh) + ((1 - alpha) * current_avg)

            if counter < 6:
                self.data["counters"]["solar"][q_str][condition] = counter + 1

            # GHI ratio learning: ratio = actual_wh / GHI_W_per_m2
            # Captures all site-specific factors (shading, tracker type, panel angle, losses)
            # Only learns from unthrottled data; valid across all system types
            if ghi is not None and ghi > 10.0 and actual_solar_wh > 0:
                ratio = actual_solar_wh / ghi
                ghi_counter = self.data["solar_ghi_ratio_counters"].get(q_str, 0)
                ghi_alpha = self._calculate_alpha(ghi_counter)
                current_ratio = self.data["solar_ghi_ratio"].get(q_str, 0.0)
                if current_ratio == 0.0:
                    self.data["solar_ghi_ratio"][q_str] = ratio
                else:
                    self.data["solar_ghi_ratio"][q_str] = (ghi_alpha * ratio) + ((1 - ghi_alpha) * current_ratio)
                if ghi_counter < 6:
                    self.data["solar_ghi_ratio_counters"][q_str] = ghi_counter + 1

        self._current_quarter_solar_acc = 0.0
        self._current_quarter_solar_count = 0
        self._current_quarter_solar_throttled = False

        # Finalize balcony
        if self._current_quarter_balcony_count > 0:
            condition = self._get_cloud_category(cloud_cover)
            counter = self.data["counters"]["balcony"][q_str][condition]
            alpha = self._calculate_alpha(counter)

            avg_power = self._current_quarter_balcony_acc / self._current_quarter_balcony_count
            quarter_wh = avg_power / 4.0
            current_avg = self.data["balcony"][q_str][condition]

            self.data["balcony"][q_str][condition] = (alpha * quarter_wh) + ((1 - alpha) * current_avg)

            if counter < 6:
                self.data["counters"]["balcony"][q_str][condition] = counter + 1

            # GHI ratio for balcony (no absorption skip needed — balcony feeds house directly)
            b_ghi = balcony_ghi if balcony_ghi is not None else ghi
            if b_ghi is not None and b_ghi > 10.0 and quarter_wh > 0:
                b_ratio = quarter_wh / b_ghi
                b_ghi_counter = self.data["balcony_ghi_ratio_counters"].get(q_str, 0)
                b_ghi_alpha = self._calculate_alpha(b_ghi_counter)
                b_current_ratio = self.data["balcony_ghi_ratio"].get(q_str, 0.0)
                if b_current_ratio == 0.0:
                    self.data["balcony_ghi_ratio"][q_str] = b_ratio
                else:
                    self.data["balcony_ghi_ratio"][q_str] = (b_ghi_alpha * b_ratio) + ((1 - b_ghi_alpha) * b_current_ratio)
                if b_ghi_counter < 6:
                    self.data["balcony_ghi_ratio_counters"][q_str] = b_ghi_counter + 1

            self._current_quarter_balcony_acc = 0.0
            self._current_quarter_balcony_count = 0

        return actual_consumption_wh, actual_solar_wh




    def predict_consumption_for_quarter(self, quarter: int, dow: int = None) -> float:
        """Predict consumption (Wh) for a specific 15-min interval.

        When learning_mode_active=False and dow is provided, uses the day-of-week specific
        model if it has sufficient data (>= 2 observations for that slot).
        Falls back to the general blended model otherwise.
        """
        base_load_w = float(self.config.get(CONF_BASE_LOAD_W, 250))
        min_wh = base_load_w / 4.0

        if dow is not None and not self.data.get("learning_mode_active", False):
            d_str = str(dow)
            q_str = str(quarter)
            dow_val = float(self.data.get("consumption_dow", {}).get(d_str, {}).get(q_str, 0.0))
            dow_count = self.data.get("consumption_dow_counters", {}).get(d_str, {}).get(q_str, 0)
            if dow_val > 0.0 and dow_count >= 2:
                return max(dow_val, min_wh * 0.5)

        val = float(self.data["consumption"].get(str(quarter), 0.0))
        return max(val, min_wh * 0.5)

    def predict_solar_for_quarter(self, quarter: int, cloud_cover: float = 50.0, ghi: float = None) -> float:
        """Predict solar generation (Wh) for a specific 15-min interval.

        Uses GHI-ratio model when GHI is provided and ratio is learned — works for any
        installation type (fixed, single-axis, dual-axis tracker) without explicit configuration.
        Falls back to cloud-cover bucket model for backward compatibility.
        """
        q_str = str(quarter)

        if ghi is not None and ghi > 10.0:
            ratio = self.data.get("solar_ghi_ratio", {}).get(q_str, 0.0)
            counter = self.data.get("solar_ghi_ratio_counters", {}).get(q_str, 0)
            if ratio > 0.0 and counter > 0:
                return ratio * ghi

        if q_str not in self.data["solar"]:
            return 0.0
        condition = self._get_cloud_category(cloud_cover)
        return float(self.data["solar"][q_str].get(condition, 0.0))

    def predict_balcony_for_quarter(self, quarter: int, cloud_cover: float = 50.0, ghi: float = None) -> float:
        """Predict balcony solar generation (Wh) for a specific 15-min interval."""
        q_str = str(quarter)

        if ghi is not None and ghi > 10.0:
            ratio = self.data.get("balcony_ghi_ratio", {}).get(q_str, 0.0)
            counter = self.data.get("balcony_ghi_ratio_counters", {}).get(q_str, 0)
            if ratio > 0.0 and counter > 0:
                return ratio * ghi

        if q_str not in self.data["balcony"]:
            return 0.0
        condition = self._get_cloud_category(cloud_cover)
        return float(self.data["balcony"][q_str].get(condition, 0.0))
