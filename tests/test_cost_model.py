"""Cost comparison: without system / balcony only / real (battery + panels)."""
import importlib.util
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"
spec = importlib.util.spec_from_file_location("cost_model", BASE / "cost_model.py")
cm = importlib.util.module_from_spec(spec)
sys.modules["cost_model"] = cm
spec.loader.exec_module(cm)


def r(v):
    return round(v, 3)


def test_evening_battery_covers_house():
    # 1 h, house uses 500 W: 100 W from grid, 400 W from the battery, no sun
    a, b, c, house, solar, imp, unused, panel, shift = cm.interval(0.50, 1.0, 100, 0, 400, 0, 0, 0)
    assert r(house) == 0.5 and solar == 0 and unused == 0
    assert r(a) == 0.25 and r(b) == 0.25 and r(c) == 0.05  # battery saved 0.20
    assert shift == 0  # without a pool nothing is known about the origin


def test_noon_balcony_covers_house_panels_are_surplus():
    # house 300 W, balcony 400 W covers it, 100 W exported; panels push 1000 W into the battery
    a, b, c, house, solar, imp, unused, panel, shift = cm.interval(0.40, 1.0, 0, 100, 0, 400, 0, 1000)
    assert r(house) == 0.3 and r(solar) == 1.4 and r(panel) == 1.0
    assert r(unused) == 1.1   # all panel energy plus the balcony surplus would be lost without the battery
    assert a > 0 and b == 0 and c == 0


def test_house_could_have_used_part_of_the_panel_power():
    # 14:00: balcony 150 W, panels 800 W into the battery, house 400 W -> 250 W from the grid
    a, b, c, house, solar, imp, unused, panel, shift = cm.interval(0.40, 1.0, 250, 0, 0, 150, 0, 800)
    assert r(house) == 0.4
    assert b == 0                                  # without battery the panels would have covered the house
    assert r(c) == 0.1                             # real: the 250 W came from the grid
    assert r(unused) == r(0.8 - 0.25 / 0.93)       # only the surplus (~530 W DC) counts for the battery


def test_balcony_and_grid_charger():
    # balcony 200 W covers part of the house, grid charger draws 600 W into the battery
    a, b, c, house, solar, imp, unused, panel, shift = cm.interval(0.30, 1.0, 900, 0, 0, 200, 600, 0)
    assert r(house) == 0.5 and r(solar) == 0.2 and unused == 0
    assert r(a) == 0.15 and r(b) == 0.09 and r(c) == 0.27
    assert shift == 0   # charging alone shifts nothing yet


def test_export_not_paid():
    a, b, c, house, *_ = cm.interval(0.30, 1.0, 0, 200, 500, 0, 0, 0)
    assert r(house) == 0.3 and c == 0


def test_price_shift_compares_use_with_charge_time_price():
    pool = cm.new_pool()
    # night: 1 kWh from the grid at 0.20 (0.9 kWh DC, cost 0.20), noon: 0.9 kWh from the panels at 0.40
    cm.interval(0.20, 1.0, 1000, 0, 0, 0, 1000, 0, pool, 10)
    cm.interval(0.40, 1.0, 0, 0, 0, 0, 0, 900, pool, 10)
    assert r(pool["kwh"]) == 1.8 and r(pool["eur"]) == 0.56
    # evening at 0.60: battery gives 465 W AC for 1 h (0.5 kWh DC); mean charge price 0.56/1.8 per DC kWh
    *_, shift = cm.interval(0.60, 1.0, 0, 0, 465, 0, 0, 0, pool, 10)
    assert r(shift) == r(0.465 * (0.60 - 0.56 / 1.8))
    assert r(pool["kwh"]) == 1.3 and r(pool["eur"]) == r(0.56 - 0.5 * 0.56 / 1.8)


def test_solar_shift_without_grid_charging():
    pool = cm.new_pool()
    cm.interval(0.30, 1.0, 0, 0, 0, 0, 0, 1000, pool, 10)      # 1 kWh of sun at 0.30
    *_, shift = cm.interval(0.50, 1.0, 0, 0, 930, 0, 0, 0, pool, 10)
    assert r(shift) == r(0.93 * 0.20) and r(pool["kwh"]) == 0


def test_pool_capped_at_capacity():
    pool = cm.new_pool()
    cm.interval(0.30, 10.0, 0, 0, 0, 0, 0, 1000, pool, 5)
    assert r(pool["kwh"]) == 5.0 and r(pool["eur"]) == 1.5


def test_summary_periods_and_months():
    days = {"2026-10-30": [10, 8, 7, 20, 5, 15, 3, 4, 0.5], "2026-11-03": [5, 4, 3, 10, 2, 6, 1, 1, 0.2],
            "2026-11-04": [1, 1, 0.5, 2, 0, 1, 0, 0, 0.1]}
    s = cm.summarize(days, date(2026, 11, 4))
    assert s["heute"]["gesamt_spart"] == 0.5 and s["heute"]["tage"] == 1
    assert s["7_tage"]["ohne_system"] == 16 and s["monat"]["ohne_system"] == 6
    g = s["gesamt"]
    assert g["solar_direkt_spart"] == 3 and g["akku_spart"] == 2.5
    assert g["akku_preis_spart"] == 0.8 and g["akku_solar_spart"] == 1.7
    assert g["ungenutzt_ohne_akku_kwh"] == 4 and g["panel_kwh"] == 5
    assert list(s["monate"]) == ["2026-10", "2026-11"] and s["seit"] == "2026-10-30"
    assert s["monate"]["2026-11"]["ohne_system"] == 6 and s["monate"]["2026-11"]["tage"] == 2
    assert s["jahre"]["2026"]["gesamt_spart"] == 5.5
