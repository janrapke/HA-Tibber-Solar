"""Tesla preheating with a fake Home Assistant: the car must confirm "climate on", else retry and alert.

Case of 06.11.2026: the setpoint 21 °C reached the car, "climate on" got lost over BLE and the
seat/wheel commands hung for 8 minutes - the climate never ran.
"""
import asyncio
import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"
TZ = timezone(timedelta(hours=1))
PKG = "sbo_ev_test"


def _stub_ha():
    import test_climate_control  # noqa: F401 - installs the shared fake homeassistant modules (core, event, storage)
    mods = {n: sys.modules.get(n) or types.ModuleType(n) for n in (
        "homeassistant", "homeassistant.helpers.event", "homeassistant.util", "homeassistant.util.dt")}
    ev = mods["homeassistant.helpers.event"]
    if not hasattr(ev, "async_track_time_interval"):
        ev.async_track_time_interval = lambda *a: (lambda: None)
    dt = mods["homeassistant.util.dt"]
    dt.as_local = lambda t: t.astimezone(TZ)
    dt.as_utc = lambda t: t.astimezone(timezone.utc)
    dt.utcnow = lambda: datetime.now(timezone.utc)
    dt.parse_datetime = datetime.fromisoformat
    dt.DEFAULT_TIME_ZONE = TZ
    mods["homeassistant.util"].dt = dt
    for n, m in mods.items():
        sys.modules.setdefault(n, m)
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(BASE)]
    sys.modules[PKG] = pkg
    spec = importlib.util.spec_from_file_location(f"{PKG}.ev_control", BASE / "ev_control.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


evc = _stub_ha()


class State:
    def __init__(self, state):
        self.state = state


class Hass:
    def __init__(self):
        self.st = {evc.E_CLIMATE: "off", evc.E_LINK: "on", evc.E_ASLEEP: "on",
                   evc.OUTDOOR_FALLBACK: "1.75", evc.E_CABIN: "3.8"}
        self.calls = []
        self.lose_on = 0        # this many "climate on" commands get lost
        self.hang = set()       # services that never return
        self.states = types.SimpleNamespace(get=lambda e: State(self.st[e]) if e in self.st else None)
        self.services = types.SimpleNamespace(async_call=self._call)

    async def _call(self, domain, service, data, blocking=False):
        self.calls.append((domain, service, data))
        if f"{domain}.{service}" in self.hang:
            await asyncio.sleep(3600)
        if (domain, service) == ("climate", "set_hvac_mode") and data["hvac_mode"] == "heat_cool":
            if self.lose_on:
                self.lose_on -= 1
            else:
                self.st[evc.E_CLIMATE] = "heat_cool"


def _ctrl(hass):
    c = evc.EvChargeController(hass, "e", {}, coordinator=None)
    return c


def _run(c, start, end):
    """Minute cycle from start to end (local times); returns the status per minute."""
    out = {}
    t = start
    while t <= end:
        asyncio.run(c._preheat(t))
        out[t.strftime("%H:%M")] = c.pre_status
        t += timedelta(minutes=1)
    return out


def _count(hass, domain, service, **match):
    return sum(1 for d, s, data in hass.calls
               if (d, s) == (domain, service) and all(data.get(k) == v for k, v in match.items()))


DAY = datetime(2026, 11, 6, 6, 0, tzinfo=TZ)   # Friday, ready 06:40


def test_lost_on_command_is_repeated_and_extras_follow_confirmation():
    hass = Hass()
    hass.lose_on = 1
    c = _ctrl(hass)
    st = _run(c, DAY, DAY.replace(minute=45))
    assert _count(hass, "climate", "set_hvac_mode", hvac_mode="heat_cool") == 2
    assert c._pre_on_at is not None and "läuft" in st.values()
    # seat/wheel only after the car reported the climate on (1.75 °C outside < 3 °C)
    first_seat = next(i for i, x in enumerate(hass.calls) if x[0] == "select")
    on_calls = [i for i, x in enumerate(hass.calls) if x[1] == "set_hvac_mode"]
    assert first_seat > on_calls[1]
    assert _count(hass, "notify", evc.NOTIFY) == 0


def test_never_on_alerts_after_three_tries_and_gives_up_at_ready_time():
    hass = Hass()
    hass.lose_on = 99
    c = _ctrl(hass)
    st = _run(c, DAY, DAY.replace(minute=45))
    assert st["06:41"] == "fehlgeschlagen"
    assert _count(hass, "notify", evc.NOTIFY) == 1          # once, not every minute
    assert _count(hass, "select", "select_option") == 0     # nothing else while the climate is off
    assert c._pre_started is None


def test_hanging_command_does_not_block_the_loop():
    hass = Hass()
    hass.hang = {"select.select_option"}
    evc.CMD_TIMEOUT = 0.01
    try:
        c = _ctrl(hass)
        st = _run(c, DAY, DAY.replace(minute=45))
    finally:
        evc.CMD_TIMEOUT = 45
    assert "läuft" in st.values()
    assert _count(hass, "switch", "turn_on") == 1            # wheel still switched after the hung seat
    assert st["06:45"] == "läuft"                            # within the 10 min hold


def test_manual_off_after_confirmation_is_left_alone():
    hass = Hass()
    c = _ctrl(hass)
    _run(c, DAY, DAY.replace(minute=35))
    assert c._pre_on_at is not None
    hass.st[evc.E_CLIMATE] = "off"
    _run(c, DAY.replace(minute=36), DAY.replace(minute=45))
    assert c._pre_started is None
    assert _count(hass, "climate", "set_hvac_mode", hvac_mode="heat_cool") == 1


def _now(c, local):
    """Press "Jetzt vorheizen" at local time and run the background command."""
    evc.dt_util.utcnow = lambda: local.astimezone(timezone.utc)
    tasks = []
    c.hass.async_create_task = lambda coro: tasks.append(coro)
    c._changed = lambda: None
    asyncio.run(c.async_preheat_now(True))
    for t in tasks:
        asyncio.run(t)


def test_preheat_now_starts_shows_duration_and_switches_off_after_hold():
    hass = Hass()
    c = _ctrl(hass)
    t0 = datetime(2026, 11, 7, 14, 0, tzinfo=TZ)   # Saturday, no schedule
    _now(c, t0)
    assert _count(hass, "climate", "set_hvac_mode", hvac_mode="heat_cool") == 1
    view = c.pre_now_view()
    assert view["laeuft"] and view["manuell"] and view["minuten"] >= 3
    st = _run(c, t0.replace(minute=1), t0.replace(minute=45))
    assert st["14:05"] == "läuft"
    warm = c._pre_until
    assert warm == t0.replace(minute=1) + timedelta(minutes=c._pre_mins)   # from the confirmation on
    assert c._pre_started is None and st["14:45"] == "geplant"   # back to the next scheduled day
    assert _count(hass, "climate", "set_hvac_mode", hvac_mode="off") == 1


def test_preheat_now_runs_even_with_the_schedule_switched_off():
    hass = Hass()
    c = _ctrl(hass)
    c.s["pre_on"] = False
    t0 = datetime(2026, 11, 7, 14, 0, tzinfo=TZ)
    _now(c, t0)
    st = _run(c, t0.replace(minute=1), t0.replace(minute=5))
    assert st["14:05"] == "läuft"


def test_preheat_now_refused_when_car_is_away():
    hass = Hass()
    hass.st[evc.E_HOME] = "not_home"
    c = _ctrl(hass)
    t0 = datetime(2026, 11, 7, 14, 0, tzinfo=TZ)
    _now(c, t0)
    assert c._pre_started is None and hass.calls == []
    assert c.pre_now_view()["zuhause"] is False


def test_asleep_at_home_counts_as_home():
    hass = Hass()
    hass.st.update({evc.E_HOME: "home", evc.E_LINK: "off", evc.E_ASLEEP: "on"})
    c = _ctrl(hass)
    assert c.at_home()
    hass.st[evc.E_HOME] = "unavailable"       # tracker unknown after a restart: link / Wall Connector decide
    assert not c.at_home()
    hass.st[evc.E_WC_CAR] = "on"
    assert c.at_home()


def test_driving_away_is_seen_before_the_tracker():
    """Fr 06.11.: unplugged 06:47:23, driver in 06:47:24, BLE link off 06:47:52, tracker "not_home" only 06:51:53."""
    hass = Hass()
    hass.st.update({evc.E_HOME: "home", evc.E_LINK: "on"})
    c = _ctrl(hass)
    at = lambda h, m, s=0: setattr(evc.dt_util, "utcnow",
                                   lambda: datetime(2026, 11, 6, h, m, s, tzinfo=TZ).astimezone(timezone.utc))
    at(6, 47, 23)
    c._note_departure_signs(evc.E_WC_CAR, "on", "off")
    assert c.at_home()                         # unplugged, but still connected: may stay
    at(6, 48)
    hass.st[evc.E_LINK] = "off"
    assert not c.at_home()                     # unplugged and out of BLE reach: driving away
    at(6, 58)
    hass.st[evc.E_LINK] = "on"                 # (would be "not_home" by then; here: link back = home)
    assert c.at_home()


def test_reconnect_blips_at_home_are_not_a_departure():
    """ESP reconnect: link off for 30 s and user_present unavailable -> off; nobody got in."""
    hass = Hass()
    hass.st.update({evc.E_HOME: "home", evc.E_LINK: "off"})
    c = _ctrl(hass)
    c._note_departure_signs(evc.E_PRESENT, "unavailable", "off")
    assert c.at_home()
    c._note_departure_signs(evc.E_PRESENT, "off", "on")   # someone got in, then the link is lost
    assert not c.at_home()
