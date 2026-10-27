from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .climate_ac_entities import build as build_climate
from .const import DOMAIN

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]
    entities = list(coordinator.appliance_entities.get('select', []))
    entities += build_climate("select", coordinator, config_entry.entry_id)
    if entities:
        async_add_entities(entities)
