"""Cost comparison: without system / solar only / real."""
import importlib.util
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"
spec = importlib.util.spec_from_file_location("cost_model", BASE / "cost_model.py")
cm = importlib.util.module_from_spec(spec)
sys.modules["cost_model"] = cm
spec.loader.exec_module(cm)


def test_evening_battery_covers_house():
    # 1 h, house uses 500 W: 100 W from grid, 400 W from the battery, no sun
    a, b, c, house, solar, imp = cm.interval(0.50, 1.0, 100, 0, 400, 0, 0, 0)
    assert round(house, 3) == 0.5 and solar == 0
    assert round(a, 3) == 0.25 and round(b, 3) == 0.25 and round(c, 3) == 0.05  # battery saved 0.20


def test_noon_sun_goes_into_battery_solar_only_would_feed_directly():
    # 1 h, house 300 W all from grid, 1000 W panel power charges the battery
    a, b, c, *_ = cm.interval(0.40, 1.0, 300, 0, 0, 0, 0, 1000)
    assert round(a, 3) == 0.12 and b == 0 and round(c, 3) == 0.12  # here the battery "costs" vs direct feed


def test_balcony_and_grid_charger():
    # balcony 200 W covers part of the house, grid charger draws 600 W into the battery
    a, b, c, house, solar, imp = cm.interval(0.30, 1.0, 900, 0, 0, 200, 600, 0)
    assert round(house, 3) == 0.5 and round(solar, 3) == 0.2
    assert round(a, 3) == 0.15 and round(b, 3) == 0.09 and round(c, 3) == 0.27


def test_export_not_paid():
    a, b, c, house, *_ = cm.interval(0.30, 1.0, 0, 200, 500, 0, 0, 0)
    assert round(house, 3) == 0.3 and c == 0


def test_summary_periods_and_months():
    days = {"2026-10-30": [10, 8, 7, 20, 5, 15], "2026-11-03": [5, 4, 3, 10, 2, 6], "2026-11-04": [1, 1, 0.5, 2, 0, 1]}
    s = cm.summarize(days, date(2026, 11, 4))
    assert s["heute"]["gesamt_spart"] == 0.5 and s["heute"]["tage"] == 1
    assert s["7_tage"]["ohne_system"] == 16 and s["monat"]["ohne_system"] == 6
    assert s["gesamt"]["solar_spart"] == 3 and s["gesamt"]["akku_spart"] == 2.5
    assert list(s["monate"]) == ["2026-10", "2026-11"] and s["seit"] == "2026-10-30"
    assert s["monate"]["2026-11"]["ohne_system"] == 6 and s["monate"]["2026-11"]["tage"] == 2
    assert s["jahre"]["2026"]["gesamt_spart"] == 5.5
