"""Switch platform for Smart Battery Optimizer."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartBatteryOptimizerCoordinator

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the switch platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = [
        OptimizerEnableSwitch(coordinator, entry.entry_id),
        ManualZeroExportSwitch(coordinator, entry.entry_id),
    ]
    async_add_entities(entities)

class OptimizerEnableSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable the entire automation."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:robot"

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator)
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
