"""Number entities for Smart Battery Optimizer."""
import logging
from homeassistant.components.number import NumberEntity, NumberDeviceClass, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_MIN_SWITCH_INTERVAL_MINUTES, CONF_PRESUNNY_SOLAR_MARGIN_PCT

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the number entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    async_add_entities([
        ExtremePriceThresholdNumber(coordinator, entry.entry_id),
        ExtremePriceFactorNumber(coordinator, entry.entry_id),
        PrimaryExcessOnThreshold(coordinator, entry.entry_id),
        PrimaryExcessOffThreshold(coordinator, entry.entry_id),
        SecondaryExcessOnThreshold(coordinator, entry.entry_id),
        SecondaryExcessOffThreshold(coordinator, entry.entry_id),
        ExcessCloudToleranceNumber(coordinator, entry.entry_id),
        MinSwitchIntervalNumber(coordinator, entry.entry_id),
        LearningRateNumber(coordinator, entry.entry_id),
        GridChargeEfficiencyNumber(coordinator, entry.entry_id),
        GridChargeBufferNumber(coordinator, entry.entry_id),
        GridChargeMarginNumber(coordinator, entry.entry_id),
        PresunnySolarMarginNumber(coordinator, entry.entry_id),
        *coordinator.appliance_entities.get('number', []),
    ])

class GridChargeMarginNumber(CoordinatorEntity, NumberEntity):
    """Number entity to set the minimum profit margin for grid charging in cents."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:currency-eur"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_grid_charge_margin"
        self._attr_name = "Netzladen: Mindestgewinn (ct/kWh)"

        self._attr_native_min_value = 0.0
        self._attr_native_max_value = 25.0
        self._attr_native_step = 0.5
        self._attr_native_value = getattr(coordinator, "grid_charge_margin", 2.0)
        self.coordinator.grid_charge_margin = self._attr_native_value

    @property
    def native_value(self) -> float:
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.grid_charge_margin = value
        self.async_write_ha_state()

class GridChargeEfficiencyNumber(CoordinatorEntity, NumberEntity):
    """Number entity to set the grid charging efficiency."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:percent"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_grid_charge_efficiency"
        self._attr_name = "Netzladen: Wirkungsgrad (%)"

        self._attr_native_min_value = 50
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_native_value = getattr(coordinator, "grid_charge_efficiency", 80)
        self.coordinator.grid_charge_efficiency = self._attr_native_value

    @property
    def native_value(self) -> float:
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.grid_charge_efficiency = value
        self.async_write_ha_state()

class GridChargeBufferNumber(CoordinatorEntity, NumberEntity):
    """Number entity to set the solar safety buffer for grid charging."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:shield-sun"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_grid_charge_buffer"
        self._attr_name = "Netzladen: Solar-Sicherheitspuffer (%)"

        self._attr_native_min_value = 10
        self._attr_native_max_value = 50
        self._attr_native_step = 1
        self._attr_native_value = getattr(coordinator, "grid_charge_buffer", 25)
        self.coordinator.grid_charge_buffer = self._attr_native_value

    @property
    def native_value(self) -> float:
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.grid_charge_buffer = value
        self.async_write_ha_state()

class ExtremePriceThresholdNumber(CoordinatorEntity, NumberEntity):
    """Number entity to set the extreme price threshold."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:lightning-bolt-circle"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0.0
    _attr_native_max_value = 1.0
    _attr_native_step = 0.01

    def __init__(self, coordinator, entry_id):
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_extreme_price_threshold"
        self._attr_name = "Extrempreisgrenze (€/kWh)"
        self._attr_native_value = coordinator.extreme_price_threshold

    @property
    def native_value(self) -> float | None:
        """Return the native value."""
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        self._attr_native_value = value
        self.coordinator.extreme_price_threshold = value
        self.async_write_ha_state()


class ExtremePriceFactorNumber(CoordinatorEntity, NumberEntity):
    """Number entity to set the solar reserve factor for extreme prices."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:solar-power"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 5

    def __init__(self, coordinator, entry_id):
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_extreme_price_factor"
        self._attr_name = "Extrempreis: Solar-Reserve (%)"
        self._attr_native_value = coordinator.extreme_price_factor * 100.0

    @property
    def native_value(self) -> float | None:
        """Return the native value."""
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        self._attr_native_value = value
        self.coordinator.extreme_price_factor = value / 100.0
        self.async_write_ha_state()


class PrimaryExcessOnThreshold(CoordinatorEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:battery-arrow-up"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_primary_excess_on"
        self._attr_name = "Überschuss primär: Einschalten ab (%)"
        self._attr_native_value = getattr(coordinator, "primary_excess_on", 95.0)
        self.coordinator.primary_excess_on = self._attr_native_value

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.primary_excess_on = value
        self.async_write_ha_state()


class PrimaryExcessOffThreshold(CoordinatorEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:battery-arrow-down"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_primary_excess_off"
        self._attr_name = "Überschuss primär: Ausschalten unter (%)"
        self._attr_native_value = getattr(coordinator, "primary_excess_off", 90.0)
        self.coordinator.primary_excess_off = self._attr_native_value

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.primary_excess_off = value
        self.async_write_ha_state()


class SecondaryExcessOnThreshold(CoordinatorEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:battery-arrow-up"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_secondary_excess_on"
        self._attr_name = "Überschuss sekundär: Einschalten ab (%)"
        self._attr_native_value = getattr(coordinator, "secondary_excess_on", 98.0)
        self.coordinator.secondary_excess_on = self._attr_native_value

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.secondary_excess_on = value
        self.async_write_ha_state()


class SecondaryExcessOffThreshold(CoordinatorEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:battery-arrow-down"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_secondary_excess_off"
        self._attr_name = "Überschuss sekundär: Ausschalten unter (%)"
        self._attr_native_value = getattr(coordinator, "secondary_excess_off", 95.0)
        self.coordinator.secondary_excess_off = self._attr_native_value

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.secondary_excess_off = value
        self.async_write_ha_state()


class ExcessCloudToleranceNumber(CoordinatorEntity, NumberEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:weather-cloudy"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 60
    _attr_native_step = 1

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_excess_cloud_tolerance"
        self._attr_name = "Überschuss: Wolken-Toleranz (min)"
        self._attr_native_value = getattr(coordinator, "excess_cloud_tolerance_mins", 5.0)
        self.coordinator.excess_cloud_tolerance_mins = self._attr_native_value

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.excess_cloud_tolerance_mins = value
        self.async_write_ha_state()

class MinSwitchIntervalNumber(CoordinatorEntity, NumberEntity):
    """Minimum time between any ON/OFF state change for consumers and inverter."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:timer-outline"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 1
    _attr_native_max_value = 15
    _attr_native_step = 1

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_min_switch_interval"
        self._attr_name = "Mindest-Schaltpause (min)"
        default = coordinator.config.get(CONF_MIN_SWITCH_INTERVAL_MINUTES, 15)
        self._attr_native_value = float(default)

    @property
    def native_value(self) -> float:
        return float(self.coordinator.config.get(CONF_MIN_SWITCH_INTERVAL_MINUTES, 15))

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.config[CONF_MIN_SWITCH_INTERVAL_MINUTES] = int(value)
        self._attr_native_value = value
        self.async_write_ha_state()


class LearningRateNumber(CoordinatorEntity, NumberEntity):
    """Number entity to set the custom learning rate factor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:speedometer"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_learning_rate_factor"
        self._attr_name = "Lerngeschwindigkeit (Faktor)"

        self._attr_native_min_value = 0.1
        self._attr_native_max_value = 0.99
        self._attr_native_step = 0.01

    @property
    def native_value(self) -> float:
        """Return the current learning rate factor."""
        return self.coordinator.learning_rate_factor

    async def async_set_native_value(self, value: float) -> None:
        """Update the learning rate factor."""
        self.coordinator.learning_rate_factor = value
        self.async_write_ha_state()


class PresunnySolarMarginNumber(CoordinatorEntity, NumberEntity):
    """Sicherheitspuffer für Solar-Prognose bei der Laderaum-Vorbereitung.

    20% bedeutet: nutze nur 80% der vorhergesagten Solar-Energie für die Berechnung.
    Höherer Puffer = mehr Reserve = Batterie-Ziel liegt etwas höher (sicherer, weniger Raum).
    Niedrigerer Puffer = aggressivere Entladung = mehr Platz für Solar.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:shield-sun"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 60
    _attr_native_step = 5
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_presunny_solar_margin_pct"
        self._attr_name = "Laderaum-Vorbereitung: Solar-Puffer (%)"
        coordinator.config.setdefault(CONF_PRESUNNY_SOLAR_MARGIN_PCT, 20)

    @property
    def native_value(self) -> float:
        return float(self.coordinator.config.get(CONF_PRESUNNY_SOLAR_MARGIN_PCT, 20))

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.config[CONF_PRESUNNY_SOLAR_MARGIN_PCT] = int(value)
        self.async_write_ha_state()


