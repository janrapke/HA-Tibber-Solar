"""Number entities for Smart Battery Optimizer."""
import logging
from homeassistant.components.number import NumberEntity, NumberDeviceClass, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

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
    ])


class ExtremePriceThresholdNumber(CoordinatorEntity, NumberEntity):
    """Number entity to set the extreme price threshold."""

    _attr_has_entity_name = True
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
        self._attr_name = "Extrem-Preis Schwelle (€)"
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
        self._attr_name = "Extrem-Preis Solar-Reserve Faktor (%)"
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
        self._attr_name = "Primär Überschuss Ein (%)"
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
        self._attr_name = "Primär Überschuss Aus (%)"
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
        self._attr_name = "Sekundär Überschuss Ein (%)"
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
        self._attr_name = "Sekundär Überschuss Aus (%)"
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
        self._attr_name = "Überschuss Wolken-Toleranz (Minuten)"
        self._attr_native_value = getattr(coordinator, "excess_cloud_tolerance_mins", 5.0)
        self.coordinator.excess_cloud_tolerance_mins = self._attr_native_value

    @property
    def native_value(self) -> float | None:
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.coordinator.excess_cloud_tolerance_mins = value
        self.async_write_ha_state()
