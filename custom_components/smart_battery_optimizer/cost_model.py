"""Pure math of the electricity cost comparison (no Home Assistant imports, unit-tested)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

INVERTER_EFF = 0.93          # a direct-feed micro inverter would turn panel DC into this much AC
# day record: [without_eur, solar_only_eur, real_eur, house_kwh, solar_kwh, import_kwh]
FIELDS = 6


def interval(price: float, hours: float, imp_w: float, exp_w: float, inverter_w: float, balcony_w: float,
             charger_w: float, panel_w: float) -> list[float]:
    """Costs and energies of one interval (powers in W, result in EUR / kWh)."""
    house = max(0.0, imp_w - exp_w + inverter_w + balcony_w - charger_w)
    solar = INVERTER_EFF * max(0.0, panel_w) + max(0.0, balcony_w)
    kwh = hours / 1000.0
    return [house * kwh * price, max(0.0, house - solar) * kwh * price, max(0.0, imp_w) * kwh * price,
            house * kwh, solar * kwh, max(0.0, imp_w) * kwh]


def add(day: list | None, vals: list[float]) -> list[float]:
    day = list(day or [0.0] * FIELDS)
    return [a + b for a, b in zip(day, vals)]


def summarize(days: dict, today: date) -> dict:
    """Totals for the card's periods plus the last 13 months."""
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
    for k, v in days.items():
        months[k[:7]] = [a + b for a, b in zip(months[k[:7]], v)]
    keys = sorted(months)[-13:]
    out["monate"] = {k: [round(months[k][0], 2), round(months[k][1], 2), round(months[k][2], 2)] for k in keys}
    out["seit"] = min(days) if days else None
    return out


def _fmt(t: list[float], n: int) -> dict:
    return {"ohne_system": round(t[0], 2), "nur_solar": round(t[1], 2), "tatsaechlich": round(t[2], 2),
            "solar_spart": round(t[0] - t[1], 2), "akku_spart": round(t[1] - t[2], 2),
            "gesamt_spart": round(t[0] - t[2], 2), "verbrauch_kwh": round(t[3], 1), "solar_kwh": round(t[4], 1),
            "netz_kwh": round(t[5], 1), "tage": n}
