"""Entities of the device "Strompreis-Prognose": forecast, quality (shadow mode) and the release switch."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity

from .const import DOMAIN
from .price_forecast import RELEASE


def build(platform: str, coordinator, entry_id: str) -> list:
    pf = getattr(coordinator, "price_forecast", None)
    if pf is None:
        return []
    if platform == "sensor":
        return [PriceForecastSensor(pf, entry_id), PriceForecastQualitySensor(pf, entry_id)]
    if platform == "switch":
        return [PriceForecastUseSwitch(pf, entry_id)]
    return []


class _PfEntity:
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, pf, entry_id: str, key: str, name: str):
        self.pf = pf
        self._attr_unique_id = f"{entry_id}_price_forecast_{key}"
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry_id}_price_forecast")},
            "name": "Strompreis-Prognose",
            "manufacturer": "Smart Battery Optimizer",
            "model": "Tibber-Preis Tag 2-4 (Wind, Sonne, Temperatur, Kalender)",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.pf.listeners.append(self._refresh)

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._refresh in self.pf.listeners:
            self.pf.listeners.remove(self._refresh)

    def _refresh(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()


class PriceForecastSensor(_PfEntity, SensorEntity):
    """State = forecast price of the 6 cheapest hours of the day after tomorrow."""
    _attr_icon = "mdi:crystal-ball"
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(self, pf, entry_id):
        super().__init__(pf, entry_id, "days", "Preisprognose übermorgen (günstigste 6 h)")

    @property
    def native_value(self):
        days = self.pf.upcoming()
        return days[0]["guenstigste_6h"] if days else None

    @property
    def extra_state_attributes(self):
        return {"tage": self.pf.upcoming(), "genutzt": self.pf.use_enabled,
                "hinweis": "Schattenbetrieb: wird erst nach Freigabe für Ladeentscheidungen genutzt"}


class PriceForecastQualitySensor(_PfEntity, SensorEntity):
    """State = mean hourly error of the day-2 forecast over the last 30 days (EUR/kWh)."""
    _attr_icon = "mdi:bullseye-arrow"
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3

    def __init__(self, pf, entry_id):
        super().__init__(pf, entry_id, "quality", "Preisprognose Güte (Fehler Tag 2)")

    @property
    def native_value(self):
        return self.pf.quality().get("mae_tag2")

    @property
    def extra_state_attributes(self):
        return {**self.pf.quality(), "kriterien": RELEASE, "letzte_ergebnisse": self.pf.results[-12:]}


class PriceForecastUseSwitch(_PfEntity, SwitchEntity):
    """Set by the nightly agent once the release rule holds (off = shadow mode)."""
    _attr_icon = "mdi:check-decagram"

    def __init__(self, pf, entry_id):
        super().__init__(pf, entry_id, "use", "Preisprognose nutzen")

    @property
    def is_on(self) -> bool:
        return self.pf.use_enabled

    async def async_turn_on(self, **kwargs) -> None:
        await self.pf.async_set_use(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.pf.async_set_use(False)
