"""Thermal model of the living room heat pump (split AC) - learned from measurements.

Pure Python (no Home Assistant imports). Every 15 minutes the climate control
records one Sample. From the stream of samples the model learns:

- the offset between the unit's own sensor and the room sensor (heat / cool),
- the cooling rate k (K/h per K indoor-outdoor difference) from idle phases,
- the heating efficiency (heat out / electricity in) per 2-K outdoor class,
- the electrical power per compressor frequency.

Heat balance used for the efficiency:
    C * dT/dt = eff * P_el - C * k * (T_in - T_out)
C is the effective thermal capacity of the room. It cannot be separated from
the efficiency with a room sensor alone, so it is a constant derived from the
measurements of 2026-10-25 (~0.42 kW loss at ~10 K, cooling ~0.4 K/h).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

# outdoor °C -> efficiency, realistic incl. defrosting (prior before learning)
EFFICIENCY_CURVE: list[tuple[float, float]] = [
    (-15, 1.7), (-10, 2.0), (-7, 2.2), (-5, 2.4), (-2, 2.7), (0, 2.9),
    (2, 3.1), (5, 3.6), (7, 3.9), (10, 4.3), (12, 4.6), (16, 5.1),
]
EFF_MIN, EFF_MAX = 1.2, 7.0
EFF_CLASS_K = 2.0
EFF_ALPHA = 0.05  # new efficiency observations are blended in slowly

OIL_KWH_PER_L = 10.0
SAFETY_MARGIN = 0.10

THERMAL_CAPACITY_KWH_K = 1.0
PRIOR_OFFSET_HEAT = 5.0
PRIOR_OFFSET_COOL = 0.0
PRIOR_K = 1.0 / 28.0  # time constant ~28 h
PRIOR_W_PER_HZ = 27.0  # 30 Hz ~ 810 W

RAW_DAYS = 30
OFFSET_MIN_RUN_MIN = 20.0
IDLE_PHASE_H = 2.0
HEAT_PHASE_H = 1.0
MIN_DIFF_K = 3.0
MIN_HEAT_KWH = 0.3
MAX_GAP = timedelta(minutes=25)
HZ_BIN = 5


def interpolate(curve: list[tuple[float, float]], x: float) -> float:
    """Linear interpolation, held constant outside the curve."""
    if x <= curve[0][0]:
        return curve[0][1]
    if x >= curve[-1][0]:
        return curve[-1][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return curve[-1][1]


def oil_cost_per_kwh_heat(oil_eur_per_l: float, boiler_eff: float) -> float:
    """EUR per kWh of heat from oil (1.00 EUR/L, 85 % -> 0.118)."""
    return oil_eur_per_l / (OIL_KWH_PER_L * max(0.1, boiler_eff))


def break_even_price(oil_eur_kwh_heat: float, efficiency: float, margin: float = SAFETY_MARGIN) -> float:
    """Highest electricity price (EUR/kWh) at which the heat pump beats oil."""
    return oil_eur_kwh_heat * efficiency * (1.0 - margin)


@dataclass
class Sample:
    ts: datetime
    room: float | None
    outdoor: float | None
    device: float | None  # unit's own indoor sensor
    hvac: str  # off / heat / cool / ...
    power_w: float  # compressor_power
    freq_hz: float  # compressor_frequency, 0 = compressor off
    radiators_on: bool
    fan: str | None = None
    run_min: float = 0.0  # minutes the compressor has been running without pause
    openings: dict = field(default_factory=dict)  # window/door entity -> share of the quarter it was open

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Sample":
        return cls(**{**d, "ts": datetime.fromisoformat(d["ts"])})

    @property
    def running(self) -> bool:
        return self.freq_hz > 0 and self.hvac in ("heat", "cool")

    @property
    def idle(self) -> bool:
        return not self.running and not self.radiators_on


def _ema(old: float, new: float, alpha: float) -> float:
    return old + alpha * (new - old)


class ThermalModel:
    def __init__(self, capacity_kwh_k: float = THERMAL_CAPACITY_KWH_K):
        self.capacity = capacity_kwh_k
        self.samples: list[Sample] = []
        self.offset = {"heat": PRIOR_OFFSET_HEAT, "cool": PRIOR_OFFSET_COOL}
        self.offset_n = {"heat": 0, "cool": 0}
        self.k = PRIOR_K
        self.k_n = 0
        self.eff: dict[str, list[float]] = {}  # class lower bound -> [value, n]
        self.power_hz: dict[str, list[float]] = {}  # Hz bin -> [W, n]
        self._idle_start: Sample | None = None
        self._heat_start: Sample | None = None
        self._heat_kwh = 0.0

    # --- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "v": 1,
            "samples": [s.to_dict() for s in self.samples],
            "offset": self.offset, "offset_n": self.offset_n,
            "k": self.k, "k_n": self.k_n,
            "eff": self.eff, "power_hz": self.power_hz,
        }

    def load_dict(self, d: dict) -> None:
        if not d or d.get("v") != 1:
            return
        self.samples = [Sample.from_dict(s) for s in d.get("samples", [])]
        self.offset = {**self.offset, **d.get("offset", {})}
        self.offset_n = {**self.offset_n, **d.get("offset_n", {})}
        self.k = float(d.get("k", PRIOR_K))
        self.k_n = int(d.get("k_n", 0))
        self.eff = {k: list(v) for k, v in d.get("eff", {}).items()}
        self.power_hz = {k: list(v) for k, v in d.get("power_hz", {}).items()}

    def reset(self) -> None:
        """Forget learned values, keep the raw samples."""
        samples = self.samples
        self.__init__(self.capacity)
        self.samples = samples

    # --- queries -----------------------------------------------------------
    @staticmethod
    def eff_class(outdoor: float) -> int:
        return int((outdoor // EFF_CLASS_K) * EFF_CLASS_K)

    def efficiency(self, outdoor: float) -> float:
        prior = interpolate(EFFICIENCY_CURVE, outdoor)
        cls = self.eff_class(outdoor)
        learned = self.eff.get(str(cls))
        if not learned:
            return prior
        center_prior = interpolate(EFFICIENCY_CURVE, cls + EFF_CLASS_K / 2)
        return max(EFF_MIN, min(EFF_MAX, prior * learned[0] / center_prior))

    def device_offset(self, mode: str) -> float:
        return self.offset.get(mode, 0.0)

    def power_at_hz(self, hz: float) -> float:
        if hz <= 0:
            return 0.0
        pts = sorted((float(b), v[0]) for b, v in self.power_hz.items())
        if not pts:
            return hz * PRIOR_W_PER_HZ
        if len(pts) == 1:
            return hz * pts[0][1] / max(1.0, pts[0][0])
        return interpolate(pts, hz)

    def summary(self) -> dict:
        return {
            "versatz_heizen_k": round(self.offset["heat"], 2),
            "versatz_kuehlen_k": round(self.offset["cool"], 2),
            "versatz_messungen": dict(self.offset_n),
            "auskuehlrate_k_pro_h_pro_k": round(self.k, 4),
            "auskuehl_messungen": self.k_n,
            "effizienz": {c: round(v[0], 2) for c, v in sorted(self.eff.items(), key=lambda i: int(i[0]))},
            "effizienz_messungen": {c: int(v[1]) for c, v in self.eff.items()},
            "leistung_je_hz": {b: round(v[0]) for b, v in sorted(self.power_hz.items(), key=lambda i: int(i[0]))},
            "datensaetze": len(self.samples),
        }

    # --- learning ----------------------------------------------------------
    def add_sample(self, s: Sample) -> None:
        prev = self.samples[-1] if self.samples else None
        if prev is not None and s.ts <= prev.ts:
            return
        self.samples.append(s)
        cutoff = s.ts - timedelta(days=RAW_DAYS)
        while self.samples and self.samples[0].ts < cutoff:
            self.samples.pop(0)
        contiguous = prev is not None and s.ts - prev.ts <= MAX_GAP
        self._learn_offset(s)
        self._learn_power(s)
        self._learn_cooling(s, prev if contiguous else None)
        self._learn_heating(s, prev if contiguous else None)

    def _learn_offset(self, s: Sample) -> None:
        if not s.running or s.run_min < OFFSET_MIN_RUN_MIN or s.room is None or s.device is None:
            return
        obs = s.device - s.room
        if not -10.0 <= obs <= 15.0:
            return
        mode = s.hvac
        n = self.offset_n[mode]
        if n >= 5 and abs(obs - self.offset[mode]) > 4.0:
            return
        self.offset[mode] = _ema(self.offset[mode], obs, 0.3 if n < 5 else 0.1)
        self.offset_n[mode] = n + 1

    def _learn_power(self, s: Sample) -> None:
        if s.freq_hz <= 0 or s.power_w <= 0:
            return
        key = str(int(round(s.freq_hz / HZ_BIN) * HZ_BIN))
        cur = self.power_hz.get(key)
        if cur is None:
            self.power_hz[key] = [float(s.power_w), 1]
        else:
            cur[0] = _ema(cur[0], s.power_w, 0.2)
            cur[1] += 1

    def _learn_cooling(self, s: Sample, prev: Sample | None) -> None:
        if not s.idle or s.room is None or s.outdoor is None or prev is None or not prev.idle:
            self._idle_start = s if s.idle and s.room is not None and s.outdoor is not None else None
            return
        start = self._idle_start or prev
        if start.room is None or start.outdoor is None:
            self._idle_start = s
            return
        self._idle_start = start
        hours = (s.ts - start.ts).total_seconds() / 3600.0
        if hours < IDLE_PHASE_H:
            return
        diff = ((start.room - start.outdoor) + (s.room - s.outdoor)) / 2.0
        self._idle_start = s
        if diff < MIN_DIFF_K:
            return
        obs = -(s.room - start.room) / hours / diff
        if not 0.0 <= obs <= 0.3:
            return
        self.k = _ema(self.k, obs, 0.3 if self.k_n < 3 else 0.1)
        self.k_n += 1

    def _learn_heating(self, s: Sample, prev: Sample | None) -> None:
        heating = s.hvac == "heat" and s.power_w > 0 and not s.radiators_on
        valid = heating and s.room is not None and s.outdoor is not None
        if not valid or prev is None or self._heat_start is None:
            self._heat_start = s if valid else None
            self._heat_kwh = 0.0
            return
        hours_step = (s.ts - prev.ts).total_seconds() / 3600.0
        self._heat_kwh += (s.power_w + prev.power_w) / 2.0 / 1000.0 * hours_step
        start = self._heat_start
        hours = (s.ts - start.ts).total_seconds() / 3600.0
        if hours < HEAT_PHASE_H:
            return
        energy = self._heat_kwh
        self._heat_start = s
        self._heat_kwh = 0.0
        if energy < MIN_HEAT_KWH:
            return
        diff = ((start.room - start.outdoor) + (s.room - s.outdoor)) / 2.0
        outdoor = (start.outdoor + s.outdoor) / 2.0
        obs = self.capacity * ((s.room - start.room) + self.k * diff * hours) / energy
        cls = str(self.eff_class(outdoor))
        cur = self.eff.get(cls)
        base = cur[0] if cur else interpolate(EFFICIENCY_CURVE, int(cls) + EFF_CLASS_K / 2)
        if not base / 2.0 <= obs <= base * 2.0:
            return  # implausible (radiators on unnoticed, door open, sensor glitch)
        val = max(EFF_MIN, min(EFF_MAX, _ema(base, obs, EFF_ALPHA)))
        self.eff[cls] = [val, (cur[1] if cur else 0) + 1]


# --- building key figures (to check insulation measures over months) -------
NIGHT_START_H, NIGHT_END_H = 21, 8
NIGHT_MIN_H = 3.0
KPI_MIN_DIFF_K = 5.0
DAY_MIN_SAMPLES = 86  # of 96 quarter hours
DAY_MIN_HEAT_KWH = 0.5


def _runs(samples: list[Sample], ok) -> list[list[Sample]]:
    """Contiguous runs (gaps <= MAX_GAP) of samples matching ok()."""
    runs, cur = [], []
    for s in samples:
        if ok(s) and (not cur or s.ts - cur[-1].ts <= MAX_GAP):
            cur.append(s)
            continue
        if cur:
            runs.append(cur)
        cur = [s] if ok(s) else []
    if cur:
        runs.append(cur)
    return runs


def night_cooling(samples: list[Sample], morning) -> dict | None:
    """Cooling rate of the night ending on `morning` (date) from the longest idle phase.

    k = -(least-squares slope of the room temperature) / mean(indoor - outdoor);
    the time constant 1/k in hours does not depend on any efficiency estimate.
    """
    start_day = morning - timedelta(days=1)
    night = [s for s in samples
             if (s.ts.date() == start_day and s.ts.hour >= NIGHT_START_H)
             or (s.ts.date() == morning and s.ts.hour < NIGHT_END_H)]
    runs = _runs(night, lambda s: s.idle and s.room is not None and s.outdoor is not None)
    if not runs:
        return None
    run = max(runs, key=lambda r: r[-1].ts - r[0].ts)
    hours = (run[-1].ts - run[0].ts).total_seconds() / 3600.0
    if hours < NIGHT_MIN_H:
        return None
    t = [(s.ts - run[0].ts).total_seconds() / 3600.0 for s in run]
    y = [s.room for s in run]
    tm_, ym = sum(t) / len(t), sum(y) / len(y)
    var = sum((a - tm_) ** 2 for a in t)
    slope = sum((a - tm_) * (b - ym) for a, b in zip(t, y)) / var if var > 0 else 0.0
    diff = sum(s.room - s.outdoor for s in run) / len(run)
    if diff < KPI_MIN_DIFF_K or slope >= 0:
        return None
    k = -slope / diff
    keys = {key for s in run for key in s.openings}
    return {
        "datum": morning.isoformat(),
        "auskuehlrate_k_pro_h_pro_k": round(k, 5),
        "zeitkonstante_h": round(1.0 / k, 1),
        "dauer_h": round(hours, 2),
        "differenz_innen_aussen_k": round(diff, 2),
        "raum_start": round(run[0].room, 2), "raum_ende": round(run[-1].room, 2),
        "offen_anteil": {key: round(sum(s.openings.get(key, 0.0) for s in run) / len(run), 2)
                         for key in sorted(keys)},
    }


OPEN_SHARE, CLOSED_SHARE = 0.8, 0.2
COMPARE_MIN_NIGHTS = 3
COMPARE_DIFF_K = 15.0  # reference indoor-outdoor difference for the kWh figure (~5 °C outside)


def compare_openings(nights: list[dict], key: str, capacity_kwh_k: float = THERMAL_CAPACITY_KWH_K) -> dict:
    """Cooling of nights with `key` (e.g. the bedroom window) open against closed.

    Nights in between (opened or closed during the night) are not used.
    """
    def share(n):
        return (n.get("offen_anteil") or {}).get(key)

    opened = [n["auskuehlrate_k_pro_h_pro_k"] for n in nights if (share(n) or 0.0) >= OPEN_SHARE]
    closed = [n["auskuehlrate_k_pro_h_pro_k"] for n in nights
              if share(n) is not None and share(n) <= CLOSED_SHARE]
    out = {"naechte_offen": len(opened), "naechte_zu": len(closed)}
    if opened:
        out["zeitkonstante_offen_h"] = round(len(opened) / sum(opened), 1)
    if closed:
        out["zeitkonstante_zu_h"] = round(len(closed) / sum(closed), 1)
    if len(opened) < COMPARE_MIN_NIGHTS or len(closed) < COMPARE_MIN_NIGHTS:
        out["ergebnis"] = f"noch zu wenig Nächte (je {COMPARE_MIN_NIGHTS} nötig)"
        return out
    extra_w_k = (sum(opened) / len(opened) - sum(closed) / len(closed)) * capacity_kwh_k * 1000.0
    out["mehrverlust_w_pro_k"] = round(extra_w_k, 1)
    out["mehr_waerme_kwh_pro_tag_bei_15k"] = round(extra_w_k * COMPARE_DIFF_K * 24.0 / 1000.0, 2)
    out["ergebnis"] = "gemessen"
    return out


def day_heat_demand(samples: list[Sample], day, efficiency) -> dict | None:
    """Heat per Kelvin indoor-outdoor difference on a day the AC heated alone.

    Only days with (almost) complete data and the radiators never heating.
    `efficiency(outdoor)` converts AC electricity into heat.
    """
    rows = [s for s in samples if s.ts.date() == day]
    if len(rows) < DAY_MIN_SAMPLES or any(s.radiators_on for s in rows):
        return None
    valid = [s for s in rows if s.room is not None and s.outdoor is not None]
    if len(valid) < DAY_MIN_SAMPLES:
        return None
    step_h = 24.0 / len(rows)
    el = sum(max(0.0, s.power_w) for s in rows if s.hvac == "heat") * step_h / 1000.0
    heat = sum(max(0.0, s.power_w) * efficiency(s.outdoor if s.outdoor is not None else 10.0)
               for s in rows if s.hvac == "heat") * step_h / 1000.0
    diff = sum(s.room - s.outdoor for s in valid) / len(valid)
    if diff < KPI_MIN_DIFF_K or heat < DAY_MIN_HEAT_KWH:
        return None
    per_k = heat / diff
    return {
        "datum": day.isoformat(),
        "waerme_kwh_pro_k_tag": round(per_k, 3),
        "verlust_w_pro_k": round(per_k * 1000.0 / 24.0, 1),
        "waerme_kwh": round(heat, 2),
        "klimastrom_kwh": round(el, 2),
        "differenz_innen_aussen_k": round(diff, 2),
        "raum_mittel": round(sum(s.room for s in valid) / len(valid), 2),
        "aussen_mittel": round(sum(s.outdoor for s in valid) / len(valid), 2),
    }
