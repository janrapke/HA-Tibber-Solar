"""Coordinator: read inputs, learn, plan, execute. Every step is isolated.

Design rules:
  * Setup never needs the network or valid sensors. A failing step logs and the
    loop continues; the coordinator update itself never raises.
  * The safe default is "inverter on": the OpenDTU power limiter then runs on
    its own (zero export, its own SoC stop threshold), exactly as without this
    integration.
  * Forecasts come from solar_model / load_model, the schedule from planner.
    Those modules are pure Python and replay-tested against recorded data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util

from .appliance_manager import ApplianceStateMachine, ProposalCalculator, SmartApplianceManager
from .const import (
    CONF_BALCONY_POWER_SENSOR,
    CONF_BASE_LOAD_W,
    CONF_BATTERY_CAPACITY_WH,
    CONF_BATTERY_EFFICIENCY_PCT,
    CONF_BATTERY_LEVEL_SENSOR,
    CONF_BATTERY_MAX_LIMIT_PCT,
    CONF_BATTERY_MIN_LIMIT_PCT,
    CONF_CLIMATE_DEVICES,
    CONF_EARLY_EXCESS_CONSUMERS,
    CONF_EARLY_EXCESS_EXPECTED_POWER_W,
    CONF_EARLY_EXCESS_MAX_BATTERY_PCT,
    CONF_EARLY_EXCESS_MIN_BATTERY_PCT,
    CONF_EXCESS_MIN_RUN_TIME_MINUTES,
    CONF_EXCLUDED_POWER_SENSORS,
    CONF_EXTREME_PRICE_THRESHOLD,
    CONF_GENERIC_INVERTER_SWITCH,
    CONF_GENERIC_OUTPUT_SENSOR,
    CONF_GRID_CHARGER_POWER_W,
    CONF_GRID_CHARGER_SWITCH,
    CONF_INVERTER_PROFILE,
    CONF_MAX_INVERTER_POWER_W,
    CONF_MIN_SWITCH_INTERVAL_MINUTES,
    CONF_OPENDTU_DPL_MODE_SELECT,
    CONF_OPENDTU_OUTPUT_SENSOR,
    CONF_OPENDTU_PRODUCING_SENSOR,
    CONF_OPENDTU_TURN_OFF_BUTTON,
    CONF_OPENDTU_TURN_ON_BUTTON,
    CONF_PRIMARY_EXCESS_CONSUMERS,
    CONF_PS_AC_OUTPUT_SWITCH,
    CONF_PS_CHARGE_POWER_ENTITY,
    CONF_PS_DISCHARGE_POWER_ENTITY,
    CONF_PS_OUTPUT_SENSOR,
    CONF_SECONDARY_EXCESS_CONSUMERS,
    CONF_SMART_DEVICES,
    CONF_SOLAR_CHARGE_STATE_SENSOR,
    CONF_SOLAR_PEAK_W,
    CONF_SOLAR_POWER_SENSOR,
    CONF_TIBBER_API_TOKEN,
    CONF_TIBBER_CONSUMPTION_SENSOR,
    CONF_TIBBER_EXPORT_SENSOR,
    CONF_TIBBER_PRICE_SENSOR,
    DOMAIN,
    INVERTER_PROFILE_GENERIC,
    INVERTER_PROFILE_OPENDTU,
    INVERTER_PROFILE_POWERSTATION,
)
from .learning import LearningEngine
from .load_model import LoadModel, quarter_of
from .planner import (
    ACT_DISCHARGE,
    ACT_GRID_CHARGE,
    ACT_HOLD,
    BatteryParams,
    Plan,
    Slot,
    optimize,
)
from .solar_model import SolarModel
from .tibber import fetch_tibber_prices

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
UNAVAILABLE = ("unknown", "unavailable", "", None)
ABSORPTION_STATES = {"absorption", "float", "ausgleichsladung", "equalization"}

SOC_MAX_AGE = timedelta(minutes=15)      # BLE sensors drop out several times per hour
SOLAR_MAX_AGE = timedelta(minutes=10)
REPLAN_EVERY = timedelta(minutes=5)
WEATHER_EVERY = timedelta(hours=1)
PRICES_EVERY = timedelta(hours=1)
SAVE_EVERY = timedelta(hours=1)
STALE_NOTIFY_AFTER = timedelta(minutes=30)
HISTORY_DAYS_SOLAR = 60
HISTORY_DAYS_LOAD = 10
EARLY_EXCESS_LOOKAHEAD_H = 6.0

ACTION_TEXT = {
    ACT_DISCHARGE: "Dispatch: Akku versorgt das Haus",
    ACT_HOLD: "Akku sparen für teurere Stunden",
    ACT_GRID_CHARGE: "Netzladen (günstiger Preis)",
}


class SmartBatteryOptimizerCoordinator(DataUpdateCoordinator):
    """Reads inputs, learns, plans and executes once per minute."""

    def __init__(self, hass: HomeAssistant, config: dict):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(minutes=1))
        self.config = config
        # kept for climate learning, savings counters, vacation mode and reset button
        self.learning_engine = LearningEngine(hass, config)

        self.appliance_manager = None
        self.appliance_state_machines = {}
        self.proposal_calculator = ProposalCalculator(self)
        self.appliance_entities = {'button': [], 'select': [], 'sensor': [], 'text': [], 'number': [], 'switch': []}
        self.climate_device_states: dict = {}

        # ---- user controls (set by switch / number entities, restored on restart) ----
        self.is_enabled = True
        self.manual_zero_export = False
        self.grid_charge_enabled = False
        self.grid_charge_margin = 2.0        # ct/kWh
        self.grid_charge_efficiency = 80     # %
        self.grid_charge_buffer = 25         # % solar safety buffer
        self.extreme_price_threshold = config.get(CONF_EXTREME_PRICE_THRESHOLD, 0.40)
        self.extreme_price_factor = 0.0
        self.primary_excess_on = 95.0
        self.primary_excess_off = 90.0
        self.secondary_excess_on = 98.0
        self.secondary_excess_off = 95.0
        self.primary_excess_auto = True
        self.secondary_excess_auto = True
        self.early_excess_auto = True
        self.excess_cloud_tolerance_mins = 10
        self.learning_rate_factor = 1.0
        self.presunny_discharge_enabled = False
        self.presunny_solar_margin_pct = 20

        # ---- models ----
        lat = hass.config.latitude or 51.0
        lon = hass.config.longitude or 10.0
        prior_k = max(0.3, float(config.get(CONF_SOLAR_PEAK_W, 1500)) / 1000.0)
        self.solar_model = SolarModel(lat, lon, prior_k=prior_k)
        self.load_model = LoadModel(base_load_w=float(config.get(CONF_BASE_LOAD_W, 300)))
        self._store: Store | None = None
        self._bootstrapped = False
        self._bootstrap_running = False
        self._bootstrap_next_try: datetime | None = None
        self._setup_done = False
        self._quarter_start: datetime | None = None

        # ---- runtime state ----
        self.tibber_prices: list[dict] = []
        self._weather: list[dict] = []   # [{"t": aware datetime (centre of hour), ghi, dni, dhi, temp}]
        self._last_weather_fetch: datetime | None = None
        self._last_price_fetch: datetime | None = None
        self._last_save: datetime | None = None
        self._last_plan_time: datetime | None = None
        self.plan: Plan = Plan()
        self.hourly_plan: list[dict] = []

        self._last_valid: dict[str, tuple[float, datetime]] = {}
        self._quarter_key: tuple | None = None
        self._quarter_acc = {"solar": [], "load": [], "limited": False}

        self._current_inverter_state = "unknown"
        self._last_inverter_command_time: datetime | None = None
        self._last_inverter_watchdog_time: datetime | None = None
        self._last_switch_time: dict[str, datetime] = {}
        self._excess_devices_last_turned_on: dict[str, datetime | None] = {}
        self._battery_recovery_mode = False
        self._last_action = ACT_DISCHARGE
        self._stale_since: datetime | None = None
        self._stale_notified = False

        # ---- outputs used by sensors ----
        self.calculated_house_consumption = 0.0
        self.predicted_remaining_solar = 0.0
        self.predicted_remaining_consumption = 0.0
        self.current_operating_mode = "Initialisiere"
        self.target_morning_soc_pct: float | None = None
        self.tomorrow_net_solar_wh: float = 0.0
        self.overfill_absorption_last_wh = 0.0
        self._overfill_emergency_active = False

    # ------------------------------------------------------------------ setup
    async def _async_setup(self):
        """Load persisted state and create appliance/climate entities. No network."""
        if self._setup_done:
            return
        self._setup_done = True
        entry_id = self.config_entry.entry_id
        steps = [self.learning_engine.async_load, self._load_models]
        try:
            if not self.appliance_manager:
                self.appliance_manager = SmartApplianceManager(self.hass, entry_id)
            steps.append(self.appliance_manager.async_load)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Appliance manager unavailable")
        for step in steps:
            try:
                await step()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Loading stored data failed in %s; continuing with defaults", step)

        self.appliance_state_machines.clear()
        self.appliance_entities = {'button': [], 'select': [], 'sensor': [], 'text': [], 'number': [], 'switch': []}
        try:
            self._create_appliance_entities(entry_id)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Creating appliance/climate entities failed")

    def _create_appliance_entities(self, entry_id: str) -> None:
        from .appliance_entities import (
            ApplianceCancelButton, ApplianceConfirmButton, ApplianceEarliestStartNumber,
            ApplianceLatestEndNumber, ApplianceProgramNameText, ApplianceProposalSelect,
            ApplianceRecalculateButton, ApplianceScheduleSelect, ApplianceStatusSensor,
            ApplianceTimerSensor,
        )
        from .climate_entities import (
            ClimateDeviceEnabledSwitch, ClimateDeviceTypeSelect, ClimateManualWNumber,
            ClimatePowerSensorText, ClimateSolltemperaturNumber,
        )

        for dev in [d.strip() for d in self.config.get(CONF_SMART_DEVICES, "").split(",") if d.strip()]:
            self.appliance_state_machines[dev] = ApplianceStateMachine(dev, self.appliance_manager)
            prop_sel = ApplianceProposalSelect(self, entry_id, dev)
            self.appliance_entities['select'] += [prop_sel, ApplianceScheduleSelect(self, entry_id, dev)]
            self.appliance_entities['number'] += [ApplianceEarliestStartNumber(self, entry_id, dev),
                                                  ApplianceLatestEndNumber(self, entry_id, dev)]
            self.appliance_entities['button'] += [ApplianceConfirmButton(self, entry_id, dev, prop_sel),
                                                  ApplianceCancelButton(self, entry_id, dev),
                                                  ApplianceRecalculateButton(self, entry_id, dev)]
            self.appliance_entities['sensor'] += [ApplianceStatusSensor(self, entry_id, dev),
                                                  ApplianceTimerSensor(self, entry_id, dev)]
            self.appliance_entities['text'].append(ApplianceProgramNameText(self, entry_id, dev))

        for entity_id in self._list(CONF_EXCLUDED_POWER_SENSORS):
            if entity_id not in self.appliance_state_machines:
                self.appliance_state_machines[entity_id] = ApplianceStateMachine(entity_id, self.appliance_manager)

        for device_config in self.config.get(CONF_CLIMATE_DEVICES, []):
            self.appliance_entities['switch'].append(ClimateDeviceEnabledSwitch(self, entry_id, device_config))
            self.appliance_entities['select'].append(ClimateDeviceTypeSelect(self, entry_id, device_config))
            self.appliance_entities['number'].append(ClimateManualWNumber(self, entry_id, device_config))
            self.appliance_entities['number'].append(ClimateSolltemperaturNumber(self, entry_id, device_config))
            self.appliance_entities['text'].append(ClimatePowerSensorText(self, entry_id, device_config))

    async def _load_models(self) -> None:
        self._store = Store(self.hass, STORE_VERSION, f"{DOMAIN}.models.{self.config_entry.entry_id}")
        data = await self._store.async_load() or {}
        self.solar_model.load_dict(data.get("solar", {}))
        self.load_model.load_dict(data.get("load", {}))
        self._bootstrapped = bool(data.get("bootstrapped"))

    async def async_save_models(self) -> None:
        if self._store is None:
            return
        await self._store.async_save({
            "solar": self.solar_model.to_dict(),
            "load": self.load_model.to_dict(),
            "bootstrapped": self._bootstrapped,
        })

    async def async_reset_models(self) -> None:
        """Forget learned solar/load behaviour and relearn from recorder history."""
        lat, lon = self.solar_model.lat, self.solar_model.lon
        self.solar_model = SolarModel(lat, lon, prior_k=self.solar_model.prior[0])
        self.load_model = LoadModel(base_load_w=self.load_model.base_load_w)
        self._bootstrapped = False
        await self.async_save_models()

    async def async_shutdown_save(self) -> None:
        for step in (self.learning_engine.async_save, self.async_save_models):
            try:
                await step()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Saving on unload failed")

    # ------------------------------------------------------------------ helpers
    def _list(self, key: str) -> list:
        v = self.config.get(key) or []
        if isinstance(v, str):
            return [v] if v else []
        return list(v) if isinstance(v, (list, tuple)) else []

    def _read_float(self, entity_id: str | None, max_age: timedelta | None = None) -> float | None:
        """Current numeric state; within max_age, fall back to the last valid value."""
        if not entity_id:
            return None
        now = dt_util.utcnow()
        st = self.hass.states.get(entity_id)
        if st is not None and st.state not in UNAVAILABLE:
            try:
                val = float(st.state)
                self._last_valid[entity_id] = (val, now)
                return val
            except (ValueError, TypeError):
                pass
        if max_age is not None and entity_id in self._last_valid:
            val, ts = self._last_valid[entity_id]
            if now - ts <= max_age:
                return val
        return None

    def _sum(self, entity_ids: list, max_age: timedelta | None = None) -> float | None:
        vals = [self._read_float(e, max_age) for e in entity_ids]
        vals = [v for v in vals if v is not None]
        return sum(vals) if vals else None

    def _is_absorption(self) -> bool:
        for eid in self._list(CONF_SOLAR_CHARGE_STATE_SENSOR):
            st = self.hass.states.get(eid)
            if st and str(st.state).lower() in ABSORPTION_STATES:
                return True
        return False

    def _battery_params(self) -> BatteryParams:
        c = self.config
        max_pct = float(c.get(CONF_BATTERY_MAX_LIMIT_PCT, c.get(CONF_EARLY_EXCESS_MAX_BATTERY_PCT, 99)))
        has_charger = bool(c.get(CONF_GRID_CHARGER_SWITCH))
        return BatteryParams(
            capacity_wh=float(c.get(CONF_BATTERY_CAPACITY_WH, 5000)),
            min_pct=float(c.get(CONF_BATTERY_MIN_LIMIT_PCT, 10)),
            max_pct=100.0,
            efficiency=float(c.get(CONF_BATTERY_EFFICIENCY_PCT, 90)) / 100.0,
            inverter_max_w=float(c.get(CONF_MAX_INVERTER_POWER_W, 800)),
            grid_charge_w=float(c.get(CONF_GRID_CHARGER_POWER_W, 1000)) if has_charger else 0.0,
            grid_charge_eff=max(0.3, float(self.grid_charge_efficiency) / 100.0),
            grid_charge_max_pct=max_pct,
            grid_charge_margin=float(self.grid_charge_margin) / 100.0,
            steps=80,
        )

    # ------------------------------------------------------------------ data sources
    async def _refresh_prices(self, now: datetime) -> None:
        token = self.config.get(CONF_TIBBER_API_TOKEN)
        if not token:
            return
        latest = max((p["datetime"] for p in self.tibber_prices), default=None)
        need = (self._last_price_fetch is None or now - self._last_price_fetch >= PRICES_EVERY
                or latest is None or latest < now + timedelta(hours=2))
        if not need or (self._last_price_fetch and now - self._last_price_fetch < timedelta(minutes=10)):
            return
        self._last_price_fetch = now
        prices = await fetch_tibber_prices(self.hass, token)
        if prices:
            self.tibber_prices = prices

    def _price_at(self, t: datetime) -> float | None:
        for p in self.tibber_prices:
            start = p.get("datetime")
            if start is None:
                continue
            length = timedelta(minutes=15) if len(self.tibber_prices) > 60 else timedelta(hours=1)
            if start <= t < start + length:
                return float(p.get("total") or 0.0)
        return None

    def _current_price(self, now: datetime) -> float | None:
        v = self._read_float(self.config.get(CONF_TIBBER_PRICE_SENSOR))
        return v if v is not None else self._price_at(now)

    async def _refresh_weather(self, now: datetime, past_days: int = 1) -> None:
        if self._last_weather_fetch and now - self._last_weather_fetch < WEATHER_EVERY and past_days <= 1:
            return
        lat, lon = self.hass.config.latitude, self.hass.config.longitude
        if lat is None or lon is None:
            return
        url = ("https://api.open-meteo.com/v1/forecast"
               f"?latitude={lat}&longitude={lon}"
               "&hourly=shortwave_radiation,direct_normal_irradiance,diffuse_radiation,temperature_2m"
               f"&past_days={past_days}&forecast_days=3&timezone=UTC")
        session = async_get_clientsession(self.hass)
        async with session.get(url, timeout=20) as resp:
            if resp.status != 200:
                _LOGGER.warning("Open-Meteo HTTP %s", resp.status)
                return
            data = await resp.json()
        h = data.get("hourly", {})
        rows = []
        for t, g, dn, df, tmp in zip(h.get("time", []), h.get("shortwave_radiation", []),
                                     h.get("direct_normal_irradiance", []), h.get("diffuse_radiation", []),
                                     h.get("temperature_2m", [])):
            ts = dt_util.parse_datetime(t + "+00:00") if t else None
            if ts is None:
                continue
            # values are averages over the preceding hour -> centre is 30 min earlier
            rows.append({"t": ts - timedelta(minutes=30), "ghi": g or 0.0, "dni": dn or 0.0,
                         "dhi": df or 0.0, "temp": tmp})
        if rows:
            if past_days > 1:
                self._weather_history = rows
            self._weather = rows
            self._last_weather_fetch = now

    def _weather_at(self, t: datetime, rows: list[dict] | None = None) -> dict | None:
        rows = rows if rows is not None else self._weather
        if not rows:
            return None
        lo, hi = 0, len(rows) - 1
        if t <= rows[0]["t"]:
            return rows[0] if rows[0]["t"] - t < timedelta(hours=1) else None
        if t >= rows[-1]["t"]:
            return rows[-1] if t - rows[-1]["t"] < timedelta(hours=1) else None
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if rows[mid]["t"] <= t:
                lo = mid
            else:
                hi = mid
        a, b = rows[lo], rows[hi]
        span = (b["t"] - a["t"]).total_seconds()
        f = (t - a["t"]).total_seconds() / span if span > 0 else 0.0
        return {k: a[k] + (b[k] - a[k]) * f for k in ("ghi", "dni", "dhi")}

    def _solar_forecast(self, t_centre: datetime) -> float:
        w = self._weather_at(t_centre)
        if w is None:
            return 0.0  # unknown weather: plan pessimistically
        return self.solar_model.predict(t_centre, w["ghi"], w["dni"], w["dhi"])

    # ------------------------------------------------------------------ bootstrap from recorder
    async def _async_bootstrap(self) -> None:
        """Teach the models from recorder statistics so they work from day one."""
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistics_during_period
        except ImportError:
            self._bootstrapped = True
            return
        now = dt_util.utcnow()
        await self._refresh_weather(now, past_days=HISTORY_DAYS_SOLAR)
        hist_weather = getattr(self, "_weather_history", None)
        solar_ids = self._list(CONF_SOLAR_POWER_SENSOR)
        soc_id = self.config.get(CONF_BATTERY_LEVEL_SENSOR)
        if not hist_weather or not solar_ids:
            return
        start = now - timedelta(days=HISTORY_DAYS_SOLAR)
        ids = set(solar_ids) | ({soc_id} if soc_id else set())
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, start, now, ids, "hour", None, {"mean", "max"})

        def series(eid, key="mean"):
            out = {}
            for row in stats.get(eid, []):
                ts = row.get("start")
                if isinstance(ts, (int, float)):
                    ts = dt_util.utc_from_timestamp(ts)
                if ts is not None and row.get(key) is not None:
                    out[ts] = float(row[key])
            return out

        solar_series = [series(e) for e in solar_ids]
        soc_max = series(soc_id, "max") if soc_id else {}
        hours = sorted(set().union(*[s.keys() for s in solar_series])) if solar_series else []
        n = 0
        for h in hours:
            vals = [s[h] for s in solar_series if h in s]
            if len(vals) < len(solar_series):
                continue
            centre = h + timedelta(minutes=30)
            w = self._weather_at(centre, hist_weather)
            if w is None:
                continue
            self.solar_model.learn(centre, w["ghi"], w["dni"], w["dhi"], sum(vals),
                                   limited=soc_max.get(h, 0.0) >= 97.0)
            n += 1

        # load profile from 5-minute statistics of the consumption inputs
        load_n = await self._bootstrap_load(statistics_during_period, get_instance, now)
        self._bootstrapped = True
        await self.async_save_models()
        _LOGGER.info("Bootstrap finished: %d solar hours, %d load quarters, model %s",
                     n, load_n, self.solar_model.quality())

    async def _bootstrap_load(self, statistics_during_period, get_instance, now) -> int:
        c = self.config
        pos = [c.get(CONF_TIBBER_CONSUMPTION_SENSOR), self._inverter_output_sensor(), c.get(CONF_BALCONY_POWER_SENSOR)]
        neg = [c.get(CONF_TIBBER_EXPORT_SENSOR)] + self._list(CONF_EXCLUDED_POWER_SENSORS)
        pos = [e for e in pos if e]
        neg = [e for e in neg if e]
        if not c.get(CONF_TIBBER_CONSUMPTION_SENSOR):
            return 0
        start = now - timedelta(days=HISTORY_DAYS_LOAD)
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, start, now, set(pos + neg), "5minute", None, {"mean"})
        buckets: dict[datetime, float] = {}
        complete: dict[datetime, int] = {}
        for sign, ids in ((1.0, pos), (-1.0, neg)):
            for eid in ids:
                for row in stats.get(eid, []):
                    ts = row.get("start")
                    if isinstance(ts, (int, float)):
                        ts = dt_util.utc_from_timestamp(ts)
                    if ts is None or row.get("mean") is None:
                        continue
                    buckets[ts] = buckets.get(ts, 0.0) + sign * float(row["mean"])
                    if eid == c.get(CONF_TIBBER_CONSUMPTION_SENSOR):
                        complete[ts] = 1
        quarters: dict[tuple, list[float]] = {}
        for ts, v in buckets.items():
            if ts not in complete:
                continue
            lt = dt_util.as_local(ts)
            quarters.setdefault((lt.date(), quarter_of(lt)), []).append(max(0.0, v))
        for (d, q), vals in quarters.items():
            self.load_model.record(d, q, sum(vals) / len(vals))
        return len(quarters)

    # ------------------------------------------------------------------ inverter I/O
    def _inverter_profile(self) -> str:
        return self.config.get(CONF_INVERTER_PROFILE, INVERTER_PROFILE_OPENDTU)

    def _inverter_output_sensor(self) -> str | None:
        profile = self._inverter_profile()
        if profile == INVERTER_PROFILE_POWERSTATION:
            return self.config.get(CONF_PS_OUTPUT_SENSOR) or self.config.get(CONF_PS_DISCHARGE_POWER_ENTITY)
        if profile == INVERTER_PROFILE_GENERIC:
            return self.config.get(CONF_GENERIC_OUTPUT_SENSOR)
        return self.config.get(CONF_OPENDTU_OUTPUT_SENSOR)

    def _inverter_read_state(self) -> bool | None:
        """True/False if known, None if the state cannot be read."""
        profile = self._inverter_profile()
        if profile == INVERTER_PROFILE_POWERSTATION:
            v = self._read_float(self.config.get(CONF_PS_DISCHARGE_POWER_ENTITY))
            if v is not None:
                return v > 0
            sw = self.hass.states.get(self.config.get(CONF_PS_AC_OUTPUT_SWITCH) or "")
            return None if sw is None else sw.state == "on"
        if profile == INVERTER_PROFILE_GENERIC:
            sw = self.hass.states.get(self.config.get(CONF_GENERIC_INVERTER_SWITCH) or "")
            return None if sw is None else sw.state == "on"
        dpl = self.hass.states.get(self.config.get(CONF_OPENDTU_DPL_MODE_SELECT) or "")
        if dpl is not None and dpl.state not in UNAVAILABLE:
            s = str(dpl.state).strip()
            if s.startswith("0"):
                return True
            if s.startswith("1"):
                return False
        prod = self.hass.states.get(self.config.get(CONF_OPENDTU_PRODUCING_SENSOR) or "")
        if prod is not None and prod.state not in UNAVAILABLE:
            return str(prod.state).lower() in ("on", "true", "1", "producing")
        return None

    async def _inverter_dispatch(self, turn_on: bool) -> None:
        profile = self._inverter_profile()
        max_w = float(self.config.get(CONF_MAX_INVERTER_POWER_W, 800))
        call = self.hass.services.async_call
        if profile == INVERTER_PROFILE_POWERSTATION:
            dis = self.config.get(CONF_PS_DISCHARGE_POWER_ENTITY)
            if dis:
                await call(dis.split(".")[0], "set_value", {"entity_id": dis, "value": max_w if turn_on else 0})
            chg = self.config.get(CONF_PS_CHARGE_POWER_ENTITY)
            if chg and not turn_on:
                await call(chg.split(".")[0], "set_value", {"entity_id": chg, "value": max_w})
            sw = self.config.get(CONF_PS_AC_OUTPUT_SWITCH)
            if sw:
                await call("switch", "turn_on" if turn_on else "turn_off", {"entity_id": sw})
            return
        if profile == INVERTER_PROFILE_GENERIC:
            sw = self.config.get(CONF_GENERIC_INVERTER_SWITCH)
            if sw:
                await call("switch", "turn_on" if turn_on else "turn_off", {"entity_id": sw})
            return
        btn = self.config.get(CONF_OPENDTU_TURN_ON_BUTTON if turn_on else CONF_OPENDTU_TURN_OFF_BUTTON)
        if btn and self.hass.states.get(btn) is not None:
            await call("button", "press", {"entity_id": btn})
        dpl = self.config.get(CONF_OPENDTU_DPL_MODE_SELECT)
        st = self.hass.states.get(dpl) if dpl else None
        if st is not None:
            domain = dpl.split(".")[0]
            mode = "0" if turn_on else "1"
            if domain in ("number", "input_number"):
                await call(domain, "set_value", {"entity_id": dpl, "value": float(mode)})
            else:
                opts = [str(o) for o in st.attributes.get("options", [])]
                option = next((o for o in opts if o.strip().startswith(mode)), mode)
                await call(domain, "select_option", {"entity_id": dpl, "option": option})

    # ------------------------------------------------------------------ main loop
    async def _async_update_data(self):
        now = dt_util.now()
        result: dict = {}
        for name, step in (
            ("appliances", self._step_appliances),
            ("sources", self._step_sources),
            ("measure", self._step_measure),
            ("plan", self._step_plan),
            ("execute", self._step_execute),
            ("persist", self._step_persist),
        ):
            try:
                await step(now, result)
            except Exception:  # noqa: BLE001 - one failing step must never stop the loop
                _LOGGER.exception("Step '%s' failed", name)
        return self._output(result)

    async def _step_appliances(self, now, result) -> None:
        naive_now = datetime.now()
        for sensor_id, sm in self.appliance_state_machines.items():
            v = self._read_float(sensor_id)
            if v is not None:
                await sm.async_process_power_reading(v, naive_now)

    async def _step_sources(self, now, result) -> None:
        if (not self._bootstrapped and not self._bootstrap_running
                and (self._bootstrap_next_try is None or now >= self._bootstrap_next_try)):
            self._bootstrap_running = True
            self._bootstrap_next_try = now + timedelta(minutes=30)
            self.hass.async_create_task(self._run_bootstrap())
        try:
            await self._refresh_prices(now)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Price refresh failed")
        try:
            await self._refresh_weather(now)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Weather refresh failed: %s", err)

    async def _run_bootstrap(self) -> None:
        try:
            await self._async_bootstrap()
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Bootstrap from recorder failed; models will learn live")
        finally:
            self._bootstrap_running = False
            self._last_plan_time = None  # replan with the new model

    async def _step_measure(self, now, result) -> None:
        c = self.config
        soc = self._read_float(c.get(CONF_BATTERY_LEVEL_SENSOR), SOC_MAX_AGE)
        solar = self._sum(self._list(CONF_SOLAR_POWER_SENSOR), SOLAR_MAX_AGE)
        imp = self._read_float(c.get(CONF_TIBBER_CONSUMPTION_SENSOR), SOLAR_MAX_AGE)
        exp = self._read_float(c.get(CONF_TIBBER_EXPORT_SENSOR), SOLAR_MAX_AGE) or 0.0
        dtu = self._read_float(self._inverter_output_sensor(), SOLAR_MAX_AGE) or 0.0
        excluded = self._sum(self._list(CONF_EXCLUDED_POWER_SENSORS)) or 0.0
        balcony = self._read_float(c.get(CONF_BALCONY_POWER_SENSOR)) or 0.0
        load = None
        if imp is not None:
            load = max(0.0, imp - exp + dtu - excluded + balcony)
            self.calculated_house_consumption = load
        absorption = self._is_absorption()
        result.update(soc=soc, solar=solar, grid=imp, load=load, absorption=absorption)
        self._account_savings(now, dtu)

        # staleness watchdog
        if soc is None:
            self._stale_since = self._stale_since or now
            if now - self._stale_since >= STALE_NOTIFY_AFTER and not self._stale_notified:
                self._stale_notified = True
                await self._notify(
                    "Smart Battery Optimizer: Akkustand fehlt",
                    f"Der Sensor {c.get(CONF_BATTERY_LEVEL_SENSOR)} liefert seit über 30 Minuten keinen Wert. "
                    "Der Wechselrichter bleibt im sicheren Modus (OpenDTU-Limiter regelt selbst).")
        else:
            if self._stale_notified:
                await self.hass.services.async_call(
                    "persistent_notification", "dismiss", {"notification_id": f"{DOMAIN}_stale"})
            self._stale_since = None
            self._stale_notified = False

        # quarter-hour learning
        lt = dt_util.as_local(now)
        key = (lt.date(), quarter_of(lt))
        if self._quarter_key is not None and key != self._quarter_key:
            self._finish_quarter()
        if self._quarter_key != key:
            self._quarter_key = key
            self._quarter_start = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
            self._quarter_acc = {"solar": [], "load": [], "limited": False}
        acc = self._quarter_acc
        if solar is not None:
            acc["solar"].append(solar)
        if load is not None:
            acc["load"].append(load)
        if absorption or (soc is not None and soc >= 97.0):
            acc["limited"] = True

    def _account_savings(self, now: datetime, inverter_w: float) -> None:
        """Battery energy delivered to the house, valued at the current price."""
        price = self._current_price(now)
        last = getattr(self, "_last_savings_time", None)
        self._last_savings_time = now
        if price is None or last is None or inverter_w <= 0:
            return
        hours = min((now - last).total_seconds(), 300) / 3600.0
        eur = inverter_w * hours / 1000.0 * price
        sav = self.learning_engine.data.setdefault("savings", {})
        sav["total_battery_savings"] = sav.get("total_battery_savings", 0.0) + eur
        sav["total_battery_savings_vs_no_battery"] = sav.get("total_battery_savings_vs_no_battery", 0.0) + eur

    def _finish_quarter(self) -> None:
        d, q = self._quarter_key
        acc = self._quarter_acc
        if self._quarter_start is None or self.learning_engine.is_vacation_mode_active():
            return
        centre = self._quarter_start + timedelta(minutes=7, seconds=30)
        if len(acc["load"]) >= 5:
            self.load_model.record(d, q, sum(acc["load"]) / len(acc["load"]))
        if len(acc["solar"]) >= 5:
            w = self._weather_at(centre)
            if w is not None:
                self.solar_model.learn(centre, w["ghi"], w["dni"], w["dhi"],
                                       sum(acc["solar"]) / len(acc["solar"]), acc["limited"])

    async def _step_plan(self, now, result) -> None:
        if self._last_plan_time and now - self._last_plan_time < REPLAN_EVERY \
                and self._quarter_key == getattr(self, "_plan_quarter", None):
            return
        soc = result.get("soc")
        if soc is None:
            return
        slot0 = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        prices_end = max((p["datetime"] for p in self.tibber_prices), default=None)
        horizon_end = (prices_end + timedelta(minutes=15)) if prices_end else slot0 + timedelta(hours=12)
        horizon_end = max(horizon_end, slot0 + timedelta(hours=6))
        horizon_end = min(horizon_end, slot0 + timedelta(hours=36))
        fallback_price = self._current_price(now) or 0.30
        current_load = result.get("load")
        slots: list[Slot] = []
        t = slot0
        while t < horizon_end:
            price = self._price_at(t)
            centre = t + timedelta(minutes=7, seconds=30)
            slots.append(Slot(
                start=t,
                price=price if price is not None else fallback_price,
                solar_w=self._solar_forecast(centre),
                load_w=self.load_model.predict(dt_util.as_local(t), dt_util.as_local(now), current_load),
            ))
            t += timedelta(minutes=15)

        params = self._battery_params()
        allow_grid = bool(self.grid_charge_enabled and params.grid_charge_w > 0 and self._bootstrapped)
        if allow_grid and self.grid_charge_buffer:
            # plan grid charging against a reduced solar forecast (safety buffer)
            factor = max(0.0, 1.0 - float(self.grid_charge_buffer) / 100.0)
            plan_slots = [Slot(s.start, s.price, s.solar_w * factor, s.load_w) for s in slots]
        else:
            plan_slots = slots
        self.plan = await self.hass.async_add_executor_job(
            lambda: optimize(plan_slots, soc, params, allow_grid_charge=allow_grid,
                             current_action=self._last_action))
        self._last_plan_time = now
        self._plan_quarter = self._quarter_key
        self._build_plan_outputs(now, slots, params)

    def _build_plan_outputs(self, now: datetime, slots: list[Slot], params: BatteryParams) -> None:
        local_today = dt_util.as_local(now).date()
        blocks = []
        rem_solar = rem_load = 0.0
        tomorrow_solar = tomorrow_load = 0.0
        for step, slot in zip(self.plan.steps, slots):
            lt = dt_util.as_local(step.start)
            if lt.date() == local_today:
                rem_solar += slot.solar_w / 4
                rem_load += slot.load_w / 4
            elif lt.date() == local_today + timedelta(days=1) and 6 <= lt.hour < 20:
                tomorrow_solar += slot.solar_w / 4
                tomorrow_load += slot.load_w / 4
            if step.curtailed_wh > 1:
                action = "Überschussvermeidung: Batterie wird voll"
            elif step.action == ACT_DISCHARGE:
                action = "Dispatch"
            elif step.action == ACT_GRID_CHARGE:
                action = "Netzladen"
            elif step.soc_start_pct <= params.min_pct + 0.5:
                action = "Minimum"
            else:
                action = "Akku sparen"
            blocks.append({
                "hour": lt.strftime("%H:%M"),
                "ts": int(step.start.timestamp() * 1000),
                "price": round(step.price, 4),
                "solar_wh": round(slot.solar_w / 4),
                "house_wh": round(slot.load_w / 4),
                "consumption_wh": round(slot.load_w / 4),
                "balcony_wh": 0,
                "battery_pct_end": round(step.soc_end_pct, 1),
                "curtailed_wh": round(step.curtailed_wh),
                "will_overfill": step.curtailed_wh > 1,
                "planned_action": action,
            })
        self.hourly_plan = blocks
        self.predicted_remaining_solar = rem_solar
        self.predicted_remaining_consumption = rem_load
        self.tomorrow_net_solar_wh = tomorrow_solar - tomorrow_load
        # morning SoC the plan arrives at (06:00 tomorrow)
        self.target_morning_soc_pct = None
        for step in self.plan.steps:
            lt = dt_util.as_local(step.start)
            if lt.date() == local_today + timedelta(days=1) and lt.hour == 6 and lt.minute == 0:
                self.target_morning_soc_pct = step.soc_start_pct
                break

    # ------------------------------------------------------------------ execution
    async def _step_execute(self, now, result) -> None:
        if not self.is_enabled:
            self.current_operating_mode = "Optimizer deaktiviert (keine Steuerung)"
            return
        soc = result.get("soc")
        absorption = result.get("absorption", False)
        price = self._current_price(now)
        params = self._battery_params()
        min_pct = params.min_pct
        max_pct = float(self.config.get(CONF_BATTERY_MAX_LIMIT_PCT, 99))
        planned = self.plan.action_now() if self.plan.steps else None
        if self.plan.steps and now - self.plan.steps[0].start > timedelta(minutes=20):
            planned = None  # stale plan

        grid_charge = False
        if soc is None:
            inverter_on = True
            mode = "Sicherer Modus: Akkustand unbekannt, OpenDTU-Limiter regelt selbst"
        elif price is not None and price < 0:
            inverter_on = False
            grid_charge = self.grid_charge_enabled and soc < max_pct
            mode = f"Negativer Preis ({price:.3f} €): Netzstrom nutzen"
        elif self.manual_zero_export:
            inverter_on = True
            mode = "Manuell: Wechselrichter an"
        elif soc <= min_pct and not absorption:
            self._battery_recovery_mode = True
            inverter_on = False
            mode = "Batterie am Minimum: Wechselrichter aus"
        elif self._battery_recovery_mode and soc < min_pct + 2 and not absorption:
            inverter_on = False
            mode = "Batterie am Minimum (Erholung): Wechselrichter aus"
        elif absorption or soc >= max_pct - 1:
            self._battery_recovery_mode = False
            inverter_on = True
            mode = "Batterie voll/Absorption: Solar direkt nutzen"
        elif planned is None:
            self._battery_recovery_mode = False
            inverter_on = True
            mode = "Kein Plan: Standardbetrieb (Wechselrichter an)"
        else:
            self._battery_recovery_mode = False
            inverter_on = planned == ACT_DISCHARGE
            grid_charge = planned == ACT_GRID_CHARGE and self.grid_charge_enabled
            mode = ACTION_TEXT.get(planned, "?")
            if planned == ACT_HOLD:
                nxt = next((s for s in self.plan.steps if s.action == ACT_DISCHARGE), None)
                if nxt:
                    mode += f" (Entladen ab {dt_util.as_local(nxt.start).strftime('%H:%M')})"
        if planned is not None:
            self._last_action = ACT_DISCHARGE if inverter_on else (ACT_GRID_CHARGE if grid_charge else ACT_HOLD)
        self.current_operating_mode = mode

        await self._apply_grid_charger(grid_charge)
        await self._apply_inverter(now, inverter_on)
        await self._apply_excess(now, soc, absorption, price)

    async def _apply_grid_charger(self, on: bool) -> None:
        sw = self.config.get(CONF_GRID_CHARGER_SWITCH)
        st = self.hass.states.get(sw) if sw else None
        if st is None:
            return
        if on and st.state != "on":
            await self.hass.services.async_call("switch", "turn_on", {"entity_id": sw})
        elif not on and st.state == "on":
            await self.hass.services.async_call("switch", "turn_off", {"entity_id": sw})

    async def _apply_inverter(self, now: datetime, turn_on: bool) -> None:
        is_on = self._inverter_read_state()
        interval = timedelta(minutes=int(self.config.get(CONF_MIN_SWITCH_INTERVAL_MINUTES, 15)))
        want = "on" if turn_on else "off"
        locked = self._last_inverter_command_time is not None and now - self._last_inverter_command_time < interval
        if not locked:
            if self._current_inverter_state != want or (is_on is not None and is_on != turn_on):
                await self._inverter_dispatch(turn_on)
                self._current_inverter_state = want
                self._last_inverter_command_time = now
                self._last_inverter_watchdog_time = now
            return
        # watchdog: command sent but the device did not follow -> retry every 2 min
        if (self._current_inverter_state == want and is_on is not None and is_on != turn_on
                and self._last_inverter_watchdog_time
                and now - self._last_inverter_watchdog_time >= timedelta(minutes=2)):
            _LOGGER.warning("Inverter did not follow command '%s', retrying", want)
            await self._inverter_dispatch(turn_on)
            self._last_inverter_watchdog_time = now

    async def _set_switches(self, now: datetime, entities: list, turn_on: bool) -> None:
        interval = timedelta(minutes=int(self.config.get(CONF_MIN_SWITCH_INTERVAL_MINUTES, 15)))
        min_run = timedelta(minutes=int(self.config.get(CONF_EXCESS_MIN_RUN_TIME_MINUTES, 10)))
        for eid in entities:
            st = self.hass.states.get(eid)
            if st is None or st.state in UNAVAILABLE:
                continue
            is_on = st.state == "on"
            if is_on == turn_on:
                continue
            last = self._last_switch_time.get(eid)
            if last and now - last < interval:
                continue
            started = self._excess_devices_last_turned_on.get(eid)
            if not turn_on and started and now - started < min_run:
                continue
            domain = eid.split(".")[0]
            if domain not in ("switch", "input_boolean", "light", "fan", "climate"):
                domain = "homeassistant"
            await self.hass.services.async_call(domain, "turn_on" if turn_on else "turn_off", {"entity_id": eid})
            self._last_switch_time[eid] = now
            self._excess_devices_last_turned_on[eid] = now if turn_on else None

    async def _apply_excess(self, now: datetime, soc: float | None, absorption: bool, price: float | None) -> None:
        primary = self._list(CONF_PRIMARY_EXCESS_CONSUMERS)
        secondary = self._list(CONF_SECONDARY_EXCESS_CONSUMERS)
        early = self._list(CONF_EARLY_EXCESS_CONSUMERS)
        out = self.hass.states.get(self._inverter_output_sensor() or "")
        dtu_down = self._inverter_output_sensor() is not None and (out is None or out.state in UNAVAILABLE)
        if soc is None or dtu_down:
            for group, auto in ((primary, self.primary_excess_auto), (secondary, self.secondary_excess_auto),
                                (early, self.early_excess_auto)):
                if auto:
                    await self._set_switches(now, group, False)
            return
        virtual = 100.0 if absorption else soc
        negative = price is not None and price < 0

        def hysteresis(group, on_thr, off_thr):
            if negative:
                return True
            if virtual >= on_thr:
                return True
            if virtual <= off_thr:
                return False
            return any((st := self.hass.states.get(e)) is not None and st.state == "on" for e in group)

        curtail_soon = self.plan.curtailed_wh(1.0) if self.plan.steps else 0.0
        curtail_ahead = self.plan.curtailed_wh(EARLY_EXCESS_LOOKAHEAD_H) if self.plan.steps else 0.0
        emergency = curtail_soon > 50 and not negative
        self._overfill_emergency_active = emergency

        if self.primary_excess_auto:
            await self._set_switches(now, primary, hysteresis(primary, self.primary_excess_on, self.primary_excess_off) or emergency)
        if self.secondary_excess_auto:
            await self._set_switches(now, secondary, hysteresis(secondary, self.secondary_excess_on, self.secondary_excess_off) or emergency)
        if self.early_excess_auto and early:
            expected_w = float(self.config.get(CONF_EARLY_EXCESS_EXPECTED_POWER_W, 400))
            min_soc = float(self.config.get(CONF_EARLY_EXCESS_MIN_BATTERY_PCT, 30))
            running = any((st := self.hass.states.get(e)) is not None and st.state == "on" for e in early)
            if negative:
                want = True
            elif virtual < min_soc:
                want = False
            elif running:
                # keep running while curtailment is still ahead or the battery is nearly full
                max_pct = float(self.config.get(CONF_BATTERY_MAX_LIMIT_PCT, 99))
                want = curtail_ahead > 0 or absorption or virtual >= max_pct - 5
            else:
                want = curtail_ahead >= expected_w * 0.5
            self.overfill_absorption_last_wh = curtail_ahead
            await self._set_switches(now, early, want or emergency)

    # ------------------------------------------------------------------ persistence / output
    async def _step_persist(self, now, result) -> None:
        if self._last_save is None or now - self._last_save >= SAVE_EVERY:
            self._last_save = now
            await self.async_save_models()
            await self.learning_engine.async_save()

    async def _notify(self, title: str, message: str) -> None:
        await self.hass.services.async_call("persistent_notification", "create", {
            "title": title, "message": message, "notification_id": f"{DOMAIN}_stale"})

    def _output(self, r: dict) -> dict:
        return {
            "calculated_house_consumption": self.calculated_house_consumption,
            "predicted_remaining_solar": self.predicted_remaining_solar,
            "predicted_remaining_consumption": self.predicted_remaining_consumption,
            "battery_will_overfill": self.plan.curtailed_wh(EARLY_EXCESS_LOOKAHEAD_H) > 0 if self.plan.steps else False,
            "current_operating_mode": self.current_operating_mode,
            "hourly_plan": self.hourly_plan,
            "current_price": self._current_price(dt_util.now()),
            "current_battery": r.get("soc"),
            "current_solar": r.get("solar"),
            "current_consumption": r.get("grid"),
            "plan_cost_eur": round(self.plan.cost_eur, 2),
            "plan_savings_eur": round(self.plan.baseline_cost_eur - self.plan.cost_eur, 2),
            "model": self.solar_model.quality(),
            "load_days": self.load_model.days_known(),
            "bootstrapped": self._bootstrapped,
        }
