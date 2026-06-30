console.info('%c SAVINGS-CARD %c v1.0 ', 'color:white;background:#4CAF50;font-weight:bold', 'color:#4CAF50;background:white;font-weight:bold');

class SavingsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._hass = null;
    this._config = {};
  }

  static getStubConfig() {
    return {};
  }

  static getConfigElement() {
    return document.createElement('savings-card-editor');
  }

  setConfig(config) {
    this._config = config || {};
    if (this.shadowRoot && !this._hass) {
      this.shadowRoot.innerHTML = '<div style="padding:16px">⏳ Ersparnisübersicht lädt…</div>';
    }
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _val(entityId) {
    if (!entityId || !this._hass) return null;
    const s = this._hass.states[entityId];
    return s ? parseFloat(s.state) : null;
  }

  _render() {
    const hass = this._hass;
    const cfg = this._config;

    const totalId  = cfg.entity_total  || 'sensor.batterie_ersparnis_gesamt';
    const vsNoBattId = cfg.entity_vs_no_batt || 'sensor.batterie_ersparnis_ggu_ohne_akku';

    const total    = this._val(totalId);
    const vsNoBatt = this._val(vsNoBattId);

    const fmt = v => v === null ? '–' : v.toLocaleString('de-DE', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €';

    // Bar: how much of vsNoBatt is covered by total (capped 0–100%)
    const barPct = (total !== null && vsNoBatt !== null && vsNoBatt > 0)
      ? Math.min(100, Math.max(0, (total / vsNoBatt) * 100))
      : 0;

    this.shadowRoot.innerHTML = `
      <style>
        * { box-sizing: border-box; }
        ha-card { overflow: hidden; }

        .header {
          display: flex; align-items: center; gap: 10px;
          padding: 14px 16px 12px;
          border-bottom: 1px solid var(--divider-color);
        }
        .header ha-icon { color: var(--primary-color); --mdi-icon-size: 22px; }
        .header-name { flex: 1; font-size: 1.05em; font-weight: 500; }

        .body { padding: 16px; }

        .metric { margin-bottom: 18px; }
        .metric-label {
          font-size: 0.78em; color: var(--secondary-text-color);
          text-transform: uppercase; letter-spacing: 0.05em;
          font-weight: 600; margin-bottom: 4px;
        }
        .metric-value {
          font-size: 2em; font-weight: 300; line-height: 1.1;
          color: var(--primary-text-color);
        }
        .metric-value.green { color: var(--success-color, #4caf50); }

        .divider { height: 1px; background: var(--divider-color); margin: 14px 0; }

        .bar-section { margin-bottom: 4px; }
        .bar-label {
          display: flex; justify-content: space-between;
          font-size: 0.82em; color: var(--secondary-text-color); margin-bottom: 6px;
        }
        .bar-bg {
          height: 8px; background: var(--divider-color);
          border-radius: 4px; overflow: hidden;
        }
        .bar-fill {
          height: 100%; border-radius: 4px;
          background: var(--success-color, #4caf50);
          transition: width 0.6s ease;
          width: ${barPct.toFixed(1)}%;
        }
        .bar-hint {
          font-size: 0.78em; color: var(--secondary-text-color);
          margin-top: 6px; text-align: right;
        }
      </style>

      <ha-card>
        <div class="header">
          <ha-icon icon="mdi:piggy-bank"></ha-icon>
          <span class="header-name">${cfg.title || 'Batterie Ersparnisse'}</span>
        </div>
        <div class="body">

          <div class="metric">
            <div class="metric-label">Gesamtersparnis durch Batterie</div>
            <div class="metric-value green">${fmt(total)}</div>
          </div>

          <div class="divider"></div>

          <div class="metric" style="margin-bottom:12px">
            <div class="metric-label">Ersparnis ggü. System ohne Akku</div>
            <div class="metric-value">${fmt(vsNoBatt)}</div>
          </div>

          ${vsNoBatt !== null && vsNoBatt > 0 ? `
            <div class="bar-section">
              <div class="bar-label">
                <span>Batterie-Anteil</span>
                <span>${barPct.toFixed(0)}%</span>
              </div>
              <div class="bar-bg"><div class="bar-fill"></div></div>
              <div class="bar-hint">Anteil der Gesamtersparnis durch aktive Steuerung</div>
            </div>
          ` : ''}

        </div>
      </ha-card>
    `;
  }

  getCardSize() { return 3; }
}


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
  }

  setConfig(config) {
    this._config = config || {};
    if (!this._initialized) this._render();
    else this._syncValues();
  }

  _monetarySensors() {
    if (!this._hass) return [];
    return Object.entries(this._hass.states)
      .filter(([, s]) => s.attributes?.unit_of_measurement === '€')
      .map(([id]) => id)
      .sort();
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
      </style>
      <div class="form">
        <div>
          <label>Titel (optional)</label>
          <input id="title" type="text" placeholder="Batterie Ersparnisse">
        </div>
        <div>
          <label>Sensor: Gesamtersparnis</label>
          <select id="entity_total"></select>
        </div>
        <div>
          <label>Sensor: Ersparnis ggü. ohne Akku</label>
          <select id="entity_vs_no_batt"></select>
        </div>
      </div>
    `;

    this.shadowRoot.querySelector('#title').addEventListener('change', e =>
      this._dispatch({ ...this._config, title: e.target.value }));
    this.shadowRoot.querySelector('#entity_total').addEventListener('change', e =>
      this._dispatch({ ...this._config, entity_total: e.target.value }));
    this.shadowRoot.querySelector('#entity_vs_no_batt').addEventListener('change', e =>
      this._dispatch({ ...this._config, entity_vs_no_batt: e.target.value }));

    this._initialized = true;
    this._populateSelects();
    this._syncValues();
  }

  _populateSelects() {
    const sensors = this._monetarySensors();
    const opts = sensors.map(id => `<option value="${id}">${this._hass.states[id].attributes.friendly_name || id}</option>`).join('');
    ['entity_total', 'entity_vs_no_batt'].forEach(id => {
      const sel = this.shadowRoot.querySelector(`#${id}`);
      if (sel) sel.innerHTML = '<option value="">– bitte wählen –</option>' + opts;
    });
  }

  _syncValues() {
    const active = this.shadowRoot.activeElement;
    const set = (id, val) => {
      const el = this.shadowRoot.querySelector(`#${id}`);
      if (el && el !== active) el.value = val || '';
    };
    set('title', this._config.title);
    set('entity_total', this._config.entity_total || 'sensor.batterie_ersparnis_gesamt');
    set('entity_vs_no_batt', this._config.entity_vs_no_batt || 'sensor.batterie_ersparnis_ggu_ohne_akku');
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
  name: 'Batterie Ersparnisse',
  description: 'Zeigt Gesamtersparnis und Vergleich ggü. System ohne Akku',
  preview: true,
});
