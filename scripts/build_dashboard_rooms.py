"""Builds ha_packages/dashboard_heizung.yaml and dashboard_card_raeume.yaml from one room list."""
import yaml

REPO = 'C:/Git/HA-Tibber-Solar/ha_packages/'
R = {
    'bad': dict(name='Badezimmer', icon='mdi:shower', temp='sensor.h5179_f06f_temperature', hum='sensor.h5179_f06f_humidity',
                bts=['climate.bt_bad'], valves=['climate.fritz_smart_thermo_302_1'], k='input_number.heizung_warm_k_badezimmer'),
    'flur': dict(name='Flur', icon='mdi:door', temp='sensor.temperatursensor_flur_temperatur', hum='sensor.temperatursensor_flur_luftfeuchtigkeit',
                 bts=['climate.bt_flur_bt_flur'], valves=['climate.heizung_flur'], k='input_number.heizung_warm_k_flur'),
    'wohn': dict(name='Wohnzimmer', icon='mdi:sofa-outline', temp='sensor.wohnzimmer_temperatur', hum='sensor.temp_wohnzimmer_luftfeuchtigkeit',
                 bts=['climate.bt_wohnzimmer_bt_wohnzimmer', 'climate.bt_esszimmer_bt_esszimmer'], valves=['climate.wohnzimmer', 'climate.esszimmer'],
                 k='input_number.heizung_warm_k_wohnzimmer', ac='climate.152832118046533_climate', ac_hz='sensor.klima_wohnzimmer_kompressor'),
    'kueche': dict(name='Küche', icon='mdi:fridge-outline', temp='sensor.temperatursensor_kuche_temperatur', hum='sensor.temperatursensor_kuche_luftfeuchtigkeit',
                   bts=['climate.bt_kuche_bt_kuche'], valves=['climate.kuche'], k='input_number.heizung_warm_k_kuche',
                   off_flag='automation.heizung_kuche_dauerhaft_aus_halten'),
    'schlaf': dict(name='Schlafzimmer', icon='mdi:bed-outline', temp='sensor.temperature_schalfzimmer_temperatur', hum='sensor.temperature_schalfzimmer_luftfeuchtigkeit',
                   bts=['climate.bt_schlafzimmer_bt_schlafzimmer'], valves=['climate.produktion_2'], k='input_number.heizung_warm_k_schlafzimmer'),
    'gast': dict(name='Gästezimmer', icon='mdi:desk', temp='sensor.temperature_gastezimmer_temperatur', hum='sensor.temperature_gastezimmer_luftfeuchtigkeit',
                 bts=['climate.bt_gastezimmer_bt_gastezimmer'], valves=['climate.produktion_4'], k='input_number.heizung_warm_k_gastezimmer',
                 ac='climate.air_conditioner'),
    'kind': dict(name='Kinderzimmer', icon='mdi:teddy-bear', temp='sensor.temperature_kinderzimmer_temperatur', hum='sensor.temperature_kinderzimmer_luftfeuchtigkeit',
                 bts=['climate.bt_kinderzimmer_bt_kinderzimmer'], valves=['climate.produktion_1'], k='input_number.heizung_warm_k_kinderzimmer'),
    'keller': dict(name='Keller', icon='mdi:briefcase-outline', temp='sensor.temperatursensor_keller_temperatur', hum='sensor.temperatursensor_keller_luftfeuchtigkeit',
                   note='Thermostat manuell'),
    'baelle': dict(name='Keller Bällebad', icon='mdi:baby-face-outline', temp='sensor.temperature_humidity_sensor_16_temperatur',
                   hum='sensor.temperature_humidity_sensor_16_luftfeuchtigkeit', note='Thermostat manuell'),
}
HOME_ORDER = ['bad', 'flur', 'wohn', 'kueche', 'schlaf', 'gast', 'kind', 'keller', 'baelle']


def room(key, big=False, link=True, **grid):
    c = {'type': 'custom:button-card', 'template': 'raum', 'variables': dict(R[key], **({'big': True} if big else {}))}
    if link:  # tap opens the room page with history; hold still opens the thermostat
        c['tap_action'] = {'action': 'navigate', 'navigation_path': f'/dashboard-home/raum-{key}'}
    if grid:
        c['grid_options'] = grid
    return c


def thermostat(entity, name='Thermostat', conditional=False):
    tile = {'type': 'tile', 'entity': entity, 'name': name, 'icon': 'mdi:thermostat', 'color': 'deep-orange',
            'features_position': 'bottom', 'grid_options': {'columns': 'full'},
            'features': [{'type': 'target-temperature'}, {'type': 'climate-hvac-modes', 'hvac_modes': ['heat', 'off']}]}
    if conditional:  # disabled entity: show only once it exists again
        tile['visibility'] = [{'condition': 'state', 'entity': entity, 'state': ['heat', 'off', 'heat_cool', 'auto']}]
    return tile


def heading(text, icon):
    return {'type': 'heading', 'heading': text, 'icon': icon}


def sub(text):
    return {'type': 'heading', 'heading': text, 'heading_style': 'subtitle'}


summary = {
    'type': 'custom:button-card', 'grid_options': {'columns': 'full'}, 'entity': 'binary_sensor.olheizung_brenner_lauft',
    'show_name': False, 'show_icon': False, 'show_state': False, 'triggers_update': 'all',
    'tap_action': {'action': 'navigate', 'navigation_path': '/dashboard-home/oel-heizung'},
    'variables': {'rooms': [dict(R[k]) for k in HOME_ORDER]},
    'styles': {'card': [{'padding': 0}, {'border-radius': '18px'}, {'overflow': 'hidden'}],
               'grid': [{'grid-template-areas': '"c"'}, {'grid-template-columns': '1fr'}],
               'custom_fields': {'c': [{'white-space': 'normal'}, {'text-align': 'left'}, {'width': '100%'}]}},
    'custom_fields': {'c': r"""[[[
  const n = (x) => { const y = parseFloat(x); return isNaN(y) ? null : y; };
  const f = (x, d) => x === null || isNaN(x) ? '–' : Number(x).toLocaleString('de-DE', {minimumFractionDigits: d, maximumFractionDigits: d});
  const rooms = variables.rooms.map(r => {
    const t = n(states[r.temp]?.state);
    const bts = (r.bts || []).map(b => states[b]).filter(Boolean);
    const want = bts.some(s => s.attributes.hvac_action === 'heating');
    const k = n(states[r.k]?.state) ?? 1.5;
    const warm = t !== null && (r.valves || []).some(id => { const vt = n(states[id]?.attributes?.current_temperature); return vt !== null && vt - t >= k; });
    return {name: r.name, t, want, warm};
  });
  const want = rooms.filter(r => r.want).length, warm = rooms.filter(r => r.warm).length;
  const temps = rooms.map(r => r.t).filter(x => x !== null);
  const avg = temps.length ? temps.reduce((a, b) => a + b, 0) / temps.length : null;
  const cold = rooms.filter(r => r.t !== null).sort((a, b) => a.t - b.t)[0];
  const on = entity.state === 'on';
  const col = on ? '#f97316' : want ? '#f59e0b' : '#10b981';
  const out = n(states['sensor.openweathermap_temperature']?.state);
  const tile = (icon, c, v, lab) => `<div style="padding:10px;border-radius:14px;background:rgba(127,127,127,.08)">
      <ha-icon icon="${icon}" style="--mdc-icon-size:18px;color:${c}"></ha-icon>
      <div style="font-size:17px;font-weight:600;margin-top:2px;white-space:nowrap">${v}</div>
      <div style="font-size:11px;color:var(--secondary-text-color)">${lab}</div></div>`;
  return `<div style="padding:18px;background:linear-gradient(135deg, ${col}22 0%, transparent 60%)">
    <div style="display:flex;align-items:center;gap:12px">
      <div style="width:44px;height:44px;border-radius:14px;display:flex;align-items:center;justify-content:center;background:${col}2e">
        <ha-icon icon="${on ? 'mdi:fire' : 'mdi:home-thermometer-outline'}" style="--mdc-icon-size:26px;color:${col}"></ha-icon></div>
      <div style="flex:1">
        <div style="font-size:12px;color:var(--secondary-text-color);text-transform:uppercase;letter-spacing:.06em">Heizung</div>
        <div style="font-size:18px;font-weight:700">${on ? 'Ölbrenner läuft' : want ? want + (want === 1 ? ' Raum fordert' : ' Räume fordern') + ' Wärme' : 'Alle Räume zufrieden'}</div></div>
      <div style="text-align:right">
        <div style="font-size:30px;font-weight:300;line-height:1">${f(out, 1)}<span style="font-size:15px;vertical-align:top">°</span></div>
        <div style="font-size:11px;color:var(--secondary-text-color)">außen</div></div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:14px">
      ${tile('mdi:fire', '#f59e0b', want, 'fordern')}
      ${tile('mdi:radiator', '#f97316', warm, 'warm')}
      ${tile('mdi:thermometer', '#10b981', f(avg, 1) + '°', 'Ø Räume')}
      ${tile('mdi:snowflake-thermometer', '#3b82f6', cold ? f(cold.t, 1) + '°' : '–', cold ? cold.name : 'kältester')}
    </div></div>`;
]]]"""},
}

view = {
    'title': 'Heizung', 'path': 'heizung', 'icon': 'mdi:radiator', 'type': 'sections', 'max_columns': 3,
    'dense_section_placement': True,
    'sections': [
        {'type': 'grid', 'cards': [
            heading('Heizung', 'mdi:radiator'), summary,
            sub('Wohnbereich'),
            room('wohn', True, columns='full'), thermostat('climate.bt_wohnzimmer_bt_wohnzimmer', 'Thermostat Wohnzimmer'),
            thermostat('climate.bt_esszimmer_bt_esszimmer', 'Thermostat Esszimmer'),
        ]},
        {'type': 'grid', 'cards': [
            sub('Küche und Flur'),
            room('kueche', True, columns='full'), thermostat('climate.bt_kuche_bt_kuche', 'Thermostat Küche', conditional=True),
            {'type': 'tile', 'entity': 'automation.heizung_kuche_dauerhaft_aus_halten', 'name': 'Dauerhaft aus',
             'icon': 'mdi:radiator-off', 'color': 'blue-grey', 'grid_options': {'columns': 'full'},
             'features_position': 'inline', 'features': [{'type': 'toggle'}]},
            room('flur', True, columns='full'), thermostat('climate.bt_flur_bt_flur'),
            sub('Bad'),
            room('bad', True, columns='full'), thermostat('climate.bt_bad'),
            {'type': 'tile', 'entity': 'input_boolean.bad_urlaub', 'name': 'Bad-Urlaub', 'icon': 'mdi:palm-tree',
             'color': 'teal', 'grid_options': {'columns': 'full'}, 'features_position': 'inline', 'features': [{'type': 'toggle'}]},
        ]},
        {'type': 'grid', 'cards': [
            sub('Schlafbereich'),
            room('schlaf', True, columns='full'), thermostat('climate.bt_schlafzimmer_bt_schlafzimmer'),
            room('kind', True, columns='full'), thermostat('climate.bt_kinderzimmer_bt_kinderzimmer'),
            room('gast', True, columns='full'), thermostat('climate.bt_gastezimmer_bt_gastezimmer'),
            sub('Keller'),
            room('keller', True, columns=6, rows=2), room('baelle', True, columns=6, rows=2),
        ]},
    ],
}

def chart(title, span, series, height=200, yaxis=None):
    c = {'type': 'custom:apexcharts-card', 'grid_options': {'columns': 'full'}, 'graph_span': span,
         'header': {'show': True, 'title': title, 'show_states': True, 'colorize_states': True},
         'apex_config': {'chart': {'height': height, 'toolbar': {'show': False}}, 'grid': {'strokeDashArray': 4},
                         'legend': {'show': False}},
         'series': series}
    if yaxis:
        c['yaxis'] = yaxis
    return c


def room_page(key):
    r = R[key]
    g = '10min'
    temp = [{'entity': r['temp'], 'name': 'Raum', 'color': '#f59e0b', 'type': 'area', 'opacity': 0.15, 'stroke_width': 2,
             'float_precision': 1, 'group_by': {'func': 'avg', 'duration': g}}]
    if r.get('bts'):
        temp.append({'entity': r['bts'][0], 'attribute': 'temperature', 'name': 'Ziel', 'color': '#10b981', 'curve': 'stepline',
                     'stroke_width': 2, 'float_precision': 1, 'group_by': {'func': 'last', 'duration': g}})
    for i, v in enumerate(r.get('valves', [])):
        temp.append({'entity': v, 'attribute': 'current_temperature', 'name': 'Heizkörper' + (f' {i + 1}' if len(r['valves']) > 1 else ''),
                     'color': ['#ef4444', '#f97316'][i % 2], 'stroke_width': 1, 'float_precision': 1, 'group_by': {'func': 'avg', 'duration': g}})
    temp.append({'entity': 'sensor.openweathermap_temperature', 'name': 'Außen', 'color': '#818cf8', 'stroke_width': 1,
                 'float_precision': 1, 'group_by': {'func': 'avg', 'duration': g}})
    cards1 = [{'type': 'heading', 'heading': r['name'], 'icon': r['icon']}, room(key, True, link=False, columns='full'),
              chart('Temperatur 24 h', '24h', temp)]
    if r.get('hum'):
        cards1.append(chart('Luftfeuchte 24 h', '24h', [
            {'entity': r['hum'], 'name': 'Luftfeuchte', 'color': '#14b8a6', 'type': 'area', 'opacity': 0.15, 'stroke_width': 2,
             'float_precision': 0, 'group_by': {'func': 'avg', 'duration': g}}], height=160, yaxis=[{'min': 20, 'max': 80, 'decimals': 0}]))
    week = [{'entity': r['temp'], 'name': 'Temperatur', 'yaxis_id': 't', 'color': '#f59e0b', 'stroke_width': 2, 'float_precision': 1,
             'group_by': {'func': 'avg', 'duration': '1h'}}]
    yax = [{'id': 't', 'decimals': 0}]
    if r.get('hum'):
        week.append({'entity': r['hum'], 'name': 'Luftfeuchte', 'yaxis_id': 'h', 'color': '#14b8a6', 'stroke_width': 2,
                     'float_precision': 0, 'group_by': {'func': 'avg', 'duration': '1h'}})
        yax.append({'id': 'h', 'opposite': True, 'min': 20, 'max': 80, 'decimals': 0})
    cards2 = [{'type': 'heading', 'heading': 'Woche', 'icon': 'mdi:calendar-week'},
              chart('Temperatur und Luftfeuchte 7 Tage', '7d', week, height=220, yaxis=yax)]
    bts = r.get('bts', [])
    if bts:
        cards2.append({'type': 'heading', 'heading': 'Thermostat', 'icon': 'mdi:thermostat'})
        for b in bts:
            cards2.append(thermostat(b, states_name(b), conditional=(b == 'climate.bt_kuche_bt_kuche')))
    return {'title': r['name'], 'path': f'raum-{key}', 'icon': r['icon'], 'subview': True, 'back_path': '/dashboard-home/0',
            'type': 'sections', 'max_columns': 2, 'sections': [{'type': 'grid', 'cards': cards1}, {'type': 'grid', 'cards': cards2}]}


def states_name(b):
    return {'climate.bt_esszimmer_bt_esszimmer': 'Thermostat Esszimmer',
            'climate.bt_wohnzimmer_bt_wohnzimmer': 'Thermostat Wohnzimmer'}.get(b, 'Thermostat')


home_grid = {'type': 'grid', 'columns': 2, 'square': False, 'cards': [room(k) for k in HOME_ORDER]}

hdr = '# GENERIERT von scripts/build_dashboard_rooms.py (Raumliste). Vorlage „raum“: dashboard_button_templates.yaml\n'
yaml.safe_dump(view, open(REPO + 'dashboard_heizung.yaml', 'w', encoding='utf-8'), allow_unicode=True, sort_keys=False, width=200)
yaml.safe_dump(home_grid, open(REPO + 'dashboard_card_raeume.yaml', 'w', encoding='utf-8'), allow_unicode=True, sort_keys=False, width=200)
yaml.safe_dump([room_page(k) for k in HOME_ORDER], open(REPO + 'dashboard_raum_seiten.yaml', 'w', encoding='utf-8'),
               allow_unicode=True, sort_keys=False, width=200)
for p in ('dashboard_heizung.yaml', 'dashboard_card_raeume.yaml', 'dashboard_raum_seiten.yaml'):
    s = open(REPO + p, encoding='utf-8').read()
    open(REPO + p, 'w', encoding='utf-8').write(hdr + s)
print('ok')
