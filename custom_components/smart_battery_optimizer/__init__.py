"""The Smart Battery Optimizer integration."""
import logging
import pathlib

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "switch", "number", "button", "select", "text"]

from .coordinator import SmartBatteryOptimizerCoordinator

_WWW = pathlib.Path(__file__).parent / "www"
_CARDS = [
    ("appliance-card.js", "/smart_battery_optimizer/appliance-card.js"),
    ("battery-forecast-card.js", "/smart_battery_optimizer/battery-forecast-card.js"),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Battery Optimizer from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Register custom Lovelace cards (once per HA instance)
    if not hass.data[DOMAIN].get("_cards_registered"):
        for filename, url in _CARDS:
            hass.http.register_static_path(url, str(_WWW / filename), cache_headers=False)
            add_extra_js_url(hass, url)
        hass.data[DOMAIN]["_cards_registered"] = True

    # Store config entry data
    hass.data[DOMAIN][entry.entry_id] = {}

    config = dict(entry.data)
    if entry.options:
        config.update(entry.options)

    coordinator = SmartBatteryOptimizerCoordinator(hass, config)
    await coordinator._async_setup()
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id]["coordinator"] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True

async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    return True
