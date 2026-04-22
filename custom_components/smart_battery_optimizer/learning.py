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
        }

        self._current_quarter_consumption_acc = 0.0
        self._current_quarter_consumption_count = 0
        self._current_quarter_solar_acc = 0.0
        self._current_quarter_solar_count = 0
        self._last_quarter_processed = -1

        # Initialize default structures for 96 quarters (24h * 4)
        for q in range(96):
            self.data["consumption"][str(q)] = 0.0
            self.data["solar"][str(q)] = {
                "clear": 0.0,    # 0-33% cloud cover
                "partly": 0.0,   # 34-66% cloud cover
                "cloudy": 0.0,   # 67-100% cloud cover
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

        for q in range(96):
            self.data["consumption"][str(q)] = base_load_wh_15min

            # Calculate time of day (0.0 to 24.0)
            hour_of_day = q / 4.0

            # Simple bell curve for solar between 6am and 6pm (12h duration)
            solar_wh_15min = 0.0
            if 6.0 <= hour_of_day <= 18.0:
                # Map 6..18 to -pi/2 .. pi/2 for cosine curve
                normalized_time = ((hour_of_day - 6.0) / 12.0) * math.pi - (math.pi / 2)
                # Cosine curve scaled to peak power, divided by 4 for 15-min Wh
                solar_wh_15min = math.cos(normalized_time) * (solar_peak_w / 4.0)

            self.data["solar"][str(q)]["clear"] = solar_wh_15min
            self.data["solar"][str(q)]["partly"] = solar_wh_15min * 0.5
            self.data["solar"][str(q)]["cloudy"] = solar_wh_15min * 0.2

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

    async def finalize_quarter(self, quarter: int, cloud_cover: float):
        """Called once at the end of a 15-min interval to calculate the average Wh and apply EMA."""
        q_str = str(quarter)
        alpha = 0.1  # Learning rate 10%

        # Finalize consumption
        if self._current_quarter_consumption_count > 0:
            # Average power in W over 15 min equals energy in Wh for that 15 min if divided by 4
            avg_power = self._current_quarter_consumption_acc / self._current_quarter_consumption_count
            quarter_wh = avg_power / 4.0
            current_avg = self.data["consumption"][q_str]
            self.data["consumption"][q_str] = (alpha * quarter_wh) + ((1 - alpha) * current_avg)

            self._current_quarter_consumption_acc = 0.0
            self._current_quarter_consumption_count = 0

        # Finalize solar
        if self._current_quarter_solar_count > 0:
            avg_power = self._current_quarter_solar_acc / self._current_quarter_solar_count
            quarter_wh = avg_power / 4.0
            condition = self._get_cloud_category(cloud_cover)
            current_avg = self.data["solar"][q_str][condition]

            self.data["solar"][q_str][condition] = (alpha * quarter_wh) + ((1 - alpha) * current_avg)

            self._current_quarter_solar_acc = 0.0
            self._current_quarter_solar_count = 0

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
