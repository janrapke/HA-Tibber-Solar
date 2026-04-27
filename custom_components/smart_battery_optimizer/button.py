from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_SMART_DEVICES
from .appliance_entities import ApplianceRecordButton, ApplianceConfirmButton, ApplianceProgramSelect, ApplianceProposalSelect

from .coordinator import SmartBatteryOptimizerCoordinator

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the button entities."""
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    buttons = [
        ResetLearningDataButton(coordinator, config_entry)
    ]

    # Add Smart Appliance Buttons
    smart_devices_str = coordinator.config.get(CONF_SMART_DEVICES, "")
    if smart_devices_str:
        devices = [d.strip() for d in smart_devices_str.split(",") if d.strip()]
        for dev in devices:
            # Note: Select entities are created in select.py, we need a way to link them.
            # For simplicity in this architecture, we will instantiate the Selects here
            # or rely on HA's entity registry.
            # Actually, to pass references, we should create all appliance entities in a single file and return them,
            # but since HA requires platforms to be separated, we can use the coordinator to hold references to the Selects.
            pass


    app_entities = coordinator.appliance_entities.get('button', [])
    if app_entities:
        async_add_entities(app_entities)

    async_add_entities(buttons)


class ResetLearningDataButton(CoordinatorEntity, ButtonEntity):
    """Button to completely reset the stored learning data to priors."""

    def __init__(self, coordinator: SmartBatteryOptimizerCoordinator, config_entry: ConfigEntry):
        """Initialize the button."""
        super().__init__(coordinator)
        self.config_entry = config_entry
        self._attr_has_entity_name = True
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
