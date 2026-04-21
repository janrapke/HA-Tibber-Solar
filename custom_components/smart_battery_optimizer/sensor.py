"""Sensor platform for Smart Battery Optimizer."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartBatteryOptimizerCoordinator

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = [
        CalculatedConsumptionSensor(coordinator, entry.entry_id),
        PredictedRemainingSolarSensor(coordinator, entry.entry_id),
        PredictedRemainingConsumptionSensor(coordinator, entry.entry_id),
        TargetLimitSensor(coordinator, entry.entry_id),
    ]
    async_add_entities(entities)

class CalculatedConsumptionSensor(CoordinatorEntity, SensorEntity):
    """Sensor for true calculated house consumption."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_calculated_house_consumption"
        self._attr_name = "Calculated House Consumption"

    @property
    def native_value(self):
        if self.coordinator.data:
            return round(self.coordinator.data.get("calculated_house_consumption", 0), 1)
        return None

class PredictedRemainingSolarSensor(CoordinatorEntity, SensorEntity):
    """Sensor for predicted remaining solar generation."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = "Wh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_predicted_remaining_solar"
        self._attr_name = "Predicted Remaining Solar (Today)"

    @property
    def native_value(self):
        if self.coordinator.data:
            return round(self.coordinator.data.get("predicted_remaining_solar", 0), 0)
        return None

class PredictedRemainingConsumptionSensor(CoordinatorEntity, SensorEntity):
    """Sensor for predicted remaining consumption."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = "Wh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_predicted_remaining_consumption"
        self._attr_name = "Predicted Remaining Consumption (Today)"

    @property
    def native_value(self):
        if self.coordinator.data:
            return round(self.coordinator.data.get("predicted_remaining_consumption", 0), 0)
        return None

class TargetLimitSensor(CoordinatorEntity, SensorEntity):
    """Sensor showing the current target limit set to OpenDTU."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_target_limit"
        self._attr_name = "Target OpenDTU Limit"

    @property
    def native_value(self):
        if self.coordinator.data:
            return round(self.coordinator.data.get("target_limit", 0), 1)
        return None
