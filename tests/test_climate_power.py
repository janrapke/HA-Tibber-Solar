"""Tests for the learned power regulation of the heat pump (climate_power.py)."""
import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"
spec = importlib.util.spec_from_file_location("climate_power", BASE / "climate_power.py")
cp = importlib.util.module_from_spec(spec)
sys.modules["climate_power"] = cp
spec.loader.exec_module(cp)

T0 = datetime(2026, 11, 7, 12, 0)
M = timedelta(minutes=1)


def _round_half(x):
    return round(x * 2) / 2


class Unit:
    """Integrator like the real unit: Hz change per minute = slope * (d - hold)."""

    def __init__(self, hold=1.6, slope=-2.5, hz=30.0):
        self.hold, self.slope, self.hz = hold, slope, hz

    def step(self, d):
        self.hz = max(27.0, min(75.0, self.hz + self.slope * (d - self.hold)))
        return self.hz

    @property
    def power(self):
        return 545.0 + (self.hz - 27.0) * 25.0


def test_bins_by_outdoor_temperature():
    assert cp.outdoor_bin(5.3) == "4" and cp.outdoor_bin(-0.5) == "-2" and cp.outdoor_bin(None) is None


def test_defaults_without_data():
    pl = cp.PowerLearner()
    assert pl.d_hold(5.0) == cp.D_HOLD_DEFAULT and pl.d_restart(5.0) == cp.D_RESTART_DEFAULT
    assert pl.p_hold(5.0) == 800.0 and pl.p_hold(-5.0) == 1200.0


def test_learns_the_hold_distance_of_its_bin():
    pl = cp.PowerLearner()
    unit = Unit(hold=2.0)
    t = T0
    ds = [1.2, 1.5, 1.8, 2.1, 2.4, 2.7]
    for k in range(240):
        d = ds[(k // 8) % len(ds)]
        unit.step(d)
        if unit.hz in (27.0, 75.0):
            unit.hz = 50.0
        pl.observe(t, 5.0, 23.0 + d, 23.0, unit.hz, unit.power, 20.0, 40.0, True)
        t += M
    assert abs(pl.d_hold(5.0) - 2.0) < 0.15
    assert pl.d_hold(3.0) != cp.D_HOLD_DEFAULT  # neighbour bin borrows it
    assert pl.d_hold(15.0) == cp.D_HOLD_DEFAULT  # too far away: default


def test_no_learning_during_defrost_start_or_when_pinned():
    pl = cp.PowerLearner()
    t = T0
    for _ in range(60):
        pl.observe(t, 5.0, 24.0, 21.0, 27.0, 545.0, 20.0, 40.0, True)  # pinned at the minimum
        t += M
    for _ in range(60):
        pl.observe(t, 5.0, 24.0, 21.0, 50.0, 1000.0, 2.0, 40.0, True)  # start program
        t += M
    for _ in range(60):
        pl.observe(t, 5.0, 24.0, 21.0, 50.0, 1000.0, 20.0, 10.0, True)  # defrost (cold coil)
        t += M
    assert pl.bins["4"].n == 0


def test_learns_restart_and_stop_distances():
    pl = cp.PowerLearner()
    t = T0
    seq = [(4.0, 27.0), (4.0, 0.0), (2.0, 0.0), (1.4, 0.0), (1.4, 56.0)]
    for d, hz in seq:
        pl.observe(t, 5.0, 22.0 + d, 22.0, hz, 545.0 if hz else 3.0, 10.0 if hz else 0.0, 40.0, True)
        t += M
    b = pl.bins["4"]
    assert b.stop_n == 1 and abs(b.d_stop - 4.0) < 1e-6
    assert b.restart_n == 1 and abs(b.d_restart - 1.4) < 1e-6
    assert abs(pl.d_restart(5.0) - 1.4) < 1e-6


def test_learns_the_power_the_room_needs():
    pl = cp.PowerLearner()
    t = T0
    for _ in range(300):
        pl.observe(t, 5.0, 25.0, 23.5, 40.0, 700.0, 30.0, 40.0, True, room=21.1, target=21.0)
        t += M
    assert abs(pl.p_hold(5.0) - 620.0) < 15  # 700 W made it 0.1 K too warm


def test_roundtrip():
    pl = cp.PowerLearner()
    pl.observe(T0, 5.0, 25.0, 23.5, 40.0, 700.0, 30.0, 40.0, True, room=21.0, target=21.0)
    pl2 = cp.PowerLearner()
    pl2.load_dict(pl.to_dict())
    assert pl2.bins["4"].p_hold == 700.0 and "4..6 °C" in pl2.summary()


def test_wanted_power():
    assert cp.wanted_power(21.4, 21.0, 800, True, False) == 0.0
    assert cp.wanted_power(19.0, 21.0, 800, False, True) == cp.P_MAX_W
    assert cp.wanted_power(20.5, 21.0, 800, False, False) == 1200.0
    assert cp.wanted_power(20.9, 21.0, 600, False, False, free_w=1000.0) == 1000.0
    assert abs(cp.wanted_power(21.2, 21.0, 600, False, False, free_w=1000.0) - 440.0) < 1e-6  # warm: no free floor


def _closed_loop(p_target, minutes=40, hold=1.6, hz=30.0, d_hold=1.6):
    unit = Unit(hold=hold, hz=hz)
    t1, sp, powers = 25.0, None, []
    for k in range(minutes):
        if k % 2 == 0:  # setpoint every 2 min, 0.5 K steps like the real unit
            dist, _ = cp.wanted_distance(p_target, unit.power, unit.hz, 10.0, d_hold, 1.3)
            if dist is not None:
                sp = _round_half(t1 - dist)
        unit.step(t1 - sp)
        powers.append(unit.power)
    return powers


def test_closed_loop_reaches_the_target_without_big_overshoot():
    for target in (800.0, 1000.0, 1200.0):
        p = _closed_loop(target)
        assert max(p) <= target + 200, (target, max(p))
        assert abs(sum(p[-10:]) / 10 - target) <= 120, (target, p[-10:])


def test_closed_loop_with_wrong_hold_distance_still_converges_near():
    p = _closed_loop(1000.0, minutes=60, hold=1.9, d_hold=1.6)
    assert abs(sum(p[-10:]) / 10 - 1000.0) <= 250
