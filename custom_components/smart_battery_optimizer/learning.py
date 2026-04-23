"""Learning and Prediction Engine for Smart Battery Optimizer."""
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
import math

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import CONF_BASE_LOAD_W, CONF_SOLAR_PEAK_W

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
            "learning_mode_end_time": None
        }

        self._current_quarter_consumption_acc = 0.0
        self._current_quarter_consumption_count = 0
        self._current_quarter_solar_acc = 0.0
        self._current_quarter_solar_count = 0
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

            if "learning_mode_end_time" in stored_data:
                self.data["learning_mode_end_time"] = stored_data["learning_mode_end_time"]

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

    def reset_learning_counters(self):
        """Reset all learning counters to 0 and set learning mode end time to 7 days from now."""
        for q in range(96):
            q_str = str(q)
            self.data["counters"]["consumption"][q_str] = 0
            for cond in ["clear", "partly", "cloudy"]:
                self.data["counters"]["solar"][q_str][cond] = 0
                self.data["counters"]["balcony"][q_str][cond] = 0

        self.data["learning_mode_end_time"] = (datetime.now() + timedelta(days=7)).isoformat()
        _LOGGER.info("Learning mode reset. Counters set to 0. Mode active until %s", self.data["learning_mode_end_time"])

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
        """Calculate dynamic alpha based on counter (0=0.7, 1=0.6... 6+=0.1)."""
        if counter >= 6:
            return 0.1
        return max(0.7 - (0.1 * counter), 0.1)

    async def finalize_quarter(self, quarter: int, cloud_cover: float):
        """Called once at the end of a 15-min interval to calculate the average Wh and apply EMA."""
        q_str = str(quarter)

        # Finalize consumption
        if self._current_quarter_consumption_count > 0:
            counter = self.data["counters"]["consumption"][q_str]
            alpha = self._calculate_alpha(counter)

            # Average power in W over 15 min equals energy in Wh for that 15 min if divided by 4
            avg_power = self._current_quarter_consumption_acc / self._current_quarter_consumption_count
            quarter_wh = avg_power / 4.0
            current_avg = self.data["consumption"][q_str]
            self.data["consumption"][q_str] = (alpha * quarter_wh) + ((1 - alpha) * current_avg)

            if counter < 6:
                self.data["counters"]["consumption"][q_str] = counter + 1

            self._current_quarter_consumption_acc = 0.0
            self._current_quarter_consumption_count = 0

        # Finalize solar
        if self._current_quarter_solar_count > 0:
            condition = self._get_cloud_category(cloud_cover)
            counter = self.data["counters"]["solar"][q_str][condition]
            alpha = self._calculate_alpha(counter)

            avg_power = self._current_quarter_solar_acc / self._current_quarter_solar_count
            quarter_wh = avg_power / 4.0
            current_avg = self.data["solar"][q_str][condition]

            self.data["solar"][q_str][condition] = (alpha * quarter_wh) + ((1 - alpha) * current_avg)

            if counter < 6:
                self.data["counters"]["solar"][q_str][condition] = counter + 1

            self._current_quarter_solar_acc = 0.0
            self._current_quarter_solar_count = 0

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

            self._current_quarter_balcony_acc = 0.0
            self._current_quarter_balcony_count = 0

    def predict_consumption_for_quarter(self, quarter: int) -> float:
        """Predict consumption (Wh) for a specific 15-min interval based on learned data."""
        return float(self.data["consumption"].get(str(quarter), 0.0))

    def predict_solar_for_quarter(self, quarter: int, cloud_cover: float) -> float:
        """Predict solar generation (Wh) for a specific 15-min interval and cloud cover based on learned data."""
        q_str = str(quarter)
        if q_str not in self.data["solar"]:
            return 0.0

        condition = self._get_cloud_category(cloud_cover)
        return float(self.data["solar"][q_str].get(condition, 0.0))

    def predict_balcony_for_quarter(self, quarter: int, cloud_cover: float) -> float:
        """Predict balcony solar generation (Wh) for a specific 15-min interval and cloud cover based on learned data."""
        q_str = str(quarter)
        if q_str not in self.data["balcony"]:
            return 0.0

        condition = self._get_cloud_category(cloud_cover)
        return float(self.data["balcony"][q_str].get(condition, 0.0))
