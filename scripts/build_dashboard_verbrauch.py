"""Builds the „Verbrauch“ view (ha_packages/dashboard_verbrauch.yaml) and the device list for the
energy dashboard (ha_packages/energy_device_consumption.json) from one device list.

A new smart plug: add one line to DEVICES, run this script, deploy both files.
"""
import json
import pathlib
import yaml

OUT = pathlib.Path(__file__).resolve().parents[1] / 'ha_packages'

# category -> (label, colour, icon)
CATS = {
    'auto': ('Auto', '#8b5cf6', 'mdi:car-electric'),
    'klima': ('Klima', '#0ea5e9', 'mdi:air-conditioner'),
    'heizung': ('Heizung', '#ef4444', 'mdi:fire'),
    'spuel': ('Spülmaschine', '#3b82f6', 'mdi:dishwasher'),
    'wasch': ('Waschmaschine', '#06b6d4', 'mdi:washing-machine'),
    'licht': ('Licht', '#f59e0b', 'mdi:lightbulb-on-outline'),
    'tv': ('TV & Unterhaltung', '#ec4899', 'mdi:television'),
    'computer': ('Computer', '#6366f1', 'mdi:laptop'),
    'akku': ('Akku laden', '#10b981', 'mdi:battery-charging'),
    'pool': ('Pool & Hydro', '#14b8a6', 'mdi:pool'),
    'klaeren': ('Zu klären', '#a855f7', 'mdi:help-circle-outline'),
}

# name, category, power sensor (W), energy sensor (kWh, for history)
DEVICES = [
    ('Tesla Wallbox', 'auto', 'sensor.tesla_wall_connector_gesamtleistung', 'sensor.tesla_wall_connector_energie'),
    ('Klima Wohnzimmer', 'klima', 'sensor.klima_wohnzimmer_klima_leistung', 'sensor.klima_wohnzimmer_klimastrom_gesamt'),
    # no plug: the burner adds ~300 W while it runs (load step measured 27.10.2026: +295 / -298 W);
    # helpers sensor.olbrenner_leistung (template on binary_sensor.olheizung_brenner_lauft) and
    # sensor.olbrenner_strom (Riemann integral) were created in the UI
    ('Ölheizung Brenner (≈300 W)', 'heizung', 'sensor.olbrenner_leistung', 'sensor.olbrenner_strom'),
    ('Spülmaschine', 'spuel', 'sensor.tasmota_energy_power', 'sensor.tasmota_energy_total'),
    ('Waschmaschine', 'wasch', 'sensor.waschmaschiene_energy_power', 'sensor.miner_energy_total'),
    ('Licht außen 1', 'licht', 'sensor.shellydimmer2_08f9e04436dc_power', 'sensor.shellydimmer2_08f9e04436dc_energy'),
    ('Licht außen 2', 'licht', 'sensor.shellydimmer2_4cebd6edf59d_power', 'sensor.shellydimmer2_4cebd6edf59d_energy'),
    ('Licht Wohnzimmer', 'licht', 'sensor.licht_wohnzimmer_leistung', 'sensor.licht_wohnzimmer_energie_gesamt'),
    ('Licht Bücherregal', 'licht', 'sensor.antela_smart_steckdose_leistung', 'sensor.antela_smart_steckdose_energie_gesamt'),
    # plug formerly on the bunk bed light, since 06.11.2026: TV with Teufel box and everything around it
    ('Fernseher Wohnzimmer', 'tv', 'sensor.kinderzimmer_hochbett_licht_leistung', 'sensor.kinderzimmer_hochbett_licht_energie_gesamt'),
    ('Licht Keller Lasercutter', 'licht', 'sensor.licht_keller_lasercutter_leistung', 'sensor.licht_keller_lasercutter_energie_gesamt'),
    ('Licht LED Keller (?)', 'licht', 'sensor.licht_schrank_esszimmer_leistung', 'sensor.licht_led_keller_energie_gesamt'),
    # no plug: HASS.Agent runs scripts/surface_power.ps1 every 60 s (estimate, 0 away from home / on battery);
    # UI helpers sensor.surface_netzbezug (0 when stale > 3 min) and sensor.surface_strom (kWh)
    ('Surface (geschätzt)', 'computer', 'sensor.surface_netzbezug', 'sensor.surface_strom'),
    ('Akku laden (Netz)', 'akku', 'sensor.shellyplus1pm_e86beaeae1a0_switch_0_power', 'sensor.shellyplus1pm_e86beaeae1a0_switch_0_energy'),
    ('Hydrotower Erdbeere (?)', 'pool', 'sensor.pool_warmepumpe_leistung', 'sensor.pool_warmepumpe_energie_gesamt'),
    ('Hydrotower Salat (?)', 'pool', 'sensor.poolpumpe_leistung', 'sensor.poolpumpe_energie_gesamt'),
    ('Pool-Pumpe (?)', 'pool', 'sensor.steckdose_pool_pumpe_leistung', 'sensor.steckdose_pool_pumpe_energie_gesamt'),
    ('Pool-Pumpe Dach (?)', 'pool', 'sensor.steckdose_pool_pumpe_dach_leistung', 'sensor.steckdose_pool_pumpe_dach_energie_gesamt'),
    ('Pool außen (?)', 'pool', 'sensor.antela_smart_plug_leistung', 'sensor.antela_smart_plug_energie_gesamt'),
    ('Hydo Paprika (?)', 'klaeren', 'sensor.tasmota_energy_power_2', 'sensor.tasmota_energy_total_2'),
    ('Monitor Kamera Keller (?)', 'klaeren', 'sensor.wlan_steckdose_2_leistung', 'sensor.monitor_kamera_keller_energie_gesamt'),
]

# House load of the optimizer: grid - export + inverters (Hoymiles, Ecoflow plug), but WITHOUT wallbox
# and AC (left out on purpose so they do not distort the learned load). Add them back for the real total.
TOTAL = 'sensor.calculated_house_consumption'
ADD_BACK = ['sensor.tesla_wall_connector_gesamtleistung', 'sensor.klima_wohnzimmer_klima_leistung']
# Ecoflow feeds in through this plug: a source, not a consumer (energy dashboard: solar)
ECOFLOW_ENERGY = 'sensor.antela_smart_steckdose_4_energie_gesamt'
BASE = 'sensor.grundlast'
PHASES = [('L1', 'sensor.tibber_locl_0100240700ff'), ('L2', 'sensor.tibber_locl_0100380700ff'), ('L3', 'sensor.tibber_locl_01004c0700ff')]

dev_js = json.dumps([{'n': n, 'c': c, 'p': p} for n, c, p, _ in DEVICES], ensure_ascii=False)
cat_js = json.dumps({k: {'l': v[0], 'col': v[1], 'i': v[2]} for k, v in CATS.items()}, ensure_ascii=False)
HELP = """
  const n = (id) => { const x = parseFloat(states[id]?.state); return isNaN(x) ? null : x; };
  const W = (v) => v === null ? '–' : Math.abs(v) >= 1000 ? (v / 1000).toLocaleString('de-DE', {maximumFractionDigits: 2}) + ' kW' : Math.round(v) + ' W';
  const DEV = %s, CAT = %s;
""" % (dev_js, cat_js)

BTN = {'show_name': False, 'show_icon': False, 'show_state': False, 'triggers_update': 'all',
       'styles': {'card': [{'padding': '16px'}, {'border-radius': '18px'}],
                  'grid': [{'grid-template-areas': '"c"'}, {'grid-template-columns': '1fr'}],
                  'custom_fields': {'c': [{'white-space': 'normal'}, {'text-align': 'left'}, {'width': '100%'}]}}}

now_card = dict(BTN, type='custom:button-card', entity=TOTAL, grid_options={'columns': 'full'},
                tap_action={'action': 'more-info'}, custom_fields={'c': '[[[' + HELP + r"""
  const total = Math.max(0, (n('""" + TOTAL + r"""') ?? 0) + """ + json.dumps(ADD_BACK) + r""".reduce((a, id) => a + Math.max(0, n(id) ?? 0), 0));
  const base = n('""" + BASE + r"""');
  const devs = DEV.map(d => ({...d, w: n(d.p), off: ['unavailable', 'unknown', undefined].includes(states[d.p]?.state)}));
  const byCat = {};
  devs.forEach(d => { if (d.w > 0) byCat[d.c] = (byCat[d.c] || 0) + d.w; });
  const measured = Object.values(byCat).reduce((a, b) => a + b, 0);
  const rest = Math.max(0, total - measured);
  const baseShare = base === null ? 0 : Math.min(rest, base);
  const segs = Object.entries(byCat).sort((a, b) => b[1] - a[1]).map(([c, w]) => [CAT[c].l, w, CAT[c].col])
    .concat([['Grundlast', baseShare, '#64748b'], ['Nicht zugeordnet', rest - baseShare, 'rgba(127,127,127,.35)']]);
  const sum = Math.max(total, measured, 1);
  const bar = segs.filter(s => s[1] > 0).map(s => `<div title="${s[0]}: ${W(s[1])}" style="width:${s[1] / sum * 100}%;background:${s[2]}"></div>`).join('');
  const legend = segs.filter(s => s[1] > 0.5).map(s => `<span style="display:inline-flex;align-items:center;gap:5px;margin:0 10px 4px 0;font-size:12px">
      <span style="width:10px;height:10px;border-radius:3px;background:${s[2]}"></span>${s[0]} <b>${W(s[1])}</b></span>`).join('');
  const run = devs.filter(d => d.w > 1).sort((a, b) => b.w - a.w);
  const rows = run.map(d => { const c = CAT[d.c]; return `<div style="display:flex;align-items:center;gap:10px;padding:6px 0">
      <div style="width:30px;height:30px;flex:none;border-radius:10px;display:flex;align-items:center;justify-content:center;background:${c.col}22">
        <ha-icon icon="${c.i}" style="--mdc-icon-size:17px;color:${c.col}"></ha-icon></div>
      <div style="flex:1;min-width:0"><div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${d.n}</div>
        <div style="height:4px;border-radius:2px;background:rgba(127,127,127,.12);margin-top:4px;overflow:hidden">
          <div style="height:100%;width:${Math.min(100, d.w / sum * 100)}%;background:${c.col}"></div></div></div>
      <div style="width:70px;text-align:right;font-size:13px;font-weight:600">${W(d.w)}</div></div>`; }).join('');
  const offline = devs.filter(d => d.off).length;
  return `<div style="display:flex;justify-content:space-between;align-items:flex-end">
      <div><div style="font-size:12px;color:var(--secondary-text-color);text-transform:uppercase;letter-spacing:.06em">Haus verbraucht gerade</div>
        <div style="font-size:40px;font-weight:300;line-height:1.1">${W(total)}</div></div>
      <div style="text-align:right;font-size:12px;color:var(--secondary-text-color)">davon erfasst<br><b style="font-size:18px;color:var(--primary-text-color)">${Math.round(measured / Math.max(total, 1) * 100)} %</b></div></div>
    <div style="display:flex;height:18px;border-radius:9px;overflow:hidden;margin:14px 0 10px;background:rgba(127,127,127,.12)">${bar}</div>
    <div>${legend}</div>
    <div style="font-size:12px;color:var(--secondary-text-color);margin:12px 0 2px">Was gerade Strom zieht</div>
    ${rows || '<div style="font-size:13px;padding:6px 0">Kein erfasstes Gerät über 1 W.</div>'}
    <div style="font-size:11px;color:var(--secondary-text-color);margin-top:8px">${DEV.length} Messstellen · ${offline} gerade offline · Rest = Grundlast und nicht erfasste Geräte</div>`;
]]]"""})

base_card = dict(BTN, type='custom:button-card', entity=BASE, grid_options={'columns': 'full'},
                 tap_action={'action': 'more-info'}, custom_fields={'c': r"""[[[
  const n = (id) => { const x = parseFloat(states[id]?.state); return isNaN(x) ? null : x; };
  const base = n('""" + BASE + r"""');
  const cost = n('sensor.monthly_cost_holunderweg_3'), kwh = n('sensor.monthly_net_consumption_holunderweg_3');
  const ct = cost && kwh ? cost / kwh : 0.35;
  const year = base === null ? null : base * 8.76;
  const f = (v, d) => v === null ? '–' : v.toLocaleString('de-DE', {minimumFractionDigits: d, maximumFractionDigits: d});
  const ph = """ + json.dumps(PHASES) + r""".map(([l, id]) => [l, n(id) ?? 0]);
  const pm = Math.max(...ph.map(p => p[1]), 1);
  const phases = ph.map(([l, w]) => `<div style="display:grid;grid-template-columns:24px 1fr 60px;gap:8px;align-items:center;margin:4px 0">
      <b style="font-size:12px">${l}</b><div style="height:8px;border-radius:4px;background:rgba(127,127,127,.12);overflow:hidden">
      <div style="height:100%;width:${w / pm * 100}%;background:#64748b"></div></div><span style="text-align:right;font-size:12px">${Math.round(w)} W</span></div>`).join('');
  return `<div style="display:flex;gap:14px;align-items:center">
      <div style="width:48px;height:48px;border-radius:15px;display:flex;align-items:center;justify-content:center;background:#64748b26">
        <ha-icon icon="mdi:sleep" style="--mdc-icon-size:26px;color:#64748b"></ha-icon></div>
      <div style="flex:1"><div style="font-size:12px;color:var(--secondary-text-color);text-transform:uppercase;letter-spacing:.06em">Grundlast</div>
        <div style="font-size:32px;font-weight:300;line-height:1.1">${f(base, 0)} W</div></div>
      <div style="text-align:right"><div style="font-size:18px;font-weight:600">${f(year, 0)} kWh</div>
        <div style="font-size:12px;color:var(--secondary-text-color)">≈ ${f(year * ct, 0)} € im Jahr</div></div></div>
    <div style="font-size:13px;line-height:1.4;margin-top:10px">So viel läuft immer – auch nachts, wenn niemand etwas tut (unterste 3 % der letzten 7 Tage).
      Typisch: Router, Kühlschrank, Kameras und Rekorder, Heizungspumpe, Geräte im Standby. Jede neue Mess-Steckdose an einem Dauerläufer macht diesen Block kleiner.</div>
    <div style="font-size:11px;color:var(--secondary-text-color);margin-top:4px">Rechenpreis Ø ${f(ct * 100, 1)} ct/kWh (Tibber diesen Monat)</div>
    <div style="font-size:12px;color:var(--secondary-text-color);margin:12px 0 4px">Last je Phase jetzt – hilft beim Suchen großer Dauerverbraucher</div>${phases}`;
]]]"""})

COLORS = {k: v[1] for k, v in CATS.items()}
stack = {'type': 'custom:apexcharts-card', 'grid_options': {'columns': 'full'}, 'graph_span': '24h', 'stacked': True,
         'header': {'show': True, 'title': 'Erfasste Geräte 24 h (gestapelt)', 'show_states': False},
         'apex_config': {'chart': {'height': 240, 'toolbar': {'show': False}}, 'grid': {'strokeDashArray': 4},
                         'legend': {'show': False}, 'stroke': {'width': 1}, 'yaxis': {'decimalsInFloat': 0}},
         'series': [{'entity': p, 'name': n, 'color': COLORS[c], 'type': 'area', 'opacity': 0.55, 'float_precision': 0,
                     'group_by': {'func': 'avg', 'duration': '10min', 'fill': 'zero'}}
                    for n, c, p, _ in DEVICES if c not in ('pool', 'klaeren')]}
week = {'type': 'custom:apexcharts-card', 'grid_options': {'columns': 'full'}, 'graph_span': '7d',
        'header': {'show': True, 'title': 'Haus ohne Auto und Klima, 7 Tage', 'show_states': True, 'colorize_states': True},
        'apex_config': {'chart': {'height': 200, 'toolbar': {'show': False}}, 'grid': {'strokeDashArray': 4}, 'legend': {'show': False}},
        'series': [{'entity': TOTAL, 'name': 'Haus', 'color': '#3b82f6', 'type': 'area', 'opacity': 0.15, 'stroke_width': 1,
                    'float_precision': 0, 'group_by': {'func': 'avg', 'duration': '30min'}},
                   {'entity': BASE, 'name': 'Grundlast', 'color': '#64748b', 'stroke_width': 2, 'float_precision': 0,
                    'group_by': {'func': 'last', 'duration': '1h'}}]}

view = {
    'title': 'Verbrauch', 'path': 'verbrauch', 'icon': 'mdi:chart-donut', 'type': 'sections', 'max_columns': 3,
    'dense_section_placement': True,
    'sections': [
        {'type': 'grid', 'cards': [{'type': 'heading', 'heading': 'Jetzt', 'icon': 'mdi:flash'}, now_card, stack]},
        {'type': 'grid', 'cards': [{'type': 'heading', 'heading': 'Grundlast', 'icon': 'mdi:sleep'}, base_card, week]},
        {'type': 'grid', 'cards': [
            {'type': 'heading', 'heading': 'Wer verbraucht wie viel', 'icon': 'mdi:chart-bar'},
            {'type': 'energy-date-selection', 'grid_options': {'columns': 'full'}},
            {'type': 'energy-sankey', 'grid_options': {'columns': 'full'}},
            {'type': 'energy-devices-graph', 'grid_options': {'columns': 'full'}},
            {'type': 'energy-devices-detail-graph', 'grid_options': {'columns': 'full'}},
        ]},
    ],
}

hdr = '# GENERIERT von scripts/build_dashboard_verbrauch.py – dort Geräte ergänzen, nicht hier.\n'
(OUT / 'dashboard_verbrauch.yaml').write_text(hdr + yaml.safe_dump(view, allow_unicode=True, sort_keys=False, width=200), encoding='utf-8')
(OUT / 'energy_device_consumption.json').write_text(json.dumps(
    [{'stat_consumption': e, 'name': n} for n, c, _, e in DEVICES], ensure_ascii=False, indent=1), encoding='utf-8')
print('ok', len(DEVICES), 'devices')
