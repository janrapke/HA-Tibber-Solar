"""Heating checks for the nightly agent: living area at night, bath, radiator display, AC, oil.

Reads Home Assistant history (with attributes) and prints facts with numbers, plus suggested
values for the display thresholds input_number.heizung_warm_k_*. It changes nothing.

Usage: python scripts/night_checks.py [--hours 24] [--json out.json]
Needs HA_URL; HA_TOKEN only when no proxy adds the token.
"""
from __future__ import annotations

import argparse
import bisect
import http.client
import json
import os
import statistics
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    LOCAL = ZoneInfo("Europe/Berlin")
except Exception:  # noqa: BLE001  (Windows without tzdata)
    LOCAL = timezone(timedelta(hours=1))

GRID = timedelta(minutes=5)
NIGHT = ("23:40", "07:00")  # living area night phase (ha_packages/heizung_wohnbereich.yaml)
TV = "media_player.lg_webos_tv_up75009lf"
LIVING_BTS = {"Wohnzimmer": "climate.bt_wohnzimmer_bt_wohnzimmer", "Esszimmer": "climate.bt_esszimmer_bt_esszimmer",
              "Küche": "climate.bt_kuche_bt_kuche"}
# room: (Better Thermostats, valve heads, room sensor, threshold slug) - same as the home tiles
ROOMS = {
    "Bad": (["climate.bt_bad"], ["climate.fritz_smart_thermo_302_1"], "sensor.h5179_f06f_temperature", "badezimmer"),
    "Flur": (["climate.bt_flur_bt_flur"], ["climate.heizung_flur"], "sensor.temperatursensor_flur_temperatur", "flur"),
    "Wohnzimmer": (["climate.bt_wohnzimmer_bt_wohnzimmer", "climate.bt_esszimmer_bt_esszimmer"],
                   ["climate.wohnzimmer", "climate.esszimmer"], "sensor.wohnzimmer_temperatur", "wohnzimmer"),
    "Küche": (["climate.bt_kuche_bt_kuche"], ["climate.kuche"], "sensor.temperatursensor_kuche_temperatur", "kuche"),
    "Schlafzimmer": (["climate.bt_schlafzimmer_bt_schlafzimmer"], ["climate.produktion_2"],
                     "sensor.temperature_schalfzimmer_temperatur", "schlafzimmer"),
    "Gästezimmer": (["climate.bt_gastezimmer_bt_gastezimmer"], ["climate.produktion_4"],
                    "sensor.temperature_gastezimmer_temperatur", "gastezimmer"),
    "Kinderzimmer": (["climate.bt_kinderzimmer_bt_kinderzimmer"], ["climate.produktion_1"],
                     "sensor.temperature_kinderzimmer_temperatur", "kinderzimmer"),
}
BURNER = "binary_sensor.olheizung_brenner_lauft"
AC = "climate.152832118046533_climate"
AC_DECISION = "sensor.klima_wohnzimmer_entscheidung"


def api(path: str):
    url = os.environ["HA_URL"].rstrip("/") + path
    req = urllib.request.Request(url)
    if os.environ.get("HA_TOKEN"):
        req.add_header("Authorization", "Bearer " + os.environ["HA_TOKEN"])
    for attempt in range(4):  # Nabu Casa drops long answers now and then
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)
        except (OSError, http.client.HTTPException):
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def history(entities, start, end, attributes=True):
    """History in 6-h pieces: thermostats update every minute and a long answer breaks the connection."""
    out = {e: [] for e in entities}
    a = start
    while a < end:
        b = min(end, a + timedelta(hours=6))
        q = {"filter_entity_id": ",".join(entities), "end_time": b.isoformat()}
        if not attributes:
            q["minimal_response"] = ""
            q["no_attributes"] = ""
        for ser in api(f"/api/history/period/{a.isoformat()}?" + urllib.parse.urlencode(q)):
            if not ser:
                continue
            eid = ser[0]["entity_id"]
            for i, row in enumerate(ser):
                if i == 0 and a > start and out[eid]:
                    continue  # each piece repeats the state at its start
                t = datetime.fromisoformat(row.get("last_updated") or row["last_changed"])
                out[eid].append((t, row["state"], row.get("attributes") or {}))
        a = b
    return out


def at(series, t):
    """Last row at or before t."""
    i = bisect.bisect_right(series, t, key=lambda r: r[0])
    return series[i - 1] if i else None


def grid(start, end):
    t = start
    while t < end:
        yield t
        t += GRID


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def hm(t):
    return t.astimezone(LOCAL).strftime("%H:%M")


def in_night(t):
    s = hm(t)
    return s >= NIGHT[0] or s < NIGHT[1]


def check_living(start, end):
    h = history(list(LIVING_BTS.values()) + [TV, AC_DECISION], start, end)
    issues, max_early = [], {}
    for name, bt in LIVING_BTS.items():
        bad = []
        for t in grid(start, end):
            if not in_night(t):
                continue
            row, tv = at(h[bt], t), at(h[TV], t)
            sp = num(row[2].get("temperature")) if row else None
            tv_on = bool(tv and tv[1] == "on")
            if sp is not None and (sp > 20.5 if tv_on else sp > 19.5):
                bad.append((hm(t), sp, tv_on))
            if sp is not None and "06:00" <= hm(t) < "07:00":
                max_early[name] = max(max_early.get(name, 0), sp)
        if bad:
            issues.append({"raum": name, "punkte_5min": len(bad), "erste": bad[:3]})
    tv_night = sum(1 for t in grid(start, end) if in_night(t) and (r := at(h[TV], t)) and r[1] == "on") * 5
    return {"verstoesse_nacht": issues, "fernseher_nachts_min": tv_night, "max_soll_06_07": max_early}


def check_bath(start, end):
    ents = ["climate.bt_bad", "sensor.bad_solltemperatur", "sensor.h5179_f06f_temperature",
            "sensor.h5179_f06f_humidity", "input_boolean.bad_vorheizen", "input_number.bad_aufheizrate",
            "input_datetime.bad_warm_um", "timer.bad_dusch_boost", "input_boolean.dusche"]
    h = history(ents, start, end)
    mismatch, run = [], 0
    by_reason = {}
    for t in grid(start, end):
        bt, soll = at(h["climate.bt_bad"], t), at(h["sensor.bad_solltemperatur"], t)
        room = at(h["sensor.h5179_f06f_temperature"], t)
        sp, target = (num(bt[2].get("temperature")) if bt else None), (num(soll[1]) if soll else None)
        if sp is not None and target is not None and abs(sp - target) > 0.1:
            run += 5
            if run == 15:  # 15 min and longer
                mismatch.append(hm(t))
        else:
            run = 0
        if soll and room and num(room[1]) is not None:
            by_reason.setdefault(soll[2].get("grund", "?"), []).append(num(room[1]))
    temps = {k: {"min": min(v), "mittel": round(statistics.mean(v), 1), "max": max(v), "stunden": round(len(v) / 12, 1)}
             for k, v in by_reason.items()}
    # radio gaps: neither temperature nor humidity changed for > 20 min (the Govee reports changes only)
    changes = sorted(r[0] for e in ("sensor.h5179_f06f_temperature", "sensor.h5179_f06f_humidity")
                     for r in h[e] if r[0] > start)
    gaps = [(hm(a), round((b - a).total_seconds() / 60)) for a, b in zip(changes, changes[1:])
            if b - a > timedelta(minutes=20)]
    pre = [(hm(r[0]), r[1]) for r in h["input_boolean.bad_vorheizen"] if r[0] > start]
    rate = [num(r[1]) for r in h["input_number.bad_aufheizrate"]]
    warm = [r[1] for r in h["input_datetime.bad_warm_um"] if r[0] > start]
    boosts = [(hm(r[0]), r[1]) for r in h["timer.bad_dusch_boost"] if r[0] > start]
    return {"sollwert_abweichung_ab_15min": mismatch, "temperatur_je_phase": temps,
            "govee_luecken_ueber_20min": gaps, "vorheizen_wechsel": pre,
            "aufheizrate_start_ende": [rate[0], rate[-1]] if rate else None, "warm_um": warm,
            "boost_wechsel": boosts}


def check_display(start, end):
    h = history([BURNER], start, end)
    for bts, valves, sensor, _ in ROOMS.values():  # one request per room: all at once is too big for HA
        h.update(history(bts + valves + [sensor], start, end))
    thresholds = {k: num(api(f"/api/states/input_number.heizung_warm_k_{k}")["state"])
                  for k in (r[3] for r in ROOMS.values())}
    out = {}
    for name, (bts, valves, sensor, slug) in ROOMS.items():
        cold, warm = [], []
        for t in grid(start, end):
            room = at(h[sensor], t)
            r = num(room[1]) if room else None
            if r is None:
                continue
            for v in valves:
                vr = at(h[v], t)
                if not vr:
                    continue
                cur, sp = num(vr[2].get("current_temperature")), num(vr[2].get("temperature"))
                if cur is None:
                    continue
                d = cur - r

                def valve(minutes, open_):
                    """Head's own setpoint above (open) / below (closed) its sensor at every 15 min step."""
                    for m in range(0, minutes + 1, 15):
                        x = at(h[v], t - timedelta(minutes=m))
                        s, c = (num(x[2].get("temperature")), num(x[2].get("current_temperature"))) if x else (None, None)
                        if s is None or c is None or (s > c) != open_:
                            return False
                    return True
                # BT "heating" alone is no proof: while the AC replaces the radiators the heads are held shut
                burner = any((b := at(h[BURNER], t - timedelta(minutes=m))) and b[1] == "on" for m in range(0, 61, 5))
                if valve(30, True) and burner:
                    warm.append(d)
                elif valve(120, False):
                    cold.append(d)
        res = {"aktuell": thresholds.get(slug), "kalt_n": len(cold), "warm_n": len(warm)}
        if cold:
            c95 = sorted(cold)[int(0.95 * (len(cold) - 1))]
            res["kalt_p95"] = round(c95, 1)
            target = c95 + 0.5
            if warm:
                res["warm_median"] = round(statistics.median(warm), 1)
                target = min(target, res["warm_median"] - 0.5)
                res["trennbar"] = c95 < res["warm_median"]
            suggestion = max(1.0, min(6.0, round(target * 2) / 2))
            if not warm and res["aktuell"] is not None:
                suggestion = max(suggestion, res["aktuell"])  # never lower without a warm radiator to compare
            # a closed head can still be warm (it closes because the radiator is warm, pipes in the floor):
            # when cold and warm overlap there is no honest suggestion
            res["vorschlag"] = None if res.get("trennbar") is False else suggestion
        out[name] = res
    return out


def check_ac_oil(start, end):
    h = history([AC, AC_DECISION, BURNER], start, end)
    modes, prev = {}, None
    for r in h[AC_DECISION]:
        if prev:
            modes[prev[1]] = modes.get(prev[1], 0) + (r[0] - prev[0]).total_seconds() / 3600
        prev = (r[0], r[2].get("modus"))
    if prev:
        modes[prev[1]] = modes.get(prev[1], 0) + (end - prev[0]).total_seconds() / 3600
    starts, short, run_start = 0, 0, None
    for r in h[AC]:
        hz = num(r[2].get("compressor_frequency")) or 0
        if hz > 0 and run_start is None:
            run_start, starts = r[0], starts + 1
        elif hz == 0 and run_start is not None:
            short += (r[0] - run_start) < timedelta(minutes=15)
            run_start = None
    b_starts, b_min, on = 0, 0.0, None
    for r in h[BURNER]:
        if r[1] == "on" and on is None:
            on, b_starts = r[0], b_starts + 1
        elif r[1] != "on" and on is not None:
            b_min += (r[0] - on).total_seconds() / 60
            on = None
    return {"klima_modus_stunden": {k: round(v, 1) for k, v in modes.items()}, "kompressor_starts": starts,
            "kompressor_laeufe_unter_15min": short, "brenner_starts": b_starts,
            "brenner_min": round(b_min), "brenner_min_je_start": round(b_min / b_starts, 1) if b_starts else None}


def trends():
    k = api("/api/states/sensor.olheizung_heizkennlinie")["attributes"]
    n = api("/api/states/sensor.klima_wohnzimmer_auskuhlzeit_nacht")["attributes"]
    days = [{x: d.get(x) for x in ("datum", "oel_l", "starts", "brenner_min", "aussen_mittel", "innen_mittel",
                                   "klima_waerme_kwh")} for d in (k.get("heiztage") or [])[-7:]]
    return {"heiztage": days, "oelmodell": {x: k.get(x) for x in ("basis_l_tag", "l_je_gradstunde", "quelle")},
            "auskuehlzeit_h": n.get("verlauf"), "fenster_vergleich": n.get("vergleich_offen_zu")}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=float, default=24)
    p.add_argument("--json")
    a = p.parse_args()
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=a.hours)
    res = {"zeitraum": [start.astimezone(LOCAL).isoformat(), end.astimezone(LOCAL).isoformat()]}
    for key, fn in (("wohnbereich", check_living), ("bad", check_bath), ("anzeige", check_display),
                    ("klima_oel", check_ac_oil)):
        try:
            res[key] = fn(start, end)
        except Exception as err:  # noqa: BLE001  one failing check must not hide the others
            res[key] = {"fehler": repr(err)}
    try:
        res["trend_7_tage"] = trends()
    except Exception as err:  # noqa: BLE001
        res["trend_7_tage"] = {"fehler": repr(err)}
    text = json.dumps(res, ensure_ascii=False, indent=1, default=str)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
