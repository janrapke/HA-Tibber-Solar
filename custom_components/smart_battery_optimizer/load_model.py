"""House load forecast from recent history.

Pure Python (no Home Assistant imports). Keeps a rolling window of daily
15-min load profiles and predicts each quarter hour as a trimmed mean of the
same quarter on recent days. The level is then pulled towards the last two complete
days (LEVEL_*): the 14-day mean alone lagged a week behind when the house load dropped
from ~16.5 to ~10 kWh/day on 04.11.2026 (backtest 31.10.-09.11.: daily error 3.2 -> 2.3 kWh,
after the drop 3.6 -> 1.1 kWh). Short-term, the forecast is blended with the load
measured right now, fading out over two hours.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

QUARTERS = 96
WINDOW_DAYS = 14
NOWCAST_FADE_H = 2.0
LEVEL_DAYS = 2           # recent complete days that set the level
LEVEL_MIN_DAYS = 4       # complete days needed before the level is adjusted at all
LEVEL_BLEND = 0.75       # share of the recent level in the factor
LEVEL_CLAMP = (0.6, 1.4)
COMPLETE_QUARTERS = 80


def quarter_of(dt: datetime) -> int:
    return dt.hour * 4 + dt.minute // 15


class LoadModel:
    def __init__(self, base_load_w: float = 300.0):
        self.base_load_w = base_load_w
        self.days: dict[str, list[float | None]] = {}  # ISO date -> 96 quarter averages (W)
        self._level_cache: tuple[tuple, float] | None = None

    def to_dict(self) -> dict:
        return {"v": 1, "days": self.days}

    def load_dict(self, d: dict) -> None:
        if d and d.get("v") == 1:
            self.days = {k: list(v) for k, v in d.get("days", {}).items()}
            self._trim()

    def _trim(self) -> None:
        for k in sorted(self.days)[:-WINDOW_DAYS]:
            del self.days[k]

    def record(self, day: date, quarter: int, avg_w: float) -> None:
        key = day.isoformat()
        prof = self.days.setdefault(key, [None] * QUARTERS)
        prof[quarter] = max(0.0, float(avg_w))
        self._trim()

    def profile_value(self, quarter: int) -> float:
        vals = [p[quarter] for p in self.days.values() if p[quarter] is not None]
        if not vals:
            return self.base_load_w
        vals.sort()
        cut = len(vals) // 5  # drop top/bottom 20 % (car charging, holidays)
        core = vals[cut:len(vals) - cut] or vals
        return sum(core) / len(core)

    def _day_wh(self, prof: list[float | None]) -> float:
        n = sum(v is not None for v in prof)
        return sum(v for v in prof if v is not None) / 4.0 * QUARTERS / n if n else 0.0

    def level_factor(self) -> float:
        """Scale of the profile so its daily total moves towards the last two complete days."""
        complete = [k for k in sorted(self.days) if sum(v is not None for v in self.days[k]) >= COMPLETE_QUARTERS]
        key = (tuple(complete), sum(sum(v is not None for v in p) for p in self.days.values()))
        if self._level_cache and self._level_cache[0] == key:
            return self._level_cache[1]
        f = 1.0
        if len(complete) >= LEVEL_MIN_DAYS:
            base = sum(self.profile_value(q) for q in range(QUARTERS)) / 4.0
            recent = [self._day_wh(self.days[k]) for k in complete[-LEVEL_DAYS:]]
            if base > 0:
                ratio = min(max(sum(recent) / len(recent) / base, LEVEL_CLAMP[0]), LEVEL_CLAMP[1])
                f = 1.0 + LEVEL_BLEND * (ratio - 1.0)
        self._level_cache = (key, f)
        return f

    def predict(self, dt: datetime, now: datetime | None = None, current_w: float | None = None) -> float:
        base = self.profile_value(quarter_of(dt)) * self.level_factor()
        if now is None or current_w is None:
            return base
        h = (dt - now).total_seconds() / 3600.0
        if h >= NOWCAST_FADE_H or h < -0.25:
            return base
        w = max(0.0, 1.0 - h / NOWCAST_FADE_H)
        return w * current_w + (1 - w) * base

    def days_known(self) -> int:
        return sum(1 for p in self.days.values() if sum(v is not None for v in p) > 48)
