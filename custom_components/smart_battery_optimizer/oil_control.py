"""Oil heating monitor: burner runs, oil counter, tank level, heating days, reach forecast.

Listens to the grid meter, the inverter output and the exhaust pipe sensor on every
state change (seconds), so burner starts/stops are timed from the load steps.
Everything else runs once a minute from the coordinator.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

try:
    from .const import (
        CONF_OIL_EXHAUST_SENSOR, CONF_OIL_ROOMS, CONF_OIL_WINDOW, CONF_TIBBER_CONSUMPTION_SENSOR,
        CONF_TIBBER_EXPORT_SENSOR, DOMAIN, OIL_DEFAULTS,
    )
    from .oil_model import BurnerDetector, HeatDay, Run, Tank, fit, reach
except ImportError:  # loaded standalone by the tests
    from const import (
        CONF_OIL_EXHAUST_SENSOR, CONF_OIL_ROOMS, CONF_OIL_WINDOW, CONF_TIBBER_CONSUMPTION_SENSOR,
        CONF_TIBBER_EXPORT_SENSOR, DOMAIN, OIL_DEFAULTS,
    )
    from oil_model import BurnerDetector, HeatDay, Run, Tank, fit, reach

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
SAVE_DELAY_S = 60
KEEP_RUNS_DAYS = 60
KEEP_DAYS = 800
REACH_EVERY = timedelta(hours=1)
CALIBRATE_MIN_L = 60.0       # counted litres needed between two tank readings to calibrate
UNAVAILABLE = ("unknown", "unavailable", "", None)


class Settings:
    def __init__(self):
        self.flow_l_h = 2.0          # nozzle 0.50 gal/h at pump pressure, calibrated by tank readings
        self.reserve_cm = 30.0       # red zone of the gauge (0-30 cm): sludge / safety reserve that stays in the tanks
        self.offer_eur_l = 0.0       # current local offer price, only for the order hint
        self.boiler_eff_pct = 85.0


class OilMonitor:
    def __init__(self, hass, entry_id: str, config: dict, coordinator):
        self.hass = hass
        self.entry_id = entry_id
        self.coordinator = coordinator
        self.cfg = {k: (config.get(k) or v) for k, v in OIL_DEFAULTS.items()}
        self.grid_id = config.get(CONF_TIBBER_CONSUMPTION_SENSOR)
        self.export_id = config.get(CONF_TIBBER_EXPORT_SENSOR)
        self.settings = Settings()
        self.tank = Tank()
        self.detector = BurnerDetector()
        self.runs: list[Run] = []
        self.days: dict[str, HeatDay] = {}
        self.reading: dict | None = None      # {"ts", "cm", "liters"} last manual gauge reading
        self.burned_since_reading = 0.0       # litres counted since that reading
        self.calibrations: list[dict] = []
        self.forecast: dict = {}
        self.model: dict = fit([])
        self.listeners: list = []
        self._store = None
        self._unsub = None
        self._vals: dict[str, float] = {}
        self._last_forecast: datetime | None = None
        self._last_minute: datetime | None = None

    # ------------------------------------------------------------ lifecycle
    async def async_start(self) -> None:
        self._store = Store(self.hass, STORE_VERSION, f"{DOMAIN}.oil.{self.entry_id}")
        try:
            self.load_dict(await self._store.async_load() or {})
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Loading oil data failed; starting with defaults")
        ids = [e for e in (self.grid_id, self.export_id, self._dtu_id(), self.cfg[CONF_OIL_EXHAUST_SENSOR]) if e]
        self._unsub = async_track_state_change_event(self.hass, ids, self._on_change)

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._store is not None:
            await self._store.async_save(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "settings": vars(self.settings).copy(),
            "runs": [r.to_dict() for r in self.runs],
            "days": {k: vars(v).copy() for k, v in self.days.items()},
            "reading": self.reading,
            "burned_since_reading": self.burned_since_reading,
            "calibrations": self.calibrations,
        }

    def load_dict(self, d: dict) -> None:
        for k, v in (d.get("settings") or {}).items():
            if hasattr(self.settings, k):
                setattr(self.settings, k, float(v))
        self.runs = [Run.from_dict(r) for r in d.get("runs") or []]
        self.days = {k: HeatDay(**v) for k, v in (d.get("days") or {}).items()}
        self.reading = d.get("reading")
        self.burned_since_reading = float(d.get("burned_since_reading") or 0.0)
        self.calibrations = list(d.get("calibrations") or [])
        self.model = fit(list(self.days.values()), self.settings.boiler_eff_pct / 100.0)

    def _save(self) -> None:
        if self._store is not None:
            self._store.async_delay_save(self.to_dict, SAVE_DELAY_S)

    def _changed(self) -> None:
        for cb in list(self.listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Oil listener failed")

    # ------------------------------------------------------------ inputs
    def _dtu_id(self) -> str | None:
        try:
            return self.coordinator._inverter_output_sensor()
        except Exception:  # noqa: BLE001
            return None

    def _ac_w(self) -> float:
        ac = getattr(self.coordinator, "climate", None)
        return ac.power_w() if ac is not None else 0.0

    @callback
    def _on_change(self, event) -> None:
        st = event.data.get("new_state")
        if st is None or st.state in UNAVAILABLE:
            return
        try:
            val = float(st.state)
        except (TypeError, ValueError):
            return
        eid = event.data.get("entity_id")
        t = st.last_updated
        self._vals[eid] = val
        run = None
        if eid == self.cfg[CONF_OIL_EXHAUST_SENSOR]:
            run = self.detector.feed_exhaust(t, val)
        elif self.grid_id in self._vals:
            # house load without the AC: grid import - export + inverter output
            load = (self._vals[self.grid_id] - self._vals.get(self.export_id, 0.0)
                    + self._vals.get(self._dtu_id(), 0.0) - self._ac_w())
            run = self.detector.feed_load(t, load)
        if run is not None:
            self._add_run(run)

    def _add_run(self, run: Run) -> None:
        liters = run.minutes / 60.0 * self.settings.flow_l_h
        self.runs.append(run)
        cutoff = run.end - timedelta(days=KEEP_RUNS_DAYS)
        self.runs = [r for r in self.runs if r.end >= cutoff]
        day = self._day(run.start)
        day.oil_l += liters
        day.burner_min += run.minutes
        day.starts += 1
        self.burned_since_reading += liters
        _LOGGER.info("Burner run %s-%s (%.1f min, %.2f L, %s)", run.start.strftime("%H:%M:%S"),
                     run.end.strftime("%H:%M:%S"), run.minutes, liters, run.source)
        self._save()
        self._changed()

    def _day(self, t: datetime) -> HeatDay:
        key = t.date().isoformat()
        if key not in self.days:
            self.days[key] = HeatDay(key)
            if len(self.days) > KEEP_DAYS:
                for k in sorted(self.days)[:-KEEP_DAYS]:
                    del self.days[k]
        return self.days[key]

    def _float(self, eid: str | None) -> float | None:
        st = self.hass.states.get(eid) if eid else None
        if st is None or st.state in UNAVAILABLE:
            return None
        try:
            return float(st.state)
        except (TypeError, ValueError):
            return None

    def house_indoor(self) -> float | None:
        """Area-weighted mean of the room sensors that have a value."""
        num = den = 0.0
        for eid, m2 in self.cfg[CONF_OIL_ROOMS].items():
            v = self._float(eid)
            if v is not None and 5.0 <= v <= 35.0:
                num += v * m2
                den += m2
        return num / den if den else None

    # ------------------------------------------------------------ user inputs
    def set_setting(self, name: str, value: float) -> None:
        if hasattr(self.settings, name):
            setattr(self.settings, name, float(value))
            self._last_forecast = None
            self._save()
            self._changed()

    def set_reading(self, cm: float, now: datetime) -> None:
        """New gauge reading. Calibrates the burner flow when enough oil was counted since the last one."""
        liters = self.tank.liters(cm)
        prev = self.reading
        if prev is not None and self.burned_since_reading >= CALIBRATE_MIN_L:
            measured = prev["liters"] - liters
            factor = measured / self.burned_since_reading
            if 0.5 <= factor <= 2.0:
                old = self.settings.flow_l_h
                self.settings.flow_l_h = round(old * (1.0 + 0.5 * (factor - 1.0)), 3)
                self.calibrations.append({"datum": now.date().isoformat(), "gemessen_l": round(measured, 1),
                                          "gezaehlt_l": round(self.burned_since_reading, 1), "faktor": round(factor, 3),
                                          "durchsatz_vorher": old, "durchsatz_neu": self.settings.flow_l_h})
        self.reading = {"ts": now.isoformat(), "cm": cm, "liters": round(liters, 1)}
        self.burned_since_reading = 0.0
        self._last_forecast = None
        self._save()
        self._changed()

    # ------------------------------------------------------------ outputs
    def tank_liters(self) -> float | None:
        if self.reading is None:
            return None
        return max(0.0, self.reading["liters"] - self.burned_since_reading)

    def reserve_liters(self) -> float:
        return self.tank.liters(self.settings.reserve_cm)

    def usable_liters(self) -> float | None:
        t = self.tank_liters()
        return None if t is None else max(0.0, t - self.reserve_liters())

    def today(self, now: datetime) -> HeatDay:
        return self.days.get(now.date().isoformat()) or HeatDay(now.date().isoformat())

    def month_oil(self, now: datetime) -> float:
        prefix = now.strftime("%Y-%m")
        return sum(d.oil_l for k, d in self.days.items() if k.startswith(prefix))

    def runs_today(self, now: datetime) -> list[dict]:
        day = now.date()
        return [{"start": r.start.strftime("%H:%M:%S"), "ende": r.end.strftime("%H:%M:%S"),
                 "minuten": round(r.minutes, 1), "liter": round(r.minutes / 60 * self.settings.flow_l_h, 2),
                 "quelle": r.source} for r in self.runs if r.start.date() == day]

    def burner_on(self) -> bool:
        return self.detector.running

    def _update_forecast(self, now: datetime) -> None:
        self.model = fit(list(self.days.values()), self.settings.boiler_eff_pct / 100.0)
        usable = self.usable_liters()
        recent = sorted((d for d in self.days.values() if d.complete), key=lambda d: d.day)[-14:]
        indoor = (sum(d.indoor_sum / max(1, d.minutes) for d in recent) / len(recent)) if recent else 20.5
        kh = sum(d.kh for d in recent)
        ac_per_kh = sum(d.ac_heat_kwh for d in recent) / kh if kh > 0 else 0.0
        self.forecast = {"innen_annahme": round(indoor, 1), "klima_waerme_je_gradstunde": round(ac_per_kh, 4)}
        if usable is not None:
            r = reach(usable, now.date(), self.model, indoor, ac_per_kh)
            self.forecast.update({k: (v.isoformat() if v else None) for k, v in r.items()})
        self._last_forecast = now

    # ------------------------------------------------------------ minute step
    async def async_update(self, now: datetime, ext: dict) -> None:
        dt_min = 1.0
        if self._last_minute is not None:
            dt_min = max(0.0, min(5.0, (now - self._last_minute).total_seconds() / 60.0))
        self._last_minute = now
        day = self._day(now)
        indoor = self.house_indoor()
        outdoor = ext.get("outdoor")
        if indoor is not None and outdoor is not None and dt_min > 0:
            day.minutes += int(round(dt_min))
            day.indoor_sum += indoor * dt_min
            day.outdoor_sum += outdoor * dt_min
            day.kh += max(0.0, indoor - outdoor) * dt_min / 60.0
        ac_w = self._ac_w()
        day.ac_kwh_el += ac_w * dt_min / 60000.0
        day.ac_heat_kwh += ac_w * (ext.get("ac_efficiency") or 0.0) * dt_min / 60000.0
        day.house_kwh += max(0.0, ext.get("house_w") or 0.0) * dt_min / 60000.0
        win = self.hass.states.get(self.cfg[CONF_OIL_WINDOW]) if self.cfg[CONF_OIL_WINDOW] else None
        if win is not None and win.state == "on":
            day.window_min += dt_min
        if self._last_forecast is None or now - self._last_forecast >= REACH_EVERY:
            self._update_forecast(now)
        if now.minute % 15 == 0:
            self._save()
        self._changed()
