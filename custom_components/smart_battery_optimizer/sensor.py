"""Sensor platform for Smart Battery Optimizer."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import EntityCategory

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
        CurrentOperatingModeSensor(coordinator, entry.entry_id),
        ForecastPlanSensor(coordinator, entry.entry_id),
        # Diagnostic Sensors
        DiagCurrentPriceSensor(coordinator, entry.entry_id),
        DiagCurrentBatterySensor(coordinator, entry.entry_id),
        DiagCurrentSolarSensor(coordinator, entry.entry_id),
        DiagCurrentGridConsumptionSensor(coordinator, entry.entry_id),
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
    _attr_state_class = None

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
    _attr_state_class = None

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_predicted_remaining_consumption"
        self._attr_name = "Predicted Remaining Consumption (Today)"

    @property
    def native_value(self):
        if self.coordinator.data:
            return round(self.coordinator.data.get("predicted_remaining_consumption", 0), 0)
        return None

class CurrentOperatingModeSensor(CoordinatorEntity, SensorEntity):
    """Text sensor showing what the integration is currently doing."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_current_operating_mode"
        self._attr_name = "Aktueller Betriebsmodus"

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("current_operating_mode", "Unbekannt")
        return "Initialisiere..."

class ForecastPlanSensor(CoordinatorEntity, SensorEntity):
    """Sensor containing the hourly plan for the rest of the day in attributes."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_forecast_plan"
        self._attr_name = "Tagesplan Vorhersage"

    @property
    def native_value(self):
        """Return the number of hours planned."""
        if self.coordinator.data:
            plan = self.coordinator.data.get("hourly_plan", [])
            return f"{len(plan)} Stunden geplant"
        return "Kein Plan"

    @property
    def extra_state_attributes(self):
        """Return the plan as a list in attributes for use in cards like ApexCharts."""
        if self.coordinator.data:
            return {"hourly_plan": self.coordinator.data.get("hourly_plan", [])}
        return {}

# ==========================================
# DIAGNOSTIC SENSORS (Mirrored inputs)
# ==========================================

class DiagCurrentPriceSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor mirroring the current price used internally."""
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "EUR/kWh" # Or whatever is standard
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_diag_current_price"
        self._attr_name = "Interner aktueller Tibber Preis"

    @property
    def native_value(self):
        if self.coordinator.data:
            price = self.coordinator.data.get("current_price")
            if price is not None:
                return round(price, 4)
        return None

class DiagCurrentBatterySensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor mirroring the current battery percent."""
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_diag_current_battery"
        self._attr_name = "Interner Batterie Stand"

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("current_battery")
        return None

class DiagCurrentSolarSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor mirroring the current solar input."""
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_diag_current_solar"
        self._attr_name = "Interne Solar Leistung"

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("current_solar")
        return None

class DiagCurrentGridConsumptionSensor(CoordinatorEntity, SensorEntity):
    """Diagnostic sensor mirroring the grid consumption."""
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_diag_current_grid"
        self._attr_name = "Interner Netzbezug (Tibber)"

    @property
    def native_value(self):
        if self.coordinator.data:
            return self.coordinator.data.get("current_consumption")
        return None
