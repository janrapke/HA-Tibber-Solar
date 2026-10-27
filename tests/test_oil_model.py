"""Oil heating model: burner detection on a recorded run, tank curve, fit and reach."""
import csv
import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"
spec = importlib.util.spec_from_file_location("oil_model", BASE / "oil_model.py")
om = importlib.util.module_from_spec(spec)
sys.modules["oil_model"] = om
spec.loader.exec_module(om)

LOC = timezone(timedelta(hours=1))


def recorded():
    """27.10.2026, Schornsteinfeger-Taste: flame ~23:34:44, user heard the stop at 23:40:47."""
    rows = []
    with open(Path(__file__).parent / "data" / "burner_2026-10-27.csv") as f:
        for r in csv.DictReader(f):
            h, m, s = map(int, r["time"].split(":"))
            rows.append((datetime(2026, 10, 27, h, m, s, tzinfo=LOC), float(r["grid_w"]) + float(r["dtu_w"]),
                         float(r["abgas_c"])))
    return rows


def run_detector(rows, use_load=True):
    det, runs, last_c = om.BurnerDetector(), [], None
    for t, w, c in rows:
        if use_load:
            det.feed_load(t, w)
        if c != last_c:  # the sensor only reports changes
            r = det.feed_exhaust(t, c)
            last_c = c
            if r:
                runs.append(r)
    return runs


def test_recorded_run_found_to_the_second_with_load():
    runs = run_detector(recorded())
    assert len(runs) == 1
    r = runs[0]
    assert abs((r.start - datetime(2026, 10, 27, 23, 34, 44, tzinfo=LOC)).total_seconds()) <= 25
    assert abs((r.end - datetime(2026, 10, 27, 23, 40, 47, tzinfo=LOC)).total_seconds()) <= 10
    assert r.source == "strom" and 180 <= r.step_w <= 350  # fan pre-purge and flame come in two steps
    assert 5.6 <= r.minutes <= 6.6


def test_recorded_run_found_from_exhaust_alone():
    runs = run_detector(recorded(), use_load=False)
    assert len(runs) == 1 and runs[0].source == "abgas"
    assert abs((runs[0].end - datetime(2026, 10, 27, 23, 40, 47, tzinfo=LOC)).total_seconds()) <= 40
    assert 5.0 <= runs[0].minutes <= 7.0


def test_no_run_without_exhaust_rise():
    t0 = datetime(2026, 10, 28, 12, 0, tzinfo=LOC)
    det = om.BurnerDetector()
    for i in range(200):  # a 300 W device switching on and off, exhaust flat
        det.feed_load(t0 + timedelta(seconds=3 * i), 500 + (300 if 50 < i < 150 else 0))
        assert det.feed_exhaust(t0 + timedelta(seconds=3 * i), 20.0 + (0.1 if i % 2 else 0.0)) is None
    assert not det.running


def test_run_roundtrip():
    r = om.Run(datetime(2026, 10, 28, 6, 0, tzinfo=LOC), datetime(2026, 10, 28, 6, 10, tzinfo=LOC), 290.0, "strom")
    assert om.Run.from_dict(r.to_dict()) == r and r.minutes == 10.0


def test_tank_curve():
    tank = om.Tank()
    assert abs(tank.liters(150) - 6000) < 1 and tank.liters(0) == 0
    mid = tank.liters(80) - tank.liters(79)
    low = tank.liters(2) - tank.liters(1)
    assert 40 < mid < 44 and low < mid  # less per cm in the rounded bottom
    assert 1800 < tank.liters(48) < 1900  # gauge 47-48 cm on 28.10.2026, user estimated ~1800 L
    assert abs(tank.cm(tank.liters(48)) - 48) < 0.05


def test_fit_learns_from_days():
    days = []
    for i in range(14):
        kh = 100 + 20 * i
        ac = 15.0 if i % 2 else 0.0
        oil = 1.0 + 0.03 * kh - 0.1 * ac
        days.append(om.HeatDay(f"2026-11-{i + 1:02d}", oil_l=oil, kh=kh, ac_heat_kwh=ac, minutes=1440))
    m = om.fit(days)
    assert m["quelle"] == "gelernt"
    assert abs(m["basis_l_tag"] - 1.0) < 1e-6 and abs(m["l_je_gradstunde"] - 0.03) < 1e-6
    assert abs(m["l_je_kwh_klimawaerme"] - 0.1) < 1e-6
    assert om.fit(days[:5])["quelle"] == "Startwerte"


def test_reach_scenarios_ordered():
    m = om.fit([])
    r = om.reach(1300.0, date(2026, 10, 28), m, indoor=20.5, ac_heat_per_kh=0.0)
    assert r["pessimistisch"] < r["neutral"] < r["optimistisch"]
    with_ac = om.reach(1300.0, date(2026, 10, 28), m, indoor=20.5, ac_heat_per_kh=0.05)
    assert (with_ac["neutral"] is None) or with_ac["neutral"] > r["neutral"]
    assert om.reach(0.0, date(2026, 10, 28), m, 20.5, 0.0)["neutral"] == date(2026, 10, 29)


SAMPLE_XML = ('<?xml version="1.0" encoding="utf-8"?><result><plz>31582</plz><taxRate>19</taxRate><deliveries>'
              '<delivery unloadingPoints="1" litre="2000"><price oilGrade="Heizöl Standard Schwefelarm" '
              'orderLink="https://www.heizoel24.de//heizoel/angebotsliste?zipCode=31582">173,38</price>'
              '<price oilGrade="Heizöl Super Schwefelarm" orderLink="x">176,12</price></delivery></deliveries></result>')


def test_parse_heizoel24_price():
    p = om.parse_heizoel24(SAMPLE_XML)
    assert p["eur_100l"] == 173.38 and p["eur_l"] == 1.7338 and p["menge_l"] == 2000
    assert p["sorte"] == "Heizöl Standard Schwefelarm" and p["plz"] == "31582"
    assert p["bestellen"].startswith("https://www.heizoel24.de/heizoel/")
    assert om.parse_heizoel24("<html>kaputt") is None
    assert om.parse_heizoel24("<result><deliveries/></result>") is None


def test_order_litres():
    assert om.order_litres(1578, 6000) == 4000
    assert om.order_litres(5500, 6000) == 1000 and om.order_litres(None, 6000) == 5500
