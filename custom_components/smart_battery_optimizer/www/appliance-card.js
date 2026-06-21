class ApplianceCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._hass = null;
    this._config = null;
    this._entities = null;
  }

  setConfig(config) {
    if (!config.entity) throw new Error('Bitte "entity" (Status-Sensor) angeben');
    this._config = config;
    this._entities = null;
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

        /* Schedule setting row */
        .setting-row {
          display: flex; align-items: center; gap: 10px;
          padding-top: 12px; margin-top: 2px;
          border-top: 1px solid var(--divider-color);
        }
        .setting-label { font-size: 0.83em; color: var(--secondary-text-color); flex-shrink: 0; }
        .setting-select {
          flex: 1; padding: 5px 8px; border-radius: 6px;
          border: 1px solid var(--divider-color);
          background: var(--card-background-color); color: var(--primary-text-color);
          font-size: 0.83em; cursor: pointer;
        }
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
          ${scheduleOptions.length > 0 && !isRunning ? `
            <div class="setting-row">
              <span class="setting-label">Zeitsteuerung:</span>
              <select class="setting-select" id="schedule-sel">
                ${scheduleOptions.map(o => `<option ${o === scheduleOption ? 'selected' : ''}>${o}</option>`).join('')}
              </select>
            </div>
          ` : ''}
        </div>
      </ha-card>
    `;

    // Events
    this.shadowRoot.querySelector('#proposal-sel')?.addEventListener('change', e =>
      this._selectOption(ents.select, e.target.value));

    this.shadowRoot.querySelector('#schedule-sel')?.addEventListener('change', e =>
      this._selectOption(ents.scheduleSelect, e.target.value));

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
}

customElements.define('appliance-card', ApplianceCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'appliance-card',
  name: 'Smart Appliance Card',
  description: 'Steuerung für Smart Battery Optimizer Geräte',
});
