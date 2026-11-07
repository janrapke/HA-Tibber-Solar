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

Safety net against unplanned charging (the car has a schedule 00:00-01:00, but starts on its own
e.g. when plugged in within six hours after it): the Wall Connector is the truth for "power flows",
independent of BLE. While it charges outside plan / instant / surplus the watchdog checks every
15 s: stop over BLE (repeated every minute), no BLE link -> refresh, after 2 min also wake,
after 3 min a phone notification that the stop does not work.
"""
from __future__ import annotations

import asyncio

from datetime import datetime, time, timedelta
import logging

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
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
# BLE presence via the ESP32 proxy at the Wall Connector: the car advertises also while asleep, so this
# stays "home" all night and turns "not_home" only when it is driven away (history 04.-07.11.2026)
E_HOME = f"device_tracker.{CAR}_location"
# the tracker says "not_home" only 4-5 min after the car is gone. Every departure 05.-07.11.2026 went:
# driver in the car (user_present) -> unplugged -> BLE link off 20-40 s later -> tracker 4-5 min later.
# So: link off within DEPART_WINDOW after someone got in or the car was unplugged = driving away now.
# Replayed on 04.-07.11.2026: all 7 departures 4-5 min earlier, no false "away" at home (the link drops
# for 10-60 s on ESP reconnects, but nobody gets in or unplugs then). Only "-> on" / "on -> off" count:
# a reconnect also moves user_present from unavailable to off.
E_PRESENT = f"binary_sensor.{CAR}_user_present"
DEPART_WINDOW = timedelta(minutes=10)
E_SOC_TIBBER = "sensor.reskey_ladezustand"           # Tibber (Tesla cloud), also while BLE is out of reach
E_PLUG_TIBBER = "binary_sensor.reskey_stecker"
E_WC_CAR = "binary_sensor.tesla_wall_connector_fahrzeug_verbunden"
E_WC_STATUS = "sensor.tesla_wall_connector_status"
E_WC_POWER = "sensor.tesla_wall_connector_gesamtleistung"
NOTIFY = "mobile_app_sm_s938b"
NOTIFY_ID = f"{DOMAIN}_ev_unplanned"
REFRESH_GAP = timedelta(seconds=20)
WATCH_EVERY = timedelta(seconds=15)
WAKE_AFTER = timedelta(minutes=2)
ALERT_AFTER = timedelta(minutes=3)
WC_CHARGING_W = 800
E_ASLEEP = f"binary_sensor.{CAR}_asleep"
E_SEAT = f"select.aussen_{CAR}_front_left_seat_climate"
E_WHEEL = f"switch.aussen_{CAR}_steering_wheel_heater"
OUTDOOR_FALLBACK = "sensor.openweathermap_temperature"
PRE_HOLD = timedelta(minutes=10)     # safety: 10 min after the ready time the climate goes off if the car is still in reach
PRE_NOW_HOLD = timedelta(minutes=15) # "Jetzt vorheizen": stays on this long after the estimated warm time
PRE_NOW_REACH = timedelta(minutes=10)  # "Jetzt vorheizen": time to reach and wake the car before giving up
PRE_GIVE_UP = timedelta(minutes=60)  # no BLE contact until then: assume driven away and forget it
PRE_WAKE = timedelta(minutes=3)      # wake the car this long before the start so the values are fresh
PRE_COLD_EXTRAS = 3.0                # below this outside temperature also seat and steering wheel heating
PRE_ALERT_TRIES = 3                  # climate still off after this many tries: tell the phone (06.11.2026: "on" got lost)
PRE_NOTIFY_ID = f"{DOMAIN}_ev_preheat"
CMD_TIMEOUT = 45                     # s; a BLE command hung 8 min on 06.11.2026 and blocked the whole loop
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
        self._unsub_watch = None
        self._unplanned_since: datetime | None = None
        self._unplanned_woke = False
        self._alerted = False
        self.watch_status = "ok"
        # preheating state
        self.pre_status = "aus"
        self._holidays: set = set()
        self.pre_next: dict = {}
        self._pre_started: datetime | None = None   # we sent "climate on" first at this time
        self._pre_on_at: datetime | None = None     # the car reported the climate on at this time
        self._pre_tries = 0
        self._pre_alerted = False
        self._pre_extras = False                    # seat / steering wheel switched on by us
        self._pre_outdoor: float | None = None
        self._pre_from: float | None = None         # cabin (or outside) temperature at the start
        self._pre_learned = False
        self._pre_done_for: datetime | None = None  # ready time already started
        self._pre_until: datetime | None = None     # running: warm at (scheduled: the ready time)
        self._pre_manual = False                    # started with "Jetzt vorheizen"
        self._pre_mins: float | None = None         # estimated warm-up of the current run, minutes
        self._entered_at: datetime | None = None    # someone got into the car (user_present -> on)
        self._unplugged_at: datetime | None = None  # unplugged at the Wall Connector (on -> off)
        self._pre_woken_for: datetime | None = None

    async def async_start(self) -> None:
        data = await self.store.async_load() or {}
        self.s.update({k: v for k, v in data.items() if k in DEFAULTS})
        if data.get("pre_per_k") == 0.5:  # first default (measured against outside), not learned
            self.s["pre_per_k"] = PREHEAT_MIN_PER_K
        self._unsub = async_track_state_change_event(
            self.hass, [E_WC_CAR, E_WC_STATUS, E_PLUG_TIBBER, E_LINK, E_PLUG, E_CHARGING, E_PRESENT],
            self._on_car_event)
        self._unsub_watch = async_track_time_interval(self.hass, self._watch, WATCH_EVERY)

    def async_stop(self) -> None:
        for name in ("_unsub", "_unsub_watch"):
            unsub = getattr(self, name)
            if unsub:
                unsub()
                setattr(self, name, None)

    @callback
    def _watch(self, _now) -> None:
        """Between the minute cycles: look again while power flows that nobody asked for."""
        if self._last_result and self.s["active"] and self._wc_charging():
            self.hass.async_create_task(self.async_update(dt_util.utcnow(), self._last_result, quick=True))

    @callback
    def _on_car_event(self, event) -> None:
        """Plug-in seen: get the BLE link up and decide now, not at the next minute."""
        new, old = event.data.get("new_state"), event.data.get("old_state")
        eid = event.data.get("entity_id")
        self._note_departure_signs(eid, old.state if old else None, new.state if new else None)
        if eid == E_PRESENT:
            return
        if new is None or (old is not None and old.state == new.state) or not self._last_result:
            return
        now = dt_util.utcnow()
        if eid in (E_WC_CAR, E_PLUG_TIBBER) and new.state == "on" and old is not None and old.state == "off"                 and not self.s["active"]:
            # switched off and forgotten: a new plug-in hands the car back to the control
            _LOGGER.info("Tesla plugged in while the control was off - switching it on again")
            self.hass.async_create_task(self.async_set("active", True))
            self.s["active"] = True
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
        if key in ("active", "now_on", "now_target", "travel_on", "travel_at", "travel_target", "max_target",
                   "min_target") and self._last_result and not self._lock.locked():
            # a changed wish (e.g. instant charging off) acts now, not at the next minute
            self._last_cmd = None
            self.hass.async_create_task(self.async_update(dt_util.utcnow(), self._last_result, quick=True))

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

    def _wc_charging(self) -> bool:
        """Power flows into the car per Wall Connector. Not while the car climates (that also draws)."""
        if self._state(E_CLIMATE) not in (None, "off") or self._pre_started is not None:
            return False
        return self._state(E_WC_STATUS) == "charging" or (self._float(E_WC_POWER) or 0.0) > WC_CHARGING_W

    async def _guard(self, now: datetime, target_amps: int, link: bool) -> None:
        """Escalate while the car charges although nothing is planned."""
        if not (self.s["active"] and target_amps == 0 and self._wc_charging()):
            if self._unplanned_since is not None:
                _LOGGER.info("Tesla: unplanned charging ended")
            if self._alerted:
                await self._try("persistent_notification", "dismiss", None, notification_id=NOTIFY_ID)
            self._unplanned_since, self._unplanned_woke, self._alerted = None, False, False
            self.watch_status = "ok"
            return
        if self._unplanned_since is None:
            self._unplanned_since = now
            _LOGGER.warning("Tesla charges although not planned (%s W) - stopping", self._float(E_WC_POWER))
        since = now - self._unplanned_since
        self.watch_status = "stoppe" if link else "keine Verbindung"
        if not link:
            if self._refresh_at is None or now - self._refresh_at >= REFRESH_GAP:
                self._refresh_at = now
                await self._try("button", "press", E_REFRESH)
            if since >= WAKE_AFTER and not self._unplanned_woke:
                self._unplanned_woke = True
                await self._try("button", "press", E_WAKE)
        if since >= ALERT_AFTER and not self._alerted:
            self._alerted = True
            self.watch_status = "Stoppen klappt nicht"
            kw = (self._float(E_WC_POWER) or 0.0) / 1000
            msg = (f"Das Auto lädt seit {int(since.total_seconds() // 60)} min ungeplant ({kw:.1f} kW). "
                   f"Stoppen über Bluetooth klappt nicht ({'verbunden' if link else 'keine Verbindung'}). "
                   "Bitte in der Tesla-App stoppen.")
            await self._try("persistent_notification", "create", None, title="Tesla lädt ungeplant",
                            message=msg, notification_id=NOTIFY_ID)
            await self._try("notify", NOTIFY, None, title="Tesla lädt ungeplant", message=msg)

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
        link = self._state(E_LINK) == "on"
        if self.s["active"] and plugged and link:
            await self._execute(now, target_amps, travel)
        try:
            await self._guard(now, target_amps, link)
        except Exception:  # noqa: BLE001 - the guard must never break the loop
            _LOGGER.exception("Tesla charge guard failed")
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
        if self._pre_started and self._pre_on_at is None and climate_on:
            # the car confirms the climate: only now seat and wheel, so they cannot hold up the main command
            self._pre_on_at = local
            if self._pre_manual:
                self._pre_until = local + timedelta(minutes=self._pre_mins)
            _LOGGER.info("Tesla preheat: climate on after %d tries", self._pre_tries)
            if self._pre_alerted:
                await self._try("persistent_notification", "dismiss", None, notification_id=PRE_NOTIFY_ID)
            if self._pre_outdoor is not None and self._pre_outdoor < PRE_COLD_EXTRAS:
                self._pre_extras = True
                await self._try("select", "select_option", E_SEAT, option="Medium heat")
                await self._try("switch", "turn_on", E_WHEEL)
        if self._pre_started and self._pre_on_at is None:
            # sent, but the car does not report it on (06.11.2026: the setpoint arrived, "on" got lost)
            if local >= self._pre_until or not (self.s["pre_on"] or self._pre_manual):
                _LOGGER.warning("Tesla preheat: climate did not come on after %d tries - giving up", self._pre_tries)
                self._pre_started, self.pre_status = None, "fehlgeschlagen"
                if link and not self._pre_alerted:  # no link: probably driven off already, no message while driving
                    await self._pre_alert("Das Auto hat die Vorklimatisierung bis zur Fertigzeit nicht angenommen.")
            elif link:
                self._pre_tries += 1
                self.pre_status = f"startet (Versuch {self._pre_tries})"
                await self._pre_on(target)
                if self._pre_tries >= PRE_ALERT_TRIES and not self._pre_alerted:
                    await self._pre_alert(f"Die Klima geht nach {self._pre_tries} Versuchen nicht an, "
                                          "es wird weiter versucht.")
            else:
                self.pre_status = "startet, wartet auf Verbindung"
                await self._try("button", "press", E_WAKE)
        elif self._pre_started:                    # running: learn the warm-up, end after the hold
            cabin = self._float(E_CABIN)
            mins = (local - self._pre_on_at).total_seconds() / 60
            if not self._pre_learned and cabin is not None and cabin >= target - 1 and self._pre_from is not None:
                frost = self._pre_outdoor is not None and self._pre_outdoor < 0
                await self.async_set("pre_per_k", learn_per_k(self.s["pre_per_k"], mins, self._pre_from, target, frost))
                self._pre_learned = True
                _LOGGER.info("Tesla preheat: from %.1f °C to %.0f °C in %.1f min, now %.2f min/K",
                             self._pre_from, target - 1, mins, self.s["pre_per_k"])
            if link and not climate_on:
                self._pre_started = None           # switched off by hand: leave it alone
            elif local >= self._pre_until + (PRE_NOW_HOLD if self._pre_manual else PRE_HOLD) \
                    or not (self.s["pre_on"] or self._pre_manual):
                if link:                           # still in BLE reach = not driven away: switch off
                    await self._pre_off(link)
                elif local >= self._pre_until + PRE_GIVE_UP:
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
            self._pre_done_for = self._pre_until = ready
            self._pre_manual, self._pre_mins = False, mins
            if climate_on:                         # already on by hand: nothing to do
                return
            self._pre_started, self._pre_outdoor, self._pre_from, self._pre_learned = local, outdoor, from_temp, False
            self._pre_on_at, self._pre_tries, self._pre_alerted, self._pre_extras = None, 1, False, False
            self.pre_status = "startet"
            await self._pre_on(target)   # confirmed (and repeated if needed) in the next cycles
        elif local >= start and self._pre_woken_for == ready:
            await self._try("button", "press", E_WAKE)  # no link yet: keep trying to reach the car

    def pre_estimate(self) -> tuple[float, float | None, float | None]:
        """Minutes to a warm cabin if started now, the start temperature and the outside temperature."""
        outdoor = self._outdoor()
        # the cabin value is only fresh while the car is awake; asleep it is about the outside temperature
        cabin = self._float(E_CABIN) if self._state(E_ASLEEP) == "off" else None
        from_temp = cabin if cabin is not None else outdoor
        mins = preheat_minutes(from_temp, float(self.s["pre_target"]), self.s["pre_per_k"],
                               outdoor is not None and outdoor < 0)
        return mins, from_temp, outdoor

    def at_home(self) -> bool:
        """Car at home (in BLE reach of the house), also while it sleeps."""
        state = self._state(E_HOME)
        if state == "not_home":
            return False
        if state == "home":
            return not self.departing()
        # tracker unknown (e.g. right after a restart): BLE link or the Wall Connector sees the car
        return self._state(E_LINK) == "on" or self._state(E_WC_CAR) == "on"

    def _note_departure_signs(self, eid, old, new) -> None:
        if eid == E_PRESENT and new == "on" and old != "on":
            self._entered_at = dt_util.utcnow()
        elif eid == E_WC_CAR and old == "on" and new == "off":
            self._unplugged_at = dt_util.utcnow()

    def departing(self) -> bool:
        """BLE link lost shortly after someone got in or unplugged: driving away (before the tracker sees it)."""
        if self._state(E_LINK) != "off":
            return False
        now = dt_util.utcnow()
        return any(t is not None and now - t < DEPART_WINDOW for t in (self._entered_at, self._unplugged_at))

    async def async_preheat_now(self, on: bool) -> bool:
        """Dashboard button: start a preheat now (confirmed and repeated by the minute cycle) or stop it.

        Only at home: away the car is out of BLE reach and must not be heated from here. False = refused."""
        local = dt_util.as_local(dt_util.utcnow())
        if on:
            if self._pre_started:
                return True
            if not self.at_home():
                _LOGGER.info("Tesla preheat now refused: car not at home")
                return False
            mins, from_temp, outdoor = self.pre_estimate()
            self._pre_started, self._pre_outdoor, self._pre_from, self._pre_learned = local, outdoor, from_temp, False
            self._pre_on_at, self._pre_tries, self._pre_alerted, self._pre_extras = None, 1, False, False
            self._pre_manual, self._pre_mins = True, mins
            self._pre_until = local + PRE_NOW_REACH   # until then the car must take it, then warm-up from there
            self.pre_status = "startet"
            _LOGGER.info("Tesla preheat now: about %.0f min from %s °C", mins, from_temp)
            self._changed()
            self.hass.async_create_task(self._pre_kick())
        elif self._pre_started:
            self.pre_status = "wird gestoppt"
            self._changed()
            self.hass.async_create_task(self._pre_stop())
        return True

    async def _pre_kick(self) -> None:
        if self._state(E_LINK) != "on":
            self.pre_status = "startet, wartet auf Verbindung"
            await self._try("button", "press", E_WAKE)
        else:
            await self._pre_on(float(self.s["pre_target"]))
        self._changed()

    async def _pre_stop(self) -> None:
        if self._state(E_LINK) != "on":
            await self._try("button", "press", E_WAKE)
        await self._pre_off(True)
        self.pre_status = "gestoppt"
        self._changed()

    def pre_now_view(self) -> dict:
        """For the dashboard button: running or not, how long it takes, warm at."""
        mins, from_temp, _ = self.pre_estimate()
        running = self._pre_started is not None
        warm = None
        if running:
            mins = self._pre_mins or mins
            warm = (self._pre_until if self._pre_on_at or not self._pre_manual
                    else dt_util.as_local(dt_util.utcnow()) + timedelta(minutes=mins))
        return {"laeuft": running, "zuhause": self.at_home(), "manuell": running and self._pre_manual, "bestaetigt": self._pre_on_at is not None,
                "minuten": round(mins), "von": from_temp, "ziel": float(self.s["pre_target"]),
                "innen": self._float(E_CABIN), "warm_um": warm.isoformat() if warm else None}

    async def _pre_on(self, target: float) -> None:
        await self._try("climate", "set_hvac_mode", E_CLIMATE, hvac_mode="heat_cool")
        await self._try("climate", "set_temperature", E_CLIMATE, temperature=target)

    async def _pre_alert(self, text: str) -> None:
        self._pre_alerted = True
        msg = text + " Bitte in der Tesla-App vorklimatisieren."
        await self._try("persistent_notification", "create", None, title="Tesla Vorklima",
                        message=msg, notification_id=PRE_NOTIFY_ID)
        await self._try("notify", NOTIFY, None, title="Tesla Vorklima", message=msg)

    async def _pre_off(self, link: bool) -> None:
        if link:
            await self._try("climate", "set_hvac_mode", E_CLIMATE, hvac_mode="off")
            if self._pre_extras:
                await self._try("select", "select_option", E_SEAT, option="Off")
                await self._try("switch", "turn_off", E_WHEEL)
        self._pre_started, self._pre_extras = None, False
        self.pre_status = "fertig"

    async def _try(self, domain, service, entity_id, **data) -> None:
        try:
            await self._call(domain, service, entity_id, **data)
        except Exception as err:  # noqa: BLE001 - BLE may drop (a timeout is an Exception too)
            _LOGGER.warning("Tesla command %s.%s failed: %s", domain, service, err or type(err).__name__)

    async def _execute(self, now: datetime, amps: int, travel) -> None:
        if self._last_cmd and now - self._last_cmd < CMD_GAP:
            return
        if self.hass.states.get(E_CHARGING) is None or self.hass.states.get(E_CHARGING).state == "unavailable":
            # BLE link up but no car data yet (after a restart / idle backoff): commands would go nowhere
            if self._refresh_at is None or now - self._refresh_at >= REFRESH_GAP:
                self._refresh_at = now
                await self._try("button", "press", E_REFRESH)
            return
        # BLE state can lag behind; the Wall Connector shows real power
        charging = self._state(E_CHARGING) == "on" or (not amps and self._wc_charging())
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
        payload = {"entity_id": entity_id, **data} if entity_id else data
        await asyncio.wait_for(self.hass.services.async_call(domain, service, payload, blocking=True), CMD_TIMEOUT)

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
            "ladestand_quelle": self.soc_source, "waechter": self.watch_status,
            "referenz_tage": 28, "referenz_preise": len(self.reference),
            "vorklima": {"status": self.pre_status, **self.pre_next},
            "vorheizen_jetzt": self.pre_now_view(),
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
