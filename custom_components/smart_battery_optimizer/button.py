"""Button platform for Smart Battery Optimizer."""
from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

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
            entities.append(DeleteProgramButton(coordinator, device_id, object_id))
            entities.append(ClearDeviceButton(coordinator, device_id, object_id))

    if entities:
        async_add_entities(entities)

class StartLearningButton(CoordinatorEntity, ButtonEntity):
    """Button to start learning a device program."""

    def __init__(self, coordinator, device_id: str, object_id: str):
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{DOMAIN}_{object_id}_learn_start"
        self._attr_name = f"{object_id.replace('_', ' ').title()} Lernen starten"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=object_id.replace('_', ' ').title(),
            manufacturer="Smart Battery Optimizer",
            model="Smart Appliance"
        )

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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=object_id.replace('_', ' ').title(),
            manufacturer="Smart Battery Optimizer",
            model="Smart Appliance"
        )

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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=object_id.replace('_', ' ').title(),
            manufacturer="Smart Battery Optimizer",
            model="Smart Appliance"
        )

    async def async_press(self) -> None:
        """Handle the button press."""
        self.coordinator.device_manager.confirm_proposed_plan(self._device_id)
        await self.coordinator.async_request_refresh()

class DeleteProgramButton(CoordinatorEntity, ButtonEntity):
    """Button to delete the currently selected program."""

    def __init__(self, coordinator, device_id: str, object_id: str):
        super().__init__(coordinator)
        self._device_id = device_id
        self._object_id = object_id
        self._attr_unique_id = f"{DOMAIN}_{object_id}_delete_program"
        self._attr_name = f"{object_id.replace('_', ' ').title()} Programm löschen"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=object_id.replace('_', ' ').title(),
            manufacturer="Smart Battery Optimizer",
            model="Smart Appliance"
        )
        self._attr_icon = "mdi:delete"

    async def async_press(self) -> None:
        """Handle the button press."""
        select_entity_id = f"select.{self._object_id}_programm"
        state = self.coordinator.hass.states.get(select_entity_id)

        if not state or state.state == "unknown" or "Keine Programme" in state.state:
            return

        program_name = state.state
        await self.coordinator.device_manager.delete_program(self._device_id, program_name)
        await self.coordinator.async_request_refresh()

class ClearDeviceButton(CoordinatorEntity, ButtonEntity):
    """Button to clear all data for a device."""

    def __init__(self, coordinator, device_id: str, object_id: str):
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{DOMAIN}_{object_id}_clear_device"
        self._attr_name = f"{object_id.replace('_', ' ').title()} Alle Daten löschen"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=object_id.replace('_', ' ').title(),
            manufacturer="Smart Battery Optimizer",
            model="Smart Appliance"
        )
        self._attr_icon = "mdi:delete-alert"

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.coordinator.device_manager.clear_device(self._device_id)
        await self.coordinator.async_request_refresh()