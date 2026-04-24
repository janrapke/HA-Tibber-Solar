"""Manager for Smart Devices in Smart Battery Optimizer."""
import logging
import json
import os
import aiofiles
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

STORAGE_FILE = ".storage/smart_battery_optimizer_devices.json"

class SmartDeviceManager:
    """Manages smart devices, learning programs, and storage."""

    def __init__(self, hass: HomeAssistant):
        """Initialize."""
        self.hass = hass
        self.data = {
            "devices": {}
        }
        self.learning_states = {}
        self.planned_devices = {}
        self.proposed_devices = {}

    async def async_load(self):
        """Load data from storage."""
        filepath = self.hass.config.path(STORAGE_FILE)
        if os.path.exists(filepath):
            try:
                async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
                    content = await f.read()
                    self.data = json.loads(content)
            except Exception as e:
                _LOGGER.error("Failed to load device data: %s", e)

        if "devices" not in self.data:
            self.data["devices"] = {}

    async def async_save(self):
        """Save data to storage."""
        filepath = self.hass.config.path(STORAGE_FILE)
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
                await f.write(json.dumps(self.data, indent=2))
        except Exception as e:
            _LOGGER.error("Failed to save device data: %s", e)

    def start_learning(self, device_id: str):
        """Start learning a new program for a device."""
        if device_id not in self.learning_states:
            self.learning_states[device_id] = {
                "active": True,
                "profile": [],
                "zero_power_minutes": 0,
                "start_time": datetime.now()
            }

    async def stop_learning(self, device_id: str, program_name: str = None):
        """Stop learning and save the profile."""
        if device_id in self.learning_states:
            state = self.learning_states.pop(device_id)
            profile = state["profile"]

            # Trim trailing zeros
            while profile and profile[-1] == 0:
                profile.pop()

            if len(profile) > 0:
                if device_id not in self.data["devices"]:
                    self.data["devices"][device_id] = {"programs": {}}

                if not program_name:
                    program_num = len(self.data["devices"][device_id].get("programs", {})) + 1
                    program_name = f"Programm {program_num}"

                self.data["devices"][device_id]["programs"][program_name] = {
                    "duration_minutes": len(profile),
                    "profile_watt_per_minute": profile
                }

                await self.async_save()

    def record_power(self, device_id: str, power_w: float):
        """Record power for a device currently in learning mode."""
        if device_id in self.learning_states and self.learning_states[device_id]["active"]:
            self.learning_states[device_id]["profile"].append(power_w)

            if power_w <= 5.0:  # Standby threshold
                self.learning_states[device_id]["zero_power_minutes"] += 1
            else:
                self.learning_states[device_id]["zero_power_minutes"] = 0

    def get_programs(self, device_id: str) -> list[str]:
        """Get list of programs for a device."""
        if device_id in self.data["devices"]:
            return list(self.data["devices"][device_id].get("programs", {}).keys())
        return []

    def get_program_profile(self, device_id: str, program_name: str) -> list[float]:
        """Get the power profile of a specific program."""
        try:
            return self.data["devices"][device_id]["programs"][program_name]["profile_watt_per_minute"]
        except KeyError:
            return []

    def set_proposed_device(self, device_id: str, program_name: str, start_time: datetime, expected_cost: float):
        """Propose a device to run at a specific time (before confirmation)."""
        self.proposed_devices[device_id] = {
            "program_name": program_name,
            "start_time": start_time,
            "expected_cost": expected_cost,
        }

    def confirm_proposed_plan(self, device_id: str):
        """Move the proposed plan to confirmed."""
        if device_id in self.proposed_devices:
            prop = self.proposed_devices.pop(device_id)
            self.set_planned_device(
                device_id,
                prop["program_name"],
                prop["start_time"],
                prop["expected_cost"]
            )

    def set_planned_device(self, device_id: str, program_name: str, start_time: datetime, expected_cost: float):
        """Plan a device to run at a specific time."""
        self.planned_devices[device_id] = {
            "program_name": program_name,
            "start_time": start_time,
            "expected_cost": expected_cost,
            "profile": self.get_program_profile(device_id, program_name)
        }

    def cancel_planned_device(self, device_id: str):
        """Cancel a planned device."""
        if device_id in self.planned_devices:
            del self.planned_devices[device_id]

    def get_planned_power_at_time(self, dt: datetime) -> float:
        """Get total planned power consumption for all devices at a specific minute."""
        total_power = 0.0
        for device_id, plan in self.planned_devices.items():
            start_time = plan["start_time"]
            profile = plan["profile"]

            # Allow up to 30 mins late start
            if dt >= start_time:
                minute_offset = int((dt - start_time).total_seconds() / 60)
                if 0 <= minute_offset < len(profile):
                    total_power += profile[minute_offset]
        return total_power
