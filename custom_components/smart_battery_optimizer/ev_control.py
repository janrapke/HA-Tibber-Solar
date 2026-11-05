"""Car charging control (Tesla over the local Tesla BLE integration).

Every minute from the coordinator: replan (ev_charge_model) and, when "Ladesteuerung aktiv"
is on, start/stop charging and set the current. Off = the plan is only shown.
Order of commands: start charging first, then set the current; a current set while the car
is not charging was ignored in the test of 05.11.2026 (the car started with 16 A).

Plugging in: the car starts charging on its own right away. The Wall Connector sees the car
within seconds, the BLE link only after up to two minutes. So on "car connected" at the Wall
Connector the BLE link is pulled up at once (refresh button) and the control runs immediately,
without waiting for the minute cycle. While BLE is away the plan uses the Tibber charge level,
so it is ready before the car is plugged in.
"""
from __future__ import annotations

import asyncio

from datetime import datetime, time, timedelta
import logging

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
import homeassistant.util.dt as dt_util

from .const import CONF_AC_OUTDOOR_SENSOR, CONF_BALCONY_POWER_SENSOR, DOMAIN
from .ev_charge_model import (PREHEAT_MIN_PER_K, SLOT, WEEKDAY_KEYS, Settings, learn_per_k, make_plan,
                              next_departure, preheat_minutes, preheat_needed, slot_start, surplus_amps)
from .price_forecast import german_holidays

_LOGGER = logging.getLogger(__name__)

CAR = "tesla_748947"
E_SOC = f"sensor.{CAR}_battery"
E_PLUG = f"binary_sensor.{CAR}_charge_cable_connected"
E_LINK = f"binary_sensor.{CAR}_connection"
E_CHARGING = f"switch.{CAR}_charging"
E_AMPS = f"number.{CAR}_charging_amps"
E_LIMIT = f"number.{CAR}_charge_limit"
E_CLIMATE = f"climate.{CAR}_climate"
E_CABIN = f"sensor.{CAR}_cabin_temperature"
E_OUTSIDE = f"sensor.{CAR}_outside_temperature"
E_WAKE = f"button.{CAR}_wake_up"
E_REFRESH = f"button.{CAR}_force_refresh_vehicle_data"
E_SOC_TIBBER = "sensor.reskey_ladezustand"           # Tibber (Tesla cloud), also while BLE is out of reach
E_PLUG_TIBBER = "binary_sensor.reskey_stecker"
E_WC_CAR = "binary_sensor.tesla_wall_connector_fahrzeug_verbunden"
E_WC_STATUS = "sensor.tesla_wall_connector_status"
REFRESH_GAP = timedelta(seconds=20)
E_ASLEEP = f"binary_sensor.{CAR}_asleep"
E_SEAT = f"select.aussen_{CAR}_front_left_seat_climate"
E_WHEEL = f"switch.aussen_{CAR}_steering_wheel_heater"
OUTDOOR_FALLBACK = "sensor.openweathermap_temperature"
PRE_HOLD = timedelta(minutes=10)     # safety: 10 min after the ready time the climate goes off if the car is still in reach
PRE_GIVE_UP = timedelta(minutes=60)  # no BLE contact until then: assume driven away and forget it
PRE_WAKE = timedelta(minutes=3)      # wake the car this long before the start so the values are fresh
PRE_COLD_EXTRAS = 3.0                # below this outside temperature also seat and steering wheel heating
STORE_VERSION = 1
CMD_GAP = timedelta(seconds=60)
REF_EVERY = timedelta(hours=6)
FULL_AMPS = 16
HOUSE_FULL_PCT = 97.0
DEFAULTS = {"active": False, "min_target": 20.0, "max_target": 80.0, "now_target": 20.0, "now_on": False,
            "travel_on": False, "travel_at": None, "travel_target": 80.0, "dep_weekday": "06:30", "dep_weekend": "10:00",
            "wait_ct": 1.0, "negative_target": 90.0,
            # preheating: overall switch, per weekday on/off and ready time, only needed below pre_below °C
            "pre_on": True, "pre_below": 10.0, "pre_target": 21.0, "pre_per_k": PREHEAT_MIN_PER_K,
            **{f"pre_{d}_on": d in ("mo", "di", "do", "fr") for d in WEEKDAY_KEYS},
            **{f"pre_{d}_at": "06:40" for d in WEEKDAY_KEYS}}


class EvChargeController:
    def __init__(self, hass, entry_id: str, config: dict, coordinator) -> None:
        self.hass = hass
        self.config = config
        self.coordinator = coordinator
        self.store = Store(hass, STORE_VERSION, f"{DOMAIN}.ev.{entry_id}")
        self.s = dict(DEFAULTS)
        self.plan = None
        self.mode = "aus"          # aus / plan / sofort / ueberschuss
        self.reason = "Start"
        self.departure: datetime | None = None
        self.reference: list[float] = []
        self._ref_at: datetime | None = None
        self._last_cmd: datetime | None = None
        self._surplus_a = 0
        self.listeners: list = []
        self.soc_source = None
        self._last_result: dict = {}
        self._lock = asyncio.Lock()
        self._refresh_at: datetime | None = None
        self._unsub = None
        # preheating state
        self.pre_status = "aus"
        self._holidays: set = set()
        self.pre_next: dict = {}
        self._pre_started: datetime | None = None   # we switched the climate on at this time
        self._pre_outdoor: float | None = None
        self._pre_from: float | None = None         # cabin (or outside) temperature at the start
        self._pre_learned = False
        self._pre_done_for: datetime | None = None  # ready time already started
        self._pre_woken_for: datetime | None = None

    async def async_start(self) -> None:
        data = await self.store.async_load() or {}
        self.s.update({k: v for k, v in data.items() if k in DEFAULTS})
        if data.get("pre_per_k") == 0.5:  # first default (measured against outside), not learned
            self.s["pre_per_k"] = PREHEAT_MIN_PER_K
        self._unsub = async_track_state_change_event(
            self.hass, [E_WC_CAR, E_WC_STATUS, E_PLUG_TIBBER, E_LINK, E_PLUG, E_CHARGING], self._on_car_event)

    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _on_car_event(self, event) -> None:
        """Plug-in seen: get the BLE link up and decide now, not at the next minute."""
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or (old is not None and old.state == new.state) or not self._last_result:
            return
        eid = event.data.get("entity_id")
        now = dt_util.utcnow()
        if eid in (E_WC_CAR, E_PLUG_TIBBER, E_WC_STATUS) and new.state in ("on", "charging"):
            self._last_cmd = None   # a fresh plug-in must not wait for the command gap
            if self.s["active"] and self._state(E_LINK) != "on" and (
                    self._refresh_at is None or now - self._refresh_at >= REFRESH_GAP):
                self._refresh_at = now
                self.hass.async_create_task(self._try("button", "press", E_REFRESH))
        if eid in (E_LINK, E_PLUG) and new.state == "on":
            self._last_cmd = None
        self.hass.async_create_task(self.async_update(now, self._last_result, quick=True))

    async def async_set(self, key: str, value) -> None:
        self.s[key] = value
        await self.store.async_save(self.s)
        self._changed()

    def _changed(self) -> None:
        for cb in list(self.listeners):
            cb()

    # ------------------------------------------------------------------ reading
    def _state(self, eid):
        st = self.hass.states.get(eid)
        return None if st is None or st.state in ("unknown", "unavailable") else st.state

    def _float(self, eid):
        try:
            return float(self._state(eid))
        except (TypeError, ValueError):
            return None

    def _prices(self) -> dict:
        """Known Tibber prices per quarter hour (local slot start -> EUR/kWh)."""
        rows = self.coordinator.tibber_prices
        quarter = len(rows) > 60
        out = {}
        for p in rows:
            t = dt_util.as_local(p["datetime"])
            for k in range(1 if quarter else 4):
                out[slot_start(t + k * SLOT)] = float(p.get("total") or 0.0)
        return out

    async def _refresh_reference(self, now: datetime) -> None:
        pf = getattr(self.coordinator, "price_forecast", None)
        if pf is None or (self._ref_at and now - self._ref_at < REF_EVERY):
            return
        self._ref_at = now
        today = dt_util.as_local(now).date()
        prices = await pf._price_history(today, dt_util.DEFAULT_TIME_ZONE)
        cut = dt_util.as_utc(now) - timedelta(days=28)
        self.reference = sorted(p for t, p in prices.items() if t >= cut)

    def _travel_at(self) -> datetime | None:
        if not self.s["travel_on"] or not self.s["travel_at"]:
            return None
        t = dt_util.parse_datetime(self.s["travel_at"])
        return dt_util.as_local(t) if t else None

    # ------------------------------------------------------------------ loop
    async def async_update(self, now: datetime, result: dict, quick: bool = False) -> None:
        if not quick:
            self._last_result = result
        async with self._lock:
            await self._update(now, result, quick)

    def _soc(self):
        """Car charge level: BLE first, the Tibber value while the car is out of BLE reach."""
        soc = self._float(E_SOC)
        if soc is not None:
            self.soc_source = "auto"
            return soc
        soc = self._float(E_SOC_TIBBER)
        self.soc_source = "tibber" if soc is not None else None
        return soc

    def _plugged(self) -> bool:
        return "on" in (self._state(E_PLUG), self._state(E_WC_CAR), self._state(E_PLUG_TIBBER))

    async def _update(self, now: datetime, result: dict, quick: bool) -> None:
        local = dt_util.as_local(now)
        if not quick:
            await self._refresh_reference(now)
        hol = german_holidays(local.year) | german_holidays(local.year + 1)
        self._holidays = hol
        wd = time.fromisoformat(self.s["dep_weekday"])
        we = time.fromisoformat(self.s["dep_weekend"])
        self.departure = next_departure(local, (wd.hour, wd.minute), (we.hour, we.minute), hol)
        travel = self._travel_at()
        if travel and travel < local:
            await self.async_set("travel_on", False)
            travel = None
        soc = self._soc()
        plugged = self._plugged()
        if soc is not None:
            settings = Settings(self.s["min_target"], self.s["max_target"], travel, self.s["travel_target"],
                                self.s["wait_ct"] / 100.0, self.s["negative_target"])
            self.plan = make_plan(local, soc, self._prices(), self.reference, self.departure, settings)
        # what should happen now
        target_amps = 0
        if self.s["now_on"]:
            if soc is not None and soc >= self.s["now_target"]:
                await self.async_set("now_on", False)
                self.mode, self.reason = "aus", f"Sofortladen fertig ({soc:.0f} %)"
            else:
                self.mode, self.reason, target_amps = "sofort", f"Sofortladen bis {self.s['now_target']:.0f} %", FULL_AMPS
        if target_amps == 0 and self.plan and self.plan.charge_now(local):
            price, why = self.plan.slots[slot_start(local)]
            self.mode, self.reason, target_amps = "plan", f"{why} ({price:.3f} €/kWh)", FULL_AMPS
        if target_amps == 0:
            limit = (travel and self.s["travel_target"]) or self.s["max_target"]
            balcony = self._float(self.config.get(CONF_BALCONY_POWER_SENSOR)) or 0.0
            amps = surplus_amps(result.get("soc"), HOUSE_FULL_PCT, (result.get("inverter_w") or 0) > 50,
                                balcony, result.get("solar") or 0.0, self._surplus_a)
            if amps and soc is not None and soc < limit:
                self._surplus_a = amps
                self.mode, self.reason, target_amps = "ueberschuss", f"Überschuss {amps} A", amps
            else:
                self._surplus_a = 0
                self.mode = "aus"
                self.reason = self.plan.note if self.plan else "kein Ladestand"
        if not plugged:
            self.reason = "nicht angesteckt – " + self.reason
        if self.s["active"] and plugged and self._state(E_LINK) == "on":
            await self._execute(now, target_amps, travel)
        if quick:
            self._changed()
            return
        try:
            await self._preheat(local)
        except Exception:  # noqa: BLE001 - never break the charging loop
            _LOGGER.exception("Tesla preheat failed")
        self._changed()

    # ------------------------------------------------------------------ preheating
    def _outdoor(self) -> float | None:
        """House weather sensor first (the car value is stale while it sleeps), the car as fallback."""
        v = self._float(self.config.get(CONF_AC_OUTDOOR_SENSOR) or OUTDOOR_FALLBACK)
        return v if v is not None else self._float(E_OUTSIDE)

    def _next_ready(self, local: datetime) -> datetime | None:
        """Next ready time from the weekday schedule; today's stays current until its hold is over."""
        if not self.s["pre_on"]:
            return None
        for add in range(8):
            day = (local + timedelta(days=add)).date()
            key = WEEKDAY_KEYS[day.weekday()]
            if not self.s[f"pre_{key}_on"] or day in self._holidays:  # holidays: nobody drives to work
                continue
            at = time.fromisoformat(self.s[f"pre_{key}_at"])
            ready = datetime.combine(day, at, tzinfo=local.tzinfo)
            if ready + PRE_HOLD > local:
                return ready
        return None

    async def _preheat(self, local: datetime) -> None:
        target = float(self.s["pre_target"])
        climate_on = self._state(E_CLIMATE) not in (None, "off")
        link = self._state(E_LINK) == "on"
        if self._pre_started:                      # started by us: learn the warm-up, end after the hold
            cabin = self._float(E_CABIN)
            mins = (local - self._pre_started).total_seconds() / 60
            if not self._pre_learned and cabin is not None and cabin >= target - 1 and self._pre_from is not None:
                frost = self._pre_outdoor is not None and self._pre_outdoor < 0
                await self.async_set("pre_per_k", learn_per_k(self.s["pre_per_k"], mins, self._pre_from, target, frost))
                self._pre_learned = True
                _LOGGER.info("Tesla preheat: from %.1f °C to %.0f °C in %.1f min, now %.2f min/K",
                             self._pre_from, target - 1, mins, self.s["pre_per_k"])
            if link and not climate_on:
                self._pre_started = None           # switched off by hand: leave it alone
            elif local >= self._pre_done_for + PRE_HOLD or not self.s["pre_on"]:
                if link:                           # still in BLE reach = not driven away: switch off
                    await self._pre_off(link)
                elif local >= self._pre_done_for + PRE_GIVE_UP:
                    self._pre_started, self.pre_status = None, "fertig"
                else:
                    self.pre_status = "aus wartet auf Verbindung"
                    await self._try("button", "press", E_WAKE)
            else:
                self.pre_status = "läuft"
        ready = self._next_ready(local)
        if ready is None:
            self.pre_next = {}
            if not self._pre_started:
                self.pre_status = "aus" if not self.s["pre_on"] else "kein Tag aktiv"
            return
        outdoor = self._outdoor()
        # the cabin value is only fresh while the car is awake (it is woken 3 min before the start)
        cabin = self._float(E_CABIN) if self._state(E_ASLEEP) == "off" else None
        from_temp = cabin if cabin is not None else outdoor
        mins = preheat_minutes(from_temp, target, self.s["pre_per_k"], outdoor is not None and outdoor < 0)
        start = ready - timedelta(minutes=mins)
        needed = preheat_needed(outdoor, self.s["pre_below"])
        self.pre_next = {"fertig_um": ready.isoformat(), "start": start.isoformat(), "minuten": round(mins),
                         "aussen": outdoor, "innen": cabin, "noetig": needed, "min_pro_k": self.s["pre_per_k"]}
        if self._pre_started or self._pre_done_for == ready:
            return
        if not needed:
            self.pre_status = f"nicht nötig ({outdoor:.0f} °C)"
            return
        self.pre_status = "geplant"
        if local >= ready:
            return
        if start - PRE_WAKE <= local < start and self._pre_woken_for != ready:
            self._pre_woken_for = ready
            await self._try("button", "press", E_WAKE)
        if local >= start and link:
            self._pre_done_for = ready
            if climate_on:                         # already on by hand: nothing to do
                return
            await self._try("climate", "set_hvac_mode", E_CLIMATE, hvac_mode="heat_cool")
            await self._try("climate", "set_temperature", E_CLIMATE, temperature=target)
            if outdoor is not None and outdoor < PRE_COLD_EXTRAS:
                await self._try("select", "select_option", E_SEAT, option="Medium heat")
                await self._try("switch", "turn_on", E_WHEEL)
            self._pre_started, self._pre_outdoor, self._pre_from, self._pre_learned = local, outdoor, from_temp, False
            self.pre_status = "läuft"
        elif local >= start and self._pre_woken_for == ready:
            await self._try("button", "press", E_WAKE)  # no link yet: keep trying to reach the car

    async def _pre_off(self, link: bool) -> None:
        if link:
            await self._try("climate", "set_hvac_mode", E_CLIMATE, hvac_mode="off")
            if self._pre_outdoor is not None and self._pre_outdoor < PRE_COLD_EXTRAS:
                await self._try("select", "select_option", E_SEAT, option="Off")
                await self._try("switch", "turn_off", E_WHEEL)
        self._pre_started = None
        self.pre_status = "fertig"

    async def _try(self, domain, service, entity_id, **data) -> None:
        try:
            await self._call(domain, service, entity_id, **data)
        except Exception as err:  # noqa: BLE001 - BLE may drop
            _LOGGER.warning("Tesla preheat command failed: %s", err)

    async def _execute(self, now: datetime, amps: int, travel) -> None:
        if self._last_cmd and now - self._last_cmd < CMD_GAP:
            return
        charging = self._state(E_CHARGING) == "on"
        want_limit = 100 if travel and self.s["travel_target"] > 80 else max(50, int(self.s["max_target"]))
        if self.s["now_on"]:
            want_limit = max(want_limit, int(self.s["now_target"]))
        slot = self.plan.slots.get(slot_start(dt_util.as_local(now))) if self.plan else None
        if slot and slot[0] < 0:  # paid to charge: the car may go up to the negative-price target
            want_limit = max(want_limit, int(self.s["negative_target"]))
        limit, soc = self._float(E_LIMIT), self._float(E_SOC)
        try:
            if amps and not charging and limit is not None and soc is not None and soc >= limit - 1:
                if int(limit) != want_limit:  # the car would stop at its own limit right away
                    await self._call("number", "set_value", E_LIMIT, value=want_limit)
                else:
                    return
            elif amps and not charging:
                await self._call("switch", "turn_on", E_CHARGING)
            elif not amps and charging:
                await self._call("switch", "turn_off", E_CHARGING)
            elif amps and charging and self._float(E_AMPS) != amps:
                await self._call("number", "set_value", E_AMPS, value=amps)
            elif limit is not None and int(limit) != want_limit:
                await self._call("number", "set_value", E_LIMIT, value=want_limit)
            else:
                return
            self._last_cmd = now
        except Exception as err:  # noqa: BLE001 - BLE may drop, retry next minute
            _LOGGER.warning("Tesla command failed: %s", err)
            self._last_cmd = now

    async def _call(self, domain, service, entity_id, **data) -> None:
        _LOGGER.info("Tesla: %s.%s %s %s", domain, service, entity_id, data or "")
        await self.hass.services.async_call(domain, service, {"entity_id": entity_id, **data}, blocking=True)

    # ------------------------------------------------------------------ views
    def attributes(self) -> dict:
        p = self.plan
        slots = sorted(p.slots.items()) if p else []
        return {
            "modus": self.mode, "grund": self.reason, "steuerung_aktiv": self.s["active"],
            "abfahrt": self.departure.isoformat() if self.departure else None,
            # an instant charge runs outside the plan; the departure value must include it
            "ladestand_bei_abfahrt": (max(p.soc_at_departure, self.s["now_target"]) if self.s["now_on"]
                                      else p.soc_at_departure) if p else None,
            "kwh": p.kwh if p else None, "kosten_eur": p.cost_eur if p else None,
            "viertelstunden": [{"start": t.strftime("%a %H:%M"), "preis": round(v[0], 3), "grund": v[1]} for t, v in slots],
            "fenster": _windows(slots),
            "ladestand_quelle": self.soc_source,
            "referenz_tage": 28, "referenz_preise": len(self.reference),
            "vorklima": {"status": self.pre_status, **self.pre_next},
        }


WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def _windows(slots) -> list[dict]:
    """Planned quarter hours merged into charging windows for the dashboard."""
    out = []
    for t, (price, why) in slots:
        if out and out[-1]["_end"] == t:
            w = out[-1]
        else:
            w = {"tag": WEEKDAYS[t.weekday()], "von": t.strftime("%H:%M"), "_prices": [], "grund": why}
            out.append(w)
        w["_prices"].append(price)
        w["_end"] = t + SLOT
    for w in out:
        prices = w.pop("_prices")
        w["bis"] = w.pop("_end").strftime("%H:%M")
        w["preis"] = round(sum(prices) / len(prices), 3)
        w["viertelstunden"] = len(prices)
    return out
