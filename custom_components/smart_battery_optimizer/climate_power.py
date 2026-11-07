"""Power regulation of the Midea heat pump via its own sensor - learned per outdoor temperature.

Pure Python (no Home Assistant imports), so the tests and the nightly agent can run it.

What the test series of 06./07.11.2026 (about 5 °C outside) showed:
- The unit regulates by itself like an integrator on d = own sensor (T1) - setpoint:
  d ~1.0 K -> compressor rises ~2 Hz/min, d ~1.5-1.7 K -> holds, d ~1.9 K -> falls slowly,
  d >= 3 K -> falls to the minimum (27 Hz, ~545 W) and stays there, d ~3.8-4 K -> stops after minutes.
- Restart: d ~1.3 K after a long pause, 1.7-2.3 K shortly after a stop; it always starts with
  1-2 min at ~56 Hz.
- Its own sensor reads ~4 K above the room (ceiling air), but the unit compensates that itself.
So the controller does NOT integrate on top (that overshot to 1500 W in the test): it picks a
distance proportional to the power error and returns to the "hold" distance once the power fits.

The hold distance, the power the room needs at the target and the restart distance depend on the
outdoor temperature, so each is learned per 2 K outdoor bin. Learned values start from the
measured defaults and only move with enough clean data.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
import math

BIN_K = 2.0  # outdoor temperature bins: [-2, 0), [0, 2), [2, 4) ...

# measured 06./07.11.2026 at ~5 °C outside (defaults until the bin has learned its own value)
D_HOLD_DEFAULT = 1.6
D_HOLD_MIN, D_HOLD_MAX = 1.0, 2.6
D_RESTART_DEFAULT = 1.3
D_STOP_DEFAULT = 3.9
P_MIN_W = 545.0  # 27 Hz, the lowest the compressor runs (silent fan does not go lower)
P_MAX_W = 1600.0  # what the controller asks for at most (cold room); full power ~1850 W
P_HOLD_MIN, P_HOLD_MAX = 300.0, 1500.0

# controller
K_W_PER_K = 800.0  # wanted power per K room error (target - room)
K_D_PER_W = 0.002  # distance change per W power error: 300 W too low -> 0.6 K less distance
D_DOWN_MAX = 1.4  # at most this much below the hold distance (rises ~2 Hz/min at -0.6)
D_UP_MAX = 1.4  # at most this much above it (falls to the minimum)
DEAD_W = 80.0  # power error that counts as "fits": hold distance
D_MIN_STAGE = 3.0  # stays at the smallest stage, does not stop
D_STOP = 4.5  # makes the unit stop by itself (no off command, no 1 h lock)
STOP_ABOVE_K = 0.3  # room this much above the target: let the unit pause
RESUME_BELOW_K = 0.1  # ... until it is this much below the target again
START_HOLD_MIN = 3.0  # first minutes after a start: the unit runs its own start program

# learning
FORGET = 0.997  # per sample (one per minute while running): ~5 h of running memory
RATE_WINDOW = 3  # minutes between the two compressor values of one rate sample
MIN_N_HOLD = 40.0  # samples (weighted) before the learned hold distance counts fully
P_ALPHA = 0.02  # EMA of the power that holds the room (one sample per minute near the target)
EVT_ALPHA = 0.2  # EMA of the stop / restart distances
DEFROST_COIL_C = 25.0  # indoor coil below this while running = defrost or start: no learning


def outdoor_bin(outdoor: float | None) -> str | None:
    if outdoor is None or not math.isfinite(outdoor):
        return None
    return str(int(math.floor(outdoor / BIN_K) * BIN_K))


def p_hold_default(outdoor: float | None) -> float:
    """Power the living room needs at the target: ~800 W at 5 °C, +40 W per K colder."""
    o = 5.0 if outdoor is None else outdoor
    return max(P_HOLD_MIN, min(P_HOLD_MAX, 800.0 + 40.0 * (5.0 - o)))


@dataclass
class Bin:
    # weighted linear regression rate (Hz/min) over distance d
    n: float = 0.0
    sd: float = 0.0
    sr: float = 0.0
    sdd: float = 0.0
    sdr: float = 0.0
    p_hold: float | None = None
    p_n: int = 0
    d_restart: float | None = None
    restart_n: int = 0
    d_stop: float | None = None
    stop_n: int = 0
    p_min: float | None = None

    def add_rate(self, d: float, rate: float) -> None:
        self.n = self.n * FORGET + 1.0
        self.sd = self.sd * FORGET + d
        self.sr = self.sr * FORGET + rate
        self.sdd = self.sdd * FORGET + d * d
        self.sdr = self.sdr * FORGET + d * rate

    def fit(self) -> tuple[float, float] | None:
        """(slope Hz/min per K, hold distance) or None while the data is not clear."""
        if self.n < 10:
            return None
        md, mr = self.sd / self.n, self.sr / self.n
        var = self.sdd / self.n - md * md
        cov = self.sdr / self.n - md * mr
        if var < 0.02:
            return None
        slope = cov / var
        if slope >= -0.3:  # more distance must clearly mean falling power
            return None
        return slope, md - mr / slope

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in vars(self).items()}


class PowerLearner:
    """Learns the unit's behaviour per outdoor bin from one observation per minute."""

    def __init__(self) -> None:
        self.bins: dict[str, Bin] = {}
        self._hist: deque = deque(maxlen=RATE_WINDOW + 1)  # (ts, hz, d)
        self._was_running: bool | None = None
        self._last_d: float | None = None

    # -------------------------------------------------------------- queries
    def _bin(self, key: str) -> Bin:
        return self.bins.setdefault(key, Bin())

    def _near(self, outdoor: float | None, attr: str, min_n: float):
        """Value of the own bin, else of the nearest bins with data (max. 2 bins away)."""
        key = outdoor_bin(outdoor)
        if key is None:
            return None, 0.0
        k = int(key)
        for step in (0, 1, 2):
            vals = []
            for kk in {k - step * int(BIN_K), k + step * int(BIN_K)}:
                b = self.bins.get(str(kk))
                if b is None:
                    continue
                v, n = getattr(self, attr)(b)
                if v is not None and n >= min_n:
                    vals.append((v, n))
            if vals:
                n = sum(x[1] for x in vals)
                return sum(v * w for v, w in vals) / n, (n if step == 0 else n / (2 * step))
        return None, 0.0

    @staticmethod
    def _hold_of(b: Bin):
        f = b.fit()
        return (None, 0.0) if f is None else (f[1], b.n)

    @staticmethod
    def _p_of(b: Bin):
        return b.p_hold, float(b.p_n)

    @staticmethod
    def _restart_of(b: Bin):
        return b.d_restart, float(b.restart_n)

    @staticmethod
    def _stop_of(b: Bin):
        return b.d_stop, float(b.stop_n)

    def d_hold(self, outdoor: float | None) -> float:
        v, n = self._near(outdoor, "_hold_of", 10)
        if v is None:
            return D_HOLD_DEFAULT
        w = min(1.0, n / MIN_N_HOLD)
        return max(D_HOLD_MIN, min(D_HOLD_MAX, w * v + (1 - w) * D_HOLD_DEFAULT))

    def p_hold(self, outdoor: float | None) -> float:
        v, n = self._near(outdoor, "_p_of", 30)
        default = p_hold_default(outdoor)
        if v is None:
            return default
        w = min(1.0, n / 120.0)
        return max(P_HOLD_MIN, min(P_HOLD_MAX, w * v + (1 - w) * default))

    def d_restart(self, outdoor: float | None) -> float:
        v, _ = self._near(outdoor, "_restart_of", 1)
        return D_RESTART_DEFAULT if v is None else max(0.5, min(2.5, v))

    def d_stop(self, outdoor: float | None) -> float:
        v, _ = self._near(outdoor, "_stop_of", 1)
        return D_STOP_DEFAULT if v is None else max(2.5, min(5.0, v))

    # -------------------------------------------------------------- learning
    def observe(self, now: datetime, outdoor: float | None, device: float | None, setpoint: float | None,
                hz: float, power: float, run_min: float, coil_in: float | None, heating: bool,
                room: float | None = None, target: float | None = None) -> None:
        """One call per minute with the real unit values (also while the control only observes)."""
        key = outdoor_bin(outdoor)
        d = None if device is None or setpoint is None else device - setpoint
        running = hz > 0
        if not heating or key is None or d is None:
            self._hist.clear()
            self._was_running = None if not heating else running
            self._last_d = d
            return
        b = self._bin(key)
        # stop / restart events of the unit itself (in heat mode)
        if self._was_running is not None and running != self._was_running and self._last_d is not None:
            if running:
                b.d_restart = self._last_d if b.d_restart is None else b.d_restart + EVT_ALPHA * (self._last_d - b.d_restart)
                b.restart_n += 1
            elif self._last_d > 2.0:  # stopped because the distance was large (not by an off command)
                b.d_stop = self._last_d if b.d_stop is None else b.d_stop + EVT_ALPHA * (self._last_d - b.d_stop)
                b.stop_n += 1
        self._was_running = running
        self._last_d = d

        clean = running and run_min >= 5.0 and (coil_in is None or coil_in >= DEFROST_COIL_C)
        if not clean:
            self._hist.clear()
            return
        if hz <= 30.0:
            b.p_min = power if b.p_min is None else b.p_min + 0.05 * (power - b.p_min)
        self._hist.append((now, hz, d))
        if len(self._hist) > RATE_WINDOW:
            t0, hz0, _ = self._hist[0]
            dt = (now - t0).total_seconds() / 60.0
            ds = [x[2] for x in self._hist]
            if 2.0 <= dt <= 6.0 and max(ds) - min(ds) <= 0.6:
                rate = (hz - hz0) / dt
                pinned = (hz <= 28.0 and rate <= 0.1) or hz >= 75.0
                if not pinned:
                    b.add_rate(sum(ds) / len(ds), rate)
        # power that holds the room at the target (room error corrects it)
        if room is not None and target is not None and abs(room - target) <= 0.4:
            want = power + K_W_PER_K * (target - room)
            b.p_hold = want if b.p_hold is None else b.p_hold + P_ALPHA * (want - b.p_hold)
            b.p_n += 1

    # -------------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {"bins": {k: b.to_dict() for k, b in self.bins.items()}}

    def load_dict(self, d: dict | None) -> None:
        self.bins = {}
        for k, v in ((d or {}).get("bins") or {}).items():
            b = Bin()
            for f, x in (v or {}).items():
                if hasattr(b, f):
                    setattr(b, f, x)
            self.bins[str(k)] = b

    def reset(self) -> None:
        self.bins = {}
        self._hist.clear()

    def summary(self) -> dict:
        out = {}
        for k in sorted(self.bins, key=int):
            b = self.bins[k]
            f = b.fit()
            out[f"{k}..{int(k) + int(BIN_K)} °C"] = {
                "halte_abstand_k": None if f is None else round(f[1], 2),
                "steigung_hz_min_pro_k": None if f is None else round(f[0], 2),
                "messungen_abstand": round(b.n, 1),
                "halteleistung_w": None if b.p_hold is None else round(b.p_hold),
                "messungen_leistung": b.p_n,
                "neustart_abstand_k": None if b.d_restart is None else round(b.d_restart, 2),
                "neustarts": b.restart_n,
                "stopp_abstand_k": None if b.d_stop is None else round(b.d_stop, 2),
                "stopps": b.stop_n,
                "kleinste_leistung_w": None if b.p_min is None else round(b.p_min),
            }
        return out


def wanted_power(room: float, target: float, p_hold: float, stopped: bool, cold_boost: bool,
                 free_w: float = 0.0) -> float:
    """Electrical power the AC should run at (0 = pause)."""
    if stopped:
        return 0.0
    if cold_boost:
        return P_MAX_W
    p = p_hold + K_W_PER_K * (target - room)
    if free_w > 0 and room < target:
        p = max(p, free_w)  # free balcony power: use it while the room can take heat
    return max(0.0, min(P_MAX_W, p))


def wanted_distance(p_want: float, power: float, hz: float, run_min: float,
                    d_hold: float, d_restart: float) -> tuple[float | None, str]:
    """Distance own sensor - setpoint for the wanted power; None = keep the setpoint."""
    if p_want <= 0:
        return D_STOP, "Pause"
    if hz <= 0:
        return max(0.3, d_restart - 0.3), "Anlauf"
    if run_min < START_HOLD_MIN:
        return None, "Startprogramm"
    if p_want < P_MIN_W + DEAD_W:
        return D_MIN_STAGE, "kleinste Stufe"
    err = p_want - power
    if abs(err) <= DEAD_W:
        return d_hold, "halten"
    delta = max(-D_UP_MAX, min(D_DOWN_MAX, err * K_D_PER_W))
    return d_hold - delta, ("mehr Leistung" if err > 0 else "weniger Leistung")
