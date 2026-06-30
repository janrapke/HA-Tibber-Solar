console.info('%c SAVINGS-CARD %c v2.0 ', 'color:white;background:#4CAF50;font-weight:bold', 'color:#4CAF50;background:white;font-weight:bold');

const PERIODS = [
  { key: 'today',  label: 'Heute',    days: 0  },
  { key: 'week',   label: '7 Tage',   days: 7  },
  { key: 'month',  label: '30 Tage',  days: 30 },
  { key: 'total',  label: 'Gesamt',   days: -1 },
];

class SavingsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._hass = null;
    this._config = {};
    this._period = 'total';
    this._periodData = {};   // cache: { period: { savings, vsNoBatt, cost } }
    this._fetching = new Set();
    this._initialized = false;
  }

  static getStubConfig() { return {}; }
  static getConfigElement() { return document.createElement('savings-card-editor'); }

  setConfig(config) {
    this._config = config || {};
    this._period = config?.default_period || 'total';
    this._periodData = {};
    if (this.shadowRoot && !this._initialized) {
      this.shadowRoot.innerHTML = '<div style="padding:16px">⏳ Ersparnisübersicht lädt…</div>';
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._buildShell();
      this._initialized = true;
    }
    this._fetchPeriod(this._period);
  }

  // ── Shell (built once) ────────────────────────────────────────────
  _buildShell() {
    const cfg = this._config;
    this.shadowRoot.innerHTML = `
      <style>
        * { box-sizing: border-box; }
        ha-card { overflow: hidden; }

        .header {
          display: flex; align-items: center; gap: 10px;
          padding: 14px 16px 0;
        }
        .header ha-icon { color: var(--primary-color); --mdi-icon-size: 22px; flex-shrink: 0; }
        .header-name { flex: 1; font-size: 1.05em; font-weight: 500; }

        .tabs {
          display: flex; gap: 4px;
          padding: 10px 16px 0;
        }
        .tab {
          flex: 1; padding: 6px 4px; border: none; border-radius: 8px;
          font-size: 0.8em; font-weight: 500; cursor: pointer;
          background: var(--secondary-background-color);
          color: var(--secondary-text-color);
          transition: background 0.15s, color 0.15s;
        }
        .tab.active {
          background: var(--primary-color);
          color: var(--text-primary-color, #fff);
        }

        .body { padding: 16px; }

        .metrics {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
          gap: 10px;
          margin-bottom: 14px;
        }
        .metric {
          background: var(--secondary-background-color);
          border-radius: 10px; padding: 12px 14px;
        }
        .metric-label {
          font-size: 0.72em; color: var(--secondary-text-color);
          text-transform: uppercase; letter-spacing: 0.05em;
          font-weight: 600; margin-bottom: 6px;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .metric-value {
          font-size: 1.55em; font-weight: 300; line-height: 1;
          color: var(--primary-text-color);
        }
        .metric-value.green { color: var(--success-color, #4caf50); }
        .metric-value.muted { color: var(--secondary-text-color); font-size: 1.1em; }

        .comparison {
          border-top: 1px solid var(--divider-color);
          padding-top: 12px;
        }
        .comp-label {
          font-size: 0.75em; color: var(--secondary-text-color);
          margin-bottom: 8px; font-weight: 600;
          text-transform: uppercase; letter-spacing: 0.05em;
        }
        .bars { display: flex; flex-direction: column; gap: 6px; }
        .bar-row { display: flex; align-items: center; gap: 8px; font-size: 0.82em; }
        .bar-name { width: 90px; flex-shrink: 0; color: var(--secondary-text-color); }
        .bar-track { flex: 1; height: 10px; background: var(--divider-color); border-radius: 5px; overflow: hidden; }
        .bar-fill { height: 100%; border-radius: 5px; transition: width 0.5s ease; }
        .bar-fill.actual { background: var(--primary-color); }
        .bar-fill.without { background: #EF5350; }
        .bar-val { font-weight: 500; width: 60px; text-align: right; flex-shrink: 0; }

        .loading { color: var(--secondary-text-color); font-size: 0.85em; padding: 8px 0; }
      </style>

      <ha-card>
        <div class="header">
          <ha-icon icon="mdi:piggy-bank"></ha-icon>
          <span class="header-name">${cfg.title || 'Batterie Ersparnisse'}</span>
        </div>
        <div class="tabs">
          ${PERIODS.map(p => `<button class="tab${p.key === this._period ? ' active' : ''}" data-period="${p.key}">${p.label}</button>`).join('')}
        </div>
        <div class="body">
          <div id="content"><div class="loading">Lade Daten…</div></div>
        </div>
      </ha-card>
    `;

    this.shadowRoot.querySelectorAll('.tab').forEach(btn => {
      btn.addEventListener('click', () => {
        this._period = btn.dataset.period;
        this.shadowRoot.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.period === this._period));
        this._fetchPeriod(this._period);
      });
    });
  }

  // ── Data fetching ─────────────────────────────────────────────────
  async _fetchPeriod(periodKey) {
    if (this._fetching.has(periodKey)) return;
    this._fetching.add(periodKey);

    try {
      const period = PERIODS.find(p => p.key === periodKey);
      const totalId   = this._config.entity_total    || 'sensor.batterie_ersparnis_gesamt';
      const vsNoBattId = this._config.entity_vs_no_batt || 'sensor.batterie_ersparnis_ggu_ohne_akku';
      const costId    = this._config.entity_cost;

      let savings = null, vsNoBatt = null, cost = null;

      if (period.days === -1) {
        // Gesamt: read current sensor values directly
        savings  = this._stateVal(totalId);
        vsNoBatt = this._stateVal(vsNoBattId);
        cost     = costId ? this._stateVal(costId) : null;
      } else {
        const start = new Date();
        if (period.days === 0) {
          start.setHours(0, 0, 0, 0);
        } else {
          start.setDate(start.getDate() - period.days);
          start.setHours(0, 0, 0, 0);
        }

        [savings, vsNoBatt] = await Promise.all([
          this._statChange(totalId, start),
          this._statChange(vsNoBattId, start),
        ]);
        cost = costId ? await this._statChange(costId, start) : null;
      }

      this._periodData[periodKey] = { savings, vsNoBatt, cost };
    } catch (e) {
      this._periodData[periodKey] = { savings: null, vsNoBatt: null, cost: null };
    }

    this._fetching.delete(periodKey);
    if (periodKey === this._period) this._renderContent();
  }

  async _statChange(entityId, startTime) {
    if (!entityId || !this._hass) return null;
    try {
      const result = await this._hass.callWS({
        type: 'recorder/statistics_during_period',
        start_time: startTime.toISOString(),
        statistic_ids: [entityId],
        period: 'hour',
        types: ['change'],
      });
      const stats = (result[entityId] || []);
      return stats.reduce((sum, s) => sum + (s.change ?? 0), 0);
    } catch {
      // Fallback: history diff
      try {
        const end = new Date();
        const history = await this._hass.callWS({
          type: 'history/history_during_period',
          start_time: startTime.toISOString(),
          end_time: end.toISOString(),
          entity_ids: [entityId],
          minimal_response: true,
          no_attributes: true,
          significant_changes_only: false,
        });
        const entries = history[entityId] || [];
        if (entries.length < 2) return null;
        const first = parseFloat(entries[0].s ?? entries[0].state);
        const last  = parseFloat(entries[entries.length - 1].s ?? entries[entries.length - 1].state);
        return isNaN(first) || isNaN(last) ? null : last - first;
      } catch { return null; }
    }
  }

  _stateVal(entityId) {
    if (!entityId || !this._hass) return null;
    const s = this._hass.states[entityId];
    return s ? parseFloat(s.state) : null;
  }

  // ── Content rendering ─────────────────────────────────────────────
  _renderContent() {
    const content = this.shadowRoot.querySelector('#content');
    if (!content) return;

    const data = this._periodData[this._period];
    if (!data) { content.innerHTML = '<div class="loading">Lade Daten…</div>'; return; }

    const { savings, vsNoBatt, cost } = data;
    const fmt = v => v === null ? '–' : v.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €';

    const costWithout = (cost !== null && savings !== null) ? cost + savings : null;

    // Bar widths
    const maxBar = Math.max(cost ?? 0, costWithout ?? 0, 0.001);
    const actualPct  = cost !== null        ? (cost / maxBar * 100).toFixed(1) : 0;
    const withoutPct = costWithout !== null ? (costWithout / maxBar * 100).toFixed(1) : 0;

    const hasCost = this._config.entity_cost;

    content.innerHTML = `
      <div class="metrics">
        ${hasCost ? `
          <div class="metric">
            <div class="metric-label">Tatsächliche Kosten</div>
            <div class="metric-value">${fmt(cost)}</div>
          </div>
          <div class="metric">
            <div class="metric-label">Kosten ohne System</div>
            <div class="metric-value">${fmt(costWithout)}</div>
          </div>
        ` : ''}
        <div class="metric">
          <div class="metric-label">Batterie Ersparnis</div>
          <div class="metric-value green">${fmt(savings)}</div>
        </div>
        <div class="metric">
          <div class="metric-label">Ersparnis ggü. ohne Akku</div>
          <div class="metric-value green">${fmt(vsNoBatt)}</div>
        </div>
      </div>

      ${hasCost && cost !== null && costWithout !== null ? `
        <div class="comparison">
          <div class="comp-label">Kostenvergleich</div>
          <div class="bars">
            <div class="bar-row">
              <span class="bar-name">Mit System</span>
              <div class="bar-track"><div class="bar-fill actual" style="width:${actualPct}%"></div></div>
              <span class="bar-val">${fmt(cost)}</span>
            </div>
            <div class="bar-row">
              <span class="bar-name">Ohne System</span>
              <div class="bar-track"><div class="bar-fill without" style="width:${withoutPct}%"></div></div>
              <span class="bar-val">${fmt(costWithout)}</span>
            </div>
          </div>
        </div>
      ` : ''}
    `;
  }

  getCardSize() { return 3; }
}


// ── Editor ────────────────────────────────────────────────────────────
class SavingsCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._hass = null;
    this._initialized = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) this._render();
    else this._populateSelects();
  }

  setConfig(config) {
    this._config = config || {};
    if (!this._initialized) this._render();
    else this._syncValues();
  }

  _monetarySensors() {
    if (!this._hass) return [];
    return Object.entries(this._hass.states)
      .filter(([, s]) => ['€', 'EUR'].includes(s.attributes?.unit_of_measurement))
      .map(([id, s]) => ({ id, name: s.attributes.friendly_name || id }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        .form { padding: 16px; display: flex; flex-direction: column; gap: 14px; }
        label { font-size: 0.85em; color: var(--secondary-text-color); margin-bottom: 4px; display: block; }
        select, input {
          width: 100%; padding: 9px 10px; border-radius: 8px;
          border: 1px solid var(--divider-color);
          background: var(--card-background-color); color: var(--primary-text-color);
          font-size: 0.9em; box-sizing: border-box;
        }
        .hint { font-size: 0.78em; color: var(--secondary-text-color); margin-top: 3px; }
      </style>
      <div class="form">
        <div>
          <label>Titel (optional)</label>
          <input id="title" type="text" placeholder="Batterie Ersparnisse">
        </div>
        <div>
          <label>Standard-Zeitraum</label>
          <select id="default_period">
            ${PERIODS.map(p => `<option value="${p.key}">${p.label}</option>`).join('')}
          </select>
        </div>
        <div>
          <label>Sensor: Gesamtersparnis</label>
          <select id="entity_total"></select>
        </div>
        <div>
          <label>Sensor: Ersparnis ggü. ohne Akku</label>
          <select id="entity_vs_no_batt"></select>
        </div>
        <div>
          <label>Sensor: Tatsächliche Kosten (optional)</label>
          <select id="entity_cost"></select>
          <div class="hint">Wenn angegeben, wird ein Kostenvergleich Mit/Ohne System angezeigt.</div>
        </div>
      </div>
    `;

    ['title'].forEach(id => {
      this.shadowRoot.querySelector(`#${id}`).addEventListener('change', e =>
        this._dispatch({ ...this._config, [id]: e.target.value }));
    });
    ['default_period', 'entity_total', 'entity_vs_no_batt', 'entity_cost'].forEach(id => {
      this.shadowRoot.querySelector(`#${id}`).addEventListener('change', e =>
        this._dispatch({ ...this._config, [id]: e.target.value || undefined }));
    });

    this._initialized = true;
    this._populateSelects();
    this._syncValues();
  }

  _populateSelects() {
    const sensors = this._monetarySensors();
    const opts = sensors.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
    ['entity_total', 'entity_vs_no_batt'].forEach(id => {
      const sel = this.shadowRoot.querySelector(`#${id}`);
      if (sel) sel.innerHTML = '<option value="">– bitte wählen –</option>' + opts;
    });
    const costSel = this.shadowRoot.querySelector('#entity_cost');
    if (costSel) costSel.innerHTML = '<option value="">– kein –</option>' + opts;
    this._syncValues();
  }

  _syncValues() {
    const active = this.shadowRoot.activeElement;
    const set = (id, val) => {
      const el = this.shadowRoot.querySelector(`#${id}`);
      if (el && el !== active) el.value = val || '';
    };
    set('title', this._config.title);
    set('default_period', this._config.default_period || 'total');
    set('entity_total', this._config.entity_total || 'sensor.batterie_ersparnis_gesamt');
    set('entity_vs_no_batt', this._config.entity_vs_no_batt || 'sensor.batterie_ersparnis_ggu_ohne_akku');
    set('entity_cost', this._config.entity_cost || '');
  }

  _dispatch(config) {
    this._config = config;
    this.dispatchEvent(new CustomEvent('config-changed', { detail: { config }, bubbles: true, composed: true }));
  }
}

customElements.define('savings-card', SavingsCard);
customElements.define('savings-card-editor', SavingsCardEditor);
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'savings-card',
  name: 'Smart Battery: Ersparnisse',
  description: 'Smart Battery Optimizer — Kostenvergleich mit/ohne System, Zeitraum wählbar',
  preview: true,
});
