"""Text platform for Smart Battery Optimizer."""
from homeassistant.components.text import TextEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, CONF_SMART_DEVICES

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up the text platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []

    smart_devices_str = entry.options.get(CONF_SMART_DEVICES) or entry.data.get(CONF_SMART_DEVICES, "")
    if smart_devices_str:
        smart_devices = [s.strip() for s in smart_devices_str.split(",") if s.strip()]
        for device_id in smart_devices:
            object_id = device_id.split(".")[1] if "." in device_id else device_id
            entities.append(SmartDeviceProgramNameText(coordinator, device_id, object_id))

    if entities:
        async_add_entities(entities)

class SmartDeviceProgramNameText(CoordinatorEntity, TextEntity):
    """Text entity to rename the currently selected program."""

    def __init__(self, coordinator, device_id: str, object_id: str):
        super().__init__(coordinator)
        self._device_id = device_id
        self._object_id = object_id
        self._attr_unique_id = f"{DOMAIN}_{object_id}_program_name"
        self._attr_name = f"{object_id.replace('_', ' ').title()} Programm umbenennen"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=object_id.replace('_', ' ').title(),
            manufacturer="Smart Battery Optimizer",
            model="Smart Appliance"
        )
        self._attr_icon = "mdi:form-textbox"

    @property
    def native_value(self) -> str | None:
        """Return the name of the currently selected program."""
        select_entity_id = f"select.{self._object_id}_programm"
        state = self.coordinator.hass.states.get(select_entity_id)

        if state and state.state and state.state != "unknown" and "Keine Programme" not in state.state:
            return state.state
        return ""

    async def async_set_value(self, value: str) -> None:
        """Rename the program."""
        if not value:
            return

        old_name = self.native_value
        if old_name and old_name != value:
            await self.coordinator.device_manager.rename_program(self._device_id, old_name, value)

            # Since the name changed, we should trigger the select entity to update
            select_entity_id = f"select.{self._object_id}_programm"
            select_state = self.coordinator.hass.states.get(select_entity_id)
            if select_state:
                # Force select entity to update to new value
                await self.coordinator.hass.services.async_call("select", "select_option", {"entity_id": select_entity_id, "option": value}, blocking=False)

            await self.coordinator.async_request_refresh()