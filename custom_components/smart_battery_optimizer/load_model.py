"""House load forecast from recent history.

Pure Python (no Home Assistant imports). Keeps a rolling window of daily
15-min load profiles and predicts each quarter hour as a trimmed mean of the
same quarter on recent days. Short-term, the forecast is blended with the load
measured right now, fading out over two hours.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

QUARTERS = 96
WINDOW_DAYS = 14
NOWCAST_FADE_H = 2.0


def quarter_of(dt: datetime) -> int:
    return dt.hour * 4 + dt.minute // 15


class LoadModel:
    def __init__(self, base_load_w: float = 300.0):
        self.base_load_w = base_load_w
        self.days: dict[str, list[float | None]] = {}  # ISO date -> 96 quarter averages (W)

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

    def predict(self, dt: datetime, now: datetime | None = None, current_w: float | None = None) -> float:
        base = self.profile_value(quarter_of(dt))
        if now is None or current_w is None:
            return base
        h = (dt - now).total_seconds() / 3600.0
        if h >= NOWCAST_FADE_H or h < -0.25:
            return base
        w = max(0.0, 1.0 - h / NOWCAST_FADE_H)
        return w * current_w + (1 - w) * base

    def days_known(self) -> int:
        return sum(1 for p in self.days.values() if sum(v is not None for v in p) > 48)
