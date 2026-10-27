"""Tests for the climate decision logic, one block per priority."""
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, BASE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("thermal_model")
cl = _load("climate_logic")

NOON = datetime(2026, 11, 3, 12, 0)
M = timedelta(minutes=1)


def inp(**kw):
    base = dict(now=NOON, room=20.0, outdoor=8.0, price=0.30, home=True, efficiency=4.0, soc=50.0)
    base.update(kw)
    return cl.Inputs(**base)


# --- 1. safety -------------------------------------------------------------
def test_app_disabled_hands_back_radiators_and_leaves_ac():
    st = cl.State(replace_active=True)
    d = cl.decide(inp(app_enabled=False), st)
    assert d.mode == "keine_steuerung" and d.climate_on is None and d.radiators
    assert not st.replace_active


def test_invalid_sensor_ends_replace_and_turns_ac_off():
    st = cl.State()
    d = cl.decide(inp(replace_switch=True, price=0.20), st)
    assert d.mode == "ersatz" and not d.radiators
    d = cl.decide(inp(now=NOON + 10 * M, room=None, replace_switch=True, price=0.20), st)
    assert d.mode == "sicherheit" and d.radiators and d.climate_on is False


def test_cold_room_gives_radiators_back_even_while_replacing():
    st = cl.State()
    cl.decide(inp(replace_switch=True, price=0.20), st)
    d = cl.decide(inp(now=NOON + 10 * M, room=16.5, replace_switch=True, price=0.20), st)
    assert d.radiators and "Heizkörper" in d.reason


# --- 2. manual -------------------------------------------------------------
def test_manual_off_keeps_ac_off():
    st = cl.State(climate_on=True, hvac="heat", on_since=NOON - 5 * M)
    d = cl.decide(inp(mode=cl.MODE_MANUAL_OFF, room=15.0), st)
    assert d.climate_on is False and d.send_hvac and d.radiators


def test_manual_on_heats_or_cools_by_outdoor():
    d = cl.decide(inp(mode=cl.MODE_MANUAL_ON, outdoor=5.0), cl.State())
    assert d.climate_on and d.hvac == "heat" and d.send_hvac
    d = cl.decide(inp(mode=cl.MODE_MANUAL_ON, outdoor=25.0, room=26.0), cl.State())
    assert d.hvac == "cool"


# --- 3. replace heating ----------------------------------------------------
def test_replace_by_cost():
    # 1.00 EUR/L, 85 %, efficiency 4.0 -> limit 0.1176 * 4 * 0.9 = 0.424
    d = cl.decide(inp(replace_switch=True, price=0.40), cl.State())
    assert d.mode == "ersatz" and d.hvac == "heat" and not d.radiators
    assert abs(d.break_even - 0.4235) < 1e-3
    d = cl.decide(inp(replace_switch=True, price=0.45), cl.State())
    assert d.mode != "ersatz" and d.radiators and "Öl günstiger" in d.reason
    d = cl.decide(inp(replace_switch=True, price=0.20, outdoor=17.0), cl.State())
    assert d.mode != "ersatz"


def test_replace_hysteresis_two_hours():
    st = cl.State()
    cl.decide(inp(replace_switch=True, price=0.20), st)
    d = cl.decide(inp(now=NOON + 60 * M, replace_switch=True, price=0.50), st)
    assert d.mode == "ersatz" and "Mindestdauer" in d.reason
    d = cl.decide(inp(now=NOON + 121 * M, replace_switch=True, price=0.50), st)
    assert d.mode != "ersatz" and d.radiators
    d = cl.decide(inp(now=NOON + 150 * M, replace_switch=True, price=0.20), st)
    assert d.mode != "ersatz"  # not before 2 h after the last change
    d = cl.decide(inp(now=NOON + 242 * M, replace_switch=True, price=0.20), st)
    assert d.mode == "ersatz"


def test_replace_switch_off_ends_immediately():
    st = cl.State()
    cl.decide(inp(replace_switch=True, price=0.20), st)
    d = cl.decide(inp(now=NOON + 10 * M, replace_switch=False, price=0.20), st)
    assert d.radiators and d.mode != "ersatz"


def test_replace_runs_at_night_and_away():
    st = cl.State()
    d = cl.decide(inp(now=datetime(2026, 11, 3, 3, 0), home=False, replace_switch=True, price=0.20), st)
    assert d.mode == "ersatz" and d.target_room == 18.0


# --- 4. surplus ------------------------------------------------------------
def test_surplus_by_curtailment_or_soc_with_hysteresis():
    st = cl.State()
    d = cl.decide(inp(surplus_switch=True, curtailed_wh_2h=500), st)
    assert d.mode == "ueberschuss" and d.hvac == "heat" and d.fan == cl.FAN_BOOST
    assert d.target_room == 21.0  # home 20 + 1
    st = cl.State()
    d = cl.decide(inp(surplus_switch=True, soc=91), st)
    assert d.mode == "ueberschuss"
    d = cl.decide(inp(now=NOON + 40 * M, surplus_switch=True, soc=87), st)
    assert d.mode == "ueberschuss"
    d = cl.decide(inp(now=NOON + 80 * M, surplus_switch=True, soc=84), st)
    assert d.mode != "ueberschuss"


def test_no_surplus_while_grid_charging():
    d = cl.decide(inp(surplus_switch=True, soc=95, grid_charging=True), cl.State())
    assert d.mode != "ueberschuss"


def test_surplus_cools_when_warm_outside():
    d = cl.decide(inp(surplus_switch=True, soc=95, outdoor=25.0, room=24.0), cl.State())
    assert d.hvac == "cool" and d.target_room == 23.0


def test_surplus_adds_one_kelvin_to_replace():
    d = cl.decide(inp(surplus_switch=True, soc=95, replace_switch=True, price=0.20), cl.State())
    assert d.mode == "ersatz" and d.target_room == 21.0 and "+1 K" in d.reason


# --- 5. automatic ----------------------------------------------------------
def test_auto_cooling_hysteresis_and_min_runtime():
    st = cl.State()
    d = cl.decide(inp(room=25.0, outdoor=28.0, price=0.25), st)
    assert d.mode == "kuehlen" and d.hvac == "cool"
    d = cl.decide(inp(now=NOON + 10 * M, room=23.0, outdoor=28.0), st)
    assert d.climate_on and "Mindestlaufzeit" in d.reason
    d = cl.decide(inp(now=NOON + 31 * M, room=23.0, outdoor=28.0), st)
    assert d.climate_on is False and d.send_hvac
    d = cl.decide(inp(now=NOON + 40 * M, room=25.0, outdoor=28.0, price=0.25), st)
    assert d.climate_on is False and "Sperre" in d.reason
    d = cl.decide(inp(now=NOON + 95 * M, room=25.0, outdoor=28.0, price=0.25), st)
    assert d.climate_on


def test_auto_cooling_needs_cheap_price_home_and_daytime():
    assert cl.decide(inp(room=25.0, price=0.35), cl.State()).climate_on is False
    assert cl.decide(inp(room=25.0, home=False), cl.State()).climate_on is False
    assert cl.decide(inp(room=25.0, now=datetime(2026, 7, 1, 23, 0)), cl.State()).climate_on is False


def test_auto_heating_only_when_cheaper_than_oil():
    d = cl.decide(inp(room=18.5, price=0.30), cl.State())
    assert d.mode == "heizen" and d.radiators
    d = cl.decide(inp(room=18.5, price=0.50), cl.State())
    assert d.climate_on is False


def test_22h_switches_off_without_min_runtime():
    st = cl.State()
    cl.decide(inp(room=18.5, now=datetime(2026, 11, 3, 21, 55)), st)
    d = cl.decide(inp(room=18.6, now=datetime(2026, 11, 3, 22, 0)), st)
    assert d.climate_on is False and d.send_hvac


# --- regulation ------------------------------------------------------------
def test_setpoint_uses_offset_integral_and_rate_limit():
    st = cl.State()
    i = inp(replace_switch=True, price=0.20, room=20.0, in_living=True, offset_heat=6.0)
    d = cl.decide(i, st)
    assert d.setpoint == 28.0 and d.send_setpoint and d.fan == cl.FAN_NORMAL  # 21 + 6 + P 1.0
    t = NOON
    for _ in range(12):  # 3 h at 1 K below target -> integral saturates at +2
        t += 15 * M
        d = cl.decide(inp(now=t, replace_switch=True, price=0.20, room=20.0, in_living=True, offset_heat=6.0), st)
    assert d.setpoint == 30.0  # 21 + 6 + min(3, 1 + 2)
    d2 = cl.decide(inp(now=t + 2 * M, replace_switch=True, price=0.20, room=23.0, in_living=True, offset_heat=6.0), st)
    assert d2.send_setpoint and d2.setpoint == 27.0  # P reacts at once: 21 + 6 - 2 + I 2
    d3 = cl.decide(inp(now=t + 4 * M, replace_switch=True, price=0.20, room=20.0, in_living=True, offset_heat=6.0), st)
    assert not d3.send_setpoint and d3.setpoint == 27.0  # max. one command per 5 min
    for _ in range(20):
        t += 15 * M
        d = cl.decide(inp(now=t, replace_switch=True, price=0.20, room=10.0, in_living=False, offset_heat=6.0), st)
    assert d.radiators  # < 17 °C: safety
    st = cl.State()
    d = cl.decide(inp(replace_switch=True, price=0.20, room=21.0, offset_heat=12.0), st)
    assert d.setpoint == 30.0


def test_fan_boost_when_cold_and_change_limited():
    st = cl.State()
    d = cl.decide(inp(replace_switch=True, price=0.20, room=18.0, in_living=True), st)
    assert d.fan == cl.FAN_BOOST and d.send_fan
    d = cl.decide(inp(now=NOON + 5 * M, replace_switch=True, price=0.20, room=20.5, in_living=True), st)
    assert d.fan == cl.FAN_BOOST and not d.send_fan
    d = cl.decide(inp(now=NOON + 16 * M, replace_switch=True, price=0.20, room=20.5, in_living=True), st)
    assert d.fan == cl.FAN_NORMAL and d.send_fan


def test_state_roundtrip():
    st = cl.State()
    cl.decide(inp(replace_switch=True, price=0.20), st)
    st2 = cl.State.from_dict(st.to_dict())
    assert st2 == st
