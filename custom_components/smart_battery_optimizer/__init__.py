"""The Smart Battery Optimizer integration."""
import logging
import pathlib

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "switch", "number", "button", "select", "text"]

from .coordinator import SmartBatteryOptimizerCoordinator

_WWW = pathlib.Path(__file__).parent / "www"
_CARD_FILES = [
    ("appliance-card.js", "/smart_battery_optimizer/appliance-card.js"),
    ("battery-forecast-card.js", "/smart_battery_optimizer/battery-forecast-card.js"),
]
_CARD_RESOURCE_URLS = [url for _, url in _CARD_FILES]


async def _register_static_paths(hass: HomeAssistant) -> None:
    """Register static file paths, compatible with old and new HA APIs."""
    try:
        from homeassistant.components.http import StaticPathConfig
        await hass.http.async_register_static_paths([
            StaticPathConfig(url, str(_WWW / filename), cache_headers=False)
            for filename, url in _CARD_FILES
        ])
    except (ImportError, AttributeError):
        for filename, url in _CARD_FILES:
            hass.http.register_static_path(url, str(_WWW / filename), cache_headers=False)


async def _ensure_lovelace_resources(hass: HomeAssistant) -> None:
    """Register card JS as Lovelace resources (like HACS — survives restarts, reliable)."""
    try:
        from homeassistant.components.lovelace.resources import ResourceStorageCollection

        # Reuse HA's already-loaded collection if available (avoids duplicate store handles)
        resources = hass.data.get("lovelace", {}).get("resources")
        if not isinstance(resources, ResourceStorageCollection):
            try:
                resources = ResourceStorageCollection(hass, "core")
            except TypeError:
                resources = ResourceStorageCollection(hass)
            await resources.async_load()

        existing = {item["url"] for item in resources.async_items()}
        for url in _CARD_RESOURCE_URLS:
            if url not in existing:
                await resources.async_create_item({"res_type": "module", "url": url})
                _LOGGER.info("Lovelace resource registered: %s", url)
            else:
                _LOGGER.debug("Lovelace resource already present: %s", url)
    except Exception as err:
        _LOGGER.warning(
            "Auto-registration of Lovelace resources failed (%s). "
            "Falling back to add_extra_js_url. If cards still don't load, add manually in "
            "Settings → Dashboards → Resources (type JavaScript-Modul): %s",
            err,
            ", ".join(_CARD_RESOURCE_URLS),
        )
        try:
            from homeassistant.components.frontend import add_extra_js_url
            for url in _CARD_RESOURCE_URLS:
                add_extra_js_url(hass, url)
        except Exception:
            pass


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Battery Optimizer from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Register custom Lovelace cards (once per HA instance, not once per config entry reload)
    if not hass.data[DOMAIN].get("_cards_registered"):
        await _register_static_paths(hass)
        await _ensure_lovelace_resources(hass)
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
