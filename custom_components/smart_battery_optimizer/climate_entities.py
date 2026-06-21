"""Dynamic per-device entities for climate (heating / cooling / heat pump) devices.

Each configured climate device gets its own HA sub-device so its entities are
cleanly grouped and separated from the main Smart Battery Optimizer device.

Entity state is stored locally on each entity instance AND mirrored into
coordinator.climate_device_states[device_id] so the forecast simulation
can read it without iterating entity objects.
"""
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.text import TextEntity, TextMode
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    CLIMATE_TYPE_HEATING, CLIMATE_TYPE_COOLING, CLIMATE_TYPE_HEAT_PUMP,
    CLIMATE_LABEL_TO_INTERNAL, CLIMATE_INTERNAL_TO_LABEL,
    CLIMATE_BOOTSTRAP_ASSUMED_DELTA,
)

_CLIMATE_TYPE_OPTIONS = [CLIMATE_TYPE_HEATING, CLIMATE_TYPE_COOLING, CLIMATE_TYPE_HEAT_PUMP]


def _device_info(entry_id: str, device_id: str, device_name: str) -> dict:
    """Return device_info that creates a separate HA device per climate device."""
    return {
        "identifiers": {(DOMAIN, f"climate_{entry_id}_{device_id}")},
        "name": device_name,
        "manufacturer": "Smart Battery Optimizer",
        "model": "Klimagerät",
        "via_device": (DOMAIN, entry_id),
    }


def _ensure_state(coordinator, device_id: str):
    """Ensure coordinator.climate_device_states[device_id] dict exists."""
    if not hasattr(coordinator, "climate_device_states"):
        coordinator.climate_device_states = {}
    coordinator.climate_device_states.setdefault(device_id, {
        "enabled": False,
        "device_type": "heating",
        "setpoint": 20.0,
        "manual_w": 0.0,
        "power_sensor": "",
    })


class ClimateDeviceEnabledSwitch(CoordinatorEntity, SwitchEntity):
    """Enable / disable a climate device in consumption predictions.

    Disable when the device is physically off for the season so the optimizer
    does not forecast load that won't occur.  The learned model is preserved.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:heat-pump"

    def __init__(self, coordinator, entry_id: str, device_config: dict):
        super().__init__(coordinator)
        self._device_id = device_config["id"]
        self._attr_unique_id = f"{entry_id}_climate_{self._device_id}_enabled"
        self._attr_name = "Aktiv"
        self._attr_device_info = _device_info(entry_id, self._device_id, device_config["name"])
        _ensure_state(coordinator, self._device_id)
        coordinator.climate_device_states[self._device_id]["enabled"] = False

    @property
    def is_on(self) -> bool:
        return self.coordinator.climate_device_states.get(self._device_id, {}).get("enabled", False)

    async def async_turn_on(self, **kwargs):
        self.coordinator.climate_device_states[self._device_id]["enabled"] = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self.coordinator.climate_device_states[self._device_id]["enabled"] = False
        self.async_write_ha_state()


class ClimateDeviceTypeSelect(CoordinatorEntity, SelectEntity):
    """Select the operating type of a climate device.

    - Nur Heizen: uses heating W/°C model
    - Nur Kühlen: uses cooling W/°C model
    - Wärmepumpe: handles both modes; mode inferred from temperature delta
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:heat-pump-outline"

    def __init__(self, coordinator, entry_id: str, device_config: dict):
        super().__init__(coordinator)
        self._device_id = device_config["id"]
        self._attr_unique_id = f"{entry_id}_climate_{self._device_id}_type"
        self._attr_name = "Typ"
        self._attr_options = _CLIMATE_TYPE_OPTIONS
        self._attr_device_info = _device_info(entry_id, self._device_id, device_config["name"])

        _ensure_state(coordinator, self._device_id)
        stored_internal = device_config.get("device_type", "heating")
        coordinator.climate_device_states[self._device_id]["device_type"] = stored_internal

        # Bootstrap learning model immediately when device is first set up
        manual_w = float(device_config.get("manual_w", 0))
        coordinator.learning_engine.register_climate_device(
            self._device_id, manual_w, stored_internal
        )

    @property
    def current_option(self) -> str:
        internal = self.coordinator.climate_device_states.get(self._device_id, {}).get("device_type", "heating")
        return CLIMATE_INTERNAL_TO_LABEL.get(internal, CLIMATE_TYPE_HEATING)

    async def async_select_option(self, option: str) -> None:
        internal = CLIMATE_LABEL_TO_INTERNAL.get(option, "heating")
        self.coordinator.climate_device_states[self._device_id]["device_type"] = internal

        manual_w = self.coordinator.climate_device_states[self._device_id].get("manual_w", 0.0)
        if manual_w > 0:
            self.coordinator.learning_engine.bootstrap_climate_slot(
                self._device_id, manual_w, internal
            )
            await self.coordinator.learning_engine.async_save()
        self.async_write_ha_state()


class ClimateManualWNumber(CoordinatorEntity, NumberEntity):
    """Electrical power consumption (W) when the device is running.

    Enter the actual electrical draw — NOT the heating/cooling output capacity.
    For heat pumps: use Leistungsaufnahme (e.g. 2500 W), not Heizleistung (9000 W).
    Set to 0 if a power sensor covers the measurement.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:lightning-bolt"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 10000
    _attr_native_step = 50

    def __init__(self, coordinator, entry_id: str, device_config: dict):
        super().__init__(coordinator)
        self._device_id = device_config["id"]
        self._attr_unique_id = f"{entry_id}_climate_{self._device_id}_manual_w"
        self._attr_name = "Leistungsaufnahme (W)"
        self._attr_device_info = _device_info(entry_id, self._device_id, device_config["name"])

        _ensure_state(coordinator, self._device_id)
        self._value = float(device_config.get("manual_w", 0))
        coordinator.climate_device_states[self._device_id]["manual_w"] = self._value

    @property
    def native_value(self) -> float:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        self.coordinator.climate_device_states[self._device_id]["manual_w"] = value
        device_type = self.coordinator.climate_device_states[self._device_id].get("device_type", "heating")
        if value > 0:
            self.coordinator.learning_engine.bootstrap_climate_slot(
                self._device_id, value, device_type
            )
            await self.coordinator.learning_engine.async_save()
        self.async_write_ha_state()


class ClimateSolltemperaturNumber(CoordinatorEntity, NumberEntity):
    """Target indoor temperature (Solltemperatur).

    The device heats when outdoor temp < setpoint, cools when outdoor temp > setpoint.
    One setpoint covers both modes for heat pumps.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:thermometer"
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 10
    _attr_native_max_value = 35
    _attr_native_step = 0.5

    def __init__(self, coordinator, entry_id: str, device_config: dict):
        super().__init__(coordinator)
        self._device_id = device_config["id"]
        self._attr_unique_id = f"{entry_id}_climate_{self._device_id}_setpoint"
        self._attr_name = "Solltemperatur (°C)"
        self._attr_device_info = _device_info(entry_id, self._device_id, device_config["name"])

        _ensure_state(coordinator, self._device_id)
        self._value = float(device_config.get("setpoint", 20.0))
        coordinator.climate_device_states[self._device_id]["setpoint"] = self._value

    @property
    def native_value(self) -> float:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = value
        self.coordinator.climate_device_states[self._device_id]["setpoint"] = value
        self.async_write_ha_state()


class ClimatePowerSensorText(CoordinatorEntity, TextEntity):
    """Entity ID of the power sensor for this climate device.

    Can be a smart-plug sensor (sensor.steckdose_waermepumpe_power)
    or a power sensor from a WiFi climate integration (e.g. sensor.daikin_power).
    Must report power in Watts.  Leave empty to use manual wattage only.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:connection"
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = 255

    def __init__(self, coordinator, entry_id: str, device_config: dict):
        super().__init__(coordinator)
        self._device_id = device_config["id"]
        self._attr_unique_id = f"{entry_id}_climate_{self._device_id}_power_sensor"
        self._attr_name = "Leistungssensor (Entity ID)"
        self._attr_device_info = _device_info(entry_id, self._device_id, device_config["name"])

        _ensure_state(coordinator, self._device_id)
        self._value = device_config.get("power_sensor", "")
        coordinator.climate_device_states[self._device_id]["power_sensor"] = self._value

    @property
    def native_value(self) -> str:
        return self._value

    async def async_set_value(self, value: str) -> None:
        self._value = value.strip()
        self.coordinator.climate_device_states[self._device_id]["power_sensor"] = self._value
        self.async_write_ha_state()
