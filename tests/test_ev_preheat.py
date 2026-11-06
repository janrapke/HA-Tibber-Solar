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
