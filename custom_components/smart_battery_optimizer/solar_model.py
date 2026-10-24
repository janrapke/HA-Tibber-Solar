"""Solar forecast: weather physics x learned site response, capped at charger limit.

Pure Python (no Home Assistant imports) so it can be unit-tested and replayed
against recorded data.

Model per 15-min slot:
    P = min(cap, k(sun position) * POA)
where POA is the plane-of-array irradiance for a generic south-facing 30° plane
computed from Open-Meteo GHI/DNI/DHI, and k is learned per sun-position bin.
The bins absorb everything the generic plane gets wrong: tracker geometry,
horizon shading, wiring losses. Samples taken while the battery is full or the
chargers are clipping are not used for learning (production was limited, not
the sun).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

TILT = math.radians(30.0)
AZIMUTH = math.radians(180.0)
ALBEDO = 0.2

ELEV_BIN_DEG = 5.0
AZ_BIN_DEG = 20.0
# Shrinkage weight towards the global factor, in (W/m²)² units: one bin needs
# roughly this many "full-sun quarter hours" before it fully trusts itself.
SHRINK = 4 * 300.0 ** 2
PRIOR_WEIGHT = 2.0e4  # weak prior for the global beam/diffuse fit
DECAY = 0.995  # per learning sample; slowly forgets old seasons


def sun_position(dt: datetime, lat: float, lon: float) -> tuple[float, float]:
    """Return (elevation_deg, azimuth_deg) using the NOAA approximation."""
    dt = dt.astimezone(timezone.utc)
    doy = dt.timetuple().tm_yday
    hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    g = 2.0 * math.pi / 365.0 * (doy - 1 + (hour - 12) / 24.0)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = hour * 60.0 + eqtime + 4.0 * lon
    ha = math.radians(tst / 4.0 - 180.0)
    phi = math.radians(lat)
    cos_zen = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
    cos_zen = max(-1.0, min(1.0, cos_zen))
    zen = math.acos(cos_zen)
    elev = 90.0 - math.degrees(zen)
    sin_zen = math.sin(zen)
    if sin_zen < 1e-6:
        return elev, 180.0
    cos_az = (math.sin(decl) - math.sin(phi) * cos_zen) / (math.cos(phi) * sin_zen)
    az = math.degrees(math.acos(max(-1.0, min(1.0, cos_az))))
    if ha > 0:
        az = 360.0 - az
    return elev, az


def poa_components(ghi: float, dni: float, dhi: float, elev: float, az: float) -> tuple[float, float]:
    """(beam, diffuse) irradiance on the reference plane in W/m², isotropic sky."""
    if elev <= 0:
        return 0.0, 0.0
    zen = math.radians(90.0 - elev)
    saz = math.radians(az)
    cos_inc = (math.cos(zen) * math.cos(TILT)
               + math.sin(zen) * math.sin(TILT) * math.cos(saz - AZIMUTH))
    beam = max(0.0, dni) * max(0.0, cos_inc)
    sky = max(0.0, dhi) * (1 + math.cos(TILT)) / 2
    ground = max(0.0, ghi) * ALBEDO * (1 - math.cos(TILT)) / 2
    return beam, sky + ground


def _bin(elev: float, az: float) -> str:
    return f"{int(elev // ELEV_BIN_DEG)}:{int(az // AZ_BIN_DEG)}"


class SolarModel:
    """Learns the site response to beam and diffuse light plus the charger cap.

    Global part: P = kb * beam + kd * diffuse (online least squares, 2x2).
    Local part: a multiplicative correction per sun-position bin, shrunk towards
    1.0 until the bin has seen enough sunny quarter hours. It captures horizon
    shading and tracker geometry.
    """

    def __init__(self, lat: float, lon: float, prior_k: float = 1.5, prior_cap_w: float | None = None):
        self.lat = lat
        self.lon = lon
        self.prior = [prior_k, prior_k, 0.0]  # kb, kd, offset (W)
        # normal equations for features (beam, diffuse, 1): A (3x3, symmetric) and y
        self.a = [[0.0] * 3 for _ in range(3)]
        self.y = [0.0, 0.0, 0.0]
        self.bins: dict[str, list[float]] = {}  # bin -> [sum measured*pred, sum pred²]
        self.cap_w = prior_cap_w
        self.samples = 0

    # ---------- persistence ----------
    def to_dict(self) -> dict:
        return {"v": 3, "a": self.a, "y": self.y, "bins": self.bins, "cap_w": self.cap_w,
                "samples": self.samples}

    def load_dict(self, d: dict) -> None:
        if not d or d.get("v") != 3:
            return
        self.a = [[float(x) for x in row] for row in d["a"]]
        self.y = [float(x) for x in d["y"]]
        self.bins = {k: [float(v[0]), float(v[1])] for k, v in d.get("bins", {}).items()}
        self.cap_w = d.get("cap_w")
        self.samples = int(d.get("samples", 0))

    # ---------- model ----------
    def coefficients(self) -> list[float]:
        """Ridge-regularised solution of the 3x3 normal equations (towards the prior)."""
        lam = [PRIOR_WEIGHT, PRIOR_WEIGHT, 50.0]
        m = [[self.a[i][j] + (lam[i] if i == j else 0.0) for j in range(3)] for i in range(3)]
        v = [self.y[i] + lam[i] * self.prior[i] for i in range(3)]
        sol = _solve3(m, v)
        if sol is None:
            return list(self.prior)
        sol[0] = max(0.0, sol[0])
        sol[1] = max(0.0, sol[1])
        sol[2] = min(0.0, sol[2])  # only a start-up loss, never free power
        return sol

    def _base(self, beam: float, diff: float) -> float:
        kb, kd, c = self.coefficients()
        return max(0.0, kb * beam + kd * diff + c)

    def bin_factor(self, elev: float, az: float) -> float:
        b = self.bins.get(_bin(elev, az))
        if not b:
            return 1.0
        return max(0.0, (b[0] + SHRINK) / (b[1] + SHRINK))

    def predict(self, dt: datetime, ghi: float, dni: float, dhi: float) -> float:
        """Expected average solar power (W) at time dt (use the slot centre)."""
        elev, az = sun_position(dt, self.lat, self.lon)
        if elev <= 0:
            return 0.0
        beam, diff = poa_components(ghi, dni, dhi, elev, az)
        p = self._base(beam, diff) * self.bin_factor(elev, az)
        if self.cap_w:
            p = min(p, self.cap_w)
        return max(0.0, p)

    def learn(self, dt: datetime, ghi: float, dni: float, dhi: float, measured_w: float,
              limited: bool) -> None:
        """Feed one 15-min average. `limited`: battery full / absorption / float."""
        if measured_w is None or measured_w < 0 or measured_w != measured_w:
            return
        if self.cap_w is None or measured_w > self.cap_w:
            self.cap_w = measured_w
        else:
            self.cap_w = max(measured_w, self.cap_w * 0.9999)

        elev, az = sun_position(dt, self.lat, self.lon)
        if elev <= 0 or limited:
            return
        beam, diff = poa_components(ghi, dni, dhi, elev, az)
        if beam + diff < 20:
            return
        if self.cap_w and measured_w >= 0.93 * self.cap_w:
            return  # clipped by the chargers: says nothing about the sun
        pred = self._base(beam, diff)
        x = (beam, diff, 1.0)
        for i in range(3):
            self.y[i] = self.y[i] * DECAY + x[i] * measured_w
            for j in range(3):
                self.a[i][j] = self.a[i][j] * DECAY + x[i] * x[j]
        if pred > 0:
            # bin statistics in "base prediction" units, normalised to ~W/m² scale
            scale = 1.0 / max(self.prior[0], 0.1)
            b = self.bins.setdefault(_bin(elev, az), [0.0, 0.0])
            b[0] = b[0] * DECAY + measured_w * pred * scale * scale
            b[1] = b[1] * DECAY + pred * pred * scale * scale
        self.samples += 1

    def quality(self) -> dict:
        kb, kd, c = self.coefficients()
        return {"samples": self.samples, "bins": len(self.bins), "k_beam": round(kb, 3),
                "k_diffuse": round(kd, 3), "offset_w": round(c), "cap_w": round(self.cap_w or 0.0)}


def _solve3(m: list[list[float]], v: list[float]) -> list[float] | None:
    """Solve a 3x3 linear system by Gaussian elimination with pivoting."""
    a = [row[:] + [v[i]] for i, row in enumerate(m)]
    for c in range(3):
        p = max(range(c, 3), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-12:
            return None
        a[c], a[p] = a[p], a[c]
        for r in range(3):
            if r != c:
                f = a[r][c] / a[c][c]
                for k in range(c, 4):
                    a[r][k] -= f * a[c][k]
    return [a[i][3] / a[i][i] for i in range(3)]
