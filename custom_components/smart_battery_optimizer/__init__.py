"""The Smart Battery Optimizer integration."""
import logging
import pathlib

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "binary_sensor", "switch", "number", "button", "select", "text"]

from .coordinator import SmartBatteryOptimizerCoordinator

_WWW = pathlib.Path(__file__).parent / "www"
_CARD_FILES = [
    ("appliance-card.js", "/smart_battery_optimizer/appliance-card.js"),
    ("battery-forecast-card.js", "/smart_battery_optimizer/battery-forecast-card.js"),
    ("savings-card.js", "/smart_battery_optimizer/savings-card.js"),
    ("forecast-chart-card.js", "/smart_battery_optimizer/forecast-chart-card.js"),
]
_CARD_RESOURCE_URLS = [url for _, url in _CARD_FILES]


async def _register_static_paths(hass: HomeAssistant) -> None:
    """Register static file paths, compatible with old and new HA APIs."""
    try:
        from homeassistant.components.http import StaticPathConfig
        configs = [
            StaticPathConfig(url, str(_WWW / filename), cache_headers=False)
            for filename, url in _CARD_FILES
            if (_WWW / filename).exists()
        ]
        if configs:
            await hass.http.async_register_static_paths(configs)
    except (ImportError, AttributeError):
        for filename, url in _CARD_FILES:
            if not (_WWW / filename).exists():
                continue
            try:
                hass.http.register_static_path(url, str(_WWW / filename), cache_headers=False)
            except Exception:
                pass
    except Exception as err:
        _LOGGER.warning("Static path registration failed: %s", err)


async def _ensure_lovelace_resources(hass: HomeAssistant) -> None:
    """Register card JS as Lovelace resources using HA's own collection."""
    try:
        from homeassistant.components.lovelace.resources import ResourceStorageCollection

        # Use HA's live collection — hass.data["lovelace"] is a LovelaceData namedtuple
        # with a .resources attribute set by the lovelace component on startup.
        lovelace_data = hass.data.get("lovelace")
        resources = getattr(lovelace_data, "resources", None)

        if not isinstance(resources, ResourceStorageCollection):
            _LOGGER.warning(
                "Lovelace ResourceStorageCollection not found in hass.data['lovelace']. "
                "Cards may need to be added manually in Settings → Dashboards → Resources."
            )
            return

        existing = {item["url"] for item in resources.async_items()}
        for url in _CARD_RESOURCE_URLS:
            if url not in existing:
                await resources.async_create_item({"res_type": "module", "url": url})
                _LOGGER.info("Lovelace resource registered: %s", url)
            else:
                _LOGGER.debug("Lovelace resource already present: %s", url)
    except Exception as err:
        _LOGGER.warning("Lovelace resource registration failed: %s", err)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Battery Optimizer from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Register static paths on first setup (survives entry reloads)
    if not hass.data[DOMAIN].get("_paths_registered"):
        await _register_static_paths(hass)
        hass.data[DOMAIN]["_paths_registered"] = True

    # Register Lovelace card resources once HA is fully started so lovelace is ready.
    # If HA is already running (e.g. integration reload), call immediately.
    if not hass.data[DOMAIN].get("_cards_registered"):
        async def _register_cards(_event=None) -> None:
            await _ensure_lovelace_resources(hass)
            hass.data[DOMAIN]["_cards_registered"] = True

        if hass.is_running:
            await _register_cards()
        else:
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_cards)

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
    _link_sub_devices(hass, entry)

    entry.async_on_unload(entry.add_update_listener(update_listener))

    async def _on_stop(_event) -> None:
        await coordinator.async_safe_shutdown()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _on_stop))

    return True

def _link_sub_devices(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hang sub-devices (Klima Wohnzimmer, appliances) below the main device.

    `via_device` in device_info is deprecated and makes adding entities fail,
    so the link is set in the device registry with via_device_id.
    """
    try:
        reg = dr.async_get(hass)
        devices = dr.async_entries_for_config_entry(reg, entry.entry_id)
        main = next((d for d in devices if (DOMAIN, entry.entry_id) in d.identifiers), None)
        if main is None:
            return
        for dev in devices:
            if dev.id != main.id and dev.via_device_id != main.id:
                reg.async_update_device(dev.id, via_device_id=main.id)
    except Exception:  # noqa: BLE001 - cosmetic only
        _LOGGER.exception("Linking sub-devices failed")


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data[DOMAIN].get(entry.entry_id, {}).get("coordinator")
    if coordinator:
        await coordinator.async_safe_shutdown()
        await coordinator.async_shutdown_save()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    return True
