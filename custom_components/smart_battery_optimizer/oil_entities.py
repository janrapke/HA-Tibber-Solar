"""Entities of the device "Ölheizung" (burner runs, oil counter, tank, reach).

All values live in the OilMonitor and its own Store; the entities only show and change them.
"""
from __future__ import annotations

from datetime import date

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfTime, UnitOfVolume
import homeassistant.util.dt as dt_util

from .const import DOMAIN


def build(platform: str, coordinator, entry_id: str) -> list:
    oil = getattr(coordinator, "oil", None)
    if oil is None:
        return []
    if platform == "binary_sensor":
        return [OilBurnerOnSensor(oil, entry_id)]
    if platform == "number":
        return [OilGaugeNumber(oil, entry_id),
                OilSettingNumber(oil, entry_id, "reserve_cm", "Reserve (rot auf der Anzeige)", 0, 60, 1, "cm",
                                 "mdi:car-brake-low-pressure"),
                OilSettingNumber(oil, entry_id, "flow_l_h", "Öldurchsatz Brenner", 0.5, 5.0, 0.01, "L/h",
                                 "mdi:spray"),
                OilSettingNumber(oil, entry_id, "offer_eur_l", "Ölpreis Angebot", 0, 3.0, 0.01, "€/L",
                                 "mdi:cash")]
    if platform == "sensor":
        return [OilTodaySensor(oil, entry_id, "burner_min", "Brennerlaufzeit heute"),
                OilTodaySensor(oil, entry_id, "starts", "Brennerstarts heute"),
                OilTodaySensor(oil, entry_id, "oil_l", "Öl heute"),
                OilMonthSensor(oil, entry_id),
                OilTankSensor(oil, entry_id, "total", "Tankinhalt"),
                OilTankSensor(oil, entry_id, "usable", "Öl nutzbar"),
                *[OilReachSensor(oil, entry_id, s) for s in ("optimistisch", "neutral", "pessimistisch")],
                OilModelSensor(oil, entry_id)]
    return []


class _OilEntity:
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, oil, entry_id: str, key: str, name: str):
        self.oil = oil
        self._attr_unique_id = f"{entry_id}_oil_{key}"
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry_id}_oil")},
            "name": "Ölheizung",
            "manufacturer": "Smart Battery Optimizer",
            "model": "intercal Euroline E-18, 3 x 2000 L Tank",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.oil.listeners.append(self._refresh)

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._refresh in self.oil.listeners:
            self.oil.listeners.remove(self._refresh)

    def _refresh(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()


class OilBurnerOnSensor(_OilEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:fire"

    def __init__(self, oil, entry_id):
        super().__init__(oil, entry_id, "burner_on", "Brenner läuft")

    @property
    def is_on(self) -> bool:
        return self.oil.burner_on()


class OilTodaySensor(_OilEntity, SensorEntity):
    _UNITS = {"burner_min": (UnitOfTime.MINUTES, "mdi:fire-circle", 0),
              "starts": (None, "mdi:counter", 0),
              "oil_l": (UnitOfVolume.LITERS, "mdi:oil", 1)}

    def __init__(self, oil, entry_id, field, name):
        super().__init__(oil, entry_id, f"today_{field}", name)
        self._field = field
        unit, icon, prec = self._UNITS[field]
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_suggested_display_precision = prec
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        if field == "oil_l":
            self._attr_device_class = SensorDeviceClass.VOLUME

    @property
    def native_value(self):
        return round(getattr(self.oil.today(dt_util.now()), self._field), 2)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self._field != "burner_min":
            return None
        now = dt_util.now()
        yday = self.oil.days.get(date.fromordinal(now.date().toordinal() - 1).isoformat())
        return {"laeufe": self.oil.runs_today(now), "heute": self.oil.today(now).summary(),
                "gestern": yday.summary() if yday else None}


class OilMonthSensor(_OilEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.VOLUME
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 1
    _attr_icon = "mdi:oil"

    def __init__(self, oil, entry_id):
        super().__init__(oil, entry_id, "month_l", "Öl Monat")

    @property
    def native_value(self) -> float:
        return round(self.oil.month_oil(dt_util.now()), 2)


class OilTankSensor(_OilEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.VOLUME_STORAGE
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:storage-tank"

    def __init__(self, oil, entry_id, kind, name):
        super().__init__(oil, entry_id, f"tank_{kind}", name)
        self._kind = kind

    @property
    def native_value(self) -> float | None:
        v = self.oil.tank_liters() if self._kind == "total" else self.oil.usable_liters()
        return None if v is None else round(v)

    @property
    def extra_state_attributes(self) -> dict:
        o, tank = self.oil, self.oil.tank
        total = o.tank_liters()
        attrs = {
            "reserve_l": round(o.reserve_liters()), "reserve_cm": o.settings.reserve_cm,
            "kapazitaet_l": round(tank.capacity),
            "anzeige_cm_berechnet": round(tank.cm(total), 1) if total is not None else None,
            "letzte_ablesung": o.reading, "seit_ablesung_verbraucht_l": round(o.burned_since_reading, 1),
            "kalibrierungen": o.calibrations[-5:],
        }
        if total is not None and o.settings.offer_eur_l > 0:
            fill = max(0.0, tank.capacity * 0.95 - total)
            attrs.update(auffuellen_bis_95_prozent_l=round(fill), auffuellen_kosten_eur=round(fill * o.settings.offer_eur_l))
        return attrs


class OilReachSensor(_OilEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DATE
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, oil, entry_id, scenario):
        super().__init__(oil, entry_id, f"reach_{scenario}", f"Öl reicht bis ({scenario})")
        self._scenario = scenario

    @property
    def native_value(self) -> date | None:
        v = self.oil.forecast.get(self._scenario)
        return date.fromisoformat(v) if v else None

    @property
    def extra_state_attributes(self) -> dict:
        return {"hinweis": "Bis die rote Reserve erreicht ist. Kein Datum = reicht länger als zwei Jahre "
                           "oder noch keine Tank-Ablesung.",
                "annahmen": self.oil.forecast, "modell": self.oil.model}


class OilModelSensor(_OilEntity, SensorEntity):
    _attr_icon = "mdi:chart-line"
    _attr_native_unit_of_measurement = "L/Kh"
    _attr_suggested_display_precision = 4

    def __init__(self, oil, entry_id):
        super().__init__(oil, entry_id, "model", "Heizkennlinie")

    @property
    def native_value(self) -> float:
        return self.oil.model["l_je_gradstunde"]

    @property
    def extra_state_attributes(self) -> dict:
        days = sorted(self.oil.days.values(), key=lambda d: d.day)[-30:]
        return {**self.oil.model, "heiztage": [d.summary() for d in days]}


class OilGaugeNumber(_OilEntity, NumberEntity):
    """Enter the gauge reading (cm). Shows the level the app computes from the burner runs since then."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 150
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "cm"
    _attr_icon = "mdi:gauge"

    def __init__(self, oil, entry_id):
        super().__init__(oil, entry_id, "gauge_cm", "Tankanzeige ablesen")

    @property
    def native_value(self) -> float | None:
        total = self.oil.tank_liters()
        return None if total is None else round(self.oil.tank.cm(total))

    async def async_set_native_value(self, value: float) -> None:
        self.oil.set_reading(float(value), dt_util.now())


class OilSettingNumber(_OilEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, oil, entry_id, key, name, lo, hi, step, unit, icon):
        super().__init__(oil, entry_id, key, name)
        self._key = key
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon

    @property
    def native_value(self) -> float:
        return getattr(self.oil.settings, self._key)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self._key != "offer_eur_l" or not self.oil.price:
            return None
        return {"quelle": "heizoel24.de", **self.oil.price}

    async def async_set_native_value(self, value: float) -> None:
        self.oil.set_setting(self._key, value)
