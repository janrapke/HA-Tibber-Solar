"""Price forecast: holidays, ridge fit on synthetic wind-driven prices, scoring and release rule."""
import importlib.util
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"
spec = importlib.util.spec_from_file_location("price_forecast", BASE / "price_forecast.py")
pf = importlib.util.module_from_spec(spec)
sys.modules["price_forecast"] = pf
spec.loader.exec_module(pf)

TZ = timezone(timedelta(hours=1))


def test_holidays_2026():
    h = pf.german_holidays(2026)
    assert date(2026, 4, 3) in h and date(2026, 4, 6) in h  # Good Friday, Easter Monday
    assert date(2026, 5, 14) in h and date(2026, 5, 25) in h and date(2026, 10, 31) in h


def test_day_hours_dst():
    assert len(pf.day_hours(date(2026, 3, 29), timezone.utc)) == 24


def synthetic(days=160):
    """Price = 0.30 + 0.04 at 18 h - 0.004 per m/s wind; wind varies by day."""
    w = pf.Weather()
    prices = {}
    start = date(2026, 1, 1)
    for k in range(days):
        d = start + timedelta(days=k)
        wind = 6 + 5 * math.sin(k / 3.0)
        for t in pf.day_hours(d, TZ):
            w.wind[t], w.sun[t], w.temp[t] = wind, 0.0, 5.0
            prices[t] = 0.30 + (0.04 if t.astimezone(TZ).hour == 18 else 0.0) - 0.004 * wind
    return prices, w, start + timedelta(days=days - 1)


def test_forecast_learns_wind_and_hour():
    prices, w, last = synthetic()
    today = last - timedelta(days=5)  # leave real prices for the target days
    known = {t: p for t, p in prices.items() if t.astimezone(TZ).date() <= today + timedelta(days=1)}
    fc = pf.make_forecast(known, w, today, TZ)
    assert set(fc) == {(today + timedelta(days=h)).isoformat() for h in (2, 3, 4)}
    for day in fc.values():
        s = pf.score(day["hours"], prices)
        assert s["mae"] < 0.02 and abs(s["bias"]) < 0.02  # level of the last 7 days carries their wind
        hours = {datetime.fromisoformat(t).astimezone(TZ).hour: p for t, p in day["hours"].items()}
        assert abs(hours[18] - hours[17] - 0.04) < 0.005  # evening peak learned


def test_quality_release_rule():
    good = [{"horizon": 2, "mae": 0.03, "bias": 0.005, "cheap_err": 0.03, "wait": i % 4 == 0,
             "gain": -0.01 if i == 4 else 0.04} for i in range(25)]
    q = pf.quality(good)
    assert q["tage"] == 25 and q["freigabe_erfuellt"]
    q = pf.quality(good[:10])
    assert not q["freigabe_erfuellt"]  # too few days
    bad = [dict(r, mae=0.06) for r in good]
    assert not pf.quality(bad)["freigabe_erfuellt"]


def test_wait_signal():
    assert pf.wait_signal([0.40] * 24, [0.37] * 24)
    assert not pf.wait_signal([0.40] * 24, [0.39] * 24)


def test_parse_open_meteo():
    point = {"hourly": {"time": ["2026-11-05T00:00"], "wind_speed_100m": [10.0], "shortwave_radiation": [0.0],
                        "temperature_2m": [5.0]}}
    w = pf.parse_open_meteo([point] * 7)
    t = datetime(2026, 11, 5, tzinfo=timezone.utc)
    assert w.wind[t] == 10.0 and w.temp[t] == 5.0
