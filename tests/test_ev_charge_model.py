"""Charge planning: minimum first, caps by price, waiting for cheaper quarter hours, travel."""
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"
spec = importlib.util.spec_from_file_location("ev_charge_model", BASE / "ev_charge_model.py")
ev = importlib.util.module_from_spec(spec)
sys.modules["ev_charge_model"] = ev
spec.loader.exec_module(ev)

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 5, 18, 0, tzinfo=TZ)
DEP = datetime(2026, 11, 6, 6, 30, tzinfo=TZ)
REF = sorted([0.21, 0.29, 0.30, 0.33, 0.36, 0.38, 0.40, 0.42, 0.45, 0.48] * 10)  # like the last 4 weeks


def prices(f, hours=36):
    return {NOW + timedelta(minutes=15 * k): f(NOW + timedelta(minutes=15 * k)) for k in range(hours * 4)}


def test_caps():
    assert ev.price_cap(0.46, REF, 80) == 20
    assert ev.price_cap(0.42, REF, 80) == 20  # dear relative to 4 weeks as well
    assert ev.price_cap(0.32, REF, 80) == 50
    assert ev.price_cap(0.18, REF, 80) == 80  # always worth it
    # a long dear phase: 4 weeks of 0.41-0.50 make 0.41 "cheap" relatively, the absolute cap still holds
    dear = sorted([0.41 + 0.01 * k for k in range(10)] * 10)
    assert ev.price_cap(0.41, dear, 80) == 25


def test_dear_night_only_minimum():
    p = prices(lambda t: 0.41 if t.hour < 5 else 0.50)
    plan = ev.make_plan(NOW, 10.0, p, REF, DEP, ev.Settings())
    assert 20 <= plan.soc_at_departure <= 26  # 0.41 may fill to its cap of 25 %
    assert all(t.hour < 5 for t in plan.slots)  # cheapest quarter hours, not the first ones


def test_cheap_night_fills_but_waits_for_cheaper_later():
    p = prices(lambda t: 0.30 if (t.date() == NOW.date() or t.hour < 7) else (0.14 if 10 <= t.hour < 11 else 0.35))
    plan = ev.make_plan(NOW, 10.0, p, REF, DEP, ev.Settings())
    assert plan.soc_at_departure < 25  # 0.14 tomorrow at 10 h: only the minimum tonight
    p2 = prices(lambda t: 0.30 if (t.date() == NOW.date() or t.hour < 7) else 0.35)
    plan2 = ev.make_plan(NOW, 10.0, p2, REF, DEP, ev.Settings())
    assert 45 <= plan2.soc_at_departure <= 52  # 0.30 is the best that is known: fill to its cap (50 %)


def test_travel_100_last_part_late():
    s = ev.Settings(travel_at=DEP, travel_target=100.0)
    p = prices(lambda t: 0.30)
    plan = ev.make_plan(NOW, 40.0, p, REF, DEP, s)
    assert plan.soc_at_departure >= 100
    late = [t for t, (_, r) in plan.slots.items() if r.startswith("Reise (zuletzt")]
    assert late and max(late) + ev.SLOT == DEP


def test_next_departure_and_surplus():
    fri = datetime(2026, 11, 6, 7, 0, tzinfo=TZ)
    assert ev.next_departure(fri, (6, 30), (10, 0)).strftime("%a %H:%M") == "Sat 10:00"
    assert ev.next_departure(NOW, (6, 30), (10, 0)) == DEP
    assert ev.surplus_amps(98, 97, True, 600, 900, 0) == 2
    assert ev.surplus_amps(98, 97, True, 100, 900, 0) == 1
    assert ev.surplus_amps(94, 97, True, 100, 900, 1) == 1   # hysteresis keeps it running
    assert ev.surplus_amps(91, 97, True, 100, 900, 1) == 0
    assert ev.surplus_amps(99, 97, False, 600, 900, 0) == 0  # inverter off: the car would charge from the grid


def test_negative_price_allows_90():
    assert ev.price_cap(-0.02, REF, 80) == 90
    assert ev.price_cap(0.05, REF, 80) == 80  # cheap but not negative: daily maximum
    p = prices(lambda t: -0.03 if 1 <= t.hour < 4 and t.date() != NOW.date() else 0.40)
    plan = ev.make_plan(NOW, 60.0, p, REF, DEP, ev.Settings())
    assert 85 <= plan.soc_at_departure <= 92


def test_preheat_need_duration_learning():
    assert ev.preheat_needed(4.0, 10.0) and not ev.preheat_needed(12.0, 10.0) and ev.preheat_needed(None, 10.0)
    assert ev.preheat_minutes(8.0, 21.0) == 11.5
    assert ev.preheat_minutes(-5.0, 21.0) == 23.0  # incl. ice
    assert ev.preheat_minutes(-30.0, 21.0) == 30.0
    k = ev.learn_per_k(0.5, 3.0, 8.0, 21.0)  # faster than expected -> fewer minutes per K
    assert k < 0.5
    assert ev.learn_per_k(0.5, 10.0, 20.0, 21.0) == 0.5  # too small a difference to learn
