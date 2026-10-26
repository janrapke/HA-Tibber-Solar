"""Cost-optimal battery schedule by dynamic programming over 15-min slots.

Pure Python (no Home Assistant imports) so it can be unit-tested and replayed.

System model (DC-coupled, zero export):
  * Solar charges the battery through the MPPT chargers.
  * When the inverter is enabled, the OpenDTU power limiter covers the house
    load up to `inverter_max_w` from the battery (never exports).
  * When disabled, the house runs from the grid and the battery only charges.
  * Optional grid charger adds energy to the battery from the grid.
  * Energy above the max SoC is curtailed (lost).

The DP minimises grid cost over the horizon minus the value of the energy left
in the battery at the end.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

SLOT_H = 0.25

ACT_HOLD = 0          # inverter off, battery charges from solar only
ACT_DISCHARGE = 1     # inverter on, covers house load
ACT_GRID_CHARGE = 2   # inverter off, grid charger on


@dataclass
class Slot:
    start: datetime
    price: float          # EUR/kWh
    solar_w: float        # expected DC solar power
    load_w: float         # expected house load the inverter may cover


@dataclass
class BatteryParams:
    capacity_wh: float
    min_pct: float
    max_pct: float = 100.0
    efficiency: float = 0.9          # applied on discharge (battery -> AC)
    inverter_max_w: float = 800.0
    grid_charge_w: float = 0.0       # 0 = no grid charger
    grid_charge_eff: float = 0.85
    grid_charge_max_pct: float = 90.0
    grid_charge_margin: float = 0.02  # EUR/kWh a grid charge must at least earn
    switch_penalty: float = 0.01      # EUR per action change (keeps the plan calm)
    steps: int = 100


@dataclass
class PlanStep:
    start: datetime
    action: int
    price: float
    solar_w: float
    load_w: float
    soc_start_pct: float
    soc_end_pct: float
    grid_import_wh: float
    discharge_wh: float
    curtailed_wh: float


@dataclass
class Plan:
    steps: list[PlanStep] = field(default_factory=list)
    cost_eur: float = 0.0
    baseline_cost_eur: float = 0.0  # cost if the battery did nothing

    def action_now(self) -> int | None:
        return self.steps[0].action if self.steps else None

    def curtailed_wh(self, hours: float | None = None) -> float:
        n = len(self.steps) if hours is None else int(hours / SLOT_H)
        return sum(s.curtailed_wh for s in self.steps[:n])


def _transition(e_wh: float, action: int, slot: Slot, p: BatteryParams,
                e_min: float, e_max: float) -> tuple[float, float, float, float]:
    """Return (new_energy_wh, grid_import_wh, discharge_wh, curtailed_wh)."""
    solar_wh = max(0.0, slot.solar_w) * SLOT_H
    load_wh = max(0.0, slot.load_w) * SLOT_H
    e = e_wh + solar_wh
    grid = load_wh
    dis = 0.0
    if action == ACT_DISCHARGE:
        want = min(load_wh, p.inverter_max_w * SLOT_H)
        avail = max(0.0, (e - e_min) * p.efficiency)
        dis = min(want, avail)
        e -= dis / p.efficiency
        grid -= dis
    elif action == ACT_GRID_CHARGE:
        g = p.grid_charge_w * SLOT_H
        cap = p.capacity_wh * p.grid_charge_max_pct / 100.0
        room = max(0.0, cap - e)
        add = min(g * p.grid_charge_eff, room)
        e += add
        grid += add / p.grid_charge_eff if p.grid_charge_eff > 0 else 0.0
    curtailed = 0.0
    if e > e_max:
        curtailed = e - e_max
        e = e_max
    return e, grid, dis, curtailed


def optimize(slots: list[Slot], soc_pct: float, p: BatteryParams,
             allow_grid_charge: bool = False, terminal_price: float | None = None,
             current_action: int = ACT_HOLD) -> Plan:
    """Compute the cost-optimal action sequence for the given slots."""
    if not slots or p.capacity_wh <= 0:
        return Plan()
    n = len(slots)
    steps = max(10, p.steps)
    e_min = p.capacity_wh * p.min_pct / 100.0
    e_max = p.capacity_wh * p.max_pct / 100.0
    de = (e_max - e_min) / steps if e_max > e_min else 1.0

    def pos(e: float) -> float:
        return min(float(steps), max(0.0, (e - e_min) / de))

    def lvl(i: int) -> float:
        return e_min + i * de

    def interp(v: list[float], e: float) -> float:
        # linear interpolation: rounding to the grid would create phantom energy
        # (up to half a step per slot) and make e.g. unprofitable grid charging look good
        x = pos(e)
        j = min(int(x), steps - 1)
        f = x - j
        return v[j] * (1.0 - f) + v[j + 1] * f

    # discharge first: on equal cost the plan uses the battery (the safe default)
    actions = [ACT_DISCHARGE, ACT_HOLD]
    if allow_grid_charge and p.grid_charge_w > 0:
        actions.append(ACT_GRID_CHARGE)

    if terminal_price is None:
        prices = sorted(s.price for s in slots)
        # slightly discounted: energy used inside the horizon is a sure saving
        terminal_price = prices[len(prices) // 2] * 0.95
    # stored energy is worth what it saves later, after discharge losses
    terminal = [-(lvl(i) - e_min) * p.efficiency * terminal_price / 1000.0 for i in range(steps + 1)]

    def step_cost(e: float, a: int, slot: Slot) -> tuple[float, float]:
        e2, grid, _dis, _cur = _transition(e, a, slot, p, e_min, e_max)
        c = slot.price * grid / 1000.0
        if a == ACT_GRID_CHARGE:
            c += p.grid_charge_margin * (grid - slot.load_w * SLOT_H) / 1000.0
        return c, e2

    na = len(actions)
    # values[t][action_index][soc_index]: cost-to-go from slot t, given the action of slot t-1
    values: list[list[list[float]]] = [None] * (n + 1)  # type: ignore[list-item]
    values[n] = [list(terminal) for _ in range(na)]
    for t in range(n - 1, -1, -1):
        slot = slots[t]
        nxt = values[t + 1]
        # cost of each action from each level (independent of the previous action)
        cost = [[0.0] * na for _ in range(steps + 1)]
        for i in range(steps + 1):
            for ai, a in enumerate(actions):
                c, e2 = step_cost(lvl(i), a, slot)
                cost[i][ai] = c + interp(nxt[ai], e2)
        cur = [[0.0] * (steps + 1) for _ in range(na)]
        for pi in range(na):
            for i in range(steps + 1):
                cur[pi][i] = min(cost[i][ai] + (p.switch_penalty if ai != pi else 0.0) for ai in range(na))
        values[t] = cur

    # forward pass with continuous energy: decide each slot exactly, not from the grid
    plan = Plan()
    e = max(e_min, min(e_max, p.capacity_wh * soc_pct / 100.0))
    if p.capacity_wh * soc_pct / 100.0 < e_min:
        e = p.capacity_wh * soc_pct / 100.0  # below reserve: nothing to discharge
    prev = current_action if current_action in actions else ACT_DISCHARGE
    for t, slot in enumerate(slots):
        best = None
        a = actions[0]
        for ai, cand in enumerate(actions):
            c, e2 = step_cost(e, cand, slot)
            c += interp(values[t + 1][ai], e2) + (p.switch_penalty if cand != prev else 0.0)
            if best is None or c < best - 1e-9:
                best, a = c, cand
        e_start = e
        e2, grid, dis, cur_wh = _transition(e, a, slot, p, e_min, e_max)
        if a == ACT_DISCHARGE and dis <= 0.0:
            a = ACT_HOLD
        plan.steps.append(PlanStep(
            start=slot.start, action=a, price=slot.price, solar_w=slot.solar_w, load_w=slot.load_w,
            soc_start_pct=100.0 * e_start / p.capacity_wh, soc_end_pct=100.0 * e2 / p.capacity_wh,
            grid_import_wh=grid, discharge_wh=dis, curtailed_wh=cur_wh,
        ))
        plan.cost_eur += slot.price * grid / 1000.0
        plan.baseline_cost_eur += slot.price * max(0.0, slot.load_w) * SLOT_H / 1000.0
        e = e2
        prev = a
    return plan
