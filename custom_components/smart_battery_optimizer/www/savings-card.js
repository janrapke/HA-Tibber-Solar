/**
 * Smart Battery Optimizer – Stromkosten-Vergleich v3
 * Electricity bill in three scenarios: without any system, solar panels only (direct feed-in, export unpaid),
 * real (with battery). Data: sensor "Stromkosten-Vergleich" (attributes heute/7_tage/30_tage/monat/jahr/gesamt, monate).
 *
 * Config:
 *   type: custom:savings-card
 *   entity: sensor.smart_battery_optimizer_stromkosten_vergleich   # optional, found automatically
 *   default_period: gesamt                                          # optional
 */
console.info('%c SAVINGS-CARD %c v3.0 ', 'color:white;background:#4CAF50;font-weight:bold', 'color:#4CAF50;background:white;font-weight:bold');

// fixed periods; months ("m:YYYY-MM") and years ("y:YYYY") are picked from the drop-downs
const PERIODS = [
  { key: 'heute', label: 'Heute' },
  { key: '7_tage', label: '7 Tage' },
  { key: '30_tage', label: '30 Tage' },
  { key: 'gesamt', label: 'Gesamt' },
];
const MONTHS_LONG = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];
const C_GRID = '#ef5350', C_SOLAR = '#f9a825', C_BATT = '#1e88e5', C_REAL = '#43a047';
const MONTHS = ['Jan', 'Feb', 'Mär', 'Apr', 'Mai', 'Jun', 'Jul', 'Aug', 'Sep', 'Okt', 'Nov', 'Dez'];

class SavingsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._period = 'gesamt';
    this._sig = '';
  }

  static getStubConfig() { return {}; }

  setConfig(config) {
    this._config = config || {};
    this._period = this._config.default_period || 'gesamt';
    this._sig = '';
  }

  getCardSize() { return 6; }

  set hass(hass) {
    this._hass = hass;
    const st = this._state();
    const sig = st ? st.last_updated + this._period : 'none';
    if (sig !== this._sig) { this._sig = sig; this._render(st); }
  }

  _state() {
    const h = this._hass;
    if (!h) return null;
    if (this._config.entity) return h.states[this._config.entity] || null;
    const id = Object.keys(h.states).find(e => e.startsWith('sensor.') && h.states[e].attributes?.monate && h.states[e].attributes?.gesamt);
    return id ? h.states[id] : null;
  }

  _render(st) {
    const eur = (v, d = 2) => (v ?? 0).toLocaleString('de-DE', { minimumFractionDigits: d, maximumFractionDigits: d }) + ' €';
    const kwh = v => Math.round(v ?? 0).toLocaleString('de-DE') + ' kWh';
    const css = `
      <style>
        ha-card { padding: 14px 16px 12px; }
        .head { display: flex; align-items: center; gap: 10px; }
        .head ha-icon { color: ${C_REAL}; --mdc-icon-size: 26px; }
        .title { flex: 1; font-size: 1.05em; font-weight: 500; }
        .big { text-align: right; }
        .big .v { font-size: 1.6em; font-weight: 700; color: ${C_REAL}; line-height: 1.1; }
        .big .l { font-size: .72em; color: var(--secondary-text-color); }
        .tabs { display: flex; gap: 4px; margin: 12px 0 6px; }
        .pick { display: flex; gap: 6px; margin-bottom: 6px; }
        .pick select { flex: 1; padding: 6px 8px; border-radius: 14px; font-size: .8em; border: 1px solid var(--divider-color);
               background: var(--secondary-background-color); color: var(--primary-text-color); font-family: inherit; }
        .pick select.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); font-weight: 600; }
        .what { font-size: .75em; color: var(--secondary-text-color); text-align: center; margin-bottom: 12px; }
        svg .mb { cursor: pointer; }
        .tab { flex: 1; text-align: center; padding: 6px 0; border-radius: 14px; font-size: .8em; cursor: pointer;
               background: var(--secondary-background-color); color: var(--primary-text-color); user-select: none; }
        .tab.on { background: var(--primary-color); color: var(--text-primary-color, #fff); font-weight: 600; }
        .row { margin-bottom: 12px; }
        .rl { display: flex; align-items: center; gap: 6px; font-size: .85em; margin-bottom: 4px; }
        .rl ha-icon { --mdc-icon-size: 18px; }
        .rl .n { flex: 1; }
        .rl .c { font-weight: 700; font-size: 1.05em; }
        .bar { display: flex; height: 22px; border-radius: 6px; overflow: hidden; background: var(--divider-color); }
        .seg { height: 100%; display: flex; align-items: center; justify-content: center; font-size: .7em; color: #fff;
               white-space: nowrap; overflow: hidden; font-weight: 600; }
        .saved { background-image: repeating-linear-gradient(135deg, rgba(255,255,255,.25) 0 4px, transparent 4px 8px); }
        .chips { display: flex; gap: 6px; margin: 4px 0 12px; }
        .chip { flex: 1; border-radius: 10px; padding: 7px 6px; text-align: center; }
        .chip .v { font-size: 1.1em; font-weight: 700; }
        .chip .l { font-size: .7em; opacity: .8; }
        .kwh { display: flex; justify-content: space-around; font-size: .75em; color: var(--secondary-text-color); margin-bottom: 10px; }
        .kwh b { color: var(--primary-text-color); }
        .kwh > span { white-space: nowrap; }
        .sub { font-size: .8em; font-weight: 600; margin: 4px 0 2px; color: var(--secondary-text-color); }
        svg text { font-family: inherit; }
        .foot { font-size: .68em; color: var(--secondary-text-color); margin-top: 6px; line-height: 1.4; }
        .empty { padding: 20px; text-align: center; color: var(--secondary-text-color); }
      </style>`;
    if (!st) {
      this.shadowRoot.innerHTML = `${css}<ha-card><div class="empty">Sensor „Stromkosten-Vergleich“ nicht gefunden.</div></ha-card>`;
      return;
    }
    const a = st.attributes;
    const months = a.monate || {}, years = a.jahre || {};
    const per = this._period;
    const mName = k => MONTHS_LONG[Number(k.slice(5)) - 1] + ' ' + k.slice(0, 4);
    const p = per.startsWith('m:') ? (months[per.slice(2)] || {}) : per.startsWith('y:') ? (years[per.slice(2)] || {}) : (a[per] || {});
    const dfmt = d => new Date(d + 'T12:00').toLocaleDateString('de-DE');
    const today = new Date();
    const back = n => { const d = new Date(today); d.setDate(d.getDate() - n); return d.toLocaleDateString('de-DE'); };
    const what = per === 'heute' ? 'Heute, ' + today.toLocaleDateString('de-DE')
      : per === '7_tage' ? `Letzte 7 Tage: ${back(6)} – heute`
      : per === '30_tage' ? `Letzte 30 Tage: ${back(29)} – heute`
      : per === 'gesamt' ? `Gesamt: seit ${a.seit ? dfmt(a.seit) : '–'} (${p.tage || 0} Tage mit Messwerten)`
      : per.startsWith('m:') ? `${mName(per.slice(2))} (${p.tage || 0} Tage)`
      : `Jahr ${per.slice(2)} (${p.tage || 0} Tage)`;
    const A = p.ohne_system || 0, B = p.nur_solar || 0, C = p.tatsaechlich || 0;
    const sol = p.solar_spart || 0, bat = p.akku_spart || 0, tot = p.gesamt_spart || 0;
    const max = Math.max(A, B, C, 0.01);
    const w = v => Math.max(0, 100 * v / max).toFixed(2) + '%';
    const lbl = (v, minPct) => (100 * Math.abs(v) / max >= minPct ? eur(v, Math.abs(v) >= 100 ? 0 : 2) : '');
    const pct = (v) => A > 0 ? Math.round(100 * v / A) + ' %' : '–';

    const rows = `
      <div class="row">
        <div class="rl"><ha-icon icon="mdi:transmission-tower" style="color:${C_GRID}"></ha-icon><span class="n">Ohne System – alles aus dem Netz</span><span class="c">${eur(A)}</span></div>
        <div class="bar"><div class="seg" style="width:${w(A)};background:${C_GRID}">${lbl(A, 18)}</div></div>
      </div>
      <div class="row">
        <div class="rl"><ha-icon icon="mdi:solar-panel" style="color:${C_SOLAR}"></ha-icon><span class="n">Nur Solarmodule, ohne Speicher</span><span class="c">${eur(B)}</span></div>
        <div class="bar"><div class="seg" style="width:${w(B)};background:${C_GRID}">${lbl(B, 18)}</div>
          <div class="seg saved" style="width:${w(Math.max(0, sol))};background:${C_SOLAR}">${lbl(-sol, 14)}</div></div>
      </div>
      <div class="row">
        <div class="rl"><ha-icon icon="mdi:home-battery" style="color:${C_BATT}"></ha-icon><span class="n">Mit Speicher – tatsächlich bezahlt</span><span class="c">${eur(C)}</span></div>
        <div class="bar"><div class="seg" style="width:${w(C)};background:${C_GRID}">${lbl(C, 18)}</div>
          <div class="seg saved" style="width:${w(Math.max(0, bat))};background:${C_BATT}">${lbl(-bat, 14)}</div>
          <div class="seg saved" style="width:${w(Math.max(0, Math.min(sol, A - C - Math.max(0, bat))))};background:${C_SOLAR}">${lbl(-sol, 14)}</div></div>
      </div>`;

    const chips = `
      <div class="chips">
        <div class="chip" style="background:${C_SOLAR}22"><div class="v" style="color:${C_SOLAR}">${eur(sol)}</div><div class="l">Solar spart · ${pct(sol)}</div></div>
        <div class="chip" style="background:${C_BATT}22"><div class="v" style="color:${C_BATT}">${eur(bat)}</div><div class="l">Speicher spart zusätzlich · ${pct(bat)}</div></div>
        <div class="chip" style="background:${C_REAL}22"><div class="v" style="color:${C_REAL}">${eur(tot)}</div><div class="l">zusammen · ${pct(tot)}</div></div>
      </div>
      <div class="kwh"><span>Verbrauch <b>${kwh(p.verbrauch_kwh)}</b></span><span>Solar <b>${kwh(p.solar_kwh)}</b></span><span>aus dem Netz <b>${kwh(p.netz_kwh)}</b></span></div>`;

    // monthly savings: solar (amber) + battery (blue, may be negative) stacked
    const yearSel = per.startsWith('y:') ? per.slice(2) : (per.startsWith('m:') ? per.slice(2, 6) : null);
    const allMon = Object.entries(months);
    const mon = per.startsWith('y:') ? allMon.filter(([k]) => k.startsWith(yearSel)) : allMon.slice(-12);
    let chart = '';
    if (mon.length) {
      const W = 320, H = 120, PB = 16, PT = 12;
      const vals = mon.map(([, v]) => ({ s: v.solar_spart || 0, b: v.akku_spart || 0 }));
      const top = Math.max(1, ...vals.map(v => v.s + Math.max(0, v.b)));
      const bottom = Math.min(0, ...vals.map(v => Math.min(0, v.b)));
      const y = v => PT + (top - v) / (top - bottom) * (H - PT - PB);
      const bw = W / mon.length;
      let bars = '';
      mon.forEach(([k, _], i) => {
        const v = vals[i], x = i * bw + bw * 0.18, ww = bw * 0.64;
        if (per === 'm:' + k) bars += `<rect x="${i * bw + 1}" y="0" width="${bw - 2}" height="${H}" fill="var(--primary-color)" opacity=".12" rx="4"/>`;
        bars += `<rect class="mb" data-k="m:${k}" x="${i * bw}" y="0" width="${bw}" height="${H}" fill="transparent"/>`;
        bars += `<rect x="${x}" y="${y(v.s)}" width="${ww}" height="${Math.max(0, y(0) - y(v.s))}" fill="${C_SOLAR}" rx="2"><title>${k}: Solar ${eur(v.s)}</title></rect>`;
        if (v.b >= 0) bars += `<rect x="${x}" y="${y(v.s + v.b)}" width="${ww}" height="${Math.max(0, y(v.s) - y(v.s + v.b))}" fill="${C_BATT}" rx="2"><title>${k}: Speicher ${eur(v.b)}</title></rect>`;
        else bars += `<rect x="${x}" y="${y(0)}" width="${ww}" height="${Math.max(0, y(v.b) - y(0))}" fill="${C_BATT}" opacity=".5" rx="2"><title>${k}: Speicher ${eur(v.b)}</title></rect>`;
        const tot = v.s + v.b;
        bars += `<text x="${x + ww / 2}" y="${y(v.s + Math.max(0, v.b)) - 3}" font-size="8" text-anchor="middle" fill="var(--secondary-text-color)">${Math.round(tot)}</text>`;
        bars += `<text x="${x + ww / 2}" y="${H - 3}" font-size="9" text-anchor="middle" fill="var(--secondary-text-color)">${MONTHS[Number(k.slice(5)) - 1]}</text>`;
      });
      chart = `<div class="sub">Ersparnis pro Monat (€)${per.startsWith('y:') ? ' · ' + yearSel : ''} – Balken antippen für den Monat</div>
        <svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="none" style="display:block">
          <line x1="0" x2="${W}" y1="${y(0)}" y2="${y(0)}" stroke="var(--divider-color)"/>${bars}</svg>
        <div class="kwh" style="margin:2px 0 0"><span><span style="color:${C_SOLAR}">■</span> Solar</span><span><span style="color:${C_BATT}">■</span> Speicher</span></div>`;
    }

    const since = a.seit ? new Date(a.seit + 'T12:00').toLocaleDateString('de-DE') : '–';
    this.shadowRoot.innerHTML = `${css}<ha-card>
      <div class="head"><ha-icon icon="mdi:piggy-bank"></ha-icon><div class="title">Stromkosten-Vergleich</div>
        <div class="big"><div class="v">${eur(tot, tot >= 100 ? 0 : 2)}</div><div class="l">gespart</div></div></div>
      <div class="tabs">${PERIODS.map(x => `<div class="tab ${x.key === per ? 'on' : ''}" data-k="${x.key}">${x.label}</div>`).join('')}</div>
      <div class="pick">
        <select id="mon" class="${per.startsWith('m:') ? 'on' : ''}"><option value="">Monat wählen …</option>${Object.keys(months).reverse().map(k => `<option value="m:${k}" ${per === 'm:' + k ? 'selected' : ''}>${mName(k)}</option>`).join('')}</select>
        <select id="yr" class="${per.startsWith('y:') ? 'on' : ''}"><option value="">Jahr wählen …</option>${Object.keys(years).reverse().map(k => `<option value="y:${k}" ${per === 'y:' + k ? 'selected' : ''}>${k}</option>`).join('')}</select>
      </div>
      <div class="what">${what}</div>
      ${rows}${chips}${chart}
      <div class="foot">„Nur Solar“: Solarstrom zählt nur, soweit das Haus ihn im selben Moment verbraucht hätte; Überschuss wäre unbezahlt eingespeist worden. Speicher-Ersparnis enthält Lade­verluste und Netzladen. Daten seit ${since}, Werte vor Einführung aus Stundenmitteln. Grundgebühr nicht enthalten.</div>
    </ha-card>`;
    const go = k => { if (!k) return; this._period = k; this._sig = ''; this._render(this._state()); };
    this.shadowRoot.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => go(t.dataset.k)));
    this.shadowRoot.querySelectorAll('svg .mb').forEach(t => t.addEventListener('click', () => go(t.dataset.k)));
    this.shadowRoot.querySelectorAll('.pick select').forEach(t => t.addEventListener('change', () => go(t.value)));
  }
}

customElements.define('savings-card', SavingsCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'savings-card',
  name: 'Smart Battery: Stromkosten-Vergleich',
  description: 'Stromrechnung ohne System, nur mit Solar und tatsächlich mit Speicher',
  preview: true,
});
