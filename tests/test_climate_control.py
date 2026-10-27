"""Tests for the HA side of the climate control with a minimal fake Home Assistant."""
import asyncio
import importlib.util
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"


# ---- fake homeassistant modules (only what climate_control imports) ----
class Context:
    def __init__(self, parent_id=None):
        self.id = uuid.uuid4().hex
        self.parent_id = parent_id


def _stub_ha():
    ha = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.Context = Context
    core.callback = lambda f: f
    helpers = types.ModuleType("homeassistant.helpers")
    event = types.ModuleType("homeassistant.helpers.event")
    event.async_track_state_change_event = lambda hass, ids, cb: (lambda: None)
    storage = types.ModuleType("homeassistant.helpers.storage")

    class Store:
        def __init__(self, hass, version, key):
            self.data = None
            self.saved = 0

        async def async_load(self):
            return self.data

        async def async_save(self, data):
            self.data = data
            self.saved += 1

        def async_delay_save(self, fn, delay):
            self.data = fn()

    storage.Store = Store
    for name, mod in {"homeassistant": ha, "homeassistant.core": core, "homeassistant.helpers": helpers,
                      "homeassistant.helpers.event": event, "homeassistant.helpers.storage": storage}.items():
        sys.modules.setdefault(name, mod)


def _load(name):
    spec = importlib.util.spec_from_file_location(name, BASE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_stub_ha()
_load("const")
_load("thermal_model")
_load("climate_logic")
cc = _load("climate_control")

UTC = timezone.utc
NOON = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
M = timedelta(minutes=1)
AC = "climate.152832118046533_climate"
ROOM = "sensor.wohnzimmer_temperatur"
ECHO = "sensor.jans_echo_dot_temperatur"
BT = "climate.bt_wohnzimmer_bt_wohnzimmer"


class St:
    def __init__(self, state, attributes=None, ts=NOON, context=None):
        self.state = state
        self.attributes = attributes or {}
        self.last_updated = ts
        self.last_reported = ts
        self.last_changed = ts
        self.context = context or Context()


class Services:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data, blocking=False, context=None):
        self.calls.append((domain, service, dict(data), context))


class States(dict):
    def get(self, eid):
        return dict.get(self, eid)


class Hass:
    def __init__(self):
        self.states = States()
        self.services = Services()


def make(room=20.0, ac="off", ac_attrs=None, outdoor="8", ts=NOON):
    hass = Hass()
    hass.states[ROOM] = St(str(room), ts=ts)
    hass.states[AC] = St(ac, {"temperature": 26, "fan_mode": "silent", "compressor_power": 0,
                              "compressor_frequency": 0, "indoor_temperature": 25, **(ac_attrs or {})}, ts=ts)
    hass.states["sensor.openweathermap_temperature"] = St(outdoor, ts=ts)
    hass.states["input_boolean.anwesnedheit_irgendjemand"] = St("on", ts=ts)
    hass.states["input_boolean.anwesenheit_wohnbereich"] = St("off", ts=ts)
    hass.states[BT] = St("heat", {"hvac_action": "idle"}, ts=ts)
    ctl = cc.ClimateController(hass, "entry1", {})
    asyncio.run(ctl.async_start())
    return hass, ctl


def run(ctl, now, **ext):
    base = {"price": 0.30}
    base.update(ext)
    return asyncio.run(ctl.async_update(now, base))


def services(hass):
    return [(d, s) for d, s, *_ in hass.services.calls]


# ---- pure helpers ----
def test_plausibility_and_fallback():
    assert cc.plausible_temp("-280") is None
    assert cc.plausible_temp("41") is None
    assert cc.plausible_temp("21.5") == 21.5
    assert cc.pick_room(21.0, 20.0) == (21.0, "raumsensor")
    assert cc.pick_room(None, 20.0) == (20.5, "ersatzsensor")
    assert cc.pick_room(None, None) == (None, "keiner")


def test_stale_room_sensor_uses_fallback():
    hass, ctl = make(ts=NOON - timedelta(minutes=30))
    hass.states[ECHO] = St("19.6", ts=NOON)
    run(ctl, NOON)
    assert ctl.info["raumsensor_quelle"] == "ersatzsensor"
    assert ctl.info["raumtemperatur"] == 20.1


def test_manual_mode_for():
    assert cc.manual_mode_for("off", "heat", own=False) == "manuell_an"
    assert cc.manual_mode_for("heat", "off", own=False) == "manuell_aus"
    assert cc.manual_mode_for("heat", "off", own=True) is None
    assert cc.manual_mode_for("unavailable", "heat", own=False) is None
    assert cc.manual_mode_for("heat", "heat", own=False) is None


def test_account_counts_and_resets():
    c = {}
    cc.account(c, datetime(2026, 11, 3, 12), 1000, 1.0, True, 4.0, 0.25, 1.0, 0.85)
    assert c["kwh_day"] == 1.0 and c["kwh_month"] == 1.0
    assert abs(c["oil_l_month"] - 4.0 / 8.5) < 1e-9
    assert abs(c["savings_month"] - (4.0 / 8.5 - 0.25)) < 1e-9
    cc.account(c, datetime(2026, 11, 4, 0, 1), 1000, 0.5, False, 4.0, 0.25, 1.0, 0.85)
    assert c["kwh_day"] == 0.5 and c["kwh_month"] == 1.5
    cc.account(c, datetime(2026, 12, 1, 0, 1), 0, 0.5, False, 4.0, 0.25, 1.0, 0.85)
    assert c["kwh_month"] == 0.0 and c["oil_l_month"] == 0.0


# ---- observe only ----
def test_observe_mode_sends_nothing():
    hass, ctl = make(room=18.0)
    ctl.replace_switch = True
    d = run(ctl, NOON, price=0.20)
    assert d.mode == "ersatz" and d.climate_on and not d.radiators
    assert hass.services.calls == []
    assert ctl.info["steuerung_aktiv"] is False
    assert ctl.model.samples and ctl.model.samples[-1].room == 18.0


def test_samples_every_quarter_hour():
    hass, ctl = make()
    for m in range(0, 46):
        run(ctl, NOON + m * M)
    assert len(ctl.model.samples) == 4


# ---- active control ----
def test_replace_hands_over_and_back():
    hass, ctl = make(room=19.0)
    ctl.control_active = True
    ctl.replace_switch = True
    run(ctl, NOON, price=0.20)
    calls = services(hass)
    assert ("automation", "turn_off") in calls
    assert ("climate", "set_hvac_mode") in calls  # radiators off
    assert ("midea_ac_lan", "set_attribute") in calls  # prompt tone first
    assert ("climate", "set_temperature") in calls  # AC on in heat
    tone = calls.index(("midea_ac_lan", "set_attribute"))
    assert tone < calls.index(("climate", "set_temperature"))
    assert ctl.radiators_taken
    on = [c for c in hass.services.calls if c[1] == "set_temperature"][0]
    assert on[2]["hvac_mode"] == "heat" and on[2]["temperature"] >= 24

    # switching the control off hands the radiators back immediately
    hass.services.calls.clear()
    asyncio.run(ctl.async_set_control_active(False))
    calls = services(hass)
    assert ("automation", "turn_on") in calls and ("automation", "trigger") in calls
    heat = [c for c in hass.services.calls if c[1] == "set_hvac_mode"][0]
    assert heat[2]["hvac_mode"] == "heat" and BT in heat[2]["entity_id"]
    assert not ctl.radiators_taken


def test_post_start_indirect_wind_and_fan():
    hass, ctl = make(room=19.0)
    ctl.control_active = True
    ctl.replace_switch = True
    run(ctl, NOON, price=0.10)  # cheap -> boost fan
    hass.states[AC] = St("heat", {"temperature": ctl.decision.setpoint, "fan_mode": "auto",
                                  "indirect_wind": False, "compressor_power": 1200, "compressor_frequency": 50})
    hass.services.calls.clear()
    run(ctl, NOON + M, price=0.10)
    assert not any(c[2].get("attribute") == "indirect_wind" for c in hass.services.calls)
    run(ctl, NOON + 3 * M, price=0.10)
    attrs = [c[2].get("attribute") for c in hass.services.calls if c[1] == "set_attribute"]
    assert "indirect_wind" in attrs
    fan = [c for c in hass.services.calls if c[1] == "set_fan_mode"]
    assert fan and fan[0][2]["fan_mode"] == "medium"


def test_hvac_retry_is_rate_limited():
    hass, ctl = make(room=19.0)
    ctl.control_active = True
    ctl.replace_switch = True
    run(ctl, NOON, price=0.20)
    n = len([c for c in hass.services.calls if c[1] == "set_temperature"])
    run(ctl, NOON + M, price=0.20)  # AC still reports off
    assert len([c for c in hass.services.calls if c[1] == "set_temperature"]) == n
    run(ctl, NOON + 3 * M, price=0.20)
    assert len([c for c in hass.services.calls if c[1] == "set_temperature"]) == n + 1


def test_remote_control_detected_and_respected():
    hass, ctl = make(room=20.0)
    ctl.control_active = True
    old = St("off")
    new = St("cool", ts=NOON + M)
    ctl._on_ac_change(types.SimpleNamespace(data={"old_state": old, "new_state": new}))
    assert ctl.mode == "manuell_an" and ctl.manual_external
    hass.states[AC] = new
    hass.services.calls.clear()
    run(ctl, NOON + 2 * M)
    assert not any(c[0] == "climate" and c[2].get("entity_id") == AC for c in hass.services.calls)
    # remote off -> manuell_aus, the app keeps it off
    ctl._on_ac_change(types.SimpleNamespace(data={"old_state": new, "new_state": St("off", ts=NOON + 3 * M)}))
    assert ctl.mode == "manuell_aus" and not ctl.manual_external


def test_own_commands_are_not_manual():
    hass, ctl = make(room=19.0)
    ctl.control_active = True
    ctl.replace_switch = True
    run(ctl, NOON, price=0.20)
    own_ctx = [c[3] for c in hass.services.calls if c[1] == "set_temperature"][0]
    ctl._on_ac_change(types.SimpleNamespace(data={"old_state": St("off"),
                                                  "new_state": St("heat", ts=NOON + M, context=own_ctx)}))
    assert ctl.mode == "auto"
    # state written later by the integration's poll (new context) but matching our command
    ctl._on_ac_change(types.SimpleNamespace(data={"old_state": St("off"), "new_state": St("heat", ts=NOON + M)}))
    assert ctl.mode == "auto"


def test_detection_off_in_observe_mode():
    hass, ctl = make()
    ctl._on_ac_change(types.SimpleNamespace(data={"old_state": St("off"), "new_state": St("heat")}))
    assert ctl.mode == "auto"


def test_sensor_failure_gives_radiators_back():
    hass, ctl = make(room=19.0)
    ctl.control_active = True
    ctl.replace_switch = True
    run(ctl, NOON, price=0.20)
    assert ctl.radiators_taken
    hass.states[ROOM] = St("unavailable", ts=NOON + M)
    hass.services.calls.clear()
    d = run(ctl, NOON + M, price=0.20)
    assert d.radiators and not ctl.radiators_taken
    assert ("automation", "turn_on") in services(hass)


def test_persistence_roundtrip():
    hass, ctl = make(room=18.0)
    ctl.set_mode("manuell_aus")
    ctl.set_switch("surplus_switch", True)
    ctl.set_setting("target_living", 22.0)
    run(ctl, NOON)
    ctl.radiators_taken = True
    data = ctl.to_dict()
    ctl2 = cc.ClimateController(hass, "entry1", {})
    ctl2.load_dict(data)
    assert ctl2.mode == "manuell_aus" and ctl2.surplus_switch and ctl2.settings.target_living == 22.0
    assert ctl2.radiators_taken and not ctl2.control_active
    assert len(ctl2.model.samples) == 1
    # a restart in observe mode hands the radiators back
    run(ctl2, NOON + 20 * M)
    assert not ctl2.radiators_taken


def test_power_and_expected_load_for_planner():
    hass, ctl = make(ac="heat", ac_attrs={"compressor_power": 810, "compressor_frequency": 30})
    assert ctl.power_w() == 810
    assert ctl.expected_load_w(NOON + 90 * M, NOON) == 810
    assert ctl.expected_load_w(NOON + 120 * M, NOON) == 0.0
    hass.states[AC] = St("off", {"compressor_power": 300})
    assert ctl.power_w() == 0.0
    hass.states[AC] = St("unavailable")
    assert ctl.power_w() == 0.0


def test_stop_hands_radiators_back_and_saves():
    hass, ctl = make(room=19.0)
    ctl.control_active = True
    ctl.replace_switch = True
    run(ctl, NOON, price=0.20)
    assert ctl.radiators_taken
    asyncio.run(ctl.async_stop())
    assert not ctl.radiators_taken
    assert ctl._store.data["radiators_taken"] is False
