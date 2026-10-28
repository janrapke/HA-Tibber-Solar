"""Best delay-start timer: whole hours, known prices only, curtailed solar is free."""
import importlib.util
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"
spec = importlib.util.spec_from_file_location("appliance_timer", BASE / "appliance_timer.py")
at = importlib.util.module_from_spec(spec)
sys.modules["appliance_timer"] = at
spec.loader.exec_module(at)

UTC = timezone.utc
LOC = timezone(timedelta(hours=1))


def slots(prices, start=datetime(2026, 10, 28, 19, 0, tzinfo=UTC), free=None):
    return [at.PriceSlot(start + i * at.SLOT, p, (free or {}).get(i, 0.0)) for i, p in enumerate(prices)]


@dataclass
class Prog:
    name: str
    power_profile: list
    run_count: int
    probability_score: float = 0.0

    @property
    def total_wh(self):
        return sum(self.power_profile) / 60.0


def test_run_cost_quarter_prices():
    s = slots([0.40] * 4 + [0.20] * 4)
    profile = [1200.0] * 60  # 1.2 kWh in one hour
    assert abs(at.run_cost(profile, s[0].start, s) - 0.48) < 1e-9
    assert abs(at.run_cost(profile, s[0].start + timedelta(minutes=30), s) - 0.36) < 1e-9
    assert at.run_cost(profile, s[0].start + timedelta(hours=2), s) is None  # beyond known prices


def test_curtailed_solar_is_free():
    s = slots([0.30] * 4, free={0: 2000.0, 1: 2000.0})
    assert abs(at.run_cost([1000.0] * 60, s[0].start, s) - 0.15) < 1e-9


def test_best_timer_picks_cheapest_whole_hour_in_local_time():
    # 20:00 local = 19:00 UTC; cheap from 01:00 to 04:00 local
    prices = [0.45] * 20 + [0.20] * 12 + [0.40] * 60
    s = slots(prices)
    now = datetime(2026, 10, 28, 20, 0, tzinfo=LOC)
    best = at.best_timer([1000.0] * 120, now, s)
    assert best["timer_h"] == 5 and best["start"].strftime("%H:%M") == "01:00"
    assert abs(best["kosten_eur"] - 0.40) < 1e-6 and abs(best["kosten_sofort_eur"] - 0.90) < 1e-6
    assert best["fertig_in_h"] == 7.0 and best["ct_kwh"] == 20.0
    # 92 quarters = 23 h of prices, a 2 h run fits up to a 21 h delay
    assert best["stunden"][-1]["h"] == 21


def test_best_timer_prefers_earlier_on_tie_and_none_without_prices():
    s = slots([0.30] * 96)
    now = datetime(2026, 10, 28, 20, 0, tzinfo=LOC)
    assert at.best_timer([500.0] * 60, now, s)["timer_h"] == 0
    assert at.best_timer([500.0] * 60, now, []) is None


def test_typical_program_ignores_noise_and_uses_run_count():
    progs = [Prog("Programm 1", [280.0] * 143, 36, 0.17),
             Prog("Programm 2", [980.0] * 53, 8, 1.37),
             Prog("Programm 6", [2000.0] * 6, 1, 1.0)]  # short burst with a high score
    assert at.typical_program(progs).name == "Programm 1"
    assert at.typical_program([Prog("x", [5.0] * 30, 9)]) is None
