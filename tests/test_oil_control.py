"""OilMonitor with a fake Home Assistant: recorded burner run, tank readings, heating day."""
import asyncio
import csv
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import test_climate_control as tcc  # installs the fake homeassistant modules

tcc._load("oil_model")
oc = tcc._load("oil_control")
LOC = timezone(timedelta(hours=1))
GRID, EXHAUST = "sensor.power_holunderweg_3", "sensor.heizung_abgasrohr_temperatur"


class Coordinator:
    climate = None

    def _inverter_output_sensor(self):
        return "sensor.dtu"


async def _job(fn, *a):
    return fn(*a)


def make(exhaust=None):
    hass = tcc.Hass()
    hass.config = types.SimpleNamespace(path=lambda name: str(Path(__file__).parent / "data" / "no_such_dir" / name))
    hass.async_add_executor_job = _job
    cfg = {"tibber_consumption_sensor": GRID}
    if exhaust:
        cfg["oil_exhaust_sensor"] = exhaust
    mon = oc.OilMonitor(hass, "e1", cfg, Coordinator())
    asyncio.run(mon.async_start())
    return hass, mon


def event(eid, value, t):
    st = types.SimpleNamespace(state=str(value), last_updated=t)
    return types.SimpleNamespace(data={"entity_id": eid, "new_state": st})


def test_recorded_run_counts_oil():
    hass, mon = make(exhaust=EXHAUST)   # old exhaust pipe detection, still works when configured
    last_c = None
    with open(Path(__file__).parent / "data" / "burner_2026-10-27.csv") as f:
        for r in csv.DictReader(f):
            h, m, s = map(int, r["time"].split(":"))
            t = datetime(2026, 10, 27, h, m, s, tzinfo=LOC)
            mon._on_change(event(GRID, r["grid_w"], t))
            mon._on_change(event("sensor.dtu", r["dtu_w"], t))
            if r["abgas_c"] != last_c:
                mon._on_change(event(EXHAUST, r["abgas_c"], t))
                last_c = r["abgas_c"]
    day = mon.days["2026-10-27"]
    assert day.starts == 1 and 5.6 <= day.burner_min <= 6.6
    assert abs(day.oil_l - day.burner_min / 60 * 2.0) < 1e-9
    assert mon.runs_today(datetime(2026, 10, 27, 23, 50, tzinfo=LOC))[0]["quelle"] == "strom"


def test_theta_burner_sensor_counts_runs_and_mutes_exhaust():
    hass, mon = make(exhaust=EXHAUST)
    burner = "binary_sensor.heizung_theta_brenner"
    t0 = datetime(2026, 11, 12, 6, 0, 0, tzinfo=LOC)
    hass.states[burner] = tcc.St("off", ts=t0)
    # grid load: 300 W, burner step +200 W at 06:00:05 (Theta 20 s later), drop at 06:07:05 (Theta 40 s later)
    for s in range(0, 600, 5):
        t = t0 + timedelta(seconds=s)
        w = 500 if 5 <= s < 425 else 300
        mon._on_change(event(GRID, w, t))
        if s == 25:
            hass.states[burner] = tcc.St("on", ts=t)
            mon._on_change(event(burner, "on", t))
        if s == 465:
            hass.states[burner] = tcc.St("off", ts=t)
            mon._on_change(event(burner, "off", t))
        mon._on_change(event(EXHAUST, 40 + (30 if 30 <= s < 500 else 0), t))
    day = mon.days["2026-11-12"]
    assert day.starts == 1
    assert 6.7 <= day.burner_min <= 7.1   # 7.0 min from the load steps; the late Theta frames alone would give 7.3
    assert mon.runs_today(t0)[0]["quelle"] == "theta"


def test_missed_runs_are_imported_once(tmp_path):
    import json
    hass = tcc.Hass()
    hass.config = types.SimpleNamespace(path=lambda name: str(tmp_path / name))
    hass.async_add_executor_job = _job
    t = datetime(2026, 11, 11, 20, 44, 37, tzinfo=LOC)
    runs = [{"start": t.isoformat(), "end": (t + timedelta(minutes=6)).isoformat(), "source": "theta"},
            {"start": (t + timedelta(minutes=2)).isoformat(), "end": (t + timedelta(minutes=4)).isoformat()}]
    (tmp_path / oc.IMPORT_FILE).write_text(json.dumps({"runs": runs}), encoding="utf-8")
    mon = oc.OilMonitor(hass, "e1", {"tibber_consumption_sensor": GRID}, Coordinator())
    asyncio.run(mon.async_start())
    day = mon.days["2026-11-11"]
    assert day.starts == 1 and abs(day.burner_min - 6.0) < 1e-6      # the overlapping second run is skipped
    assert not (tmp_path / oc.IMPORT_FILE).exists() and (tmp_path / (oc.IMPORT_FILE + ".done")).exists()


def test_tank_reading_usable_and_calibration():
    hass, mon = make()
    now = datetime(2026, 10, 28, 9, 0, tzinfo=LOC)
    assert mon.tank_liters() is None and mon.usable_liters() is None
    mon.set_reading(47, now)
    full = mon.tank.liters(47)
    assert abs(mon.tank_liters() - full) < 0.1
    assert abs(mon.usable_liters() - (full - mon.tank.liters(30))) < 0.1
    # 100 L counted, the gauge says 110 L went out -> flow corrected half way (+5 %)
    mon.burned_since_reading = 100.0
    assert abs(mon.tank_liters() - (full - 100)) < 0.1
    mon.set_reading(mon.tank.cm(full - 110), now + timedelta(days=30))
    assert abs(mon.settings.flow_l_h - 2.1) < 0.01 and mon.calibrations[-1]["faktor"] == 1.1
    assert mon.burned_since_reading == 0.0


def test_minute_step_builds_heating_day_and_forecast():
    hass, mon = make()
    hass.states["sensor.wohnzimmer_temperatur"] = tcc.St("21.0")
    hass.states["sensor.temperature_schalfzimmer_temperatur"] = tcc.St("17.0")
    t0 = datetime(2026, 10, 28, 0, 0, tzinfo=LOC)
    mon.set_reading(47, t0)
    for i in range(60):
        asyncio.run(mon.async_update(t0 + timedelta(minutes=i), {"outdoor": 5.0, "ac_efficiency": 3.0, "house_w": 600}))
    day = mon.days["2026-10-28"]
    indoor = (21.0 * 37.4 + 17.0 * 15.9) / (37.4 + 15.9)
    assert abs(day.kh - (indoor - 5.0 - 5.0)) < 0.01 and day.minutes == 60  # 60 one-minute steps, limit = indoor - 5 K
    assert set(mon.forecast) >= {"optimistisch", "neutral", "pessimistisch"}
    d = mon.to_dict()
    hass2, mon2 = make()
    mon2.load_dict(d)
    assert mon2.days["2026-10-28"].kh == day.kh and mon2.reading == mon.reading
