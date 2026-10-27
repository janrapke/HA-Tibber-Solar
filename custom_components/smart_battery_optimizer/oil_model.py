"""Oil heating model: burner runs, oil tank, heating days and how long the oil lasts.

Pure Python (no Home Assistant imports).

Burner runs are found from two signals:
- the exhaust pipe temperature rises steeply within seconds after the flame starts
  and peaks 15-25 s after it stops (sure, but a little late),
- the house load jumps by ~300 W at start and drops by the same at stop (exact
  to the second, but other devices look similar).
The exhaust temperature confirms a run, the load step gives its exact times.
Measured on 2026-10-27: start step +295 W, stop step -298 W, exhaust 22 -> 52 °C in 6 min.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math
import statistics
import xml.etree.ElementTree as ET

# ---- burner detection ------------------------------------------------------
RISE_K_PER_MIN = 2.0       # exhaust rising at least this fast = flame on
RISE_MIN_K = 1.0           # ... and by at least this much
RISE_WINDOW = timedelta(seconds=90)
FALL_K = 0.6               # exhaust this far below its peak = flame off
EXHAUST_START_LAG = timedelta(seconds=10)
EXHAUST_STOP_LAG = timedelta(seconds=20)
STEP_MIN_W, STEP_MAX_W = 180.0, 450.0
STEP_WINDOW = timedelta(seconds=30)
LOAD_KEEP = timedelta(minutes=10)
MAX_RUN = timedelta(minutes=120)
MIN_RUN = timedelta(seconds=45)


@dataclass
class Run:
    start: datetime
    end: datetime
    step_w: float | None = None
    source: str = "abgas"  # "strom" when both edges came from the load steps

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "step_w": self.step_w,
                "source": self.source}

    @classmethod
    def from_dict(cls, d: dict) -> "Run":
        return cls(datetime.fromisoformat(d["start"]), datetime.fromisoformat(d["end"]),
                   d.get("step_w"), d.get("source", "abgas"))


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


class BurnerDetector:
    """Feed load and exhaust readings in time order; finished runs come back from feed_*()."""

    def __init__(self):
        self.load: list[tuple[datetime, float]] = []
        self.exhaust: list[tuple[datetime, float]] = []
        self.running = False
        self.start: datetime | None = None
        self.start_step: float | None = None
        self.start_from_power = False
        self.peak: tuple[datetime, float] | None = None
        self.stop_step: tuple[datetime, float] | None = None

    # load steps -------------------------------------------------------------
    def _level(self, t0: datetime, t1: datetime) -> float | None:
        return _median([w for t, w in self.load if t0 <= t < t1])

    def find_step(self, t0: datetime, t1: datetime, up: bool) -> tuple[datetime, float] | None:
        """Biggest load step of the burner size inside [t0, t1]."""
        best = None
        for t, _ in self.load:
            if not t0 <= t <= t1:
                continue
            before = self._level(t - STEP_WINDOW, t)
            after = self._level(t, t + STEP_WINDOW)
            if before is None or after is None:
                continue
            d = after - before
            if (up and STEP_MIN_W <= d <= STEP_MAX_W) or (not up and -STEP_MAX_W <= d <= -STEP_MIN_W):
                if best is None or abs(d) > abs(best[1]):
                    best = (t, d)
        return best

    def feed_load(self, t: datetime, w: float) -> Run | None:
        self.load.append((t, w))
        cutoff = t - LOAD_KEEP
        while self.load and self.load[0][0] < cutoff:
            self.load.pop(0)
        if self.running and self.start_step is not None:
            # a matching drop ends the run at once (exact), the exhaust peak confirms it later
            step = self.find_step(t - STEP_WINDOW, t - STEP_WINDOW / 2, up=False)
            if step and abs(abs(step[1]) - self.start_step) <= 150 and step[0] > self.start + MIN_RUN:
                self.stop_step = step
        return None

    # exhaust ----------------------------------------------------------------
    def feed_exhaust(self, t: datetime, c: float) -> Run | None:
        self.exhaust.append((t, c))
        cutoff = t - LOAD_KEEP
        while self.exhaust and self.exhaust[0][0] < cutoff:
            self.exhaust.pop(0)
        if not self.running:
            past = [(pt, pc) for pt, pc in self.exhaust if t - RISE_WINDOW <= pt < t]
            if not past:
                return None
            pt, pc = min(past, key=lambda x: x[1])
            minutes = max((t - pt).total_seconds() / 60.0, 0.05)
            if c - pc >= RISE_MIN_K and (c - pc) / minutes >= RISE_K_PER_MIN:
                self._begin(t, pt)
            return None
        if self.peak is None or c >= self.peak[1]:
            self.peak = (t, c)
            return None
        if self.peak[1] - c >= FALL_K:
            return self._finish(t)
        if t - self.start > MAX_RUN:
            return self._finish(t)
        return None

    def _begin(self, t: datetime, rise_from: datetime) -> None:
        self.running = True
        self.peak = None
        self.stop_step = None
        step = self.find_step(rise_from - timedelta(seconds=120), t, up=True)
        if step:
            self.start, self.start_step, self.start_from_power = step[0], step[1], True
        else:
            self.start, self.start_step, self.start_from_power = rise_from - EXHAUST_START_LAG, None, False

    def _finish(self, t: datetime) -> Run | None:
        peak_t = self.peak[0] if self.peak else t
        end, source = peak_t - EXHAUST_STOP_LAG, "abgas"
        stop = self.stop_step or (self.find_step(peak_t - timedelta(seconds=90), peak_t + timedelta(seconds=10), up=False)
                                  if self.start_step is not None else None)
        if stop and abs(abs(stop[1]) - (self.start_step or 0)) <= 150:
            end = stop[0]
            source = "strom" if self.start_from_power else "abgas"
        run = Run(self.start, max(end, self.start), round(self.start_step, 0) if self.start_step else None, source)
        self.running = False
        self.start = self.start_step = self.peak = self.stop_step = None
        return run if run.end - run.start >= MIN_RUN else None


# ---- tank --------------------------------------------------------------------
@dataclass
class Tank:
    """Steel battery tanks (Dehoust DIN 6620, 3 x 2000 L, 150 x 72 cm cut with rounded ends), 0-150 cm gauge."""
    count: int = 3
    liters_each: float = 2000.0
    height_cm: float = 150.0
    width_cm: float = 72.0
    corner_cm: float = 30.0

    @property
    def capacity(self) -> float:
        return self.count * self.liters_each

    def _width_at(self, y: float) -> float:
        r = min(self.corner_cm, self.width_cm / 2, self.height_cm / 2)
        d = min(y, self.height_cm - y)
        if d >= r:
            return self.width_cm
        return self.width_cm - 2 * (r - math.sqrt(max(0.0, r * r - (r - d) ** 2)))

    def _area(self, h: float, steps: int = 300) -> float:
        h = max(0.0, min(self.height_cm, h))
        dy = h / steps if steps else 0
        return sum(self._width_at((i + 0.5) * dy) for i in range(steps)) * dy

    def liters(self, cm: float) -> float:
        return self.capacity * self._area(cm) / self._area(self.height_cm)

    def cm(self, liters: float) -> float:
        lo, hi = 0.0, self.height_cm
        for _ in range(40):
            mid = (lo + hi) / 2
            if self.liters(mid) < liters:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2


# ---- heating days and model ----------------------------------------------------
@dataclass
class HeatDay:
    day: str
    oil_l: float = 0.0
    burner_min: float = 0.0
    starts: int = 0
    kh: float = 0.0            # degree hours (house mean indoor - outdoor)
    indoor_sum: float = 0.0
    outdoor_sum: float = 0.0
    minutes: int = 0
    ac_kwh_el: float = 0.0
    ac_heat_kwh: float = 0.0
    house_kwh: float = 0.0     # household electricity, ends up as heat
    window_min: float = 0.0    # minutes the bedroom window was open

    @property
    def complete(self) -> bool:
        return self.minutes >= 22 * 60

    def summary(self) -> dict:
        n = max(1, self.minutes)
        return {
            "datum": self.day, "oel_l": round(self.oil_l, 2), "brenner_min": round(self.burner_min, 1),
            "starts": self.starts, "gradstunden": round(self.kh, 1),
            "innen_mittel": round(self.indoor_sum / n, 2), "aussen_mittel": round(self.outdoor_sum / n, 2),
            "klima_strom_kwh": round(self.ac_kwh_el, 2), "klima_waerme_kwh": round(self.ac_heat_kwh, 2),
            "hausstrom_kwh": round(self.house_kwh, 2), "fenster_offen_h": round(self.window_min / 60, 1),
            "vollstaendig": self.complete,
        }


PRIOR_BASE_L = 1.2          # hot water + boiler standby per day
PRIOR_L_PER_KH = 0.021      # ~0.5 L per degree-day (27.10.2026: ~3 L, 20.5 °C in, ~12 °C out)
OIL_KWH_PER_L = 10.0
PRIOR_DAYS = 5             # the start values count like this many measured days
PRIOR_SCALE = (1.0, 60.0, 10.0)  # typical day-to-day spread: base 1, degree hours per day, AC heat kWh per day

# monthly mean outdoor temperature around Nienburg (Weser), °C
MONTH_TEMP = {1: 1.8, 2: 2.5, 3: 5.5, 4: 9.5, 5: 13.5, 6: 16.5, 7: 18.5, 8: 18.0, 9: 14.5, 10: 10.0, 11: 5.8, 12: 2.8}
SCENARIOS = {"optimistisch": 2.0, "neutral": 0.0, "pessimistisch": -3.0}  # K added to the monthly means
GAIN_K = 5.0               # sun, people and appliances cover the last ~5 K: heating limit = indoor - 5 K


def degree_hours(indoor: float, outdoor: float, hours: float) -> float:
    """Heating degree hours above the heating limit (indoor - GAIN_K): 18 °C inside needs less than 21 °C."""
    return max(0.0, indoor - GAIN_K - outdoor) * hours


def fit(days: list[HeatDay], boiler_eff: float = 0.85) -> dict:
    """Oil per day = base + per_kh * degree hours - ac_factor * AC heat, learned from day one.

    Least squares with the start values as a prior worth PRIOR_DAYS days: with few days the
    start values dominate, every complete day shifts the result further towards the data.
    The AC factor starts from physics (1 kWh heat from the AC saves 1/(10 kWh/L * boiler eff) L);
    it only moves away from that once there are days with and without the AC.
    """
    prior = [PRIOR_BASE_L, PRIOR_L_PER_KH, 1.0 / (OIL_KWH_PER_L * boiler_eff)]
    full = [d for d in days if d.complete]
    n = len(full)
    out = {"basis_l_tag": prior[0], "l_je_gradstunde": prior[1], "l_je_kwh_klimawaerme": round(prior[2], 4),
           "tage": n, "gewicht_messung": round(n / (n + PRIOR_DAYS), 2),
           "quelle": "Startwerte" if n == 0 else ("gelernt" if n >= 3 * PRIOR_DAYS else f"Startwerte + {n} Tage")}
    if n == 0:
        return out
    rows = [(1.0, d.kh, -d.ac_heat_kwh, d.oil_l) for d in full]
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(3)] for i in range(3)]
    atb = [sum(r[i] * r[3] for r in rows) for i in range(3)]
    for i, scale in enumerate(PRIOR_SCALE):  # prior = PRIOR_DAYS pseudo-days per parameter
        w = PRIOR_DAYS * scale * scale
        ata[i][i] += w
        atb[i] += w * prior[i]
    sol = _solve(ata, atb)
    if sol is None:
        return out
    out.update(basis_l_tag=round(max(0.0, sol[0]), 3),
               l_je_gradstunde=round(max(prior[1] * 0.2, sol[1]), 5),
               l_je_kwh_klimawaerme=round(max(0.0, sol[2]), 4))
    return out


def _solve(a: list[list[float]], b: list[float]) -> list[float] | None:
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < 1e-9:
            return None
        m[c], m[p] = m[p], m[c]
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] for i in range(n)]


def daily_oil(model: dict, outdoor: float, indoor: float, ac_heat_kwh: float) -> float:
    kh = degree_hours(indoor, outdoor, 24.0)
    oil = model["basis_l_tag"] + model["l_je_gradstunde"] * kh - model["l_je_kwh_klimawaerme"] * ac_heat_kwh
    return max(model["basis_l_tag"], oil)


def reach(usable_l: float, today: date, model: dict, indoor: float, ac_heat_per_kh: float,
          max_days: int = 730) -> dict:
    """Date the usable oil runs out for each scenario (None = longer than max_days)."""
    out = {}
    for name, dk in SCENARIOS.items():
        left, d = usable_l, today
        end = None
        for _ in range(max_days):
            d += timedelta(days=1)
            t_out = MONTH_TEMP[d.month] + dk
            kh = degree_hours(indoor, t_out, 24.0)
            left -= daily_oil(model, t_out, indoor, ac_heat_per_kh * kh)
            if left <= 0:
                end = d
                break
        out[name] = end
    return out


# ---- price -------------------------------------------------------------------
HEIZOEL24_URL = "https://www.heizoel24.de/DailyPriceXml.ashx?zipCode={zip}&litre={litre}&unloadingpoints=1"


def order_litres(tank_l: float | None, capacity: float) -> int:
    """Order amount for the price query: fill up to 95 % in 500 L steps, at least 1000 L."""
    free = capacity * 0.95 - (tank_l or 0.0)
    return int(max(1000, round(free / 500.0) * 500))


def parse_heizoel24(text: str) -> dict | None:
    """Cheapest grade of the heizoel24 daily price XML (EUR per 100 L incl. VAT)."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    delivery = root.find("deliveries/delivery")
    prices = delivery.findall("price") if delivery is not None else []
    rows = []
    for p in prices:
        try:
            rows.append((float((p.text or "").replace(",", ".")), p))
        except ValueError:
            continue
    if not rows:
        return None
    eur100, p = min(rows, key=lambda r: r[0])
    return {"eur_100l": eur100, "eur_l": round(eur100 / 100.0, 4), "sorte": p.get("oilGrade"),
            "menge_l": int(delivery.get("litre") or 0), "plz": root.findtext("plz"),
            "bestellen": (p.get("orderLink") or "").replace("de//", "de/")}
