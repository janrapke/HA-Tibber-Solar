"""Best delay-start timer for washing machine and dishwasher: whole hours, next 24 h.

The user only sets the machine's own timer; nothing is switched. For every delay 0..24 h the
learned power profile is laid over the quarter-hour prices. Solar power that the plan would
have to curtail (battery full) is free, everything else is priced at the Tibber price.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

MAX_DELAY_H = 24
MIN_PROGRAM_MINUTES = 20  # shorter "programs" are rinse cycles or detection noise
MIN_PROGRAM_WH = 100.0
SLOT = timedelta(minutes=15)


@dataclass
class PriceSlot:
    start: datetime
    price: float  # EUR/kWh
    free_w: float = 0.0  # solar power that would be curtailed in this quarter hour


def typical_program(programs):
    """The program run most often (scores jump to whatever ran last, run counts do not)."""
    real = [p for p in programs
            if len(p.power_profile) >= MIN_PROGRAM_MINUTES and p.total_wh >= MIN_PROGRAM_WH]
    return max(real, key=lambda p: (p.run_count, p.probability_score), default=None)


def run_cost(profile: list[float], start: datetime, slots: list[PriceSlot]) -> float | None:
    """EUR for one run starting at start; None when the run leaves the known prices."""
    if not slots:
        return None
    first = slots[0].start
    cost = 0.0
    for i, w in enumerate(profile):
        t = start + timedelta(minutes=i)
        idx = int((t - first) / SLOT)
        if idx < 0 or idx >= len(slots):
            return None
        s = slots[idx]
        cost += max(0.0, w - s.free_w) / 60000.0 * s.price
    return cost


def best_timer(profile: list[float], now: datetime, slots: list[PriceSlot]) -> dict | None:
    """Cheapest whole-hour delay within 24 h; None when not even 'now' can be priced."""
    options = []
    for h in range(MAX_DELAY_H + 1):
        start = now + timedelta(hours=h)
        cost = run_cost(profile, start, slots)
        if cost is None:
            break  # later starts would end beyond the known prices too
        options.append((h, start, cost))
    if not options:
        return None
    # on a tie the earlier start wins (min keeps the first)
    h, start, cost = min(options, key=lambda o: round(o[2], 3))
    now_cost = options[0][2]
    duration = timedelta(minutes=len(profile))
    return {
        "timer_h": h,
        "start": start,
        "ende": start + duration,
        "fertig_in_h": round((start + duration - now).total_seconds() / 3600, 1),
        "kosten_eur": round(cost, 3),
        "kosten_sofort_eur": round(now_cost, 3),
        "ersparnis_eur": round(now_cost - cost, 3),
        "teuerste_eur": round(max(o[2] for o in options), 3),
        "ct_kwh": round(cost / (sum(profile) / 60000.0) * 100, 1) if sum(profile) > 0 else None,
        "stunden": [{"h": o[0], "start": o[1].strftime("%H:%M"), "eur": round(o[2], 3)} for o in options],
    }
