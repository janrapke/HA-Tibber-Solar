"""Tests for the thermal model of the living room heat pump."""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"
_spec = importlib.util.spec_from_file_location("thermal_model", BASE / "thermal_model.py")
tm = importlib.util.module_from_spec(_spec)
sys.modules["thermal_model"] = tm
_spec.loader.exec_module(tm)

T0 = datetime(2026, 10, 27, 0, 0, tzinfo=timezone.utc)
Q = timedelta(minutes=15)


def sample(i, room, outdoor=10.0, device=None, hvac="off", power=0.0, hz=0.0, rad=False, run=0.0):
    return tm.Sample(T0 + i * Q, room, outdoor, device, hvac, power, hz, rad, "silent", run)


def test_efficiency_curve_interpolates_and_holds():
    assert tm.interpolate(tm.EFFICIENCY_CURVE, 10) == 4.3
    assert abs(tm.interpolate(tm.EFFICIENCY_CURVE, 11) - 4.45) < 1e-9
    assert tm.interpolate(tm.EFFICIENCY_CURVE, 30) == 5.1
    assert tm.interpolate(tm.EFFICIENCY_CURVE, -30) == 1.7
    assert tm.ThermalModel().efficiency(11) == tm.interpolate(tm.EFFICIENCY_CURVE, 11)


def test_break_even_price():
    oil = tm.oil_cost_per_kwh_heat(1.00, 0.85)
    assert abs(oil - 0.1176) < 1e-3
    # at 10 °C: 0.1176 * 4.3 * 0.9 = 0.455 EUR/kWh
    assert abs(tm.break_even_price(oil, 4.3) - 0.455) < 1e-3


def test_offset_learned_only_after_20_min_run():
    m = tm.ThermalModel()
    m.add_sample(sample(0, 21.0, device=27.0, hvac="heat", power=800, hz=30, run=10))
    assert m.offset_n["heat"] == 0
    for i in range(1, 30):
        m.add_sample(sample(i, 21.0, device=28.0, hvac="heat", power=800, hz=30, run=30))
    assert abs(m.device_offset("heat") - 7.0) < 0.1
    m.add_sample(sample(31, 21.0, device=40.0, hvac="heat", power=800, hz=30, run=30))  # outlier
    assert abs(m.device_offset("heat") - 7.0) < 0.1
    assert m.device_offset("cool") == tm.PRIOR_OFFSET_COOL


def test_cooling_rate_from_idle_phase():
    m = tm.ThermalModel()
    # 10 K difference, k = 0.05 -> 0.5 K/h, exponential approach
    room = 20.0
    for i in range(40):
        m.add_sample(sample(i, room, outdoor=10.0))
        room -= 0.05 * (room - 10.0) * 0.25
    assert m.k_n >= 4
    assert abs(m.k - 0.05) < 0.005


def test_no_cooling_rate_while_radiators_heat():
    m = tm.ThermalModel()
    for i in range(20):
        m.add_sample(sample(i, 20.0 - 0.1 * i, rad=True))
    assert m.k_n == 0 and m.k == tm.PRIOR_K


def test_efficiency_learned_slowly_and_bounded():
    m = tm.ThermalModel()
    m.k = 0.04
    # 10 °C outside, 0.8 kW electric, true efficiency 5.0, C = 1 kWh/K
    room, i = 18.0, 0
    for _ in range(400):
        m.add_sample(sample(i, room, outdoor=10.0, hvac="heat", power=800, hz=30, run=60))
        room += (5.0 * 0.8 - 0.04 * (room - 10.0)) * 0.25
        i += 1
        if room > 26:
            room = 18.0
            i += 2  # gap breaks the phase
    v = m.eff["10"][0]
    assert 4.8 < v < 5.1
    assert abs(m.efficiency(10.5) - 4.95) < 0.2


def test_efficiency_rejects_heating_with_radiators():
    m = tm.ThermalModel()
    for i in range(20):
        m.add_sample(sample(i, 21.0 + 0.5 * i, hvac="heat", power=800, hz=30, rad=True))
    assert m.eff == {}


def test_power_per_hz():
    m = tm.ThermalModel()
    assert m.power_at_hz(30) == 30 * tm.PRIOR_W_PER_HZ
    for i in range(10):
        m.add_sample(sample(2 * i, 21.0, hvac="heat", power=810, hz=30))
        m.add_sample(sample(2 * i + 1, 21.0, hvac="heat", power=1600, hz=56))
    assert abs(m.power_at_hz(30) - 810) < 1
    assert abs(m.power_at_hz(55) - 1600) < 1
    assert 810 < m.power_at_hz(40) < 1600
    assert m.power_at_hz(0) == 0.0


def test_roundtrip_and_raw_window():
    m = tm.ThermalModel()
    for i in range(0, 35 * 96, 8):
        m.add_sample(sample(i, 21.0, device=27.0, hvac="heat", power=800, hz=30, run=30))
    assert m.samples[-1].ts - m.samples[0].ts <= timedelta(days=30)
    m2 = tm.ThermalModel()
    m2.load_dict(m.to_dict())
    assert m2.to_dict() == m.to_dict()
    m2.reset()
    assert m2.offset_n["heat"] == 0 and len(m2.samples) == len(m.samples)
    assert "versatz_heizen_k" in m.summary()
