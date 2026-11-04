"""Charge plan for the car: as little as needed when power is dear, full only when it is cheap.

Pure Python, no Home Assistant imports. Rules agreed with the owner (05.11.2026):
- Guarantee the minimum target (default 20 %) at the next departure, in the cheapest quarter
  hours before it.
- Above that, a quarter hour may fill the car up to a cap that depends on its price, both
  relative to the last 4 weeks (so it works in every season) and absolute (so a long dear
  phase never looks "normal"). The lower of the two caps applies, never above the daily
  maximum (default 80 %).
- Optional filling waits when a quarter hour at least 1 ct (setting) cheaper is known later, even if
  the car may be away then: waiting for optional energy costs nothing.
- Travel: reach 80 % or 100 % by a set time; the part above 80 % is charged as late as
  possible (a full NCA battery should not stand long).
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta

SLOT = timedelta(minutes=15)
KWH_PER_PCT = 0.72          # grid kWh per % SoC, measured 02.11.2026 (22.4 kWh for 5 -> 36 %)
FULL_POWER_KW = 11.1        # 16 A three-phase, measured 05.11.2026
WAIT_MARGIN = 0.01          # EUR/kWh default: a later quarter hour this much cheaper makes optional filling wait
# (share of the last 4 weeks' prices that are cheaper or equal, cap %)
RELATIVE_CAPS = ((0.10, 80), (0.20, 65), (0.35, 50), (0.50, 35), (0.75, 25), (1.01, 20))
# (price at or above, cap %): a dear quarter hour never fills much, however long prices stay high
ABSOLUTE_CAPS = ((0.45, 20), (0.40, 25), (0.35, 40), (0.30, 60), (-99.0, 80))
ALWAYS_FULL_BELOW = 0.20    # EUR/kWh: always worth filling up to the maximum


@dataclass
class Settings:
    min_target: float = 20.0
    max_target: float = 80.0
    travel_at: datetime | None = None
    travel_target: float = 80.0
    wait_margin: float = WAIT_MARGIN


@dataclass
class Plan:
    slots: dict = field(default_factory=dict)   # slot start -> (price, reason)
    soc_at_departure: float = 0.0
    cost_eur: float = 0.0
    kwh: float = 0.0
    note: str = ""

    def charge_now(self, now: datetime) -> bool:
        start = slot_start(now)
        return start in self.slots


def slot_start(t: datetime) -> datetime:
    return t.replace(minute=(t.minute // 15) * 15, second=0, microsecond=0)


def pct_per_slot(power_kw: float = FULL_POWER_KW) -> float:
    return power_kw * 0.25 / KWH_PER_PCT


def price_cap(price: float, reference: list[float], max_target: float) -> float:
    """Highest SoC a quarter hour at this price may fill the car to."""
    if price < ALWAYS_FULL_BELOW:
        return max_target
    rel = 20.0
    if reference:
        share = bisect_right(reference, price) / len(reference)
        rel = next(cap for limit, cap in RELATIVE_CAPS if share <= limit)
    absolute = next(cap for limit, cap in ABSOLUTE_CAPS if price >= limit)
    return min(rel, absolute, max_target)


def make_plan(now: datetime, soc: float, prices: dict, reference: list[float], departure: datetime,
              s: Settings, step: float | None = None) -> Plan:
    """prices: slot start -> EUR/kWh (known quarter hours), reference: sorted prices of the last 4 weeks."""
    step = step or pct_per_slot()
    first = slot_start(now)
    future = {t: p for t, p in prices.items() if t >= first}
    plan = Plan()
    level = soc

    def add(t, reason):
        nonlocal level
        plan.slots[t] = (future[t], reason)
        level += step

    # 1. travel target (part up to 80 % cheapest first, the rest as late as possible)
    if s.travel_at and s.travel_at > now:
        window = sorted((t for t in future if t + SLOT <= s.travel_at), key=lambda t: future[t])
        base = min(s.travel_target, 80.0)
        for t in window:
            if level >= base:
                break
            add(t, "Reise")
        late = sorted((t for t in future if t + SLOT <= s.travel_at and t not in plan.slots), reverse=True)
        for t in late:
            if level >= s.travel_target:
                break
            add(t, "Reise (zuletzt, Akku nicht lange voll)")
    # 2. minimum at the next departure
    before = sorted((t for t in future if t + SLOT <= departure and t not in plan.slots), key=lambda t: future[t])
    for t in before:
        if level >= s.min_target:
            break
        add(t, "Minimum bis Abfahrt")
    # 3. optional filling, cheapest first, capped by price, waiting for clearly cheaper quarter hours
    for t in sorted((t for t in future if t + SLOT <= departure and t not in plan.slots), key=lambda t: future[t]):
        p = future[t]
        if level + step / 2 > price_cap(p, reference, s.max_target):
            continue
        if any(q <= p - s.wait_margin for u, q in future.items() if u > t and u not in plan.slots):
            continue
        add(t, "günstig auffüllen")
    plan.soc_at_departure = round(min(100.0, level), 1)
    plan.kwh = round(len(plan.slots) * step * KWH_PER_PCT, 1)
    plan.cost_eur = round(sum(p for p, _ in plan.slots.values()) * step * KWH_PER_PCT, 2)
    if not plan.slots:
        plan.note = "kein Laden nötig oder lohnend"
    else:
        times = sorted(plan.slots)
        plan.note = (f"{len(times)} Viertelstunden ab {times[0].strftime('%H:%M')}, "
                     f"Ø {plan.cost_eur / max(plan.kwh, 0.01):.3f} €/kWh")
    return plan


def next_departure(now: datetime, weekday_hm: tuple[int, int], weekend_hm: tuple[int, int], holidays=()) -> datetime:
    """Next departure time (local datetime with tz), weekday or weekend/holiday time."""
    for k in range(0, 8):
        d = (now + timedelta(days=k)).date()
        off = d.weekday() >= 5 or d in holidays
        h, m = weekend_hm if off else weekday_hm
        t = now.replace(year=d.year, month=d.month, day=d.day, hour=h, minute=m, second=0, microsecond=0)
        if t > now:
            return t
    return now + timedelta(days=1)


def surplus_amps(house_soc: float | None, house_full_pct: float, inverter_on: bool, balcony_w: float,
                 solar_w: float, current: int) -> int:
    """Surplus charging step: 0, 1 A (~0.8 kW) or 2 A (~1.45 kW). Hysteresis via `current`."""
    if house_soc is None or not inverter_on or solar_w < 200:
        return 0
    if current == 0 and house_soc < house_full_pct:
        return 0
    if current > 0 and house_soc < house_full_pct - 5:
        return 0
    return 2 if balcony_w >= 500 and solar_w >= 600 else 1
