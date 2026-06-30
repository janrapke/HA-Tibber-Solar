console.info('%c APPLIANCE-CARD %c v1.0 ', 'color:white;background:#2196F3;font-weight:bold', 'color:#2196F3;background:white;font-weight:bold');

class ApplianceCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._hass = null;
    this._config = null;
    this._entities = null;
  }

  static getStubConfig() {
    return { entity: '' };
  }

  setConfig(config) {
    if (!config.entity) throw new Error('Bitte "entity" (Status-Sensor) angeben');
    this._config = config;
    this._entities = null;
    // Render placeholder immediately so HA card picker spinner goes away
    if (this.shadowRoot) {
      this.shadowRoot.innerHTML = '<div style="padding:16px;font-family:var(--paper-font-body1_-_font-family,sans-serif)">&#8635; Gerätekarte lädt…</div>';
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._entities) this._resolveEntities();
    this._render();
  }

  _resolveEntities() {
    const statusId = this._config.entity;
    const entry = this._hass.entities?.[statusId];
    if (!entry?.unique_id) return;

    const base = entry.unique_id.replace(/_status$/, '');
    const all = Object.values(this._hass.entities || {});
    const find = uid => all.find(e => e.unique_id === uid)?.entity_id;

    const device = this._hass.devices?.[entry.device_id];
    this._entities = {
      status: statusId,
      timer: find(`${base}_timer`),
      select: find(`${base}_proposal_select`),
      confirm: find(`${base}_confirm_btn`),
      cancel: find(`${base}_cancel_btn`),
      scheduleSelect: find(`${base}_schedule_granularity`),
      deviceName: this._config.title || device?.name || 'Gerät',
    };
  }

  _render() {
    const hass = this._hass;
    const ents = this._entities;
    if (!ents || !hass) return;

    const statusState = hass.states[ents.status];
    const timerState = ents.timer ? hass.states[ents.timer] : null;
    const selectState = ents.select ? hass.states[ents.select] : null;
    const scheduleState = ents.scheduleSelect ? hass.states[ents.scheduleSelect] : null;
    if (!statusState) return;

    const status = statusState.state;
    const attrs = statusState.attributes || {};
    const programs = attrs.programme || [];
    const probs = attrs.wahrscheinlichkeiten || {};
    const hint = attrs.hinweis;
    const plannedStart = attrs.geplanter_start;
    const estimatedCost = attrs.geschaetzte_kosten_ct;

    const isRunning = status.startsWith('Läuft');
    const isWaiting = status.startsWith('Start in') || status === 'Wartet auf Start...';

    const selectOptions = selectState?.attributes?.options || [];
    const currentOption = selectState?.state;
    const hasProposals = selectOptions.length > 0 && selectOptions[0] !== 'Keine Vorschläge';

    const scheduleOptions = scheduleState?.attributes?.options || [];
    const scheduleOption = scheduleState?.state || '';

    const statusColor = isRunning ? 'var(--success-color,#4caf50)'
      : isWaiting ? 'var(--warning-color,#ff9800)'
      : 'var(--secondary-text-color)';

    const icon = this._config.icon || 'mdi:washing-machine';

    this.shadowRoot.innerHTML = `
      <style>
        * { box-sizing: border-box; }
        ha-card { overflow: hidden; }

        .header {
          display: flex;
          align-items: center;
          gap: 10px;
          padding: 14px 16px 12px;
          border-bottom: 1px solid var(--divider-color);
        }
        .header ha-icon { color: var(--primary-color); --mdi-icon-size: 22px; flex-shrink: 0; }
        .header-name { flex: 1; font-size: 1.05em; font-weight: 500; }
        .status-chip {
          font-size: 0.76em; font-weight: 500;
          padding: 3px 10px; border-radius: 12px;
          background: ${statusColor}1a; color: ${statusColor};
          border: 1px solid ${statusColor}44;
          white-space: nowrap; max-width: 170px;
          overflow: hidden; text-overflow: ellipsis;
        }

        .body { padding: 14px 16px; }

        .hint {
          font-size: 0.85em; color: var(--secondary-text-color);
          padding: 9px 11px; background: var(--secondary-background-color);
          border-radius: 8px; margin-bottom: 14px;
        }

        .section-label {
          font-size: 0.75em; color: var(--secondary-text-color);
          text-transform: uppercase; letter-spacing: 0.06em;
          font-weight: 600; margin-bottom: 7px;
        }

        /* Running: prob bars */
        .prob-row { margin-bottom: 10px; }
        .prob-label { display: flex; justify-content: space-between; font-size: 0.85em; margin-bottom: 4px; }
        .prob-name { font-weight: 500; }
        .prob-pct { color: var(--secondary-text-color); }
        .bar-bg { height: 6px; background: var(--divider-color); border-radius: 3px; overflow: hidden; }
        .bar-fill { height: 100%; border-radius: 3px; background: var(--primary-color); }
        .countdown { text-align: center; font-size: 2.3em; font-weight: 300; padding: 10px 0 2px; }
        .countdown-sub { text-align: center; font-size: 0.8em; color: var(--secondary-text-color); margin-bottom: 6px; }

        /* Waiting */
        .info-box {
          background: var(--secondary-background-color); border-radius: 8px;
          padding: 10px 12px; margin-bottom: 14px;
        }
        .info-row { display: flex; justify-content: space-between; font-size: 0.88em; padding: 3px 0; }
        .info-label { color: var(--secondary-text-color); }
        .info-value { font-weight: 500; }

        /* Idle */
        .proposal-wrap { margin-bottom: 10px; }
        select {
          width: 100%; padding: 9px 10px; border-radius: 8px;
          border: 1px solid var(--divider-color);
          background: var(--card-background-color); color: var(--primary-text-color);
          font-size: 0.9em; cursor: pointer;
        }
        select:focus { outline: 2px solid var(--primary-color); border-color: transparent; }

        .actions { display: flex; gap: 8px; margin-top: 12px; }
        .btn {
          padding: 10px 14px; border-radius: 8px; border: none;
          cursor: pointer; font-size: 0.88em; font-weight: 500;
          transition: filter 0.15s; white-space: nowrap;
        }
        .btn:active { filter: brightness(0.85); }
        .btn-confirm { flex: 1; background: var(--primary-color); color: var(--text-primary-color,#fff); }
        .btn-reload { background: var(--secondary-background-color); color: var(--secondary-text-color); border: 1px solid var(--divider-color); }
        .btn-cancel { background: var(--secondary-background-color); color: var(--secondary-text-color); border: 1px solid var(--divider-color); }

        .divider { height: 1px; background: var(--divider-color); margin: 12px 0; }

      </style>

      <ha-card>
        <div class="header">
          <ha-icon icon="${icon}"></ha-icon>
          <span class="header-name">${ents.deviceName}</span>
          <span class="status-chip">${status}</span>
        </div>
        <div class="body">
          ${isRunning ? this._renderRunning(probs, timerState) : ''}
          ${isWaiting ? this._renderWaiting(plannedStart, estimatedCost) : ''}
          ${!isRunning && !isWaiting
            ? this._renderIdle(hint, selectOptions, currentOption, hasProposals)
            : ''}
        </div>
      </ha-card>
    `;

    // Events
    this.shadowRoot.querySelector('#proposal-sel')?.addEventListener('change', e =>
      this._selectOption(ents.select, e.target.value));

    this.shadowRoot.querySelector('.btn-confirm')?.addEventListener('click', () =>
      this._pressButton(ents.confirm));

    this.shadowRoot.querySelector('.btn-cancel')?.addEventListener('click', () =>
      this._pressButton(ents.cancel));

    this.shadowRoot.querySelector('.btn-reload')?.addEventListener('click', () => {
      if (ents.select && this._hass) {
        // Force recalculate by calling the invalidate service (reload via select entity refresh)
        // We do this by briefly calling a dummy select then correct to trigger re-render
        this._hass.callService('homeassistant', 'update_entity', { entity_id: ents.select });
      }
    });
  }

  _renderRunning(probs, timerState) {
    const timer = timerState?.state;
    const showTimer = timer && timer !== 'Nicht geplant' && timer !== '0h 0m';
    const probEntries = Object.entries(probs);
    return `
      ${probEntries.length > 0 ? `
        <div class="section-label" style="margin-bottom:9px">Programmerkennung</div>
        ${probEntries.map(([name, pct]) => {
          const val = parseInt(pct) || 0;
          return `<div class="prob-row">
            <div class="prob-label"><span class="prob-name">${name}</span><span class="prob-pct">${pct}</span></div>
            <div class="bar-bg"><div class="bar-fill" style="width:${val}%"></div></div>
          </div>`;
        }).join('')}
      ` : ''}
      ${showTimer ? `<div class="countdown">${timer}</div><div class="countdown-sub">verbleibend</div>` : ''}
    `;
  }

  _renderWaiting(plannedStart, estimatedCost) {
    let startDisplay = '–';
    if (plannedStart) {
      const d = new Date(plannedStart);
      startDisplay = d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' }) + ' Uhr';
    }
    return `
      <div class="info-box">
        <div class="info-row"><span class="info-label">Geplanter Start</span><span class="info-value">${startDisplay}</span></div>
        ${estimatedCost != null ? `<div class="info-row"><span class="info-label">Geschätzte Kosten</span><span class="info-value">${estimatedCost} ct</span></div>` : ''}
      </div>
      <div class="actions">
        <button class="btn btn-cancel">Abbrechen</button>
      </div>
    `;
  }

  _renderIdle(hint, selectOptions, currentOption, hasProposals) {
    if (hint) return `<div class="hint">💡 ${hint}</div>`;
    if (!hasProposals) return `<div class="hint">Keine Vorschläge — Tibber-Preise prüfen.</div>`;

    return `
      <div class="proposal-wrap">
        <div class="section-label">Startzeit wählen</div>
        <select id="proposal-sel">
          ${selectOptions.map(o => `<option ${o === currentOption ? 'selected' : ''}>${o}</option>`).join('')}
        </select>
      </div>
      <div class="actions">
        <button class="btn btn-reload" title="Neue Berechnung">↻</button>
        <button class="btn btn-confirm">Plan übernehmen</button>
      </div>
    `;
  }

  _selectOption(entityId, option) {
    if (!entityId || !this._hass) return;
    this._hass.callService('select', 'select_option', { entity_id: entityId, option });
  }

  _pressButton(entityId) {
    if (!entityId || !this._hass) return;
    this._hass.callService('button', 'press', { entity_id: entityId });
  }

  getCardSize() { return 3; }

  static getConfigElement() {
    return document.createElement('appliance-card-editor');
  }
}

class ApplianceCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
    this._hass = null;
    this._initialized = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._render();
    } else {
      this._populateEntityOptions();
    }
  }

  setConfig(config) {
    this._config = config || {};
    if (!this._initialized) {
      this._render();
    } else {
      this._syncValues();
    }
  }

  _statusEntities() {
    if (!this._hass) return [];
    return Object.keys(this._hass.states)
      .filter(id => id.endsWith('_status') && this._hass.states[id].attributes?.friendly_name?.startsWith('Smart Device:'))
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
        select:focus, input:focus { outline: 2px solid var(--primary-color); border-color: transparent; }
      </style>
      <div class="form">
        <div>
          <label>Gerät (Status-Sensor) *</label>
          <select id="entity"><option value="">– bitte wählen –</option></select>
        </div>
        <div>
          <label>Anzeigename (optional)</label>
          <input id="title" type="text" placeholder="z.B. Waschmaschine">
        </div>
        <div>
          <label>Icon (optional, MDI)</label>
          <input id="icon" type="text" placeholder="mdi:washing-machine">
        </div>
      </div>
    `;

    this.shadowRoot.querySelector('#entity').addEventListener('change', e => {
      this._dispatch({ ...this._config, entity: e.target.value });
    });
    this.shadowRoot.querySelector('#title').addEventListener('change', e => {
      this._dispatch({ ...this._config, title: e.target.value });
    });
    this.shadowRoot.querySelector('#icon').addEventListener('change', e => {
      this._dispatch({ ...this._config, icon: e.target.value });
    });

    this._initialized = true;
    this._populateEntityOptions();
    this._syncValues();
  }

  _populateEntityOptions() {
    const sel = this.shadowRoot.querySelector('#entity');
    if (!sel || !this._hass) return;
    const current = sel.value || this._config.entity || '';
    const entities = this._statusEntities();
    sel.innerHTML = '<option value="">– bitte wählen –</option>' +
      entities.map(id => `<option value="${id}">${this._hass.states[id].attributes.friendly_name || id}</option>`).join('');
    sel.value = current;
  }

  _syncValues() {
    const sel = this.shadowRoot.querySelector('#entity');
    const titleEl = this.shadowRoot.querySelector('#title');
    const iconEl = this.shadowRoot.querySelector('#icon');
    if (sel && document.activeElement !== sel) sel.value = this._config.entity || '';
    if (titleEl && document.activeElement !== titleEl) titleEl.value = this._config.title || '';
    if (iconEl && document.activeElement !== iconEl) iconEl.value = this._config.icon || '';
  }

  _dispatch(config) {
    this._config = config;
    this.dispatchEvent(new CustomEvent('config-changed', { detail: { config }, bubbles: true, composed: true }));
  }
}

customElements.define('appliance-card', ApplianceCard);
customElements.define('appliance-card-editor', ApplianceCardEditor);
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'appliance-card',
  name: 'Smart Appliance Card',
  description: 'Steuerung für Smart Battery Optimizer Geräte',
});
