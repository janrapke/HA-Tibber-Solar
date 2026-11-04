"""Entities of the device "Tesla Laden": plan, settings, instant charging and travel mode."""
from __future__ import annotations

from datetime import time

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.time import TimeEntity
import homeassistant.util.dt as dt_util

from .const import DOMAIN


def build(platform: str, coordinator, entry_id: str) -> list:
    ev = getattr(coordinator, "ev", None)
    if ev is None:
        return []
    if platform == "sensor":
        return [EvPlanSensor(ev, entry_id)]
    if platform == "switch":
        return [EvSwitch(ev, entry_id, "active", "Ladesteuerung aktiv", "mdi:ev-station"),
                EvSwitch(ev, entry_id, "now_on", "Sofortladen", "mdi:lightning-bolt"),
                EvSwitch(ev, entry_id, "travel_on", "Reise aktiv", "mdi:map-marker-path")]
    if platform == "number":
        return [EvNumber(ev, entry_id, "min_target", "Mindestladestand bei Abfahrt", 10, 60),
                EvNumber(ev, entry_id, "max_target", "Höchstens laden (Alltag)", 50, 100),
                EvNumber(ev, entry_id, "now_target", "Sofortladen bis", 10, 100),
                EvNumber(ev, entry_id, "wait_ct", "Warten ab Ersparnis", 0, 5, step=0.5, unit="ct/kWh",
                         icon="mdi:timer-sand")]
    if platform == "select":
        return [EvTravelTargetSelect(ev, entry_id)]
    if platform == "datetime":
        return [EvTravelAt(ev, entry_id)]
    if platform == "time":
        return [EvDeparture(ev, entry_id, "dep_weekday", "Abfahrt werktags"),
                EvDeparture(ev, entry_id, "dep_weekend", "Abfahrt Wochenende/Feiertag")]
    return []


class _EvEntity:
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, ev, entry_id: str, key: str, name: str):
        self.ev = ev
        self.key = key
        self._attr_unique_id = f"{entry_id}_ev_{key}"
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry_id}_ev")},
            "name": "Tesla Laden",
            "manufacturer": "Smart Battery Optimizer",
            "model": "Ladeplanung nach Tibber-Preis, Tesla BLE",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.ev.listeners.append(self._refresh)

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._refresh in self.ev.listeners:
            self.ev.listeners.remove(self._refresh)

    def _refresh(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()


class EvPlanSensor(_EvEntity, SensorEntity):
    _attr_icon = "mdi:car-electric"

    def __init__(self, ev, entry_id):
        super().__init__(ev, entry_id, "plan", "Ladeplan")

    @property
    def native_value(self):
        return (self.ev.reason or "")[:250]

    @property
    def extra_state_attributes(self):
        return self.ev.attributes()


class EvSwitch(_EvEntity, SwitchEntity):
    def __init__(self, ev, entry_id, key, name, icon):
        super().__init__(ev, entry_id, key, name)
        self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        return bool(self.ev.s[self.key])

    async def async_turn_on(self, **kwargs) -> None:
        await self.ev.async_set(self.key, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.ev.async_set(self.key, False)


class EvNumber(_EvEntity, NumberEntity):
    _attr_native_unit_of_measurement = "%"
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-charging-medium"

    def __init__(self, ev, entry_id, key, name, lo, hi, step=5, unit="%", icon=None):
        super().__init__(ev, entry_id, key, name)
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        if icon:
            self._attr_icon = icon

    @property
    def native_value(self) -> float:
        return float(self.ev.s[self.key])

    async def async_set_native_value(self, value: float) -> None:
        await self.ev.async_set(self.key, float(value))


class EvTravelTargetSelect(_EvEntity, SelectEntity):
    _attr_options = ["80 %", "100 %"]
    _attr_icon = "mdi:battery-high"

    def __init__(self, ev, entry_id):
        super().__init__(ev, entry_id, "travel_target", "Reise Ladeziel")

    @property
    def current_option(self) -> str:
        return "100 %" if self.ev.s["travel_target"] >= 100 else "80 %"

    async def async_select_option(self, option: str) -> None:
        await self.ev.async_set("travel_target", 100.0 if option.startswith("100") else 80.0)


class EvTravelAt(_EvEntity, DateTimeEntity):
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, ev, entry_id):
        super().__init__(ev, entry_id, "travel_at", "Reise Abfahrt")

    @property
    def native_value(self):
        v = self.ev.s["travel_at"]
        return dt_util.parse_datetime(v) if v else None

    async def async_set_value(self, value) -> None:
        await self.ev.async_set("travel_at", value.isoformat())


class EvDeparture(_EvEntity, TimeEntity):
    _attr_icon = "mdi:clock-start"

    @property
    def native_value(self) -> time:
        return time.fromisoformat(self.ev.s[self.key])

    async def async_set_value(self, value: time) -> None:
        await self.ev.async_set(self.key, value.strftime("%H:%M"))
