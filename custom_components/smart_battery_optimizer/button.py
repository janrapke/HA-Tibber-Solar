"""Button platform for Smart Battery Optimizer."""
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_SMART_DEVICES

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up the button platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []

    smart_devices_str = entry.options.get(CONF_SMART_DEVICES) or entry.data.get(CONF_SMART_DEVICES, "")
    if smart_devices_str:
        smart_devices = [s.strip() for s in smart_devices_str.split(",") if s.strip()]
        for device_id in smart_devices:
            object_id = device_id.split(".")[1] if "." in device_id else device_id
            entities.append(StartLearningButton(coordinator, device_id, object_id))
            entities.append(StopLearningButton(coordinator, device_id, object_id))
            entities.append(ConfirmPlanButton(coordinator, device_id, object_id))

    if entities:
        async_add_entities(entities)

class StartLearningButton(CoordinatorEntity, ButtonEntity):
    """Button to start learning a device program."""

    def __init__(self, coordinator, device_id: str, object_id: str):
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{DOMAIN}_{object_id}_learn_start"
        self._attr_name = f"{object_id.replace('_', ' ').title()} Lernen starten"

    async def async_press(self) -> None:
        """Handle the button press."""
        self.coordinator.device_manager.start_learning(self._device_id)
        await self.coordinator.async_request_refresh()

class StopLearningButton(CoordinatorEntity, ButtonEntity):
    """Button to stop learning a device program."""

    def __init__(self, coordinator, device_id: str, object_id: str):
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{DOMAIN}_{object_id}_learn_stop"
        self._attr_name = f"{object_id.replace('_', ' ').title()} Lernen beenden"

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.device_manager.stop_learning(self._device_id)
        await self.coordinator.async_request_refresh()

class ConfirmPlanButton(CoordinatorEntity, ButtonEntity):
    """Button to confirm the planned program."""

    def __init__(self, coordinator, device_id: str, object_id: str):
        super().__init__(coordinator)
        self._device_id = device_id
        self._object_id = object_id
        self._attr_unique_id = f"{DOMAIN}_{object_id}_confirm_plan"
        self._attr_name = f"{object_id.replace('_', ' ').title()} Plan bestätigen"

    async def async_press(self) -> None:
        """Handle the button press."""
        self.coordinator.device_manager.confirm_proposed_plan(self._device_id)
        await self.coordinator.async_request_refresh()