"""Decision logic for the living room heat pump (split AC).

Pure Python (no Home Assistant imports). `decide()` maps the current inputs and
the remembered state to one Decision: what the AC and the radiators should do
and why. The state is updated as if the decision was carried out, so the
"observe only" mode shows exactly what the control would do.

Priorities, most important first:
1. Safety      - no valid room temperature, room < 17 °C or app disabled:
                 radiators get the heating back.
2. Manual      - mode select manuell_an / manuell_aus.
3. Replace     - heat with the AC instead of oil while it is cheaper.
4. Surplus     - battery about to curtail or nearly full: heat/cool with solar.
5. Automatic   - cooling above 24.5 °C, heating below 19 °C (like the YAML).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

try:
    from .thermal_model import break_even_price, oil_cost_per_kwh_heat
except ImportError:  # loaded standalone by the tests
    from thermal_model import break_even_price, oil_cost_per_kwh_heat

MODE_AUTO = "auto"
MODE_MANUAL_ON = "manuell_an"
MODE_MANUAL_OFF = "manuell_aus"
MODES = [MODE_AUTO, MODE_MANUAL_ON, MODE_MANUAL_OFF]

FAN_NORMAL = "silent"
FAN_BOOST = "medium"

SAFETY_MIN_ROOM = 17.0
COOL_OUTDOOR = 18.0  # above: cool instead of heat (manual / surplus)
COOL_TARGET = 24.0
COOL_ON, COOL_OFF = 24.5, 23.2
HEAT_ON, HEAT_OFF = 19.0, 21.5
DAY_START_H, DAY_END_H = 10, 22
NIGHT_END_H = 8
MIN_RUN = timedelta(minutes=30)
LOCK_AFTER_OFF = timedelta(hours=1)
REPLACE_MIN = timedelta(hours=2)
CMD_INTERVAL = timedelta(minutes=5)
FAN_INTERVAL = timedelta(minutes=15)
SURPLUS_CURTAIL_WH = 300.0
SURPLUS_SOC_HYST = 5.0
SURPLUS_DELTA_K = 1.0
BOOST_BELOW_TARGET_K = 1.5
KI = 0.5  # K setpoint per K room error and hour
I_MAX = 2.0
SP_MIN, SP_MAX = 16.0, 30.0


@dataclass
class Settings:
    target_living: float = 21.0
    target_home: float = 20.0
    target_night: float = 19.0
    target_away: float = 18.0
    oil_eur_l: float = 1.00
    boiler_eff_pct: float = 85.0
    replace_max_outdoor: float = 16.0
    cool_max_price: float = 0.30
    surplus_soc: float = 90.0
    boost_price: float = 0.15


@dataclass
class Inputs:
    now: datetime  # local time
    mode: str = MODE_AUTO
    app_enabled: bool = True
    replace_switch: bool = False
    surplus_switch: bool = False
    room: float | None = None  # already plausibility-checked, fallback applied
    outdoor: float | None = None
    price: float | None = None  # EUR/kWh now
    home: bool = True
    in_living: bool = False
    soc: float | None = None
    curtailed_wh_2h: float = 0.0
    grid_charging: bool = False
    efficiency: float = 3.5  # thermal model at current outdoor temperature
    offset_heat: float = 5.0
    offset_cool: float = 0.0
    settings: Settings = field(default_factory=Settings)


@dataclass
class State:
    climate_on: bool = False
    hvac: str = "off"
    reason_mode: str = "aus"
    on_since: datetime | None = None
    off_since: datetime | None = None
    replace_active: bool = False
    replace_changed: datetime | None = None
    surplus_active: bool = False
    integral: float = 0.0
    last_update: datetime | None = None
    setpoint: float | None = None
    setpoint_sent: datetime | None = None
    fan: str | None = None
    fan_changed: datetime | None = None

    def to_dict(self) -> dict:
        return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, d: dict | None) -> "State":
        st = cls()
        for k, v in (d or {}).items():
            if not hasattr(st, k):
                continue
            if k in _DT_FIELDS and isinstance(v, str):
                v = datetime.fromisoformat(v)
            setattr(st, k, v)
        return st


_DT_FIELDS = {"on_since", "off_since", "replace_changed", "last_update", "setpoint_sent", "fan_changed"}


@dataclass
class Decision:
    mode: str  # sicherheit / manuell_an / manuell_aus / ersatz / ueberschuss / heizen / kuehlen / nachlauf / aus / keine_steuerung
    reason: str
    climate_on: bool | None  # None = do not touch the AC
    hvac: str = "off"
    setpoint: float | None = None
    fan: str | None = None
    radiators: bool = True  # True = radiators heat as usual
    send_hvac: bool = False  # AC has to be switched on/off or change mode
    send_setpoint: bool = False
    send_fan: bool = False
    target_room: float | None = None
    break_even: float | None = None
    cost_ac: float | None = None  # EUR per kWh heat
    cost_oil: float | None = None


def zone_target(i: Inputs) -> float:
    s = i.settings
    if i.in_living:
        return s.target_living
    if i.home and i.now.hour < NIGHT_END_H:
        return s.target_night
    if i.home:
        return s.target_home
    return s.target_away


def _round_half(x: float) -> float:
    return round(x * 2) / 2


def decide(i: Inputs, st: State) -> Decision:
    s = i.settings
    now = i.now
    oil = oil_cost_per_kwh_heat(s.oil_eur_l, s.boiler_eff_pct / 100.0)
    be = break_even_price(oil, i.efficiency)
    cost_ac = i.price / i.efficiency if i.price is not None and i.efficiency > 0 else None
    target = zone_target(i)
    daytime = DAY_START_H <= now.hour < DAY_END_H
    dt_h = 0.0 if st.last_update is None else max(0.0, min(0.25, (now - st.last_update).total_seconds() / 3600))
    st.last_update = now

    def finish(d: Decision) -> Decision:
        d.target_room = d.target_room if d.target_room is not None else target
        d.break_even, d.cost_ac, d.cost_oil = round(be, 4), (round(cost_ac, 4) if cost_ac is not None else None), round(oil, 4)
        return d

    # 1. safety: app disabled -> hand everything back, leave the AC alone
    if not i.app_enabled:
        _end_replace(st, now)
        st.surplus_active = False
        return finish(Decision("keine_steuerung", "App deaktiviert – Heizkörper heizen normal, Klima wird nicht gesteuert",
                               None, radiators=True))

    sensor_ok = i.room is not None
    safety_note = ""
    if not sensor_ok:
        safety_note = "Raumsensor ungültig – Heizkörper heizen. "
    elif i.room < SAFETY_MIN_ROOM:
        safety_note = f"Raum {i.room:.1f} °C unter {SAFETY_MIN_ROOM:.0f} °C – Heizkörper heizen. "
    if safety_note:
        _end_replace(st, now)

    # 2. manual
    if i.mode == MODE_MANUAL_OFF:
        _end_replace(st, now)
        st.surplus_active = False
        return finish(_apply(i, st, Decision("manuell_aus", safety_note + "Manuell aus", False), dt_h, force=True))
    if i.mode == MODE_MANUAL_ON:
        _end_replace(st, now)
        st.surplus_active = False
        cool = i.outdoor is not None and i.outdoor > COOL_OUTDOOR
        d = Decision("manuell_an", safety_note + ("Manuell an (Kühlen)" if cool else "Manuell an (Heizen)"), True,
                     hvac="cool" if cool else "heat", target_room=COOL_TARGET if cool else target)
        return finish(_apply(i, st, d, dt_h, force=True))

    if not sensor_ok:
        st.surplus_active = False
        return finish(_apply(i, st, Decision("sicherheit", safety_note + "Klima aus", False), dt_h, force=True))

    # 3. replace heating
    replace_ok = (i.replace_switch and i.outdoor is not None and i.outdoor < s.replace_max_outdoor
                  and i.price is not None and i.price < be and not safety_note)
    replace_why = _replace_reason(i, be, safety_note)
    hold = st.replace_changed is not None and now - st.replace_changed < REPLACE_MIN
    if st.replace_active and not replace_ok:
        if not i.replace_switch or safety_note or not hold:
            _end_replace(st, now)
        else:
            replace_why = "Mindestdauer 2 h läuft noch – " + replace_why
    elif not st.replace_active and replace_ok:
        if hold:
            replace_why = "Wechsel frühestens 2 h nach dem letzten – noch nicht ersetzen"
        else:
            st.replace_active = True
            st.replace_changed = now

    # 4. surplus
    soc = i.soc if i.soc is not None else 0.0
    trigger = i.curtailed_wh_2h > SURPLUS_CURTAIL_WH or soc >= s.surplus_soc
    keep = i.curtailed_wh_2h > SURPLUS_CURTAIL_WH or soc >= s.surplus_soc - SURPLUS_SOC_HYST
    if not i.surplus_switch or i.grid_charging or not daytime:
        st.surplus_active = False
    elif st.surplus_active:
        st.surplus_active = keep
    else:
        st.surplus_active = trigger
    surplus_why = (f"Überschuss (Abregelung {i.curtailed_wh_2h:.0f} Wh in 2 h, Akku {soc:.0f} %)"
                   if st.surplus_active else "")

    if st.replace_active:
        t = target + (SURPLUS_DELTA_K if st.surplus_active else 0.0)
        why = f"Heizung ersetzen: {i.price:.3f} €/kWh < Grenze {be:.3f} €/kWh" if i.price is not None else "Heizung ersetzen"
        if not replace_ok:
            why = replace_why
        if st.surplus_active:
            why += f", {surplus_why} → +1 K"
        d = Decision("ersatz", why, True, hvac="heat", radiators=False, target_room=t)
        return finish(_apply(i, st, d, dt_h, bypass_lock=True))

    cool_outside = i.outdoor is not None and i.outdoor > COOL_OUTDOOR
    if st.surplus_active:
        if cool_outside and i.room > COOL_TARGET - SURPLUS_DELTA_K:
            d = Decision("ueberschuss", surplus_why + " → Kühlen", True, hvac="cool",
                         target_room=COOL_TARGET - SURPLUS_DELTA_K)
            return finish(_apply(i, st, d, dt_h, note=safety_note))
        if not cool_outside and i.room < target + SURPLUS_DELTA_K:
            d = Decision("ueberschuss", surplus_why + " → Heizen", True, hvac="heat",
                         target_room=target + SURPLUS_DELTA_K)
            return finish(_apply(i, st, d, dt_h, note=safety_note))

    # 5. automatic (like the YAML)
    forced_off = ""
    if not daytime:
        forced_off = "außerhalb 10–22 Uhr"
    elif not i.home:
        forced_off = "niemand zuhause"
    cur = st.hvac if st.climate_on and st.reason_mode in ("heizen", "kuehlen") else None
    price = i.price if i.price is not None else 99.0
    if forced_off:
        why = f"Automatik: {forced_off}"
        if replace_why and i.replace_switch:
            why += f"; {replace_why}"
        return finish(_apply(i, st, Decision("aus", safety_note + why, False), dt_h, force=True))
    if cur == "cool":
        if i.room < COOL_OFF:
            return finish(_apply(i, st, Decision("aus", f"Kühlen fertig ({i.room:.1f} °C < {COOL_OFF} °C)", False), dt_h))
        return finish(_apply(i, st, Decision("kuehlen", f"Kühlen bis {COOL_OFF} °C", True, hvac="cool",
                                             target_room=COOL_TARGET), dt_h))
    if cur == "heat":
        if i.room > HEAT_OFF:
            return finish(_apply(i, st, Decision("aus", f"Heizen fertig ({i.room:.1f} °C > {HEAT_OFF} °C)", False), dt_h))
        return finish(_apply(i, st, Decision("heizen", safety_note + f"Heizen bis {HEAT_OFF} °C", True, hvac="heat"), dt_h))
    if i.room > COOL_ON:
        if price <= s.cool_max_price:
            return finish(_apply(i, st, Decision("kuehlen", f"Raum {i.room:.1f} °C > {COOL_ON} °C, Preis {price:.3f} €/kWh ok",
                                                 True, hvac="cool", target_room=COOL_TARGET), dt_h))
        return finish(_apply(i, st, Decision("aus", f"Zu warm, aber Strom zu teuer ({price:.3f} > {s.cool_max_price:.2f} €/kWh)",
                                             False), dt_h))
    if i.room < HEAT_ON:
        if price < be:
            return finish(_apply(i, st, Decision("heizen", safety_note + f"Raum {i.room:.1f} °C < {HEAT_ON} °C, Klima günstiger als Öl",
                                                 True, hvac="heat"), dt_h))
        return finish(_apply(i, st, Decision("aus", safety_note + f"Zu kalt, aber Öl günstiger (Preis {price:.3f} ≥ Grenze {be:.3f} €/kWh)",
                                             False), dt_h))
    why = "Nichts zu tun"
    if i.replace_switch and replace_why:
        why += f"; {replace_why}"
    return finish(_apply(i, st, Decision("aus", safety_note + why, False), dt_h))


def _replace_reason(i: Inputs, be: float, safety_note: str) -> str:
    s = i.settings
    if not i.replace_switch:
        return ""
    if safety_note:
        return "Heizung ersetzen pausiert (Sicherheit)"
    if i.outdoor is None:
        return "Heizung ersetzen: Außentemperatur unbekannt"
    if i.outdoor >= s.replace_max_outdoor:
        return f"Heizung ersetzen: außen {i.outdoor:.1f} °C ≥ {s.replace_max_outdoor:.0f} °C"
    if i.price is None:
        return "Heizung ersetzen: Strompreis unbekannt"
    if i.price >= be:
        return f"Heizung ersetzen: Öl günstiger (Preis {i.price:.3f} ≥ Grenze {be:.3f} €/kWh)"
    return ""


def _end_replace(st: State, now: datetime) -> None:
    if st.replace_active:
        st.replace_active = False
        st.replace_changed = now


def _apply(i: Inputs, st: State, d: Decision, dt_h: float, force: bool = False,
           bypass_lock: bool = False, note: str = "") -> Decision:
    """Min. runtime / lock, setpoint regulation, fan and command rate limits."""
    now = i.now
    if note and not d.reason.startswith(note):
        d.reason = note + d.reason
    if d.climate_on is False and st.climate_on and not force and st.on_since and now - st.on_since < MIN_RUN:
        d.climate_on, d.hvac = True, st.hvac
        d.mode = st.reason_mode if st.reason_mode != "ersatz" or st.replace_active else "nachlauf"
        d.reason += " – Mindestlaufzeit 30 min, läuft weiter"
    elif d.climate_on and not st.climate_on and not bypass_lock and d.mode not in ("manuell_an",) \
            and st.off_since and now - st.off_since < LOCK_AFTER_OFF:
        d.climate_on, d.hvac = False, "off"
        d.reason += " – Sperre 1 h nach Abschalten"
        d.mode = "aus" if d.mode not in ("ersatz",) else d.mode

    if d.climate_on:
        if d.hvac != st.hvac or not st.climate_on:
            st.integral = 0.0
            d.send_hvac = True
            if not st.climate_on:
                st.on_since = now
        if d.target_room is None:
            d.target_room = zone_target(i) if d.hvac == "heat" else COOL_TARGET
        offset = i.offset_heat if d.hvac == "heat" else i.offset_cool
        if i.room is not None:
            st.integral = max(-I_MAX, min(I_MAX, st.integral + KI * (d.target_room - i.room) * dt_h))
        sp = max(SP_MIN, min(SP_MAX, _round_half(d.target_room + offset + st.integral)))
        d.setpoint = sp
        due = st.setpoint_sent is None or now - st.setpoint_sent >= CMD_INTERVAL
        if d.send_hvac or (sp != st.setpoint and due):
            d.send_setpoint = True
            st.setpoint = sp
            st.setpoint_sent = now
        else:
            d.setpoint = st.setpoint if st.setpoint is not None else sp
        fan = FAN_NORMAL
        cheap = i.price is not None and i.price < i.settings.boost_price
        cold = d.hvac == "heat" and i.room is not None and i.room < d.target_room - BOOST_BELOW_TARGET_K
        if st.surplus_active or cheap or cold:
            fan = FAN_BOOST
        fan_due = st.fan_changed is None or now - st.fan_changed >= FAN_INTERVAL
        if d.send_hvac or (fan != st.fan and fan_due):
            d.send_fan = fan != st.fan or d.send_hvac
            st.fan = fan
            st.fan_changed = now
        d.fan = st.fan
    else:
        d.hvac = "off"
        if st.climate_on:
            d.send_hvac = True
            st.off_since = now
        st.integral = 0.0
        st.setpoint = None
        st.fan = None
    st.climate_on = bool(d.climate_on)
    st.hvac = d.hvac if d.climate_on else "off"
    st.reason_mode = d.mode
    if st.replace_active:
        d.radiators = False
    return d
