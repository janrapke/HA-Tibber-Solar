"""Tests for the pure-Python core (no Home Assistant needed): pytest tests/"""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, BASE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


planner = _load("planner")
solar_model = _load("solar_model")
load_model = _load("load_model")

T0 = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
P = planner.BatteryParams(capacity_wh=5000, min_pct=10, efficiency=0.9, inverter_max_w=800, steps=50)


def slots(prices, solar=0.0, load=400.0):
    return [planner.Slot(T0 + timedelta(minutes=15 * i), pr, solar, load) for i, pr in enumerate(prices)]


def test_flat_prices_discharge_by_default():
    plan = planner.optimize(slots([0.30] * 16), 60, P)
    assert plan.action_now() == planner.ACT_DISCHARGE


def test_holds_for_expensive_evening():
    # battery covers only part of the horizon: keep it for the expensive block
    prices = [0.25] * 24 + [0.50] * 24
    plan = planner.optimize(slots(prices, load=800), 40, P)
    assert plan.action_now() == planner.ACT_HOLD
    assert any(s.action == planner.ACT_DISCHARGE for s in plan.steps[24:])
    assert plan.cost_eur < plan.baseline_cost_eur


def test_never_below_reserve():
    plan = planner.optimize(slots([0.4] * 96, load=1000), 50, P)
    assert min(s.soc_end_pct for s in plan.steps) >= 10 - 0.01


def test_grid_charge_only_when_profitable():
    pc = planner.BatteryParams(**{**P.__dict__, "grid_charge_w": 1000, "grid_charge_eff": 0.85})
    small_spread = [0.35] * 24 + [0.40] * 24
    plan = planner.optimize(slots(small_spread), 10, pc, allow_grid_charge=True)
    assert not any(s.action == planner.ACT_GRID_CHARGE for s in plan.steps)
    big_spread = [0.15] * 24 + [0.50] * 24
    plan = planner.optimize(slots(big_spread), 10, pc, allow_grid_charge=True)
    assert any(s.action == planner.ACT_GRID_CHARGE for s in plan.steps[:24])


def test_solar_model_learns_and_caps():
    m = solar_model.SolarModel(52.65, 9.22, prior_k=1.0)
    t = datetime(2026, 6, 21, 10, 0, tzinfo=timezone.utc)
    for _ in range(200):
        m.learn(t, 800, 800, 100, 1000.0, limited=False)
    m.learn(t, 900, 900, 100, 1300.0, limited=False)  # sets the cap
    p = m.predict(t, 800, 800, 100)
    assert 800 < p <= 1300
    assert m.predict(datetime(2026, 6, 21, 23, 0, tzinfo=timezone.utc), 0, 0, 0) == 0.0


def test_solar_model_roundtrip():
    m = solar_model.SolarModel(52.65, 9.22)
    m.learn(datetime(2026, 6, 21, 10, 0, tzinfo=timezone.utc), 500, 400, 100, 600.0, limited=False)
    m2 = solar_model.SolarModel(52.65, 9.22)
    m2.load_dict(m.to_dict())
    assert m2.to_dict() == m.to_dict()


def test_load_model_trims_outliers():
    lm = load_model.LoadModel()
    d0 = datetime(2026, 10, 1).date()
    for i in range(10):
        lm.record(d0 + timedelta(days=i), 40, 11000.0 if i == 3 else 400.0)  # one EV charge
    assert abs(lm.profile_value(40) - 400.0) < 1
