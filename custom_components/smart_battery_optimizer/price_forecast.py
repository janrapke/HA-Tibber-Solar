"""Tibber price forecast for day 2-4 ahead (shadow mode first).

Pure Python, no Home Assistant imports. The model predicts the deviation of each hour's
price from the mean of the last 7 known days, from wind (north/east/west Germany), sun
(centre/south), temperature, hour of day, weekday/holiday. Ridge regression, refitted on
the last 120 days at each daily run.

Backtest 15.01.-03.11.2026 with measured weather (scripts not in the repo): mean error
3.2 ct (day 2) to 3.4 ct (day 4) per hour against 4.1-4.3 ct for "like the last 14 days";
the "day after tomorrow is >= 2 ct cheaper" signal was right 85 of 100 times.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from datetime import date, datetime, timedelta, timezone
import statistics

# Open-Meteo points: wind (north/east/west coast, Brandenburg), sun (centre, Bavaria, Rhine)
POINTS = [(54.5, 8.5), (54.1, 12.1), (53.4, 7.2), (52.4, 13.0), (51.3, 9.5), (48.1, 11.6), (49.5, 8.5)]
WIND_POINTS = (0, 1, 2, 3)
SUN_POINTS = (4, 5, 6)
TRAIN_DAYS = 120
LEVEL_DAYS = 7
RIDGE_LAMBDA = 2.0
HORIZONS = (2, 3, 4)
CHEAP_HOURS = 6
WAIT_MARGIN = 0.02  # EUR/kWh: "day after tomorrow is cheaper" when its cheapest hours are this much below
# release rule checked by the nightly agent (reported by the quality sensor)
RELEASE = {"min_days": 21, "mae_day2": 0.04, "cheap_err": 0.04, "wait_hit_rate": 0.75, "wait_gain": 0.02, "max_bias": 0.015}


@lru_cache(maxsize=8)
def german_holidays(year: int) -> frozenset[date]:
    """Nationwide holidays plus Reformation Day (Lower Saxony)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    easter = date(year, month, day)
    return frozenset({date(year, 1, 1), easter - timedelta(days=2), easter + timedelta(days=1), date(year, 5, 1),
            easter + timedelta(days=39), easter + timedelta(days=50), date(year, 10, 3), date(year, 10, 31),
            date(year, 12, 25), date(year, 12, 26)})


@dataclass
class Weather:
    """Hourly means over the points, keyed by UTC hour."""
    wind: dict = field(default_factory=dict)   # m/s at 100 m
    sun: dict = field(default_factory=dict)    # W/m²
    temp: dict = field(default_factory=dict)   # °C

    def merge(self, other: "Weather") -> None:
        self.wind.update(other.wind)
        self.sun.update(other.sun)
        self.temp.update(other.temp)


def parse_open_meteo(data) -> Weather:
    """Open-Meteo answer for the POINTS (list of per-point results)."""
    if isinstance(data, dict):
        data = [data]
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in data[0]["hourly"]["time"]]

    def mean(idx, key):
        out = {}
        for j, t in enumerate(times):
            vals = [data[i]["hourly"][key][j] for i in idx if i < len(data) and data[i]["hourly"][key][j] is not None]
            if vals:
                out[t] = sum(vals) / len(vals)
        return out
    return Weather(mean(WIND_POINTS, "wind_speed_100m"), mean(SUN_POINTS, "shortwave_radiation"),
                   mean(range(len(data)), "temperature_2m"))


def day_hours(d: date, tz) -> list[datetime]:
    """The 24 (23/25 at DST changes) local hours of day d as UTC datetimes."""
    start = datetime(d.year, d.month, d.day, tzinfo=tz)
    end = start + timedelta(days=1)
    out, t = [], start.astimezone(timezone.utc)
    while t < end.astimezone(timezone.utc):
        out.append(t)
        t += timedelta(hours=1)
    return out


def level(prices: dict, last_day: date, tz) -> float | None:
    """Mean price of the LEVEL_DAYS days up to and including last_day."""
    vals = [prices[t] for k in range(LEVEL_DAYS) for t in day_hours(last_day - timedelta(days=k), tz) if t in prices]
    return statistics.mean(vals) if len(vals) >= 24 * LEVEL_DAYS * 0.8 else None


def features(t: datetime, w: Weather, tz) -> list[float] | None:
    lt = t.astimezone(tz)
    wind = w.wind.get(t)
    if wind is None:
        return None
    sun = w.sun.get(t, 0.0)
    temp = w.temp.get(t, 10.0)
    hour = [0.0] * 24
    hour[lt.hour] = 1.0
    off = 1.0 if lt.weekday() >= 5 or lt.date() in german_holidays(lt.year) else 0.0
    night = 1.0 if lt.hour < 6 or lt.hour >= 22 else 0.0
    wn = wind / 30.0
    sn = sun / 500.0
    return hour + [off, off * (1.0 - night), wn, wn * wn, wn * night, sn, sn * sn, temp / 20.0, 1.0]


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (small dense system)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        piv = m[c][c] or 1e-12
        for r in range(c + 1, n):
            f = m[r][c] / piv
            if f:
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / (m[r][r] or 1e-12)
    return x


def fit(prices: dict, w: Weather, days: list[date], horizon: int, tz) -> list[float] | None:
    """Ridge fit of price - level, the level as it was known `horizon` days before."""
    rows, ys = [], []
    for d in days:
        lvl = level(prices, d - timedelta(days=horizon - 1), tz)
        if lvl is None:
            continue
        for t in day_hours(d, tz):
            if t in prices:
                f = features(t, w, tz)
                if f is not None:
                    rows.append(f)
                    ys.append(prices[t] - lvl)
    if len(rows) < 24 * 20:
        return None
    n = len(rows[0])
    ata = [[sum(r[i] * r[j] for r in rows) + (RIDGE_LAMBDA if i == j else 0.0) for j in range(n)] for i in range(n)]
    aty = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(n)]
    return solve(ata, aty)


def predict_day(coef: list[float], prices: dict, w: Weather, target: date, known_until: date, tz) -> dict | None:
    """Hourly forecast {utc hour: EUR/kWh} for target; prices known through known_until."""
    lvl = level(prices, known_until, tz)
    if lvl is None:
        return None
    out = {}
    for t in day_hours(target, tz):
        f = features(t, w, tz)
        if f is not None:
            out[t] = lvl + sum(c * x for c, x in zip(coef, f))
    return out if len(out) >= 20 else None


def cheap_level(values) -> float:
    vals = sorted(values)
    return sum(vals[:CHEAP_HOURS]) / min(CHEAP_HOURS, len(vals))


def make_forecast(prices: dict, w: Weather, today: date, tz) -> dict:
    """Forecasts for today+2..today+4, issued once tomorrow's prices are known."""
    known_until = today + timedelta(days=1)
    out = {}
    for h in HORIZONS:
        days = [known_until - timedelta(days=k) for k in range(TRAIN_DAYS)]
        coef = fit(prices, w, [d for d in days if d <= known_until], h, tz)
        if coef is None:
            continue
        target = today + timedelta(days=h)
        pred = predict_day(coef, prices, w, target, known_until, tz)
        if pred:
            out[target.isoformat()] = {"horizon": h, "hours": {t.isoformat(): round(p, 4) for t, p in sorted(pred.items())}}
    return out


def score(forecast_hours: dict, prices: dict) -> dict | None:
    """Compare one stored day forecast with the real prices."""
    pairs = [(p, prices[datetime.fromisoformat(t)]) for t, p in forecast_hours.items() if datetime.fromisoformat(t) in prices]
    if len(pairs) < 20:
        return None
    pred, act = [p for p, _ in pairs], [a for _, a in pairs]
    return {"mae": statistics.mean(abs(p - a) for p, a in pairs), "bias": statistics.mean(p - a for p, a in pairs),
            "cheap_err": abs(cheap_level(pred) - cheap_level(act)), "cheap_pred": cheap_level(pred),
            "cheap_act": cheap_level(act)}


def wait_signal(day1_actual: list[float], day2_pred: list[float]) -> bool:
    """True when the forecast says the day after tomorrow is clearly cheaper than (known) tomorrow."""
    return cheap_level(day2_pred) <= cheap_level(day1_actual) - WAIT_MARGIN


def quality(results: list[dict]) -> dict:
    """Rolling quality over stored day results (dicts with horizon, mae, bias, cheap_err, wait/gain)."""
    d2 = [r for r in results if r.get("horizon") == 2]
    waits = [r for r in d2 if r.get("wait")]
    q = {"tage": len(d2)}
    if d2:
        q.update(mae_tag2=round(statistics.mean(r["mae"] for r in d2), 4),
                 bias_tag2=round(statistics.mean(r["bias"] for r in d2), 4),
                 fehler_guenstigste_stunden=round(statistics.mean(r["cheap_err"] for r in d2), 4))
    for h in (3, 4):
        hr = [r for r in results if r.get("horizon") == h]
        if hr:
            q[f"mae_tag{h}"] = round(statistics.mean(r["mae"] for r in hr), 4)
    if waits:
        q.update(warte_signale=len(waits), warte_trefferquote=round(sum(r["gain"] > 0 for r in waits) / len(waits), 3),
                 warte_ersparnis=round(statistics.mean(r["gain"] for r in waits), 4))
    q["freigabe_erfuellt"] = bool(
        q["tage"] >= RELEASE["min_days"] and q.get("mae_tag2", 1) < RELEASE["mae_day2"]
        and q.get("fehler_guenstigste_stunden", 1) < RELEASE["cheap_err"]
        and abs(q.get("bias_tag2", 1)) <= RELEASE["max_bias"]
        and q.get("warte_trefferquote", 0) >= RELEASE["wait_hit_rate"] and q.get("warte_ersparnis", 0) > RELEASE["wait_gain"])
    return q
