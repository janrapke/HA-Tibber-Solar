from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CLIMATE_MAX_SLOTS

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    entities = list(coordinator.appliance_entities.get('text', []))
    for i in range(1, CLIMATE_MAX_SLOTS + 1):
        entities.append(ClimatePowerSensorText(coordinator, config_entry.entry_id, i))
    if entities:
        async_add_entities(entities)


class ClimatePowerSensorText(CoordinatorEntity, TextEntity):
    """Entity ID of the power sensor for a climate device slot.

    Can be a smart-plug power sensor (e.g. sensor.steckdose_waermepumpe_power)
    or a power sensor exposed by a WiFi climate integration
    (e.g. sensor.daikin_energy_today → convert to W, or sensor.ac_current_power).
    Leave empty to use manual wattage only.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:connection"
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 255

    def __init__(self, coordinator, entry_id, slot_number: int):
        super().__init__(coordinator)
        self._slot = slot_number
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_climate_slot_{slot_number}_power_sensor"
        self._attr_name = f"Klimagerät {slot_number} Leistungssensor (Entity ID)"
        if not hasattr(coordinator, "climate_slots_power_sensor"):
            coordinator.climate_slots_power_sensor = {}
        coordinator.climate_slots_power_sensor.setdefault(f"slot_{slot_number}", "")

    @property
    def native_value(self) -> str:
        return self.coordinator.climate_slots_power_sensor.get(f"slot_{self._slot}", "")

    async def async_set_value(self, value: str) -> None:
        self.coordinator.climate_slots_power_sensor[f"slot_{self._slot}"] = value.strip()
        self.async_write_ha_state()
