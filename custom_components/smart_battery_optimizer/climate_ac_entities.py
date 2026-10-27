"""Entities of the device "Klima Wohnzimmer" (living room heat pump control).

All values live in the ClimateController and its own Store, so they survive a
restart without RestoreEntity. The entities only show and change them.
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTemperature, UnitOfVolume
import homeassistant.util.dt as dt_util

from .climate_logic import MODES
from .const import DOMAIN

# (attribute in Settings, name, min, max, step, unit, icon)
NUMBERS = [
    ("target_living", "Zieltemperatur im Raum", 15, 25, 0.5, UnitOfTemperature.CELSIUS, "mdi:sofa"),
    ("target_home", "Zieltemperatur zuhause", 15, 25, 0.5, UnitOfTemperature.CELSIUS, "mdi:home-thermometer"),
    ("target_night", "Zieltemperatur Nacht", 14, 25, 0.5, UnitOfTemperature.CELSIUS, "mdi:weather-night"),
    ("target_away", "Zieltemperatur niemand", 12, 25, 0.5, UnitOfTemperature.CELSIUS, "mdi:home-export-outline"),
    ("oil_eur_l", "Ölpreis", 0.3, 3.0, 0.01, "€/L", "mdi:oil"),
    ("boiler_eff_pct", "Kesselwirkungsgrad", 50, 100, 1, "%", "mdi:water-boiler"),
    ("replace_max_outdoor", "Max. Außentemperatur Ersatz", 0, 25, 0.5, UnitOfTemperature.CELSIUS, "mdi:thermometer-chevron-up"),
    ("cool_max_price", "Max. Preis Kühlen", 0, 1, 0.01, "€/kWh", "mdi:snowflake"),
    ("surplus_soc", "Überschuss SoC-Schwelle", 50, 100, 1, "%", "mdi:battery-high"),
    ("boost_price", "Boost-Preis", 0, 0.5, 0.01, "€/kWh", "mdi:fan-plus"),
]


def build(platform: str, coordinator, entry_id: str) -> list:
    ctl = getattr(coordinator, "climate", None)
    if ctl is None:
        return []
    if platform == "select":
        return [AcModeSelect(ctl, entry_id)]
    if platform == "switch":
        return [AcControlActiveSwitch(ctl, entry_id), AcFlagSwitch(ctl, entry_id, "replace_switch", "Heizung ersetzen",
                                                                   "mdi:radiator-off"),
                AcFlagSwitch(ctl, entry_id, "surplus_switch", "Überschuss nutzen", "mdi:solar-power")]
    if platform == "number":
        return [AcSettingNumber(ctl, entry_id, *n) for n in NUMBERS]
    if platform == "sensor":
        return [AcDecisionSensor(ctl, entry_id), AcLearnedSensor(ctl, entry_id),
                AcCounterSensor(ctl, entry_id, "kwh_day", "Klimastrom heute", "kwh"),
                AcCounterSensor(ctl, entry_id, "kwh_month", "Klimastrom Monat", "kwh"),
                AcCounterSensor(ctl, entry_id, "oil_l_month", "Ersetztes Öl Monat", "oil"),
                AcCounterSensor(ctl, entry_id, "savings_month", "Ersparnis Monat", "eur"),
                AcBuildingSensor(ctl, entry_id, "nights", "Auskühlzeit Nacht", "zeitkonstante_h", "h",
                                 "mdi:home-thermometer-outline", "zeitkonstante_h"),
                AcBuildingSensor(ctl, entry_id, "days", "Wärmeverlust Tag", "verlust_w_pro_k", "W/K",
                                 "mdi:home-export-outline", "verlust_w_pro_k")]
    if platform == "button":
        return [AcResetLearningButton(ctl, entry_id)]
    return []


class _AcEntity:
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, ctl, entry_id: str, key: str, name: str):
        self.ctl = ctl
        self._attr_unique_id = f"{entry_id}_klima_wz_{key}"
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry_id}_klima_wz")},
            "name": "Klima Wohnzimmer",
            "manufacturer": "Smart Battery Optimizer",
            "model": "Wärmepumpen-Steuerung",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.ctl.listeners.append(self._refresh)

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        if self._refresh in self.ctl.listeners:
            self.ctl.listeners.remove(self._refresh)

    def _refresh(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()


class AcModeSelect(_AcEntity, SelectEntity):
    _attr_icon = "mdi:air-conditioner"
    _attr_options = MODES

    def __init__(self, ctl, entry_id):
        super().__init__(ctl, entry_id, "mode", "Betriebsmodus")

    @property
    def current_option(self) -> str:
        return self.ctl.mode

    async def async_select_option(self, option: str) -> None:
        self.ctl.set_mode(option)


class AcControlActiveSwitch(_AcEntity, SwitchEntity):
    """Off = observe only: the app shows what it would do and sends nothing."""

    _attr_icon = "mdi:robot"

    def __init__(self, ctl, entry_id):
        super().__init__(ctl, entry_id, "control_active", "Steuerung aktiv")

    @property
    def is_on(self) -> bool:
        return self.ctl.control_active

    async def async_turn_on(self, **kwargs) -> None:
        await self.ctl.async_set_control_active(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.ctl.async_set_control_active(False)


class AcFlagSwitch(_AcEntity, SwitchEntity):
    def __init__(self, ctl, entry_id, attr: str, name: str, icon: str):
        super().__init__(ctl, entry_id, attr, name)
        self._flag = attr
        self._attr_icon = icon

    @property
    def is_on(self) -> bool:
        return getattr(self.ctl, self._flag)

    async def async_turn_on(self, **kwargs) -> None:
        self.ctl.set_switch(self._flag, True)

    async def async_turn_off(self, **kwargs) -> None:
        self.ctl.set_switch(self._flag, False)


class AcSettingNumber(_AcEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, ctl, entry_id, attr, name, lo, hi, step, unit, icon):
        super().__init__(ctl, entry_id, attr, name)
        self._setting = attr
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon

    @property
    def native_value(self) -> float:
        return getattr(self.ctl.settings, self._setting)

    async def async_set_native_value(self, value: float) -> None:
        self.ctl.set_setting(self._setting, value)


class AcDecisionSensor(_AcEntity, SensorEntity):
    _attr_icon = "mdi:head-cog-outline"

    def __init__(self, ctl, entry_id):
        super().__init__(ctl, entry_id, "decision", "Entscheidung")

    @property
    def native_value(self) -> str | None:
        d = self.ctl.decision
        if d is None:
            return None
        prefix = "" if self.ctl.control_active else "Beobachten: "
        return (prefix + d.reason)[:255]

    @property
    def extra_state_attributes(self) -> dict:
        return self.ctl.info


class AcLearnedSensor(_AcEntity, SensorEntity):
    _attr_icon = "mdi:school-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "Datensätze"

    def __init__(self, ctl, entry_id):
        super().__init__(ctl, entry_id, "learned", "Gelernte Werte")

    @property
    def native_value(self) -> int:
        return len(self.ctl.model.samples)

    @property
    def extra_state_attributes(self) -> dict:
        return self.ctl.model.summary()


class AcCounterSensor(_AcEntity, SensorEntity):
    def __init__(self, ctl, entry_id, key, name, kind):
        super().__init__(ctl, entry_id, key, name)
        self._key = key
        if kind == "kwh":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
            self._attr_suggested_display_precision = 2
        elif kind == "oil":
            self._attr_device_class = SensorDeviceClass.VOLUME
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            self._attr_native_unit_of_measurement = UnitOfVolume.LITERS
            self._attr_suggested_display_precision = 1
            self._attr_icon = "mdi:oil"
        else:
            self._attr_device_class = SensorDeviceClass.MONETARY
            self._attr_state_class = SensorStateClass.TOTAL
            self._attr_native_unit_of_measurement = "EUR"
            self._attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float:
        return round(self.ctl.counter(self._key, dt_util.now()), 3)


class AcBuildingSensor(_AcEntity, SensorEntity):
    """Building key figure: one value per night / AC-only day, unknown on other days.

    Unknown instead of repeating the last value, so the long-term statistics only
    contain measured days. The full list is kept in the controller's store.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(self, ctl, entry_id, kind, name, field, unit, icon, key):
        super().__init__(ctl, entry_id, key, name)
        self._kind = kind
        self._field = field
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon

    @property
    def native_value(self) -> float | None:
        row = self.ctl.latest_kpi(self._kind, dt_util.now())
        return row.get(self._field) if row else None

    @property
    def extra_state_attributes(self) -> dict:
        rows = self.ctl.building.get(self._kind) or []
        return {
            "letzte_messung": rows[-1] if rows else None,
            "messungen": len(rows),
            "verlauf": {r["datum"]: r[self._field] for r in rows[-30:]},
        }


class AcResetLearningButton(_AcEntity, ButtonEntity):
    _attr_icon = "mdi:restore"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, ctl, entry_id):
        super().__init__(ctl, entry_id, "reset_learning", "Lernwerte zurücksetzen")

    async def async_press(self) -> None:
        self.ctl.reset_learning()
