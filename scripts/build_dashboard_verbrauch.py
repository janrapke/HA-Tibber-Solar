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
    'kuehl': ('Kühlen', '#22d3ee', 'mdi:fridge-industrial-outline'),
    'spuel': ('Spülmaschine', '#3b82f6', 'mdi:dishwasher'),
    'wasch': ('Waschmaschine', '#06b6d4', 'mdi:washing-machine'),
    'licht': ('Licht', '#f59e0b', 'mdi:lightbulb-on-outline'),
    'tv': ('TV & Unterhaltung', '#ec4899', 'mdi:television'),
    'computer': ('Computer', '#6366f1', 'mdi:laptop'),
    'akku': ('Akku laden', '#10b981', 'mdi:battery-charging'),
    'pool': ('Pool & Hydro', '#14b8a6', 'mdi:pool'),
    'klaeren': ('Zu klären', '#a855f7', 'mdi:help-circle-outline'),
}

# Groups: shown as one row (expandable) and as parent in the energy dashboard; sensor = kWh sum (group helper)
GROUPS = {
    'Licht Wohnzimmer': 'sensor.licht_wohnzimmer_gesamt_strom',
    'Außenlicht': 'sensor.aussenlicht_gesamt_strom',
    'Licht Keller': 'sensor.licht_keller_gesamt_strom',
    'Pool & Hydro': 'sensor.pool_hydro_gesamt_strom',
}

# name, category, power sensor (W), energy sensor (kWh, for history), group (optional)
# Energy: own Riemann integrals (UI helpers, every 5 min) where the plug's own counter is coarse or late.
DEVICES = [
    ('Tesla Wallbox', 'auto', 'sensor.tesla_wall_connector_gesamtleistung', 'sensor.tesla_wall_connector_energie', None),
    ('Klima Wohnzimmer', 'klima', 'sensor.klima_wohnzimmer_klima_leistung', 'sensor.klima_wohnzimmer_klimastrom_gesamt', None),
    # no plug: the burner adds ~300 W while it runs (load step measured 27.10.2026: +295 / -298 W)
    ('Ölheizung Brenner (≈300 W)', 'heizung', 'sensor.olbrenner_leistung', 'sensor.olbrenner_strom', None),
    # former plug "Monitor Kamera Keller", since 06.11.2026 on the chest freezer (history cleared, must stay on)
    ('TK-Kühltruhe', 'kuehl', 'sensor.tk_kuehltruhe_leistung', 'sensor.tk_kuehltruhe_strom', None),
    ('Spülmaschine', 'spuel', 'sensor.tasmota_energy_power', 'sensor.tasmota_energy_total', None),
    ('Waschmaschine', 'wasch', 'sensor.waschmaschiene_energy_power', 'sensor.miner_energy_total', None),
    # living room lights
    ('Stehlampe (Steckdose)', 'licht', 'sensor.licht_wohnzimmer_leistung', 'sensor.licht_wohnzimmer_strom', 'Licht Wohnzimmer'),
    ('Bücherregal', 'licht', 'sensor.antela_smart_steckdose_leistung', 'sensor.licht_buecherregal_strom', 'Licht Wohnzimmer'),
    # Shelly Plus 1 without metering; 8 W measured 06.11.2026 (3 on/off cycles at the house load, 4-12 W)
    ('Vitrine (≈8 W)', 'licht', 'sensor.licht_wohnzimmer_vitrine_leistung', 'sensor.licht_wohnzimmer_vitrine_strom', 'Licht Wohnzimmer'),
    # outdoor lights
    ('Einfahrt', 'licht', 'sensor.shellydimmer2_4cebd6edf59d_power', 'sensor.licht_aussen_einfahrt_strom', 'Außenlicht'),
    ('Garage', 'licht', 'sensor.shellydimmer2_08f9e04436dc_power', 'sensor.licht_aussen_garage_strom', 'Außenlicht'),
    # Shelly Plus 1 without metering, light columns in the bed; 32 W measured 06.11.2026 (3 cycles, 26-36 W)
    ('Ambiente Beet (≈32 W)', 'licht', 'sensor.aussen_ambiente_licht_leistung', 'sensor.aussen_ambiente_licht_strom', 'Außenlicht'),
    # basement lights; Keller Flur: Shelly Plus 1, 74 W measured 06.11.2026 (62-86 W)
    ('Flur (≈74 W)', 'licht', 'sensor.licht_keller_flur_leistung', 'sensor.licht_keller_flur_strom', 'Licht Keller'),
    ('Lasercutter', 'licht', 'sensor.licht_keller_lasercutter_leistung', 'sensor.licht_keller_lasercutter_strom', 'Licht Keller'),
    ('LED (?)', 'licht', 'sensor.licht_schrank_esszimmer_leistung', 'sensor.licht_led_keller_strom', 'Licht Keller'),
    # Shelly Plus 1 without metering; 13 W measured 06.11.2026 (rounds 2-3: 9-17 W)
    ('Licht Abstellkammer (≈13 W)', 'licht', 'sensor.licht_abstellkammer_leistung', 'sensor.licht_abstellkammer_strom', None),
    # plug formerly on the bunk bed light, since 06.11.2026: TV with Teufel box and everything around it
    ('Fernseher Wohnzimmer', 'tv', 'sensor.kinderzimmer_hochbett_licht_leistung', 'sensor.fernseher_wohnzimmer_strom', None),
    # no plug: DLNA renderer of the basement TV reachable = on; ~120 W from 23 on/off load steps (26.10.-05.11.2026)
    ('Fernseher Keller (≈120 W)', 'tv', 'sensor.fernseher_keller_leistung', 'sensor.fernseher_keller_strom', None),
    # no plug: HASS.Agent runs scripts/surface_power.ps1 every 60 s (estimate, 0 away from home / on battery)
    ('Surface (geschätzt)', 'computer', 'sensor.surface_netzbezug', 'sensor.surface_strom', None),
    ('Akku laden (Netz)', 'akku', 'sensor.shellyplus1pm_e86beaeae1a0_switch_0_power', 'sensor.shellyplus1pm_e86beaeae1a0_switch_0_energy', None),
    ('Hydrotower Erdbeere (?)', 'pool', 'sensor.pool_warmepumpe_leistung', 'sensor.pool_warmepumpe_energie_gesamt', 'Pool & Hydro'),
    ('Hydrotower Salat (?)', 'pool', 'sensor.poolpumpe_leistung', 'sensor.poolpumpe_energie_gesamt', 'Pool & Hydro'),
    ('Pool-Pumpe (?)', 'pool', 'sensor.steckdose_pool_pumpe_leistung', 'sensor.steckdose_pool_pumpe_energie_gesamt', 'Pool & Hydro'),
    ('Pool-Pumpe Dach (?)', 'pool', 'sensor.steckdose_pool_pumpe_dach_leistung', 'sensor.steckdose_pool_pumpe_dach_energie_gesamt', 'Pool & Hydro'),
    ('Pool außen (?)', 'pool', 'sensor.antela_smart_plug_leistung', 'sensor.antela_smart_plug_energie_gesamt', 'Pool & Hydro'),
    ('Hydo Paprika (?)', 'klaeren', 'sensor.tasmota_energy_power_2', 'sensor.tasmota_energy_total_2', None),
]

# House load of the optimizer: grid - export + inverters (Hoymiles, Ecoflow plug), but WITHOUT wallbox
# and AC (left out on purpose so they do not distort the learned load). Add them back for the real total.
TOTAL = 'sensor.calculated_house_consumption'
ADD_BACK = ['sensor.tesla_wall_connector_gesamtleistung', 'sensor.klima_wohnzimmer_klima_leistung']
# Ecoflow feeds in through this plug: a source, not a consumer (energy dashboard: solar)
ECOFLOW_ENERGY = 'sensor.antela_smart_steckdose_4_energie_gesamt'
BASE = 'sensor.grundlast'
PHASES = [('L1', 'sensor.tibber_locl_0100240700ff'), ('L2', 'sensor.tibber_locl_0100380700ff'), ('L3', 'sensor.tibber_locl_01004c0700ff')]

dev_js = json.dumps([{'n': n, 'c': c, 'p': p, 'g': g} for n, c, p, _, g in DEVICES], ensure_ascii=False)
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
                tap_action={'action': 'none'}, hold_action={'action': 'more-info'}, custom_fields={'c': '[[[' + HELP + r"""
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
  const open = window.__vbOpen || (window.__vbOpen = {});
  const groups = {};
  devs.forEach(d => { const k = d.g || d.n; (groups[k] = groups[k] || {n: k, c: d.c, g: !!d.g, w: 0, items: []}); groups[k].items.push(d); groups[k].w += Math.max(0, d.w || 0); });
  const row = (name, w, c, icon, small) => `<div style="display:flex;align-items:center;gap:10px;padding:${small ? '4px 0 4px 40px' : '6px 0'}">
      ${small ? '' : `<div style="width:30px;height:30px;flex:none;border-radius:10px;display:flex;align-items:center;justify-content:center;background:${c}22">
        <ha-icon icon="${icon}" style="--mdc-icon-size:17px;color:${c}"></ha-icon></div>`}
      <div style="flex:1;min-width:0"><div style="font-size:${small ? 12 : 13}px;font-weight:${small ? 400 : 600};white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${name}</div>
        <div style="height:4px;border-radius:2px;background:rgba(127,127,127,.12);margin-top:4px;overflow:hidden">
          <div style="height:100%;width:${Math.min(100, w / sum * 100)}%;background:${c}"></div></div></div>
      <div style="width:70px;text-align:right;font-size:${small ? 12 : 13}px;font-weight:${small ? 400 : 600}">${W(w)}</div></div>`;
  const rows = Object.values(groups).filter(g => g.w > 1).sort((a, b) => b.w - a.w).map(g => {
    const c = CAT[g.c];
    if (!g.g) return row(g.n, g.w, c.col, c.i, false);
    const kids = g.items.filter(d => d.w > 0.5).sort((a, b) => b.w - a.w).map(d => row(d.n, d.w, c.col, c.i, true)).join('');
    return `<details ${open[g.n] ? 'open' : ''}>
      <summary style="list-style:none;display:block;cursor:pointer"
        onclick="event.preventDefault(); event.stopPropagation(); const d = this.parentElement; d.open = !d.open; (window.__vbOpen = window.__vbOpen || {})['${g.n}'] = d.open;">${row(g.n + ` <span style="font-weight:400;color:var(--secondary-text-color)">▾ ${g.items.filter(d => d.w > 0.5).length}</span>`, g.w, c.col, c.i, false)}</summary>${kids}</details>`;
  }).join('');
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
                    for n, c, p, _, g in DEVICES if c not in ('pool', 'klaeren')]}
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
energy = []
for gname, gstat in GROUPS.items():
    energy.append({'stat_consumption': gstat, 'name': gname})
for n, c, _, e, g in DEVICES:
    item = {'stat_consumption': e, 'name': n}
    if g:
        item['included_in_stat'] = GROUPS[g]
    energy.append(item)
(OUT / 'energy_device_consumption.json').write_text(json.dumps(energy, ensure_ascii=False, indent=1), encoding='utf-8')
print('ok', len(DEVICES), 'devices')
