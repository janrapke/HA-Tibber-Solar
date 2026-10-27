"""Home Assistant side of the living room heat pump control.

Reads the room, the AC and the radiators, records a sample for the thermal model
every 15 minutes, asks climate_logic.decide() what to do and - only when
"Steuerung aktiv" is on - carries it out. With the switch off (default) nothing
is sent anywhere: the decision is only shown ("nur beobachten").

Rules for the commands:
  * every command batch starts with prompt_tone=false (the unit forgets it on restart),
  * 2-3 minutes after the unit was switched on: indirect_wind (heating) and fan mode,
  * the radiators are only switched off by us and always handed back by us
    (BT thermostats heat, heating automations on and triggered once),
  * a state change of the AC that does not come from our own context is a remote
    control / Midea app action -> mode manuell_an / manuell_aus.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
import logging

from homeassistant.core import Context, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

try:
    from .climate_logic import CMD_INTERVAL, FAN_INTERVAL, MODE_AUTO, MODE_MANUAL_OFF, MODE_MANUAL_ON, MODES, Decision, Inputs, Settings, State, decide
    from .const import (
        AC_DEFAULTS, AC_RETRIGGER_AUTOMATIONS, CONF_AC_DEVICE_ID, CONF_AC_ENTITY, CONF_AC_HEATING_AUTOMATIONS,
        CONF_AC_OUTDOOR_SENSOR, CONF_AC_PRESENCE_HOME, CONF_AC_PRESENCE_LIVING, CONF_AC_RADIATORS,
        CONF_AC_ROOM_FALLBACK, CONF_AC_ROOM_SENSOR, DOMAIN,
    )
    from .thermal_model import OIL_KWH_PER_L, Sample, ThermalModel
except ImportError:  # loaded standalone by the tests
    from climate_logic import CMD_INTERVAL, FAN_INTERVAL, MODE_AUTO, MODE_MANUAL_OFF, MODE_MANUAL_ON, MODES, Decision, Inputs, Settings, State, decide
    from const import (
        AC_DEFAULTS, AC_RETRIGGER_AUTOMATIONS, CONF_AC_DEVICE_ID, CONF_AC_ENTITY, CONF_AC_HEATING_AUTOMATIONS,
        CONF_AC_OUTDOOR_SENSOR, CONF_AC_PRESENCE_HOME, CONF_AC_PRESENCE_LIVING, CONF_AC_RADIATORS,
        CONF_AC_ROOM_FALLBACK, CONF_AC_ROOM_SENSOR, DOMAIN,
    )
    from thermal_model import OIL_KWH_PER_L, Sample, ThermalModel

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
UNAVAILABLE = ("unknown", "unavailable", "", None)
ROOM_MIN, ROOM_MAX = 5.0, 40.0
ROOM_MAX_AGE = timedelta(minutes=15)
ROOM_HOLD = timedelta(minutes=10)  # WLAN probe drops out briefly: keep the last valid value
STARTUP_GRACE = timedelta(minutes=5)  # sensors are still unknown right after a restart
OUTDOOR_MAX_AGE = timedelta(hours=3)
FALLBACK_CORRECTION_K = 0.5  # Echo reads ~0.5 K low
SAMPLE_MIN = 15
POST_START_DELAY = timedelta(seconds=150)
OWN_CMD_WINDOW = timedelta(minutes=2)
HVAC_RETRY = timedelta(minutes=2)
MAX_ACCOUNT_GAP_H = 0.1
SAVE_DELAY_S = 60
EXPECTED_LOAD_AHEAD = timedelta(hours=2)


# ---------------------------------------------------------------- pure helpers
def plausible_temp(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if ROOM_MIN <= f <= ROOM_MAX else None


def state_value(st, now: datetime, max_age: timedelta | None = None):
    """Raw state if available and (optionally) recent enough."""
    if st is None or st.state in UNAVAILABLE:
        return None
    if max_age is not None:
        seen = getattr(st, "last_reported", None) or getattr(st, "last_updated", None)
        if seen is not None and now - seen > max_age:
            return None
    return st.state


def pick_room(primary: float | None, fallback: float | None) -> tuple[float | None, str]:
    """Room temperature and its source; the fallback is corrected for its bias."""
    if primary is not None:
        return primary, "raumsensor"
    if fallback is not None:
        return round(fallback + FALLBACK_CORRECTION_K, 2), "ersatzsensor"
    return None, "keiner"


def manual_mode_for(old_hvac: str | None, new_hvac: str | None, own: bool) -> str | None:
    """Mode to switch to after a foreign state change of the AC (None = ignore)."""
    if own or old_hvac in UNAVAILABLE or new_hvac in UNAVAILABLE or old_hvac == new_hvac:
        return None
    return MODE_MANUAL_OFF if new_hvac == "off" else MODE_MANUAL_ON


def account(counters: dict, now: datetime, power_w: float, dt_h: float, replacing: bool,
            efficiency: float, price: float | None, oil_eur_l: float, boiler_eff: float) -> dict:
    """Add one interval to the energy counters (kWh today / month, oil replaced, savings)."""
    day, month = now.date().isoformat(), now.strftime("%Y-%m")
    if counters.get("month") != month:
        counters.update(month=month, kwh_month=0.0, oil_l_month=0.0, savings_month=0.0)
    if counters.get("day") != day:
        counters.update(day=day, kwh_day=0.0)
    kwh = max(0.0, power_w) * dt_h / 1000.0
    counters["kwh_day"] = counters.get("kwh_day", 0.0) + kwh
    counters["kwh_month"] = counters.get("kwh_month", 0.0) + kwh
    if replacing and kwh > 0:
        oil_l = kwh * efficiency / (OIL_KWH_PER_L * max(0.1, boiler_eff))
        counters["oil_l_month"] = counters.get("oil_l_month", 0.0) + oil_l
        if price is not None:
            counters["savings_month"] = counters.get("savings_month", 0.0) + oil_l * oil_eur_l - kwh * price
    return counters


# ---------------------------------------------------------------- controller
class ClimateController:
    """Owns settings, learned model and the remembered decision state of the AC."""

    def __init__(self, hass, entry_id: str, config: dict):
        self.hass = hass
        self.entry_id = entry_id
        self.cfg = {k: (config.get(k) or v) for k, v in AC_DEFAULTS.items()}
        self.model = ThermalModel()
        self.state = State()
        self.settings = Settings()
        self.mode = MODE_AUTO
        self.control_active = False  # off = observe only
        self.replace_switch = False
        self.surplus_switch = False
        self.manual_external = False  # manuell_an came from the remote: leave the AC alone
        self.radiators_taken = False  # we switched the radiators off and owe them back
        self.counters: dict = {}
        self.decision: Decision | None = None
        self.info: dict = {}
        self.listeners: list = []
        self._store = None
        self._unsub = None
        self._own_ctx: deque = deque(maxlen=50)
        self._last_cmd: datetime | None = None
        self._last_cmd_hvac: str | None = None
        self._last_hvac_cmd: datetime | None = None
        self._last_sp_cmd: datetime | None = None
        self._last_fan_cmd: datetime | None = None
        self._post_start: tuple[datetime, str] | None = None
        self._tone_batch: datetime | None = None
        self._compressor_since: datetime | None = None
        self._last_quarter: tuple | None = None
        self._last_account: datetime | None = None
        self._last_room: tuple[float, str, datetime] | None = None
        self._started_at: datetime | None = None

    # ------------------------------------------------------------ lifecycle
    async def async_start(self) -> None:
        self._store = Store(self.hass, STORE_VERSION, f"{DOMAIN}.climate.{self.entry_id}")
        try:
            self.load_dict(await self._store.async_load() or {})
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Loading climate data failed; starting with defaults")
        self._unsub = async_track_state_change_event(self.hass, [self.cfg[CONF_AC_ENTITY]], self._on_ac_change)

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        await self.async_safety_fallback()
        if self._store is not None:
            await self._store.async_save(self.to_dict())

    async def async_safety_fallback(self) -> None:
        """The radiators never stay off while the app is not working."""
        if self.radiators_taken:
            await self._radiators_back()

    def to_dict(self) -> dict:
        return {
            "model": self.model.to_dict(),
            "state": self.state.to_dict(),
            "settings": vars(self.settings).copy(),
            "mode": self.mode,
            "control_active": self.control_active,
            "replace_switch": self.replace_switch,
            "surplus_switch": self.surplus_switch,
            "manual_external": self.manual_external,
            "radiators_taken": self.radiators_taken,
            "counters": self.counters,
        }

    def load_dict(self, d: dict) -> None:
        self.model.load_dict(d.get("model") or {})
        self.state = State.from_dict(d.get("state"))
        for k, v in (d.get("settings") or {}).items():
            if hasattr(self.settings, k):
                setattr(self.settings, k, float(v))
        self.mode = d.get("mode") if d.get("mode") in MODES else MODE_AUTO
        self.control_active = bool(d.get("control_active", False))
        self.replace_switch = bool(d.get("replace_switch", False))
        self.surplus_switch = bool(d.get("surplus_switch", False))
        self.manual_external = bool(d.get("manual_external", False))
        self.radiators_taken = bool(d.get("radiators_taken", False))
        self.counters = dict(d.get("counters") or {})

    def _save(self) -> None:
        if self._store is not None:
            self._store.async_delay_save(self.to_dict, SAVE_DELAY_S)

    def _changed(self) -> None:
        for cb in list(self.listeners):
            try:
                cb()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Climate listener failed")

    # ------------------------------------------------------------ user controls (entities)
    def set_mode(self, mode: str) -> None:
        if mode in MODES:
            self.mode = mode
            self.manual_external = False
            self._save()
            self._changed()

    async def async_set_control_active(self, on: bool) -> None:
        self.control_active = bool(on)
        if not on:
            self._post_start = None
            await self.async_safety_fallback()
        self._save()
        self._changed()

    def set_switch(self, name: str, on: bool) -> None:
        if name in ("replace_switch", "surplus_switch"):
            setattr(self, name, bool(on))
            self._save()
            self._changed()

    def set_setting(self, name: str, value: float) -> None:
        if hasattr(self.settings, name):
            setattr(self.settings, name, float(value))
            self._save()
            self._changed()

    def reset_learning(self) -> None:
        self.model.reset()
        self._save()
        self._changed()

    # ------------------------------------------------------------ reading
    def _float(self, entity_id: str | None, now: datetime, max_age: timedelta | None = None) -> float | None:
        v = state_value(self.hass.states.get(entity_id) if entity_id else None, now, max_age)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _on(self, entity_id: str | None, default: bool) -> bool:
        v = state_value(self.hass.states.get(entity_id) if entity_id else None, None)
        return default if v is None else v == "on"

    def _radiators_heating(self) -> bool:
        for eid in self.cfg[CONF_AC_RADIATORS]:
            st = self.hass.states.get(eid)
            if st is None or st.state in UNAVAILABLE:
                continue
            action = st.attributes.get("hvac_action")
            if action is not None:
                if action == "heating":
                    return True
            elif st.state == "heat":
                return True
        return False

    def counter(self, key: str, now: datetime) -> float:
        """Counter value; 0 when its day / month is already over."""
        c = self.counters
        if key == "kwh_day" and c.get("day") != now.date().isoformat():
            return 0.0
        if key != "kwh_day" and c.get("month") != now.strftime("%Y-%m"):
            return 0.0
        return float(c.get(key, 0.0))

    def power_w(self) -> float:
        """Electrical power of the AC right now (compressor_power, W)."""
        ac = self.hass.states.get(self.cfg[CONF_AC_ENTITY])
        if ac is None or ac.state in UNAVAILABLE or ac.state == "off":
            return 0.0
        return max(0.0, _num(ac.attributes.get("compressor_power")))

    def expected_load_w(self, t: datetime, now: datetime) -> float:
        """AC load the battery planner should add for slot t (stage 1: current power for 2 h)."""
        if t - now >= EXPECTED_LOAD_AHEAD:
            return 0.0
        return self.power_w()

    # ------------------------------------------------------------ main step
    async def async_update(self, now: datetime, ext: dict | None = None) -> Decision:
        """One cycle (called every minute by the coordinator)."""
        ext = ext or {}
        ac = self.hass.states.get(self.cfg[CONF_AC_ENTITY])
        ac_ok = ac is not None and ac.state not in UNAVAILABLE
        attrs = ac.attributes if ac_ok else {}
        ac_hvac = ac.state if ac_ok else None
        power = _num(attrs.get("compressor_power"))
        freq = _num(attrs.get("compressor_frequency"))
        device = plausible_temp(attrs.get("indoor_temperature"))

        primary = plausible_temp(self._float(self.cfg[CONF_AC_ROOM_SENSOR], now, ROOM_MAX_AGE))
        fallback = plausible_temp(self._float(self.cfg[CONF_AC_ROOM_FALLBACK], now, ROOM_MAX_AGE))
        room, source = pick_room(primary, fallback)
        self._started_at = self._started_at or now
        if room is not None:
            self._last_room = (room, source, now)
        elif self._last_room is not None and now - self._last_room[2] <= ROOM_HOLD:
            room, source = self._last_room[0], self._last_room[1] + " (gehalten)"
        outdoor = self._float(self.cfg[CONF_AC_OUTDOOR_SENSOR], now, OUTDOOR_MAX_AGE)
        if outdoor is None or not -40.0 <= outdoor <= 50.0:
            outdoor = ext.get("outdoor_forecast")
        radiators_on = self._radiators_heating()

        if freq > 0:
            self._compressor_since = self._compressor_since or now
        else:
            self._compressor_since = None
        run_min = (now - self._compressor_since).total_seconds() / 60.0 if self._compressor_since else 0.0

        efficiency = self.model.efficiency(outdoor if outdoor is not None else 10.0)
        price = ext.get("price")
        s = self.settings

        if self._last_account is not None:
            dt_h = (now - self._last_account).total_seconds() / 3600.0
            if 0 < dt_h <= MAX_ACCOUNT_GAP_H:
                replacing = ac_hvac == "heat" and not radiators_on
                account(self.counters, now, power, dt_h, replacing, efficiency, price,
                        s.oil_eur_l, s.boiler_eff_pct / 100.0)
        self._last_account = now

        quarter = (now.date(), now.hour, now.minute // SAMPLE_MIN)
        if quarter != self._last_quarter:
            self._last_quarter = quarter
            self.model.add_sample(Sample(ts=now, room=room, outdoor=outdoor, device=device,
                                         hvac=ac_hvac or "unavailable", power_w=power, freq_hz=freq,
                                         radiators_on=radiators_on, fan=attrs.get("fan_mode"), run_min=round(run_min, 1)))
            self._save()

        if room is None and self._last_room is None and now - self._started_at < STARTUP_GRACE:
            # no room value yet after a restart: do not mistake that for a sensor failure
            d = Decision("start", "Start: warte auf den Raumsensor", None)
            if not self.control_active and self.radiators_taken:
                await self._radiators_back()
            self.decision = d
            self.info = {"modus": d.mode, "begruendung": d.reason, "steuerung_aktiv": self.control_active,
                         "betriebsmodus": self.mode, "raumsensor_quelle": source, "klima_ist": ac_hvac}
            self._changed()
            return d

        inputs = Inputs(
            now=now, mode=self.mode, app_enabled=ext.get("app_enabled", True),
            replace_switch=self.replace_switch, surplus_switch=self.surplus_switch,
            room=room, outdoor=outdoor, price=price,
            home=self._on(self.cfg[CONF_AC_PRESENCE_HOME], True),
            in_living=self._on(self.cfg[CONF_AC_PRESENCE_LIVING], False),
            soc=ext.get("soc"), curtailed_wh_2h=float(ext.get("curtailed_wh_2h") or 0.0),
            grid_charging=bool(ext.get("grid_charging")), efficiency=efficiency,
            offset_heat=self.model.device_offset("heat"), offset_cool=self.model.device_offset("cool"),
            settings=s,
        )
        d = decide(inputs, self.state)
        self.decision = d

        if self.control_active:
            await self._execute(now, d, ac_hvac, attrs)
        elif self.radiators_taken:
            await self._radiators_back()

        self.info = {
            "modus": d.mode,
            "begruendung": d.reason,
            "steuerung_aktiv": self.control_active,
            "betriebsmodus": self.mode,
            "klima_soll_an": d.climate_on,
            "hvac": d.hvac,
            "sollwert": d.setpoint,
            "luefter": d.fan,
            "heizkoerper": "an" if d.radiators else "aus",
            "zieltemperatur_raum": d.target_room,
            "grenzpreis": d.break_even,
            "kosten_klima_kwh_waerme": d.cost_ac,
            "kosten_oel_kwh_waerme": d.cost_oil,
            "effizienz": round(efficiency, 2),
            "raumtemperatur": room,
            "raumsensor_quelle": source,
            "aussentemperatur": outdoor,
            "geraetesensor": device,
            "klima_ist": ac_hvac,
            "klima_leistung_w": round(power),
            "kompressor_hz": freq,
            "heizkoerper_heizen": radiators_on,
            "heizkoerper_uebernommen": self.radiators_taken,
        }
        self._changed()
        return d

    # ------------------------------------------------------------ executing
    async def _execute(self, now: datetime, d: Decision, ac_hvac: str | None, attrs: dict) -> None:
        self._tone_batch = None
        if not d.radiators and not self.radiators_taken:
            await self._radiators_off(now)
        elif d.radiators and self.radiators_taken:
            await self._radiators_back()

        if ac_hvac is None or d.climate_on is None:
            return
        if self.mode == MODE_MANUAL_ON and self.manual_external:
            return  # switched on by remote / Midea app: the user's settings stay
        ac_entity = self.cfg[CONF_AC_ENTITY]
        want = d.hvac if d.climate_on else "off"
        if ac_hvac != want:
            if d.send_hvac or self._last_hvac_cmd is None or now - self._last_hvac_cmd >= HVAC_RETRY:
                self._last_hvac_cmd = now
                if want == "off":
                    self._post_start = None
                    await self._ac_call(now, "climate", "set_hvac_mode", {"entity_id": ac_entity, "hvac_mode": "off"}, want)
                else:
                    data = {"entity_id": ac_entity, "hvac_mode": want}
                    if d.setpoint is not None:
                        data["temperature"] = d.setpoint
                    await self._ac_call(now, "climate", "set_temperature", data, want)
                    self._post_start = (now + POST_START_DELAY, want)
                    self._last_sp_cmd = now
            return
        if want == "off":
            return
        sp_due = d.send_setpoint or self._last_sp_cmd is None or now - self._last_sp_cmd >= CMD_INTERVAL
        if d.setpoint is not None and _num(attrs.get("temperature"), None) != d.setpoint and sp_due:
            self._last_sp_cmd = now
            await self._ac_call(now, "climate", "set_temperature",
                                {"entity_id": ac_entity, "temperature": d.setpoint}, want)
        if self._post_start and now >= self._post_start[0]:
            _, hvac = self._post_start
            self._post_start = None
            if hvac == want:
                if hvac == "heat" and attrs.get("indirect_wind") is not True:
                    await self._ac_call(now, "midea_ac_lan", "set_attribute", {
                        "device_id": self.cfg[CONF_AC_DEVICE_ID], "attribute": "indirect_wind", "value": True}, want)
                if d.fan and attrs.get("fan_mode") != d.fan:
                    self._last_fan_cmd = now
                    await self._ac_call(now, "climate", "set_fan_mode", {"entity_id": ac_entity, "fan_mode": d.fan}, want)
            return
        fan_due = d.send_fan or self._last_fan_cmd is None or now - self._last_fan_cmd >= FAN_INTERVAL
        if d.fan and attrs.get("fan_mode") != d.fan and self._post_start is None and fan_due:
            self._last_fan_cmd = now
            await self._ac_call(now, "climate", "set_fan_mode", {"entity_id": ac_entity, "fan_mode": d.fan}, want)

    async def _ac_call(self, now: datetime, domain: str, service: str, data: dict, expected_hvac: str) -> None:
        if self._tone_batch != now:
            self._tone_batch = now
            await self._call("midea_ac_lan", "set_attribute", {
                "device_id": self.cfg[CONF_AC_DEVICE_ID], "attribute": "prompt_tone", "value": False})
        self._last_cmd = now
        self._last_cmd_hvac = expected_hvac
        await self._call(domain, service, data)

    async def _call(self, domain: str, service: str, data: dict) -> bool:
        ctx = Context()
        self._own_ctx.append(ctx.id)
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True, context=ctx)
            return True
        except Exception:  # noqa: BLE001 - one failing command must not stop the others
            _LOGGER.exception("Climate command %s.%s failed", domain, service)
            return False

    async def _radiators_off(self, now: datetime) -> None:
        self.radiators_taken = True
        self._save()
        await self._call("automation", "turn_off", {"entity_id": self.cfg[CONF_AC_HEATING_AUTOMATIONS]})
        await self._call("climate", "set_hvac_mode", {"entity_id": self.cfg[CONF_AC_RADIATORS], "hvac_mode": "off"})
        _LOGGER.info("Radiators handed over to the heat pump")

    async def _radiators_back(self) -> None:
        ok = await self._call("climate", "set_hvac_mode", {"entity_id": self.cfg[CONF_AC_RADIATORS], "hvac_mode": "heat"})
        ok = await self._call("automation", "turn_on", {"entity_id": self.cfg[CONF_AC_HEATING_AUTOMATIONS]}) and ok
        retrigger = [a for a in AC_RETRIGGER_AUTOMATIONS if a in self.cfg[CONF_AC_HEATING_AUTOMATIONS]]
        if retrigger:
            await self._call("automation", "trigger", {"entity_id": retrigger, "skip_condition": False})
        if ok:
            self.radiators_taken = False
            self._save()
            _LOGGER.info("Radiators got the heating back")

    # ------------------------------------------------------------ remote control detection
    @callback
    def _on_ac_change(self, event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if not self.control_active or old is None or new is None:
            return
        ctx = new.context
        own = ctx is not None and (ctx.id in self._own_ctx or getattr(ctx, "parent_id", None) in self._own_ctx)
        if not own and self._last_cmd is not None and new.state == self._last_cmd_hvac:
            changed = getattr(new, "last_changed", None)
            own = changed is None or changed - self._last_cmd <= OWN_CMD_WINDOW
        mode = manual_mode_for(old.state, new.state, own)
        if mode is None:
            return
        _LOGGER.info("AC changed outside the app (%s -> %s): mode %s", old.state, new.state, mode)
        self.mode = mode
        self.manual_external = mode == MODE_MANUAL_ON
        self._post_start = None
        self._save()
        self._changed()


def _num(v, default: float | None = 0.0) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
