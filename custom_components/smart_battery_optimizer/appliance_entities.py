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

        # Determine a friendly name from the sensor ID
        friendly_name = sensor_id.split('.')[-1].replace('_', ' ').title()

        self._attr_has_entity_name = True
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry_id}_{sensor_id}")},
            "name": f"Smart Device: {friendly_name}",
            "manufacturer": "Smart Battery Optimizer",
            "via_device": (DOMAIN, entry_id)
        }

    @property
    def sm(self):
        return self.coordinator.appliance_state_machines.get(self.sensor_id)


class ApplianceRecordButton(SmartApplianceBase, ButtonEntity):
    """Button to start recording a new program."""
    def __init__(self, coordinator, entry_id, sensor_id):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_record_btn"
        self._attr_name = "Aufzeichnen"
        self._attr_icon = "mdi:record-circle"

    async def async_press(self) -> None:
        if self.sm:
            self.sm.start_manual_recording()
            await self.coordinator.async_request_refresh()


class ApplianceProgramSelect(SmartApplianceBase, SelectEntity):
    """Select a program to calculate proposals for."""
    def __init__(self, coordinator, entry_id, sensor_id):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_prog_select"
        self._attr_name = "Programm Auswahl"
        self._attr_icon = "mdi:format-list-bulleted"
        self._current_option = None

    @property
    def options(self) -> list[str]:
        if not self.sm: return []
        progs = self.sm.manager.get_programs(self.sensor_id)
        return [p.name for p in progs]

    @property
    def current_option(self) -> str | None:
        if self._current_option and self._current_option in self.options:
            return self._current_option
        return self.options[0] if self.options else None

    async def async_select_option(self, option: str) -> None:
        self._current_option = option
        # Find the proposal select and force recalculation
        for entity in self.coordinator.appliance_entities.get('select', []):
            if isinstance(entity, ApplianceProposalSelect) and entity.sensor_id == self.sensor_id:
                entity.force_recalculate()
        await self.coordinator.async_request_refresh()


class ApplianceProposalSelect(SmartApplianceBase, SelectEntity):
    """Select one of the proposed times."""
    def __init__(self, coordinator, entry_id, sensor_id, prog_select: ApplianceProgramSelect):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_proposal_select"
        self._attr_name = "Vorgeschlagene Zeiten"
        self._attr_icon = "mdi:clock-outline"
        self.prog_select = prog_select
        self._proposals = []
        self._current_option = None

    def _update_proposals(self):
        # We cache proposals and only recalculate if they are empty.
        # Recalculation is heavy. To force a refresh, clear self._proposals first.
        if self._proposals:
            return

        if not self.sm or not self.prog_select.current_option:
            self._proposals = []
            return

        progs = self.sm.manager.get_programs(self.sensor_id)
        prog = next((p for p in progs if p.name == self.prog_select.current_option), None)
        if not prog:
            self._proposals = []
            return

        self._proposals = self.coordinator.proposal_calculator.calculate_proposals(prog)

    def force_recalculate(self):
        self._proposals = []
        self._update_proposals()


    @property
    def options(self) -> list[str]:
        self._update_proposals()
        if not self._proposals:
            return ["Keine Vorschläge"]

        opts = []
        for i, p in enumerate(self._proposals):
            time_str = p.start_time.strftime("%H:%M")
            day_str = "Heute" if p.start_time.date() == datetime.now().date() else "Morgen"
            cost_str = f"€ {p.cost_estimate:.2f}"
            avg_str = f"Ø {int(p.avg_price * 100)}ct"
            opts.append(f"{i+1}. {day_str} {time_str} ({cost_str} | {avg_str})")
        return opts

    @property
    def current_option(self) -> str | None:
        opts = self.options
        if self._current_option and self._current_option in opts:
            return self._current_option
        return opts[0] if opts else None

    async def async_select_option(self, option: str) -> None:
        self._current_option = option
        # Find the proposal select and force recalculation
        for entity in self.coordinator.appliance_entities.get('select', []):
            if isinstance(entity, ApplianceProposalSelect) and entity.sensor_id == self.sensor_id:
                entity.force_recalculate()
        await self.coordinator.async_request_refresh()

    def get_selected_proposal(self):
        self._update_proposals()
        if not self._proposals or not self.current_option: return None
        try:
            # Extract index from "1. Heute..."
            idx = int(self.current_option.split(".")[0]) - 1
            return self._proposals[idx]
        except (ValueError, IndexError):
            return None



class ApplianceDeleteButton(SmartApplianceBase, ButtonEntity):
    """Button to delete the currently selected program."""
    def __init__(self, coordinator, entry_id, sensor_id, prog_select: ApplianceProgramSelect):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_delete_btn"
        self._attr_name = "Programm löschen"
        self._attr_icon = "mdi:delete"
        self.prog_select = prog_select

    async def async_press(self) -> None:
        if not self.sm: return

        prog_name = self.prog_select.current_option
        if not prog_name: return

        progs = self.sm.manager.get_programs(self.sensor_id)
        prog = next((p for p in progs if p.name == prog_name), None)

        if prog:
            # Delete it
            self.sm.manager.delete_program(self.sensor_id, prog.id)
            await self.sm.manager.async_save()

            # If this is the active planned program, cancel it
            if self.sm.planned_run and self.sm.planned_run.program_id == prog.id:
                self.sm.cancel_planned_run()

            # Reset select option
            self.prog_select._current_option = None

            # Force proposals to update
            for entity in self.coordinator.appliance_entities.get('select', []):
                if isinstance(entity, ApplianceProposalSelect) and entity.sensor_id == self.sensor_id:
                    entity.force_recalculate()

            await self.coordinator.async_request_refresh()


class ApplianceManualTimeText(SmartApplianceBase, TextEntity):
    """Text entity to specify a manual start time (HH:MM)."""
    def __init__(self, coordinator, entry_id, sensor_id):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_manual_time"
        self._attr_name = "Manuelle Startzeit (HH:MM)"
        self._attr_icon = "mdi:clock-edit-outline"
        self._attr_native_value = ""

    @property
    def native_value(self) -> str | None:
        return self._attr_native_value

    async def async_set_value(self, value: str) -> None:
        # Validate HH:MM format
        try:
            h, m = map(int, value.split(":"))
            if 0 <= h <= 23 and 0 <= m <= 59:
                self._attr_native_value = f"{h:02d}:{m:02d}"
            else:
                self._attr_native_value = ""
        except ValueError:
            self._attr_native_value = ""

        await self.coordinator.async_request_refresh()


class ApplianceConfirmButton(SmartApplianceBase, ButtonEntity):
    """Confirm the selected proposal and schedule it."""
    def __init__(self, coordinator, entry_id, sensor_id, prog_select: ApplianceProgramSelect, proposal_select: ApplianceProposalSelect, manual_time_text: ApplianceManualTimeText):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_confirm_btn"
        self._attr_name = "Plan bestätigen"
        self._attr_icon = "mdi:check-circle"
        self.prog_select = prog_select
        self.proposal_select = proposal_select
        self.manual_time_text = manual_time_text

    async def async_press(self) -> None:
        if not self.sm: return

        prog_name = self.prog_select.current_option
        progs = self.sm.manager.get_programs(self.sensor_id)
        prog = next((p for p in progs if p.name == prog_name), None)

        if not prog: return

        import homeassistant.util.dt as dt_util
        now = dt_util.now().replace(tzinfo=None)

        manual_time_str = self.manual_time_text.native_value
        start_time = None
        cost = 0.0

        if manual_time_str:
            # Parse the manual time
            try:
                h, m = map(int, manual_time_str.split(":"))
                start_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
                # If the time has already passed today, schedule for tomorrow
                if start_time < now:
                    start_time += timedelta(days=1)

                # Calculate the cost for this custom time
                cost, _ = self.coordinator.proposal_calculator._simulate_run_cost(prog.power_profile, start_time)

                # Clear the manual input after consumption
                self.manual_time_text._attr_native_value = ""
            except ValueError:
                pass

        if not start_time:
            # Fall back to proposal select
            proposal = self.proposal_select.get_selected_proposal()
            if proposal:
                start_time = proposal.start_time
                cost = proposal.cost_estimate

        if start_time:
            self.sm.schedule_program(prog.id, start_time, cost)
            # Force recalculate the overall plan
            await self.coordinator.async_request_refresh()


class ApplianceRenameText(SmartApplianceBase, TextEntity):
    """Text entity to rename the currently selected program."""
    def __init__(self, coordinator, entry_id, sensor_id, prog_select: ApplianceProgramSelect):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_rename_text"
        self._attr_name = "Programm umbenennen"
        self._attr_icon = "mdi:pencil"
        self.prog_select = prog_select

    @property
    def native_value(self) -> str | None:
        return self.prog_select.current_option

    async def async_set_value(self, value: str) -> None:
        if not self.sm or not value: return

        current_name = self.prog_select.current_option
        progs = self.sm.manager.get_programs(self.sensor_id)
        prog = next((p for p in progs if p.name == current_name), None)

        if prog:
            prog.name = value
            await self.sm.manager.async_save()
            # Update select option
            self.prog_select._current_option = value
            await self.coordinator.async_request_refresh()



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
            hours, remainder = divmod(int(td.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            return f"{hours}h {minutes}m"
        else:
            return "0h 0m"


class ApplianceStatusSensor(SmartApplianceBase, SensorEntity):
    """Shows the current status/countdown of the appliance."""
    def __init__(self, coordinator, entry_id, sensor_id):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_status"
        self._attr_name = "Status"
        self._attr_icon = "mdi:information-outline"

    @property
    def native_value(self) -> str:
        if not self.sm: return "Unbekannt"

        state_map = {
            "idle": "Bereit",
            "recording": "Aufzeichnung läuft...",
            "running_scheduled": "Läuft (Geplant)",
            "running_spontaneous": "Läuft (Spontan)"
        }

        if self.sm.state.value == "waiting_for_start" and self.sm.planned_run:
            import homeassistant.util.dt as dt_util
            now = dt_util.now().replace(tzinfo=None)
            start = self.sm.planned_run.scheduled_start
            if start > now:
                td = start - now
                hours, remainder = divmod(int(td.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                return f"Start in {hours}h {minutes}m"
            else:
                return "Wartet auf Start..."

        return state_map.get(self.sm.state.value, self.sm.state.value)

class ApplianceProfileSensor(SmartApplianceBase, SensorEntity):
    """Shows the program duration and exposes the full power profile for charts."""
    def __init__(self, coordinator, entry_id, sensor_id, prog_select: ApplianceProgramSelect):
        super().__init__(coordinator, entry_id, sensor_id)
        self._attr_unique_id = f"{entry_id}_{sensor_id}_profile"
        self._attr_name = "Profil & Dauer"
        self._attr_icon = "mdi:chart-line"
        self.prog_select = prog_select

    @property
    def native_value(self) -> str:
        if not self.sm or not self.prog_select.current_option:
            return "Kein Programm gewählt"

        progs = self.sm.manager.get_programs(self.sensor_id)
        prog = next((p for p in progs if p.name == self.prog_select.current_option), None)
        if not prog or not prog.power_profile:
            return "0 Min"

        return f"{len(prog.power_profile)} Min"

    @property
    def extra_state_attributes(self):
        """Return the profile for use in ApexCharts."""
        if not self.sm or not self.prog_select.current_option:
            return {"power_profile": []}

        progs = self.sm.manager.get_programs(self.sensor_id)
        prog = next((p for p in progs if p.name == self.prog_select.current_option), None)
        if not prog or not prog.power_profile:
            return {"power_profile": []}

        # Format for charts: e.g. simply list of rounded Watts
        return {"power_profile": [round(w, 1) for w in prog.power_profile]}
