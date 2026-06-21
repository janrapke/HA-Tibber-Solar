import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from homeassistant.helpers.storage import Store
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "smart_battery_optimizer.appliances"

# Bootstrap window used for proposals before any profile is learned
DEFAULT_BOOTSTRAP_MINUTES = 180

# Decay factor applied to all program scores after each run (0.7 = recent runs dominate)
SCORE_DECAY = 0.7


@dataclass
class ApplianceProgram:
    id: str
    name: str
    power_profile: list[float] = field(default_factory=list)
    run_count: int = 0
    last_run: str | None = None
    probability_score: float = 0.0

    @property
    def duration_minutes(self) -> int:
        return len(self.power_profile)

    @property
    def total_wh(self) -> float:
        return sum(self.power_profile) / 60.0 if self.power_profile else 0.0

    @property
    def effective_power_w(self) -> float:
        if not self.power_profile:
            return 0.0
        return sum(self.power_profile) / len(self.power_profile)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "power_profile": self.power_profile,
            "run_count": self.run_count,
            "last_run": self.last_run,
            "probability_score": self.probability_score,
        }

    @classmethod
    def from_dict(cls, data):
        profile = data.get("power_profile", [])
        # Migration: if old format had duration_minutes/estimated_power_w but no profile
        if not profile and "duration_minutes" in data:
            dur = int(data["duration_minutes"])
            pw = float(data.get("estimated_power_w", data.get("learned_power_w") or 1000.0))
            profile = [pw] * dur
        return cls(
            id=data["id"],
            name=data["name"],
            power_profile=profile,
            run_count=data.get("run_count", 0),
            last_run=data.get("last_run", None),
            probability_score=float(data.get("probability_score", 0.0)),
        )


class SmartApplianceManager:
    def __init__(self, hass: HomeAssistant, entry_id: str):
        self.hass = hass
        self.entry_id = entry_id
        self.store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry_id}")
        self.programs: dict[str, list[ApplianceProgram]] = {}
        self.device_settings: dict[str, dict] = {}

    async def async_load(self):
        data = await self.store.async_load()
        if data:
            raw = {}
            if isinstance(data, dict) and "programs" in data:
                raw = data["programs"]
                self.device_settings = data.get("device_settings", {})
            else:
                raw = data  # very old format

            for sensor_id, val in raw.items():
                if isinstance(val, list):
                    self.programs[sensor_id] = [ApplianceProgram.from_dict(p) for p in val]
                elif isinstance(val, dict):
                    self.programs[sensor_id] = [ApplianceProgram.from_dict(val)]
        _LOGGER.debug("Loaded appliance programs: %s", {k: len(v) for k, v in self.programs.items()})

    async def async_save(self):
        await self.store.async_save({
            "programs": {
                sensor_id: [p.to_dict() for p in prog_list]
                for sensor_id, prog_list in self.programs.items()
            },
            "device_settings": self.device_settings,
        })

    def get_schedule_granularity(self, sensor_id: str) -> int:
        """Return proposal slot granularity in minutes (0=exact clock time, 15/30/60=delay steps)."""
        return self.device_settings.get(sensor_id, {}).get("schedule_granularity_minutes", 0)

    def set_device_setting(self, sensor_id: str, key: str, value) -> None:
        if sensor_id not in self.device_settings:
            self.device_settings[sensor_id] = {}
        self.device_settings[sensor_id][key] = value

    def get_programs(self, sensor_id: str) -> list[ApplianceProgram]:
        return self.programs.get(sensor_id, [])

    def get_program_by_id(self, sensor_id: str, program_id: str) -> ApplianceProgram | None:
        for p in self.get_programs(sensor_id):
            if p.id == program_id:
                return p
        return None

    def get_program(self, sensor_id: str) -> ApplianceProgram | None:
        """Return the most likely program to be used next (highest recency-decayed probability score)."""
        progs = self.get_programs(sensor_id)
        if not progs:
            return None
        return max(progs, key=lambda p: p.probability_score)

    def add_program(self, sensor_id: str, program: ApplianceProgram):
        if sensor_id not in self.programs:
            self.programs[sensor_id] = []
        self.programs[sensor_id].append(program)

    def find_matching_program(self, sensor_id: str, duration: int, total_wh: float) -> ApplianceProgram | None:
        """Find an existing program that matches this run by duration and total energy."""
        for prog in self.get_programs(sensor_id):
            if not prog.power_profile:
                continue
            dur_ratio = abs(duration - prog.duration_minutes) / max(prog.duration_minutes, 1)
            wh_ratio = abs(total_wh - prog.total_wh) / max(prog.total_wh, 0.1)
            if dur_ratio < 0.20 and wh_ratio < 0.25:
                return prog
        return None


class ApplianceState(Enum):
    IDLE = "idle"
    WAITING_FOR_START = "waiting_for_start"
    RUNNING_SCHEDULED = "running_scheduled"
    RUNNING_SPONTANEOUS = "running_spontaneous"


@dataclass
class PlannedRun:
    program_id: str | None  # None = bootstrap (no learned program yet)
    scheduled_start: datetime
    cost_estimate: float


class ApplianceStateMachine:
    def __init__(self, sensor_id: str, manager: SmartApplianceManager):
        self.sensor_id = sensor_id
        self.manager = manager
        self.state = ApplianceState.IDLE

        self.START_THRESHOLD_W = 10.0
        self.MIN_START_MINUTES = 1
        self.MIN_STOP_MINUTES = 5

        self.high_power_minutes = 0
        self.low_power_minutes = 0
        self.current_recording: list[float] = []
        self._run_start_time: datetime | None = None
        self._running_energy_wh: float = 0.0

        self.planned_run: PlannedRun | None = None
        self.active_program_id: str | None = None
        self.program_probabilities: dict[str, float] = {}

    async def async_process_power_reading(self, power_w: float, current_time: datetime):
        if power_w >= self.START_THRESHOLD_W:
            self.high_power_minutes += 1
            self.low_power_minutes = 0
        else:
            self.low_power_minutes += 1
            self.high_power_minutes = 0

        if self.state in (ApplianceState.IDLE, ApplianceState.WAITING_FOR_START):
            if self.high_power_minutes >= self.MIN_START_MINUTES:
                if (self.state == ApplianceState.WAITING_FOR_START
                        and self._is_within_schedule_window(current_time)):
                    self.state = ApplianceState.RUNNING_SCHEDULED
                    _LOGGER.info("Appliance %s started scheduled run", self.sensor_id)
                else:
                    self.state = ApplianceState.RUNNING_SPONTANEOUS
                    self.planned_run = None
                    _LOGGER.info("Appliance %s started spontaneous run", self.sensor_id)
                self._run_start_time = current_time - timedelta(minutes=self.high_power_minutes - 1)
                self.current_recording = [power_w] * self.high_power_minutes
                self._running_energy_wh = sum(self.current_recording) / 60.0
                self.program_probabilities = {}
                self.active_program_id = None

        elif self.state in (ApplianceState.RUNNING_SCHEDULED, ApplianceState.RUNNING_SPONTANEOUS):
            self.current_recording.append(power_w)
            self._running_energy_wh += power_w / 60.0
            self._update_program_probabilities(current_time)
            if self.low_power_minutes >= self.MIN_STOP_MINUTES:
                await self._finish_run(current_time)

        # Missed start
        if self.state == ApplianceState.WAITING_FOR_START and self.planned_run:
            if current_time > self.planned_run.scheduled_start + timedelta(minutes=30):
                _LOGGER.info("Appliance %s missed scheduled start", self.sensor_id)
                self.planned_run = None
                self.state = ApplianceState.IDLE

    def _is_within_schedule_window(self, current_time: datetime) -> bool:
        if not self.planned_run:
            return False
        start = self.planned_run.scheduled_start - timedelta(minutes=30)
        end = self.planned_run.scheduled_start + timedelta(minutes=30)
        return start <= current_time <= end

    def _update_program_probabilities(self, current_time: datetime):
        """Update per-program probability based on energy consumed so far vs expected."""
        minutes_so_far = len(self.current_recording)
        wh_so_far = self._running_energy_wh

        programs = self.manager.get_programs(self.sensor_id)
        if not programs:
            return

        scores: dict[str, float] = {}
        for prog in programs:
            if not prog.power_profile or minutes_so_far > prog.duration_minutes + 10:
                scores[prog.id] = 0.0
                continue
            # Expected Wh at this point in the program
            expected_wh = sum(prog.power_profile[:minutes_so_far]) / 60.0
            if expected_wh > 0:
                error = abs(wh_so_far - expected_wh) / expected_wh
                scores[prog.id] = max(0.0, 1.0 - error * 2)
            else:
                scores[prog.id] = 0.0

        total = sum(scores.values())
        if total > 0:
            self.program_probabilities = {pid: s / total for pid, s in scores.items()}
        else:
            self.program_probabilities = {pid: 1.0 / len(programs) for prog in programs for pid in [prog.id]}

        # Track most likely active program
        if self.program_probabilities:
            best = max(self.program_probabilities, key=lambda k: self.program_probabilities[k])
            if self.program_probabilities[best] > 0.5:
                self.active_program_id = best

    async def _finish_run(self, current_time: datetime):
        recording = (self.current_recording[:-self.MIN_STOP_MINUTES]
                     if self.MIN_STOP_MINUTES > 0 else self.current_recording)

        if recording and len(recording) >= 3:
            duration = len(recording)
            total_wh = sum(recording) / 60.0
            matched = self.manager.find_matching_program(self.sensor_id, duration, total_wh)

            if matched:
                _merge_profiles(matched, recording)
                matched.run_count += 1
                matched.last_run = current_time.isoformat()
                self.active_program_id = matched.id
                winning_prog = matched
                _LOGGER.info(
                    "Appliance %s: merged run into '%s' (%d min, %.0fW avg)",
                    self.sensor_id, matched.name, duration, total_wh * 60 / duration
                )
            else:
                n = len(self.manager.get_programs(self.sensor_id)) + 1
                new_prog = ApplianceProgram(
                    id=str(uuid.uuid4()),
                    name=f"Programm {n}",
                    power_profile=[round(w, 1) for w in recording],
                    run_count=1,
                    last_run=current_time.isoformat(),
                )
                self.manager.add_program(self.sensor_id, new_prog)
                self.active_program_id = new_prog.id
                winning_prog = new_prog
                _LOGGER.info(
                    "Appliance %s: new program '%s' detected (%d min, %.0fW avg)",
                    self.sensor_id, new_prog.name, duration, total_wh * 60 / duration
                )

            # Decay all scores, then reward the winning program
            for p in self.manager.get_programs(self.sensor_id):
                p.probability_score *= SCORE_DECAY
            winning_prog.probability_score += 1.0

            await self.manager.async_save()

        self.state = ApplianceState.IDLE
        self.current_recording = []
        self._run_start_time = None
        self._running_energy_wh = 0.0
        self.planned_run = None

    def schedule_program(self, program_id: str | None, start_time: datetime, cost: float):
        self.planned_run = PlannedRun(program_id, start_time, cost)
        self.state = ApplianceState.WAITING_FOR_START
        _LOGGER.info("Appliance %s scheduled for %s (prog=%s)", self.sensor_id, start_time, program_id)

    def cancel_planned_run(self):
        if self.state == ApplianceState.WAITING_FOR_START:
            self.planned_run = None
            self.state = ApplianceState.IDLE


def _merge_profiles(prog: ApplianceProgram, new_recording: list[float]):
    """Merge a new run into the stored profile using weighted averaging."""
    old = prog.power_profile
    weight_old = max(1, prog.run_count)
    max_len = max(len(old), len(new_recording))
    merged = []
    for i in range(max_len):
        v_old = old[i] if i < len(old) else 0.0
        v_new = new_recording[i] if i < len(new_recording) else 0.0
        merged.append((v_old * weight_old + v_new) / (weight_old + 1))
    prog.power_profile = [round(w, 1) for w in merged]


@dataclass
class Proposal:
    start_time: datetime
    cost_estimate: float
    avg_price: float = 0.0
    solar_pct: float = 0.0
    uses_solar_excess: bool = False
    category: str = "cheapest"
    label: str = ""
    is_bootstrap: bool = False


class ProposalCalculator:
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._cache: dict[str, dict] = {}

    def invalidate(self, sensor_id: str):
        self._cache.pop(sensor_id, None)

    def calculate_proposals(self, sensor_id: str) -> list[Proposal]:
        """Generate proposals for a device. Uses most recently run program if learned, else 3h bootstrap window."""
        import homeassistant.util.dt as dt_util
        now = dt_util.now().replace(tzinfo=None)

        # Pick most likely program (highest run count = statistically most probable next use)
        program = self.coordinator.appliance_manager.get_program(sensor_id)

        cache_key = f"{sensor_id}_{program.id if program else 'bootstrap'}"
        cache_entry = self._cache.get(cache_key)
        if cache_entry and (now - cache_entry["time"]).total_seconds() < 1800:
            return cache_entry["proposals"]

        if program and program.power_profile:
            profile = program.power_profile
            duration_minutes = len(profile)
            flat_power_w = None
            is_bootstrap = False
        else:
            # Bootstrap: no learned profile yet, use 3h flat window
            profile = None
            duration_minutes = DEFAULT_BOOTSTRAP_MINUTES
            flat_power_w = 1000.0  # placeholder — real cost unknown
            is_bootstrap = True

        granularity = self.coordinator.appliance_manager.get_schedule_granularity(sensor_id)
        base = now.replace(second=0, microsecond=0)
        end_search = now + timedelta(hours=48)

        if granularity == 0:
            # Uhrzeit mode: absolute 15-min clock slots (14:00, 14:15, 14:30...)
            step = 15
            start_search = base + timedelta(hours=1)
            remainder = start_search.minute % step
            if remainder != 0:
                start_search += timedelta(minutes=(step - remainder))
        else:
            # Delay mode: slots anchored to now (now+step, now+2*step, ...)
            step = granularity
            min_lead = max(step, 30)
            steps_needed = math.ceil(min_lead / step)
            start_search = base + timedelta(minutes=steps_needed * step)

        all_slots: list[Proposal] = []
        current_eval = start_search
        while current_eval + timedelta(minutes=duration_minutes) <= end_search:
            cost, avg_price, solar_pct = self._simulate_run_cost(
                profile, flat_power_w, duration_minutes, current_eval
            )
            all_slots.append(Proposal(
                start_time=current_eval,
                cost_estimate=cost,
                avg_price=avg_price,
                solar_pct=solar_pct,
                uses_solar_excess=solar_pct >= 20.0,
                is_bootstrap=is_bootstrap,
            ))
            current_eval += timedelta(minutes=step)

        if not all_slots:
            return []

        # Apply optional time-window constraints
        settings = self.coordinator.appliance_manager.device_settings.get(sensor_id, {})
        earliest_h = int(settings.get("earliest_start_hour", 0))
        latest_end_h = int(settings.get("latest_end_hour", 23))

        if earliest_h > 0 or latest_end_h < 23:
            filtered = []
            for slot in all_slots:
                if slot.start_time.hour < earliest_h:
                    continue
                end_time = slot.start_time + timedelta(minutes=duration_minutes)
                # latest_end_h means "must be done by HH:59" — allow any minute within that hour
                if end_time.hour > latest_end_h or (end_time.hour == latest_end_h and end_time.minute > 59):
                    continue
                filtered.append(slot)
            all_slots = filtered if filtered else all_slots  # never leave user with zero options

        cheapest = min(all_slots, key=lambda p: p.cost_estimate)
        cheapest.category = "cheapest"
        cheapest.label = "Günstigster"

        solar_best = max(all_slots, key=lambda p: p.solar_pct)
        solar_best.category = "solar"
        solar_best.label = "Solar Optimal"

        soonest = all_slots[0]
        soonest.category = "soonest"
        soonest.label = "Frühester"

        result: list[Proposal] = [cheapest]
        seen: set = {cheapest.start_time}
        for p in [solar_best, soonest]:
            if p.start_time not in seen:
                result.append(p)
                seen.add(p.start_time)

        self._cache[cache_key] = {"proposals": result, "time": now}
        return result

    def _simulate_run_cost(
        self,
        profile: list[float] | None,
        flat_power_w: float | None,
        duration_minutes: int,
        start_time: datetime,
    ) -> tuple[float, float, float]:
        from .const import CONF_MAX_INVERTER_POWER_W, CONF_BATTERY_CAPACITY_WH, CONF_BATTERY_MIN_LIMIT_PCT

        plan = self.coordinator.hourly_plan
        if not plan:
            return 999.0, 0.3, 0.0

        batt_cap_wh = float(self.coordinator.config.get(CONF_BATTERY_CAPACITY_WH, 5000))
        min_batt_pct = float(self.coordinator.config.get(CONF_BATTERY_MIN_LIMIT_PCT, 10))
        min_batt_wh = batt_cap_wh * (min_batt_pct / 100.0)
        max_inv = float(self.coordinator.config.get(CONF_MAX_INVERTER_POWER_W, 800))

        current_batt_wh = None
        last_block_idx = -1
        plan_batt_wh = None
        total_cost = 0.0
        total_price_sum = 0.0
        solar_covered_minutes = 0

        for min_idx in range(duration_minutes):
            minute_w = (profile[min_idx] if (profile and min_idx < len(profile))
                        else (flat_power_w or 1000.0))
            run_minute = start_time + timedelta(minutes=min_idx)
            block_idx = self._get_block_index_for_time(run_minute, plan)

            if block_idx is None or block_idx >= len(plan):
                price = getattr(self.coordinator, "current_price", 0.3)
                total_cost += (minute_w / 60000.0) * price
                total_price_sum += price
                continue

            block = plan[block_idx]
            price = block.get("price", 0.3)
            action = str(block.get("planned_action", block.get("action", "")))

            if block_idx != last_block_idx:
                if block_idx > 0:
                    start_pct = plan[block_idx - 1].get("battery_pct_end", min_batt_pct)
                else:
                    start_pct = getattr(self.coordinator, "current_battery_pct",
                                        plan[0].get("battery_pct_end", min_batt_pct))
                new_plan_batt_wh = batt_cap_wh * (start_pct / 100.0)
                if current_batt_wh is None:
                    current_batt_wh = new_plan_batt_wh
                else:
                    delta = new_plan_batt_wh - plan_batt_wh
                    current_batt_wh = max(0.0, min(batt_cap_wh, current_batt_wh + delta))
                plan_batt_wh = new_plan_batt_wh
                last_block_idx = block_idx

            will_overfill = block.get("will_overfill", False) or block.get("battery_pct_end", 0) >= 99.0
            house_w = block.get("house_wh", 0.0) * 4.0
            balcony_w = block.get("balcony_wh", 0.0) * 4.0
            baseline = max(0.0, house_w - balcony_w)

            if "batterie am minimum" in action.lower() and current_batt_wh <= min_batt_wh:
                available_w = 0.0
            else:
                available_w = max(0.0, max_inv - baseline)
                reserve_wh = max(0.0, current_batt_wh - min_batt_wh)
                available_w = min(available_w, reserve_wh * 60.0)

            used_w = min(minute_w, available_w)
            current_batt_wh = max(0.0, current_batt_wh - used_w / 60.0)
            uncovered_w = max(0.0, minute_w - used_w)
            effective_w = max(uncovered_w, minute_w * 0.05)

            if will_overfill and used_w > 0:
                effective_cost_w = effective_w
                solar_covered_minutes += 1
            else:
                effective_cost_w = minute_w

            total_cost += (effective_cost_w / 60.0 / 1000.0) * price
            total_price_sum += price

        avg_price = total_price_sum / duration_minutes if duration_minutes > 0 else 0.0
        solar_pct = solar_covered_minutes / duration_minutes * 100.0 if duration_minutes > 0 else 0.0
        return total_cost, avg_price, solar_pct

    def _get_block_index_for_time(self, target_time: datetime, plan: list[dict]) -> int | None:
        for i, block in enumerate(plan):
            time_str = block.get("hour", "00:00")
            try:
                h, m = map(int, time_str.split(":"))
                block_time = target_time.replace(hour=h, minute=m, second=0, microsecond=0)
                if target_time.hour < 12 and h > 18:
                    block_time -= timedelta(days=1)
                elif target_time.hour > 12 and h < 6:
                    block_time += timedelta(days=1)
                if block_time <= target_time < block_time + timedelta(minutes=15):
                    return i
            except ValueError:
                continue
        return None
