"""Select platform for Smart Battery Optimizer."""
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN, CONF_SMART_DEVICES

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up the select platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []

    smart_devices_str = entry.options.get(CONF_SMART_DEVICES) or entry.data.get(CONF_SMART_DEVICES, "")
    if smart_devices_str:
        smart_devices = [s.strip() for s in smart_devices_str.split(",") if s.strip()]
        for device_id in smart_devices:
            object_id = device_id.split(".")[1] if "." in device_id else device_id
            entities.append(SmartDeviceProgramSelect(coordinator, device_id, object_id))
            entities.append(SmartDeviceAlternativeTimeSelect(coordinator, device_id, object_id))

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
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=object_id.replace('_', ' ').title(),
            manufacturer="Smart Battery Optimizer",
            model="Smart Appliance"
        )
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

            top_times = await self.coordinator.async_calculate_optimal_start_times(self._device_id, option)
            if top_times:
                best_start_time, best_cost = top_times[0]
                self.coordinator.device_manager.set_proposed_device(self._device_id, option, best_start_time, best_cost)

                # Store alternatives in device manager for the Alternative Select to read
                self.coordinator.device_manager.proposed_devices[self._device_id]["alternatives"] = top_times

            await self.coordinator.async_request_refresh()

class SmartDeviceAlternativeTimeSelect(CoordinatorEntity, SelectEntity):
    """Select entity to choose an alternative start time."""

    def __init__(self, coordinator, device_id: str, object_id: str):
        """Initialize the select entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._object_id = object_id
        self._attr_unique_id = f"{DOMAIN}_{object_id}_alternative_time"
        self._attr_name = f"{object_id.replace('_', ' ').title()} Alternative Zeit"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=object_id.replace('_', ' ').title(),
            manufacturer="Smart Battery Optimizer",
            model="Smart Appliance"
        )
        self._attr_icon = "mdi:clock-fast"

    @property
    def options(self) -> list[str]:
        """Return the available top 3 times."""
        prop = self.coordinator.device_manager.proposed_devices.get(self._device_id)
        if prop and "alternatives" in prop:
            opts = []
            for i, (dt, cost) in enumerate(prop["alternatives"]):
                opts.append(f"{dt.strftime('%H:%M')} (ca. {round(cost, 2)}€)")
            return opts
        return ["Keine Vorschläge"]

    @property
    def current_option(self) -> str | None:
        """Return the currently selected proposed time."""
        prop = self.coordinator.device_manager.proposed_devices.get(self._device_id)
        if prop:
            current_dt_str = prop["start_time"].strftime('%H:%M')
            for opt in self.options:
                if opt.startswith(current_dt_str):
                    return opt
        options = self.options
        if options and "Keine Vorschläge" not in options[0]:
            return options[0]
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the proposed start time to the selected alternative."""
        if option and "Keine Vorschläge" not in option:
            prop = self.coordinator.device_manager.proposed_devices.get(self._device_id)
            if prop and "alternatives" in prop:
                # Find matching alternative
                for dt, cost in prop["alternatives"]:
                    if option.startswith(dt.strftime('%H:%M')):
                        self.coordinator.device_manager.set_proposed_device(
                            self._device_id, prop["program_name"], dt, cost
                        )
                        # Re-attach alternatives so they aren't lost
                        self.coordinator.device_manager.proposed_devices[self._device_id]["alternatives"] = prop["alternatives"]
                        break
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()