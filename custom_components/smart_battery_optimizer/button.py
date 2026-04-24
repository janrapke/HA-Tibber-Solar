from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmartBatteryOptimizerCoordinator

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the button entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]

    buttons = [
        ResetLearningDataButton(coordinator, config_entry)
    ]
    async_add_entities(buttons)

class ResetLearningDataButton(CoordinatorEntity, ButtonEntity):
    """Button to completely reset the stored learning data to priors."""

    def __init__(self, coordinator: SmartBatteryOptimizerCoordinator, config_entry: ConfigEntry):
        """Initialize the button."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._attr_name = "Reset Learning Data"
        self._attr_unique_id = f"{config_entry.entry_id}_reset_learning_data"
        self._attr_icon = "mdi:database-refresh-outline"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, config_entry.entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.learning_engine.hard_reset_data()
        await self.coordinator.async_request_refresh()
