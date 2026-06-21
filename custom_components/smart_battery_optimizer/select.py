from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, CLIMATE_MAX_SLOTS,
    CLIMATE_TYPE_DISABLED, CLIMATE_TYPE_HEATING,
    CLIMATE_TYPE_COOLING, CLIMATE_TYPE_HEAT_PUMP,
)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    entities = list(coordinator.appliance_entities.get('select', []))
    for i in range(1, CLIMATE_MAX_SLOTS + 1):
        entities.append(ClimateDeviceTypeSelect(coordinator, config_entry.entry_id, i))
    if entities:
        async_add_entities(entities)


_CLIMATE_TYPE_OPTIONS = [
    CLIMATE_TYPE_DISABLED,
    CLIMATE_TYPE_HEATING,
    CLIMATE_TYPE_COOLING,
    CLIMATE_TYPE_HEAT_PUMP,
]

# Map display labels to internal type strings used by the learning engine
_LABEL_TO_INTERNAL = {
    CLIMATE_TYPE_DISABLED: "disabled",
    CLIMATE_TYPE_HEATING: "heating",
    CLIMATE_TYPE_COOLING: "cooling",
    CLIMATE_TYPE_HEAT_PUMP: "heat_pump",
}
_INTERNAL_TO_LABEL = {v: k for k, v in _LABEL_TO_INTERNAL.items()}


class ClimateDeviceTypeSelect(CoordinatorEntity, SelectEntity):
    """Select the type of a climate device slot.

    Determines which temperature delta direction is used for learning and prediction:
    - Nur Heizen: runs when outdoor temp < heating setpoint
    - Nur Kühlen: runs when outdoor temp > cooling setpoint
    - Wärmepumpe: handles both modes independently
    - Deaktiviert: slot is ignored entirely
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:heat-pump-outline"

    def __init__(self, coordinator, entry_id, slot_number: int):
        super().__init__(coordinator)
        self._slot = slot_number
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Smart Battery Optimizer",
            "manufacturer": "Custom",
        }
        self._attr_unique_id = f"{entry_id}_climate_slot_{slot_number}_type"
        self._attr_name = f"Klimagerät {slot_number} Typ"
        self._attr_options = _CLIMATE_TYPE_OPTIONS
        if not hasattr(coordinator, "climate_slots_type"):
            coordinator.climate_slots_type = {}
        coordinator.climate_slots_type.setdefault(f"slot_{slot_number}", "disabled")

    @property
    def current_option(self) -> str:
        internal = self.coordinator.climate_slots_type.get(f"slot_{self._slot}", "disabled")
        return _INTERNAL_TO_LABEL.get(internal, CLIMATE_TYPE_DISABLED)

    async def async_select_option(self, option: str) -> None:
        internal = _LABEL_TO_INTERNAL.get(option, "disabled")
        self.coordinator.climate_slots_type[f"slot_{self._slot}"] = internal

        # Bootstrap the learning model immediately when a type is selected
        manual_w = getattr(self.coordinator, "climate_slots_manual_w", {}).get(f"slot_{self._slot}", 0.0)
        if internal != "disabled" and manual_w > 0:
            self.coordinator.learning_engine.bootstrap_climate_slot(
                f"slot_{self._slot}", manual_w, internal
            )
            await self.coordinator.learning_engine.async_save()

        self.async_write_ha_state()
