"""Pure math of the electricity cost comparison (no Home Assistant imports, unit-tested).

Without the battery the panels could only feed in like a balcony power plant: balcony and panels
together at most FEED_LIMIT_W (800 W per household). Solar counts as "used directly" as far as it fits
under that limit and the house could have used it at the moment of production (balcony first, the
panels fill up the rest). Everything beyond - above the limit or above the house load - is the surplus
the battery saves. If the battery takes panel power the house could have used directly, it only earns
the price difference on that part.

Scenarios per interval (Tibber price, exported energy is not paid):
    without system : house * price
    without battery: (house - solar used directly) * price
    real           : grid import * price (includes what the grid charger drew)
battery system saves = without battery - real, split into
    price shifting : discharged energy * (price at use - price when it went into the battery),
                     for solar and grid-charged energy alike
    stored solar   : the rest = solar energy made usable, valued at the price of its production
                     (net of the midday import while the panels fill the battery and of losses)
The battery content is tracked as one pool: energy (DC kWh) and its value at the charge-time
price (EUR), emptied at the mean price.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

INVERTER_EFF = 0.93          # battery DC -> house AC
CHARGER_EFF = 0.90           # grid charger AC -> battery DC
FEED_LIMIT_W = 800.0         # balcony power plant limit per household (AC), shared by balcony and panels
# day record: [without_eur, without_battery_eur, real_eur, house_kwh, solar_kwh, import_kwh,
#              unused_without_battery_kwh, panel_kwh, price_shift_eur]
FIELDS = 9


def new_pool() -> dict:
    return {"kwh": 0.0, "eur": 0.0}


def interval(price: float, hours: float, imp_w: float, exp_w: float, inverter_w: float, balcony_w: float,
             charger_w: float, panel_w: float, pool: dict | None = None, capacity_kwh: float = 5.0) -> list[float]:
    """Costs and energies of one interval (powers in W, result in EUR / kWh). Updates `pool` in place."""
    inverter_w, balcony_w, charger_w, panel_w = (max(0.0, x) for x in (inverter_w, balcony_w, charger_w, panel_w))
    house = max(0.0, imp_w - exp_w + inverter_w + balcony_w - charger_w)
    kwh = hours / 1000.0
    # solar the house could have used directly: balcony first, then the panels through an inverter,
    # both together at most FEED_LIMIT_W
    bal_used = min(house, balcony_w, FEED_LIMIT_W)
    room_ac = max(0.0, min(house, FEED_LIMIT_W) - bal_used)
    panel_used = min(panel_w, room_ac / INVERTER_EFF)
    direct_ac = bal_used + panel_used * INVERTER_EFF
    unused = (balcony_w - bal_used) + (panel_w - panel_used)   # surplus, only usable through the battery

    shift = 0.0
    if pool is not None:
        out_dc = inverter_w * kwh / INVERTER_EFF
        if pool["kwh"] > 1e-9 and out_dc > 0:
            mean = pool["eur"] / pool["kwh"]           # charge-time price per DC kWh in the battery
            shift = inverter_w * kwh * (price - mean)
            take = min(out_dc, pool["kwh"])
            pool["kwh"] -= take
            pool["eur"] -= take * mean
        grid_dc = charger_w * kwh * CHARGER_EFF
        pool["kwh"] += panel_w * kwh + grid_dc
        pool["eur"] += panel_w * kwh * price + charger_w * kwh * price   # grid: what the charging cost
        if pool["kwh"] > capacity_kwh > 0:   # unmodelled losses: never hold more than the battery can
            f = capacity_kwh / pool["kwh"]
            pool["kwh"] *= f
            pool["eur"] *= f

    return [house * kwh * price, (house - direct_ac) * kwh * price, max(0.0, imp_w) * kwh * price,
            house * kwh, (panel_w + balcony_w) * kwh, max(0.0, imp_w) * kwh, unused * kwh, panel_w * kwh, shift]


def add(day: list | None, vals: list[float]) -> list[float]:
    day = list(day or [0.0] * FIELDS)
    return [a + b for a, b in zip(day, vals)]


def summarize(days: dict, today: date) -> dict:
    """Totals for the card's periods plus months and years."""
    periods = {
        "heute": today, "7_tage": today - timedelta(days=6), "30_tage": today - timedelta(days=29),
        "monat": today.replace(day=1), "jahr": today.replace(month=1, day=1), "gesamt": date.min,
    }
    out = {}
    for name, start in periods.items():
        t = [0.0] * FIELDS
        n = 0
        for k, v in days.items():
            if start <= date.fromisoformat(k) <= today:
                t = [a + b for a, b in zip(t, v)]
                n += 1
        out[name] = _fmt(t, n)
    months: dict = defaultdict(lambda: [0.0] * FIELDS)
    mdays: dict = defaultdict(int)
    years: dict = defaultdict(lambda: [0.0] * FIELDS)
    ydays: dict = defaultdict(int)
    for k, v in days.items():
        months[k[:7]] = [a + b for a, b in zip(months[k[:7]], v)]
        years[k[:4]] = [a + b for a, b in zip(years[k[:4]], v)]
        mdays[k[:7]] += 1
        ydays[k[:4]] += 1
    out["monate"] = {k: _fmt(months[k], mdays[k]) for k in sorted(months)}
    out["jahre"] = {k: _fmt(years[k], ydays[k]) for k in sorted(years)}
    out["seit"] = min(days) if days else None
    return out


def _fmt(t: list[float], n: int) -> dict:
    akku = t[1] - t[2]
    return {"ohne_system": round(t[0], 2), "ohne_speicher": round(t[1], 2), "tatsaechlich": round(t[2], 2),
            "solar_direkt_spart": round(t[0] - t[1], 2), "akku_spart": round(akku, 2),
            "akku_preis_spart": round(t[8], 2), "akku_solar_spart": round(akku - t[8], 2),
            "gesamt_spart": round(t[0] - t[2], 2), "verbrauch_kwh": round(t[3], 1), "solar_kwh": round(t[4], 1),
            "panel_kwh": round(t[7], 1), "ungenutzt_ohne_akku_kwh": round(t[6], 1), "netz_kwh": round(t[5], 1),
            "tage": n}
