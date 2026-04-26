"""Manager for Smart Devices in Smart Battery Optimizer."""
import logging
from datetime import datetime, timedelta

import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = "smart_battery_optimizer_devices"
STORAGE_VERSION = 1

class SmartDeviceManager:
    """Manages smart devices, learning programs, and storage."""

    def __init__(self, hass: HomeAssistant):
        """Initialize."""
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data = {
            "devices": {}
        }
        self.learning_states = {}
        self.planned_devices = {}
        self.proposed_devices = {}
        self.running_devices = {}
        self.selected_programs = {}

    async def async_load(self):
        """Load data from storage."""
        try:
            stored_data = await self._store.async_load()
            if stored_data:
                self.data = stored_data
        except Exception as e:
            _LOGGER.error("Failed to load device data: %s", e)

        if "devices" not in self.data:
            self.data["devices"] = {}

    def set_selected_program(self, device_id: str, program_name: str):
        """Store the currently selected program for UI actions."""
        self.selected_programs[device_id] = program_name

    def get_selected_program(self, device_id: str) -> str | None:
        """Get the currently selected program."""
        return self.selected_programs.get(device_id)

    async def async_save(self):
        """Save data to storage."""
        try:
            await self._store.async_save(self.data)
        except Exception as e:
            _LOGGER.error("Failed to save device data: %s", e)

    def start_learning(self, device_id: str):
        """Start learning a new program for a device."""
        if device_id not in self.learning_states:
            self.learning_states[device_id] = {
                "active": True,
                "profile": [],
                "zero_power_minutes": 0,
                "start_time": dt_util.now()
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

    async def rename_program(self, device_id: str, old_name: str, new_name: str):
        """Rename a saved program."""
        if device_id in self.data["devices"] and old_name in self.data["devices"][device_id]["programs"]:
            if new_name and new_name != old_name:
                prog_data = self.data["devices"][device_id]["programs"].pop(old_name)
                self.data["devices"][device_id]["programs"][new_name] = prog_data
                await self.async_save()

    async def delete_program(self, device_id: str, program_name: str):
        """Delete a saved program."""
        if device_id in self.data["devices"] and program_name in self.data["devices"][device_id]["programs"]:
            del self.data["devices"][device_id]["programs"][program_name]
            await self.async_save()

    async def clear_device(self, device_id: str):
        """Delete all data for a device."""
        if device_id in self.data["devices"]:
            self.data["devices"][device_id]["programs"] = {}
            await self.async_save()

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

        # Include planned devices that haven't started yet
        for device_id, plan in self.planned_devices.items():
            start_time = plan["start_time"]
            profile = plan["profile"]

            if dt >= start_time:
                minute_offset = int((dt - start_time).total_seconds() / 60)
                if 0 <= minute_offset < len(profile):
                    total_power += profile[minute_offset]

        # Include running devices (spontaneous or auto-started)
        for device_id, run in self.running_devices.items():
            start_time = run["start_time"]
            profile = run["profile"]

            if dt >= start_time:
                minute_offset = int((dt - start_time).total_seconds() / 60)
                if 0 <= minute_offset < len(profile):
                    total_power += profile[minute_offset]

        return total_power

    async def update_live_device_states(self, device_id: str, current_power: float):
        """Update states based on live power: auto-start, auto-detect, auto-cancel, and continuous learning."""
        now = dt_util.now()

        # 1. Check Auto-Cancel for planned devices (if > 60 mins past start and NOT running)
        if device_id in self.planned_devices:
            plan = self.planned_devices[device_id]
            mins_past_start = (now - plan["start_time"]).total_seconds() / 60.0

            if mins_past_start > 60:
                # Cancel the plan if it hasn't turned on yet (power remains low)
                if current_power <= 10.0:
                    _LOGGER.info(f"Auto-canceling plan for {device_id} (60 mins late).")
                    self.cancel_planned_device(device_id)

        # 2. Check if a device just turned ON (> 10W)
        if current_power > 10.0:
            if device_id in self.planned_devices:
                # Auto-Start: It was planned, and now it's consuming power
                plan = self.planned_devices.pop(device_id)
                self.running_devices[device_id] = {
                    "program_name": plan["program_name"],
                    "start_time": now,
                    "profile": plan["profile"],
                    "spontaneous": False,
                    "live_recording": [],
                    "zero_power_minutes": 0
                }
                _LOGGER.info(f"Auto-started planned program {plan['program_name']} for {device_id}.")

            elif device_id not in self.running_devices and device_id not in self.learning_states:
                # Auto-Detect: Spontaneous start
                best_match_program = self._auto_detect_program(device_id)
                if best_match_program:
                    profile = self.get_program_profile(device_id, best_match_program)
                    self.running_devices[device_id] = {
                        "program_name": best_match_program,
                        "start_time": now,
                        "profile": profile,
                        "spontaneous": True,
                        "live_recording": [],
                        "zero_power_minutes": 0
                    }
                    _LOGGER.info(f"Auto-detected spontaneous program {best_match_program} for {device_id}.")

        # 3. Track live power and handle finish/learning
        if device_id in self.running_devices:
            run = self.running_devices[device_id]

            # Record live power for continuous learning
            run["live_recording"].append(current_power)

            if current_power <= 5.0:
                run["zero_power_minutes"] += 1
            else:
                run["zero_power_minutes"] = 0

            mins_running = (now - run["start_time"]).total_seconds() / 60.0

            # A program is considered completely finished if it has been running for at least 15 minutes
            # AND it has sat at 0W for 10 consecutive minutes (standby)
            if mins_running >= 15 and run["zero_power_minutes"] > 10:
                # Continuous Learning: Merge live recording into saved profile
                await self._merge_live_recording(device_id, run["program_name"], run["live_recording"])
                del self.running_devices[device_id]
            elif mins_running >= (len(run["profile"]) + 60):
                # Fallback: Force cancel if it runs 60 minutes longer than expected but never hit standby cleanly
                del self.running_devices[device_id]

    async def _merge_live_recording(self, device_id: str, program_name: str, live_recording: list[float]):
        """Blend a completed live recording into the saved profile using EMA."""
        if device_id not in self.data["devices"] or program_name not in self.data["devices"][device_id]["programs"]:
            return

        # Trim trailing zeros from the recording
        while live_recording and live_recording[-1] <= 5.0:
            live_recording.pop()

        if not live_recording:
            return

        saved_profile = self.data["devices"][device_id]["programs"][program_name]["profile_watt_per_minute"]

        new_profile = []
        max_len = max(len(saved_profile), len(live_recording))

        # Alpha: 0.2 means the new recording influences the saved profile by 20%.
        # This smooths out anomalies while gradually adapting to structural changes.
        alpha = 0.2

        for i in range(max_len):
            saved_val = saved_profile[i] if i < len(saved_profile) else 0.0
            live_val = live_recording[i] if i < len(live_recording) else 0.0

            if i >= len(saved_profile):
                # New run was longer, just append the new data
                new_profile.append(live_val)
            elif i >= len(live_recording):
                # New run was shorter. Taper off the saved profile towards zero faster.
                new_profile.append(saved_val * (1.0 - alpha))
            else:
                # Normal blend
                new_profile.append((saved_val * (1.0 - alpha)) + (live_val * alpha))

        self.data["devices"][device_id]["programs"][program_name]["profile_watt_per_minute"] = new_profile
        self.data["devices"][device_id]["programs"][program_name]["duration_minutes"] = len(new_profile)

        _LOGGER.info(f"Continuously learned and updated profile for {program_name} on {device_id}.")
        await self.async_save()

    def _auto_detect_program(self, device_id: str) -> str | None:
        """Find the most likely program based on user's current selection or history."""
        # For a full live pattern match we would need a rolling buffer of live power.
        # As a simple fallback for spontaneous starts, we use the currently selected program if valid,
        # or just the first available program, so the simulation has *some* curve to subtract.
        programs = self.get_programs(device_id)
        if not programs:
            return None

        # In a more advanced version, we would compare the last 5 minutes of power to the arrays.
        # For now, default to the first one to enable spontaneous tracking.
        return programs[0]