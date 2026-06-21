from datetime import datetime
import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.text import TextEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class SmartApplianceBase(CoordinatorEntity):
    def __init__(self, coordinator, entry_id: str, sensor_id: str):
        super().__init__(coordinator)
        self.entry_id = entry_id
        self.sensor_id = sensor_id

        friendly_name = sensor_id.split(".")[-1].replace("_", " ").title()

        self._attr_has_entity_name = True
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry_id}_{sensor_id}")},
            "name": f"Smart Device: {friendly_name}",
            "manufacturer": "Smart Battery Optimizer",
            "via_device": (DOMAIN, entry_id),
        }

    @property
    def sm(self):
        return self.coordinator.appliance_state_machines.get(self.sensor_id)


class ApplianceProposalSelect(SmartApplianceBase, SelectEntity):
    """Shows up to 4 optimized start proposals. User picks one and confirms."""

    def __init__(self, coordinator, entry_id, sensor_id):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_proposal_select"
        self._attr_name = "Vorgeschlagene Startzeiten"
        self._attr_icon = "mdi:clock-outline"
        self._current_option: str | None = None
        self._cached_proposals = None
        self._cached_options: list[str] = []

    def force_recalculate(self):
        self.coordinator.proposal_calculator.invalidate(self.sensor_id)
        self._current_option = None
        self._cached_proposals = None
        self._cached_options = []

    def _get_proposals(self):
        proposals = self.coordinator.proposal_calculator.calculate_proposals(self.sensor_id)
        # Only re-format option strings when proposals actually change (cache miss in calculator)
        if proposals is not self._cached_proposals:
            self._cached_proposals = proposals
            self._cached_options = self._format_options(proposals)
        return proposals

    def _format_options(self, proposals) -> list[str]:
        if not proposals:
            return ["Keine Vorschläge"]
        import homeassistant.util.dt as dt_util
        now = dt_util.now().replace(tzinfo=None)
        opts = []
        for p in proposals:
            time_str = p.start_time.strftime("%H:%M")
            td = p.start_time - now
            total_sec = max(0, int(td.total_seconds()))
            hours, rem = divmod(total_sec, 3600)
            minutes, _ = divmod(rem, 60)
            if hours > 0 and minutes > 0:
                delay_str = f"in {hours}h {minutes}m"
            elif hours > 0:
                delay_str = f"in {hours}h"
            else:
                delay_str = f"in {minutes}m"
            cost_cents = round(p.cost_estimate * 100)
            solar_str = " ☀️" if p.uses_solar_excess else ""
            bootstrap_str = " (Schätzung)" if p.is_bootstrap else ""
            opts.append(f"{p.label}: {time_str} ({delay_str}) - {cost_cents}ct{solar_str}{bootstrap_str}")
        return opts

    @property
    def options(self) -> list[str]:
        self._get_proposals()
        return self._cached_options if self._cached_options else ["Keine Vorschläge"]

    @property
    def current_option(self) -> str | None:
        opts = self.options
        if self._current_option and self._current_option in opts:
            return self._current_option
        return opts[0] if opts else None

    async def async_select_option(self, option: str) -> None:
        self._current_option = option
        self.async_write_ha_state()

    def get_selected_proposal(self):
        proposals = self._get_proposals()
        if not proposals:
            return None
        opts = self.options
        current = self.current_option
        if not current or current in ("Keine Vorschläge",):
            return None
        try:
            idx = opts.index(current)
            return proposals[idx] if idx < len(proposals) else None
        except ValueError:
            return None


class ApplianceConfirmButton(SmartApplianceBase, ButtonEntity):
    """Confirm the selected proposal and schedule the run."""

    def __init__(self, coordinator, entry_id, sensor_id, proposal_select: ApplianceProposalSelect):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_confirm_btn"
        self._attr_name = "Plan bestätigen"
        self._attr_icon = "mdi:check-circle"
        self.proposal_select = proposal_select

    async def async_press(self) -> None:
        if not self.sm:
            return
        proposal = self.proposal_select.get_selected_proposal()
        if proposal:
            # program_id may be None (bootstrap mode) — state machine handles both
            prog = self.coordinator.appliance_manager.get_program(self.sensor_id)
            prog_id = prog.id if prog else None
            self.sm.schedule_program(prog_id, proposal.start_time, proposal.cost_estimate)
            await self.coordinator.async_request_refresh()


class ApplianceCancelButton(SmartApplianceBase, ButtonEntity):
    """Cancel a scheduled run."""

    def __init__(self, coordinator, entry_id, sensor_id):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_cancel_btn"
        self._attr_name = "Plan abbrechen"
        self._attr_icon = "mdi:close-circle"

    async def async_press(self) -> None:
        if self.sm:
            self.sm.cancel_planned_run()
            await self.coordinator.async_request_refresh()


class ApplianceStatusSensor(SmartApplianceBase, SensorEntity):
    """Shows the current status, countdown, and probability breakdown."""

    def __init__(self, coordinator, entry_id, sensor_id):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_status"
        self._attr_name = "Status"
        self._attr_icon = "mdi:information-outline"

    @property
    def native_value(self) -> str:
        if not self.sm:
            return "Unbekannt"

        if self.sm.state.value == "waiting_for_start" and self.sm.planned_run:
            import homeassistant.util.dt as dt_util
            now = dt_util.now().replace(tzinfo=None)
            start = self.sm.planned_run.scheduled_start
            if start > now:
                td = start - now
                hours, rem = divmod(int(td.total_seconds()), 3600)
                minutes, _ = divmod(rem, 60)
                return f"Start in {hours}h {minutes}m"
            return "Wartet auf Start..."

        if self.sm.state.value in ("running_scheduled", "running_spontaneous"):
            probs = self.sm.program_probabilities
            if probs:
                best_id = max(probs, key=lambda k: probs[k])
                prog = self.coordinator.appliance_manager.get_program_by_id(self.sensor_id, best_id)
                prog_name = prog.name if prog else "Unbekannt"
                pct = round(probs[best_id] * 100)
                return f"Läuft: {prog_name} ({pct}%)"
            return "Läuft..."

        state_map = {
            "idle": "Bereit",
            "waiting_for_start": "Wartet auf Start...",
        }
        return state_map.get(self.sm.state.value, self.sm.state.value)

    @property
    def extra_state_attributes(self):
        attrs = {}
        manager = self.coordinator.appliance_manager
        programs = manager.get_programs(self.sensor_id)

        if programs:
            attrs["programme"] = [
                {
                    "name": p.name,
                    "dauer_min": p.duration_minutes,
                    "avg_leistung_w": round(p.effective_power_w, 0),
                    "laeufe": p.run_count,
                    "letzter_lauf": p.last_run,
                }
                for p in programs
            ]
        else:
            attrs["hinweis"] = "Noch kein Programm gelernt. Gerät einmal laufen lassen."

        if self.sm and self.sm.program_probabilities:
            attrs["wahrscheinlichkeiten"] = {
                (manager.get_program_by_id(self.sensor_id, pid) or type("", (), {"name": pid})()).name:
                f"{round(v * 100)}%"
                for pid, v in self.sm.program_probabilities.items()
            }

        if self.sm and self.sm.planned_run:
            attrs["geplanter_start"] = self.sm.planned_run.scheduled_start.isoformat()
            attrs["geschaetzte_kosten_ct"] = round(self.sm.planned_run.cost_estimate * 100, 1)

        return attrs


class ApplianceTimerSensor(SmartApplianceBase, SensorEntity):
    """Shows the countdown to the planned start time."""

    def __init__(self, coordinator, entry_id, sensor_id):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_timer"
        self._attr_name = "Timer"
        self._attr_icon = "mdi:timer-sand"

    @property
    def native_value(self) -> str:
        if not self.sm or not self.sm.planned_run:
            return "Nicht geplant"

        import homeassistant.util.dt as dt_util
        now = dt_util.now().replace(tzinfo=None)
        start = self.sm.planned_run.scheduled_start

        if start > now:
            td = start - now
            hours, rem = divmod(int(td.total_seconds()), 3600)
            minutes, _ = divmod(rem, 60)
            return f"{hours}h {minutes}m"
        return "0h 0m"


class ApplianceProgramNameText(SmartApplianceBase, TextEntity):
    """Optional: rename an auto-detected program by its index."""

    def __init__(self, coordinator, entry_id, sensor_id):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_prog_rename"
        self._attr_name = "Programm umbenennen"
        self._attr_icon = "mdi:pencil"
        self._attr_native_value = ""
        self._attr_pattern = r".{2,30}"

    @property
    def native_value(self) -> str | None:
        return self._attr_native_value

    async def async_set_value(self, value: str) -> None:
        """Format: 'Programmname|N' where N is the 1-based program index."""
        if "|" in value:
            name, _, idx_str = value.rpartition("|")
            try:
                idx = int(idx_str) - 1
                programs = self.coordinator.appliance_manager.get_programs(self.sensor_id)
                if 0 <= idx < len(programs):
                    programs[idx].name = name.strip()
                    await self.coordinator.appliance_manager.async_save()
                    self.coordinator.proposal_calculator.invalidate(self.sensor_id)
            except (ValueError, IndexError):
                pass
        self._attr_native_value = value
        self.async_write_ha_state()
