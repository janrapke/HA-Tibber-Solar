import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from homeassistant.helpers.storage import Store
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "smart_battery_optimizer.appliances"

@dataclass
class ApplianceProgram:
    id: str
    name: str
    # 1-minute power profile (Watts)
    power_profile: list[float] = field(default_factory=list)
    run_count: int = 0
    last_run: str | None = None

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "power_profile": self.power_profile,
            "run_count": self.run_count,
            "last_run": self.last_run
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data["id"],
            name=data["name"],
            power_profile=data.get("power_profile", []),
            run_count=data.get("run_count", 0),
            last_run=data.get("last_run", None)
        )

class SmartApplianceManager:
    def __init__(self, hass: HomeAssistant, entry_id: str):
        self.hass = hass
        self.entry_id = entry_id
        self.store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry_id}")
        # sensor_id -> list of ApplianceProgram
        self.programs: dict[str, list[ApplianceProgram]] = {}

    async def async_load(self):
        """Load stored appliance profiles."""
        data = await self.store.async_load()
        if data:
            for sensor_id, prog_list in data.items():
                self.programs[sensor_id] = [ApplianceProgram.from_dict(p) for p in prog_list]
        _LOGGER.debug(f"Loaded appliance programs: {self.programs}")

    async def async_save(self):
        """Save appliance profiles."""
        data_to_save = {
            sensor_id: [p.to_dict() for p in prog_list]
            for sensor_id, prog_list in self.programs.items()
        }
        await self.store.async_save(data_to_save)

    def get_programs(self, sensor_id: str) -> list[ApplianceProgram]:
        return self.programs.get(sensor_id, [])

    def add_program(self, sensor_id: str, program: ApplianceProgram):
        if sensor_id not in self.programs:
            self.programs[sensor_id] = []
        self.programs[sensor_id].append(program)

    def get_program_by_id(self, sensor_id: str, program_id: str) -> ApplianceProgram | None:
        progs = self.get_programs(sensor_id)
        for p in progs:
            if p.id == program_id:
                return p
        return None



    def delete_program(self, sensor_id: str, program_id: str) -> bool:
        if sensor_id in self.programs:
            initial_len = len(self.programs[sensor_id])
            self.programs[sensor_id] = [p for p in self.programs[sensor_id] if p.id != program_id]
            return len(self.programs[sensor_id]) < initial_len
        return False

import uuid
from enum import Enum

class ApplianceState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    WAITING_FOR_START = "waiting_for_start"
    RUNNING_SCHEDULED = "running_scheduled"
    RUNNING_SPONTANEOUS = "running_spontaneous"

@dataclass
class PlannedRun:
    program_id: str
    scheduled_start: datetime
    cost_estimate: float

class ApplianceStateMachine:
    """Manages the state of a single appliance sensor."""
    def __init__(self, sensor_id: str, manager: SmartApplianceManager):
        self.sensor_id = sensor_id
        self.manager = manager
        self.state = ApplianceState.IDLE

        # Power reading thresholds
        self.START_THRESHOLD_W = 10.0
        self.STOP_THRESHOLD_W = 5.0
        self.MIN_START_MINUTES = 1
        self.MIN_STOP_MINUTES = 5

        # State tracking
        self.high_power_minutes = 0
        self.low_power_minutes = 0
        self.current_recording: list[float] = []

        # Scheduling
        self.planned_run: PlannedRun | None = None
        self.active_program_id: str | None = None

    async def async_process_power_reading(self, power_w: float, current_time: datetime):
        """Process a 1-minute power reading tick."""
        if power_w >= self.START_THRESHOLD_W:
            self.high_power_minutes += 1
            self.low_power_minutes = 0
        else:
            self.low_power_minutes += 1
            self.high_power_minutes = 0

        # Start logic
        if self.state in [ApplianceState.IDLE, ApplianceState.WAITING_FOR_START]:
            if self.high_power_minutes >= self.MIN_START_MINUTES:
                if self.state == ApplianceState.WAITING_FOR_START and self._is_within_schedule_window(current_time):
                    self.state = ApplianceState.RUNNING_SCHEDULED
                    self.active_program_id = self.planned_run.program_id
                    _LOGGER.info(f"Appliance {self.sensor_id} started SCHEDULED run of {self.active_program_id}")
                else:
                    self.state = ApplianceState.RUNNING_SPONTANEOUS
                    self.active_program_id = self._guess_program(power_w)
                    _LOGGER.info(f"Appliance {self.sensor_id} started SPONTANEOUS run")
                    # Clear planned run if we started spontaneously
                    self.planned_run = None
                self.current_recording = [power_w] * self.high_power_minutes

        # Recording logic
        elif self.state == ApplianceState.RECORDING:
            self.current_recording.append(power_w)
            if self.low_power_minutes >= self.MIN_STOP_MINUTES:
                await self._finish_recording()

        # Running logic
        elif self.state in [ApplianceState.RUNNING_SCHEDULED, ApplianceState.RUNNING_SPONTANEOUS]:
            self.current_recording.append(power_w)
            if self.low_power_minutes >= self.MIN_STOP_MINUTES:
                await self._finish_run(current_time)

        # Check for missed schedule
        if self.state == ApplianceState.WAITING_FOR_START and self.planned_run:
            if current_time > self.planned_run.scheduled_start + timedelta(minutes=30):
                _LOGGER.info(f"Appliance {self.sensor_id} missed scheduled start. Dropping plan.")
                self.planned_run = None
                self.state = ApplianceState.IDLE

    def _is_within_schedule_window(self, current_time: datetime) -> bool:
        if not self.planned_run:
            return False
        # +/- 30 minutes window
        start = self.planned_run.scheduled_start - timedelta(minutes=30)
        end = self.planned_run.scheduled_start + timedelta(minutes=30)
        return start <= current_time <= end

    def start_manual_recording(self):
        self.state = ApplianceState.RECORDING
        self.current_recording = []
        self.high_power_minutes = 0
        self.low_power_minutes = 0
        self.planned_run = None
        _LOGGER.info(f"Started manual recording for {self.sensor_id}")

    async def _finish_recording(self):
        # Trim the trailing low power minutes
        if self.MIN_STOP_MINUTES > 0:
            profile = self.current_recording[:-self.MIN_STOP_MINUTES]
        else:
            profile = self.current_recording

        if not profile:
            self.state = ApplianceState.IDLE
            return

        progs = self.manager.get_programs(self.sensor_id)
        prog_id = str(uuid.uuid4())
        prog_name = f"Programm {len(progs) + 1}"

        new_prog = ApplianceProgram(
            id=prog_id,
            name=prog_name,
            power_profile=profile,
            run_count=1,
            last_run=datetime.now().isoformat()
        )
        self.manager.add_program(self.sensor_id, new_prog)
        await self.manager.async_save()

        self.state = ApplianceState.IDLE
        self.current_recording = []
        _LOGGER.info(f"Finished recording {prog_name} for {self.sensor_id}")

    async def _finish_run(self, current_time: datetime):
        # Trim trailing zeros
        if self.MIN_STOP_MINUTES > 0:
            profile = self.current_recording[:-self.MIN_STOP_MINUTES]
        else:
            profile = self.current_recording

        if self.active_program_id:
            prog = self.manager.get_program_by_id(self.sensor_id, self.active_program_id)
            if prog:
                # Merge and average the profiles
                self._merge_profiles(prog, profile)
                prog.run_count += 1
                prog.last_run = current_time.isoformat()
                await self.manager.async_save()

        self.state = ApplianceState.IDLE
        self.current_recording = []
        self.active_program_id = None
        self.planned_run = None
        _LOGGER.info(f"Finished run for {self.sensor_id}")

    def _merge_profiles(self, existing: ApplianceProgram, new_profile: list[float]):
        """Dynamically average the new profile into the existing one."""
        old = existing.power_profile
        merged = []
        max_len = max(len(old), len(new_profile))

        weight_old = existing.run_count
        if weight_old == 0: weight_old = 1

        for i in range(max_len):
            val_old = old[i] if i < len(old) else 0.0
            val_new = new_profile[i] if i < len(new_profile) else 0.0

            # Simple weighted average
            avg = ((val_old * weight_old) + val_new) / (weight_old + 1)
            merged.append(avg)

        existing.power_profile = merged

    def _guess_program(self, initial_power: float) -> str | None:
        """Attempt to guess the program based on initial power draw."""
        # For now, just pick the most frequently run, or None
        progs = self.manager.get_programs(self.sensor_id)
        if not progs:
            return None
        return sorted(progs, key=lambda p: p.run_count, reverse=True)[0].id

    def schedule_program(self, program_id: str, start_time: datetime, cost: float):
        self.planned_run = PlannedRun(program_id, start_time, cost)
        self.state = ApplianceState.WAITING_FOR_START
        _LOGGER.info(f"Scheduled {self.sensor_id} prog {program_id} for {start_time}")


    def cancel_planned_run(self):
        if self.state == ApplianceState.WAITING_FOR_START:
            self.planned_run = None
            self.state = ApplianceState.IDLE
            _LOGGER.info(f"Cancelled planned run for {self.sensor_id}")

@dataclass
class Proposal:
    start_time: datetime
    cost_estimate: float

class ProposalCalculator:
    """Calculates optimal start times and costs for appliance programs."""
    def __init__(self, coordinator):
        self.coordinator = coordinator

    def calculate_proposals(self, program: ApplianceProgram, horizon_hours: int = 24) -> list[Proposal]:
        """Generate time and cost proposals for the given program."""
        if not program.power_profile:
            return []

        import homeassistant.util.dt as dt_util
        now = dt_util.now().replace(tzinfo=None)
        # Round up to the next minute
        start_search = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        end_search = start_search + timedelta(hours=horizon_hours)

        # Determine duration in minutes
        duration_mins = len(program.power_profile)

        proposals = []

        # Evaluate every possible minute
        current_eval = start_search
        while current_eval + timedelta(minutes=duration_mins) <= end_search:
            cost = self._simulate_run_cost(program.power_profile, current_eval)
            proposals.append(Proposal(current_eval, cost))
            # Jump by 15 mins to save compute, or 1 min for absolute precision.
            # 15 mins is usually enough for Tibber intervals.
            current_eval += timedelta(minutes=15)

        if not proposals:
            return []

        # Sort strictly by lowest cost. The best price will always be first.
        proposals.sort(key=lambda p: p.cost_estimate)

        final_proposals = [proposals[0]]
        for p in proposals[1:]:
            if len(final_proposals) >= 4:
                break
            # Ensure it's not too close to already selected proposals
            too_close = False
            for selected in final_proposals:
                if abs((p.start_time - selected.start_time).total_seconds()) < 3600:
                    too_close = True
                    break
            if not too_close:
                final_proposals.append(p)

        return final_proposals

    def _simulate_run_cost(self, profile: list[float], start_time: datetime) -> float:
        """Simulate the cost of running the profile at the given start time."""
        total_cost = 0.0

        # For this, we need access to the coordinator's predicted battery and solar state.
        # This is a simplified forward simulation:
        # 1. Map minute -> 15min block
        # 2. Check predicted battery charge / solar at that block
        # 3. Calculate how much needs to be imported from grid (capped by max inverter)
        # 4. Multiply by tibber price

        # Get hourly plan from coordinator for context
        plan = self.coordinator.hourly_plan
        if not plan:
            return 999.0 # Fallback high cost

        from .const import (
            CONF_MAX_INVERTER_POWER_W,
            CONF_BATTERY_CAPACITY_WH,
            CONF_BATTERY_MIN_LIMIT_PCT
        )
        max_inv = float(self.coordinator.config.get(CONF_MAX_INVERTER_POWER_W, 800))
        batt_cap_wh = float(self.coordinator.config.get(CONF_BATTERY_CAPACITY_WH, 5000))
        min_batt_pct = float(self.coordinator.config.get(CONF_BATTERY_MIN_LIMIT_PCT, 10))
        min_batt_wh = batt_cap_wh * (min_batt_pct / 100.0)

        # Initialize tracking variables for available battery energy
        current_batt_wh = None
        last_block_idx = -1
        plan_batt_wh = None

        for min_idx, watts in enumerate(profile):
            run_minute = start_time + timedelta(minutes=min_idx)
            block_idx = self._get_block_index_for_time(run_minute, plan)

            if block_idx is None or block_idx >= len(plan):
                # Fallback to current price if beyond horizon
                price = getattr(self.coordinator, 'current_price', 0.3)
                total_cost += (watts / 60000.0) * price
                continue

            block = plan[block_idx]
            price = block.get('price', 0.3)
            action = str(block.get('planned_action', block.get('action', '')))

            # If we enter a new block, update our simulated battery level estimate
            if block_idx != last_block_idx:
                # Use the previous block's end percentage as the starting point, or the current block's if it's the first
                if block_idx > 0:
                    start_pct = plan[block_idx - 1].get('battery_pct_end', min_batt_pct)
                else:
                    start_pct = getattr(self.coordinator, 'current_battery_pct', plan[0].get('battery_pct_end', min_batt_pct))

                new_plan_batt_wh = batt_cap_wh * (start_pct / 100.0)

                if current_batt_wh is None:
                    # First initialization
                    current_batt_wh = new_plan_batt_wh
                else:
                    # We crossed into a new 15-minute block.
                    # The coordinator's plan advanced, adding some solar or drawing some house load.
                    # We apply the exact same delta to our simulated battery.
                    delta_wh = new_plan_batt_wh - plan_batt_wh
                    current_batt_wh += delta_wh
                    # Ensure we don't go below 0 or above capacity
                    current_batt_wh = max(0.0, min(batt_cap_wh, current_batt_wh))

                plan_batt_wh = new_plan_batt_wh
                last_block_idx = block_idx

            # Extract 15-min energy predictions from the block
            house_wh = block.get('house_wh', 0.0)
            balcony_wh = block.get('balcony_wh', 0.0)

            # Convert 15-min Wh to average Watts
            house_w = house_wh * 4.0
            balcony_w = balcony_wh * 4.0

            # Net house load that the inverter must cover (balcony feeds directly to house)
            baseline_house_load = max(0.0, house_w - balcony_w)

            # How much can we cover?
            # If the action dictates we are drawing from the grid (saving battery or empty)
            action_lower = action.lower()
            if 'netzbezug' in action_lower or 'aus' in action_lower or 'batterie am minimum' in action_lower:
                available_w = 0.0
            else:
                # The inverter capacity is shared with the rest of the house
                available_w = max(0.0, max_inv - baseline_house_load)

                # Further limit available_w by actual remaining battery capacity
                available_wh_reserve = max(0.0, current_batt_wh - min_batt_wh)
                # Convert available Wh reserve to continuous Watt equivalent for this 1 minute
                max_batt_w = available_wh_reserve * 60.0
                available_w = min(available_w, max_batt_w)

            # How much of the appliance's load cannot be covered by the inverter?
            uncovered_w = max(0.0, watts - available_w)

            # We use 'available_w' from the battery, subtract this energy from our simulated battery
            used_from_batt_w = watts - uncovered_w
            used_batt_wh = used_from_batt_w / 60.0
            current_batt_wh = max(0.0, current_batt_wh - used_batt_wh)

            # If the price is negative, we WANT grid draw to earn money.
            # In negative price situations, the action will usually contain 'dtu aus' so available_w is 0.
            # Thus, uncovered_w will be equal to watts, and grid_kwh * negative_price = negative cost (profit).

            # Safety factor:
            # Even if we think we can cover 100% from the battery, the user wants us to prefer
            # times with generally lower grid prices to mitigate risk (e.g. if house load spikes).
            # We assume a base 5% of the machine's consumption will always fall back to the grid.
            safety_margin_w = watts * 0.05

            # The effective grid draw for this minute
            effective_grid_w = max(uncovered_w, safety_margin_w)

            # kWh needed from grid for this minute
            grid_kwh = (effective_grid_w / 60.0) / 1000.0
            total_cost += grid_kwh * price

        return total_cost

    def _get_block_index_for_time(self, target_time: datetime, plan: list[dict]) -> int | None:
        """Find the block index in the plan for the given time."""
        for i, block in enumerate(plan):
            # plan contains "hour" key like "14:15", assume today/tomorrow based on target_time
            time_str = block.get('hour', '00:00')
            try:
                # The hour is just HH:MM, so we construct a full datetime for today/tomorrow
                h, m = map(int, time_str.split(':'))
                block_time = target_time.replace(hour=h, minute=m, second=0, microsecond=0)
                # If block_time is far in the past, it might be for tomorrow
                if target_time.hour < 12 and h > 18:
                    block_time = block_time - timedelta(days=1)
                elif target_time.hour > 12 and h < 6:
                    block_time = block_time + timedelta(days=1)

                if block_time <= target_time < block_time + timedelta(minutes=15):
                    return i
            except ValueError:
                continue
        return None
