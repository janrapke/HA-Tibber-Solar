"""Switch platform for Smart Battery Optimizer."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CLIMATE_MAX_SLOTS
from .coordinator import SmartBatteryOptimizerCoordinator

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the switch platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = [
        OptimizerEnableSwitch(coordinator, entry.entry_id),
        ManualZeroExportSwitch(coordinator, entry.entry_id),
        PrimaryExcessAutoSwitch(coordinator, entry.entry_id),
        SecondaryExcessAutoSwitch(coordinator, entry.entry_id),
        EarlyExcessAutoSwitch(coordinator, entry.entry_id),
        LearningModeSwitch(coordinator, entry.entry_id),
        GridChargeEnableSwitch(coordinator, entry.entry_id),
        VacationModeSwitch(coordinator, entry.entry_id),
    ]
    for i in range(1, CLIMATE_MAX_SLOTS + 1):
        entities.append(ClimateDeviceEnabledSwitch(coordinator, entry.entry_id, i))
    async_add_entities(entities)

class GridChargeEnableSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to globally enable/disable charging battery from grid."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:battery-charging-high"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_grid_charge_enable"
        self._attr_name = "Aus Netz laden aktiv"

    @property
    def is_on(self):
        return getattr(self.coordinator, "grid_charge_enabled", False)

    async def async_turn_on(self, **kwargs):
        self.coordinator.grid_charge_enabled = True
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        self.coordinator.grid_charge_enabled = False
        await self.coordinator.async_request_refresh()

class OptimizerEnableSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable the entire automation."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:robot"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_enable"
        self._attr_name = "Smart Battery Optimizer Enabled"

    @property
    def is_on(self):
        return self.coordinator.is_enabled

    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        self.coordinator.is_enabled = True
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        self.coordinator.is_enabled = False
        self.async_write_ha_state()

class ManualZeroExportSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to force zero export mode manually."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:home-lightning-bolt"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_manual_zero_export"
        self._attr_name = "Force Zero Export"

    @property
    def is_on(self):
        return self.coordinator.manual_zero_export

    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        self.coordinator.manual_zero_export = True
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        self.coordinator.manual_zero_export = False
        await self.coordinator.async_request_refresh()

class PrimaryExcessAutoSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable automatic control of primary excess consumers."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:pool"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_primary_excess_auto"
        self._attr_name = "Primäre Überschuss-Automatik"

    @property
    def is_on(self):
        return getattr(self.coordinator, "primary_excess_auto", True)

    async def async_turn_on(self, **kwargs):
        self.coordinator.primary_excess_auto = True
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        self.coordinator.primary_excess_auto = False
        await self.coordinator.async_request_refresh()

class SecondaryExcessAutoSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable automatic control of secondary excess consumers."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:bitcoin"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_secondary_excess_auto"
        self._attr_name = "Sekundäre Überschuss-Automatik"

    @property
    def is_on(self):
        return getattr(self.coordinator, "secondary_excess_auto", True)

    async def async_turn_on(self, **kwargs):
        self.coordinator.secondary_excess_auto = True
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        self.coordinator.secondary_excess_auto = False
        await self.coordinator.async_request_refresh()

class EarlyExcessAutoSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable automatic control of early excess consumers."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-fast"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_early_excess_auto"
        self._attr_name = "Frühzeitige Überschuss-Automatik"

    @property
    def is_on(self):
        return getattr(self.coordinator, "early_excess_auto", True)

    async def async_turn_on(self, **kwargs):
        self.coordinator.early_excess_auto = True
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        self.coordinator.early_excess_auto = False
        await self.coordinator.async_request_refresh()


class LearningModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to start/stop the fast learning mode."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:brain"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_learning_mode"
        self._attr_name = "Lernmodus (Dauerhaft)"

    @property
    def is_on(self):
        return self.coordinator.is_learning_mode_active

    @property
    def extra_state_attributes(self):
        """Expose learning coverage and recommendation as attributes."""
        coverage = self.coordinator.learning_engine.get_learning_coverage()
        weeks = coverage["estimated_weeks_remaining"]
        if weeks == 0:
            hint = "Lerndaten vollständig — Lernmodus kann deaktiviert werden."
        else:
            hint = (
                f"Empfehlung: Lernmodus noch ca. {weeks} Woche(n) aktiv lassen "
                f"({coverage['coverage_pct']:.0f}% der Zeitslots stabil)."
            )
        return {
            "coverage_pct": coverage["coverage_pct"],
            "estimated_weeks_remaining": weeks,
            "hinweis": hint,
        }

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_start_learning_mode()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_stop_learning_mode()
        await self.coordinator.async_request_refresh()


class VacationModeSwitch(CoordinatorEntity, SwitchEntity):
    """Pause all learning while on vacation — prevents the model from learning wrong patterns."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:airplane-takeoff"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_vacation_mode"
        self._attr_name = "Urlaubsmodus (Lernen pausieren)"

    @property
    def is_on(self):
        return self.coordinator.learning_engine.is_vacation_mode_active()

    async def async_turn_on(self, **kwargs):
        self.coordinator.learning_engine.set_vacation_mode(True)
        await self.coordinator.learning_engine.async_save()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.coordinator.learning_engine.set_vacation_mode(False)
        await self.coordinator.learning_engine.async_save()
        self.async_write_ha_state()


class ClimateDeviceEnabledSwitch(CoordinatorEntity, SwitchEntity):
    """Enable/disable a climate device slot in consumption predictions.

    Disable when the physical device is switched off for the season (e.g. AC in winter)
    so the optimizer does not forecast load that won't materialize.
    The learned model is preserved — re-enabling picks up where it left off.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:heat-pump"

    def __init__(self, coordinator, entry_id, slot_number: int):
        super().__init__(coordinator)
        self._slot = slot_number
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_climate_slot_{slot_number}_enabled"
        self._attr_name = f"Klimagerät {slot_number} aktiv"
        # Disabled by default — the user must explicitly turn on slots they've configured
        if not hasattr(coordinator, "climate_slots_enabled"):
            coordinator.climate_slots_enabled = {}
        coordinator.climate_slots_enabled.setdefault(f"slot_{slot_number}", False)

    @property
    def is_on(self):
        return self.coordinator.climate_slots_enabled.get(f"slot_{self._slot}", False)

    async def async_turn_on(self, **kwargs):
        self.coordinator.climate_slots_enabled[f"slot_{self._slot}"] = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.coordinator.climate_slots_enabled[f"slot_{self._slot}"] = False
        self.async_write_ha_state()
