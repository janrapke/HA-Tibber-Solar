"""Select platform for Smart Battery Optimizer."""
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_SMART_DEVICES

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up the select platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = []

    smart_devices_str = entry.options.get(CONF_SMART_DEVICES) or entry.data.get(CONF_SMART_DEVICES, "")
    if smart_devices_str:
        smart_devices = [s.strip() for s in smart_devices_str.split(",") if s.strip()]
        for device_id in smart_devices:
            # We use the domain and object_id to construct a friendly name
            object_id = device_id.split(".")[1] if "." in device_id else device_id
            entities.append(SmartDeviceProgramSelect(coordinator, device_id, object_id))

    if entities:
        async_add_entities(entities)

class SmartDeviceProgramSelect(CoordinatorEntity, SelectEntity):
    """Select entity to choose a program for a smart device."""

    def __init__(self, coordinator, device_id: str, object_id: str):
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._object_id = object_id
        self._attr_unique_id = f"{DOMAIN}_{object_id}_program"
        self._attr_name = f"{object_id.replace('_', ' ').title()} Programm"

        # State
        self._current_option = None

    @property
    def options(self) -> list[str]:
        """Return the available programs."""
        programs = self.coordinator.device_manager.get_programs(self._device_id)
        if not programs:
            return ["Keine Programme (Bitte anlernen)"]
        return programs

    @property
    def current_option(self) -> str | None:
        """Return the currently selected program."""
        options = self.options
        if self._current_option in options:
            return self._current_option
        if options and "Keine Programme" not in options[0]:
            return options[0]
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option and "Keine Programme" not in option:
            self._current_option = option
            self.async_write_ha_state()

            # Recalculate optimal time and propose it
            start_time, cost = await self.coordinator.async_calculate_optimal_start_time(self._device_id, option)
            self.coordinator.device_manager.set_proposed_device(self._device_id, option, start_time, cost)

            await self.coordinator.async_request_refresh()
