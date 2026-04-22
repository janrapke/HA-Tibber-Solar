"""Learning and Prediction Engine for Smart Battery Optimizer."""
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "smart_battery_optimizer.learning_data"
STORAGE_VERSION = 1

class LearningEngine:
    """Class to manage learning of consumption and solar generation."""

    def __init__(self, hass: HomeAssistant):
        """Initialize the learning engine."""
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: Dict[str, Any] = {
            "consumption": {},  # Format: {"hour": average_consumption_w}
            "solar": {},        # Format: {"hour": {"cloud_cover_range": average_solar_w}}
        }

        self._current_hour_consumption_acc = 0.0
        self._current_hour_consumption_count = 0
        self._current_hour_solar_acc = 0.0
        self._current_hour_solar_count = 0
        self._last_hour_processed = -1

        # Initialize default structures
        for hour in range(24):
            self.data["consumption"][str(hour)] = 0.0
            self.data["solar"][str(hour)] = {
                "clear": 0.0,    # 0-33% cloud cover
                "partly": 0.0,   # 34-66% cloud cover
                "cloudy": 0.0,   # 67-100% cloud cover
            }

    async def async_load(self):
        """Load historical data from storage."""
        stored_data = await self._store.async_load()
        if stored_data:
            # Simple merge to ensure new keys exist if schema changes
            if "consumption" in stored_data:
                for hour, val in stored_data["consumption"].items():
                    if hour in self.data["consumption"]:
                        self.data["consumption"][hour] = val
            if "solar" in stored_data:
                for hour, conditions in stored_data["solar"].items():
                    if hour in self.data["solar"]:
                        for condition, val in conditions.items():
                            if condition in self.data["solar"][hour]:
                                self.data["solar"][hour][condition] = val
            _LOGGER.debug("Loaded learning data: %s", self.data)

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

    async def record_consumption(self, current_hour: int, power_w: float):
        """Accumulate real house consumption data for the current hour."""
        if power_w < 0:
            return # Ignore invalid values

        self._current_hour_consumption_acc += power_w
        self._current_hour_consumption_count += 1

    async def record_solar(self, current_hour: int, power_w: float, cloud_cover: float):
        """Accumulate solar production data for the current hour."""
        if power_w < 0:
            return

        self._current_hour_solar_acc += power_w
        self._current_hour_solar_count += 1

    async def finalize_hour(self, hour: int, cloud_cover: float):
        """Called once at the end of an hour to calculate the average Wh and apply EMA."""
        hour_str = str(hour)
        alpha = 0.1  # Learning rate 10%

        # Finalize consumption
        if self._current_hour_consumption_count > 0:
            # Average power in W over the hour equals energy in Wh for that hour
            hourly_wh = self._current_hour_consumption_acc / self._current_hour_consumption_count
            current_avg = self.data["consumption"][hour_str]
            if current_avg == 0:
                self.data["consumption"][hour_str] = hourly_wh
            else:
                self.data["consumption"][hour_str] = (alpha * hourly_wh) + ((1 - alpha) * current_avg)

            self._current_hour_consumption_acc = 0.0
            self._current_hour_consumption_count = 0

        # Finalize solar
        if self._current_hour_solar_count > 0:
            hourly_wh = self._current_hour_solar_acc / self._current_hour_solar_count
            condition = self._get_cloud_category(cloud_cover)
            current_avg = self.data["solar"][hour_str][condition]

            if current_avg == 0:
                self.data["solar"][hour_str][condition] = hourly_wh
            else:
                self.data["solar"][hour_str][condition] = (alpha * hourly_wh) + ((1 - alpha) * current_avg)

            self._current_hour_solar_acc = 0.0
            self._current_hour_solar_count = 0

    def predict_consumption_for_hour(self, hour: int) -> float:
        """Predict consumption (Wh) for a specific hour based on learned data."""
        return float(self.data["consumption"].get(str(hour), 0.0))

    def predict_solar_for_hour(self, hour: int, cloud_cover: float) -> float:
        """Predict solar generation (Wh) for a specific hour and cloud cover based on learned data."""
        hour_str = str(hour)
        if hour_str not in self.data["solar"]:
            return 0.0

        condition = self._get_cloud_category(cloud_cover)
        return float(self.data["solar"][hour_str].get(condition, 0.0))

    def predict_remaining_consumption(self, current_hour: int) -> float:
        """Predict total remaining consumption (Wh) for the rest of the day."""
        remaining_wh = 0.0
        for h in range(current_hour, 24):
            # The average power in W equals the energy in Wh for 1 hour
            remaining_wh += self.predict_consumption_for_hour(h)
        return remaining_wh

    def predict_remaining_solar(self, current_hour: int, hourly_forecasts: List[dict]) -> float:
        """
        Predict remaining solar generation (Wh) for the rest of the day based on forecasts.
        forecasts should be a list of dicts: [{'hour': 14, 'cloud_cover': 20}, ...]
        """
        remaining_wh = 0.0

        # Create a dictionary for quick lookup of forecasted cloud cover
        forecast_dict = {f.get('hour'): f.get('cloud_cover', 50) for f in hourly_forecasts}

        for h in range(current_hour, 24):
            # Default to 'partly' if no forecast is available for that hour
            cloud_cover = forecast_dict.get(h, 50)

            # Add predicted generation for this hour
            remaining_wh += self.predict_solar_for_hour(h, cloud_cover)

        return remaining_wh
