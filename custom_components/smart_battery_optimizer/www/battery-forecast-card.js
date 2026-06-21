/**
 * Smart Battery Optimizer — Battery Forecast Card
 * Zeigt Akkustand (Ist + Prognose), Tibber-Preis und Dispatch-Plan in einem Chart.
 * Wird automatisch beim HA-Start geladen — keine manuelle Konfiguration nötig.
 */

const ACTION_COLOR = {
  DIS: '#4CAF50',  // grün   — Dispatch (DTU an)
  SAV: '#EF5350',  // rot    — Akku sparen
  OVF: '#FF9800',  // orange — Überschussvermeidung
  FUL: '#81C784',  // hellgrün — Batterie voll
  LAD: '#2196F3',  // blau   — Netzladen
  NEG: '#9C27B0',  // lila   — Negativpreis
  MIN: '#9E9E9E',  // grau   — Minimum
  PRE: '#00BCD4',  // cyan   — Laderaum-Vorbereitung
  MAN: '#795548',  // braun  — Manuell
};

const ACTION_LABEL = {
  DIS: 'Dispatch', SAV: 'Sparen', OVF: 'Überfüll-Schutz',
  FUL: 'Voll', LAD: 'Netzladen', NEG: 'Negativpreis',
  MIN: 'Minimum', PRE: 'Laderaum', MAN: 'Manuell',
};

class BatteryForecastCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._hass = null;
    this._config = {};
    this._history = null;
    this._fetchPending = false;
    this._refreshTimer = null;
    this._lastFetch = 0;
  }

  // ── HA Card API ────────────────────────────────────────────────

  static getStubConfig() {
    return {};  // Alles wird auto-discovered
  }

  static getConfigElement() {
    return document.createElement('battery-forecast-card-editor');
  }

  setConfig(config) {
    this._config = config || {};
    this._history = null;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
    // Historie alle 5 Minuten neu laden
    const now = Date.now();
    if (!this._fetchPending && now - this._lastFetch > 4 * 60 * 1000) {
      this._scheduleFetch(0);
    }
  }

  connectedCallback() {
    this._scheduleFetch(500);
    this._refreshTimer = setInterval(() => this._scheduleFetch(0), 5 * 60 * 1000);
  }

  disconnectedCallback() {
    clearInterval(this._refreshTimer);
  }

  // ── Daten ──────────────────────────────────────────────────────

  _findPlanSensor() {
    if (this._config.plan_sensor) return this._config.plan_sensor;
    const ent = Object.values(this._hass?.entities || {}).find(
      e => e.unique_id?.endsWith('_forecast_plan')
    );
    return ent?.entity_id;
  }

  _findBattSensor() {
    if (this._config.battery_sensor) return this._config.battery_sensor;
    // Aus Attribut des Plan-Sensors lesen
    const planId = this._findPlanSensor();
    return planId ? (this._hass?.states[planId]?.attributes?.battery_sensor || null) : null;
  }

  _scheduleFetch(delay) {
    if (this._fetchPending) return;
    this._fetchPending = true;
    setTimeout(() => this._fetchHistory(), delay);
  }

  async _fetchHistory() {
    try {
      const battSensor = this._findBattSensor();
      if (!battSensor || !this._hass) return;

      const end = new Date();
      const start = new Date(end.getTime() - 8 * 3600 * 1000);
      const url = `history/period/${start.toISOString()}` +
        `?end_time=${end.toISOString()}` +
        `&filter_entity_id=${battSensor}` +
        `&minimal_response=true&significant_changes_only=false`;

      const resp = await this._hass.callApi('GET', url);
      this._history = (resp?.[0] || [])
        .filter(s => s.state !== 'unavailable' && s.state !== 'unknown')
        .map(s => ({ ts: new Date(s.last_changed).getTime(), pct: parseFloat(s.state) }))
        .filter(s => !isNaN(s.pct));

      this._lastFetch = Date.now();
      this._render();
    } catch (e) {
      this._history = [];
    } finally {
      this._fetchPending = false;
    }
  }

  // ── Chart-Rendering ────────────────────────────────────────────

  _render() {
    const hass = this._hass;
    if (!hass) return;

    const planId = this._findPlanSensor();
    const battId = this._findBattSensor();
    const planState = planId ? hass.states[planId] : null;
    const plan = planState?.attributes?.hourly_plan || [];
    const history = this._history || [];
    const currentBatt = battId ? parseFloat(hass.states[battId]?.state) : null;
    const currentMode = hass.states['sensor.' + (planId || '').replace('sensor.', '').replace('_tagesplan_vorhersage', '_current_operating_mode')]?.state || '';

    // Zeitfenster: -8h bis +24h
    const nowTs = Date.now();
    const startTs = nowTs - 8 * 3600 * 1000;
    const endTs = nowTs + 24 * 3600 * 1000;

    // Plan-Blöcke mit Timestamps
    const planStart = new Date(nowTs);
    planStart.setMinutes(Math.floor(planStart.getMinutes() / 15) * 15, 0, 0);
    const planBlocks = plan.map((b, i) => ({
      ...b,
      ts: planStart.getTime() + i * 15 * 60 * 1000,
    }));

    // Preise analysieren
    const prices = planBlocks.map(b => b.price * 100).filter(p => !isNaN(p));
    const maxPrice = prices.length ? Math.max(...prices, 0.1) : 30;

    // SVG aufbauen
    const W = 600, H = 300;
    const P = { t: 28, r: 55, b: 52, l: 42 };
    const cW = W - P.l - P.r;
    const cH = H - P.t - P.b;

    const xOf = ts => P.l + ((ts - startTs) / (endTs - startTs)) * cW;
    const yBatt = pct => P.t + (1 - Math.min(105, Math.max(0, pct)) / 105) * cH;
    const yPrice = ct => P.t + (1 - Math.min(maxPrice * 1.1, Math.max(0, ct)) / (maxPrice * 1.1)) * cH;
    const nowX = xOf(nowTs);

    // ── Preis-Balken + Aktions-Färbung ───────────────────────────
    let priceBars = '';
    let actionStrip = '';
    const stripH = 14;
    const stripY = P.t + cH + 2;

    for (const b of planBlocks) {
      if (b.ts < startTs || b.ts > endTs) continue;
      const x = xOf(b.ts);
      const bW = Math.max(1, (15 * 60 * 1000 / (endTs - startTs)) * cW - 0.5);
      const ct = b.price * 100;
      const barH = Math.max(1, yPrice(0) - yPrice(ct));
      const barY = yPrice(ct);
      const col = ACTION_COLOR[b.planned_action] || '#90A4AE';

      priceBars += `<rect x="${x.toFixed(1)}" y="${barY.toFixed(1)}" width="${bW.toFixed(1)}" height="${barH.toFixed(1)}" fill="${col}" opacity="0.35"/>`;
      actionStrip += `<rect x="${x.toFixed(1)}" y="${stripY}" width="${bW.toFixed(1)}" height="${stripH}" fill="${col}" title="${ACTION_LABEL[b.planned_action] || b.planned_action}"/>`;
    }

    // ── Historische Akku-Linie ────────────────────────────────────
    let histPath = '';
    const histPts = history.filter(h => h.ts >= startTs && h.ts <= nowTs);
    if (histPts.length > 1) {
      histPath = histPts.map((h, i) =>
        `${i === 0 ? 'M' : 'L'}${xOf(h.ts).toFixed(1)},${yBatt(h.pct).toFixed(1)}`
      ).join(' ');
    }

    // ── Prognose-Linie ────────────────────────────────────────────
    let forecastPath = '';
    const fPts = planBlocks.filter(b => b.ts >= nowTs - 900000 && b.ts <= endTs);
    if (fPts.length > 1) {
      forecastPath = fPts.map((b, i) =>
        `${i === 0 ? 'M' : 'L'}${xOf(b.ts).toFixed(1)},${yBatt(b.battery_pct_end).toFixed(1)}`
      ).join(' ');
    }

    // ── X-Achsen-Beschriftung ─────────────────────────────────────
    let xLabels = '';
    const labelInterval = 4 * 3600 * 1000; // alle 4h
    let lt = Math.ceil(startTs / labelInterval) * labelInterval;
    while (lt <= endTs) {
      const x = xOf(lt);
      const d = new Date(lt);
      const lbl = `${d.getHours().toString().padStart(2, '0')}:00`;
      const isNight = d.getHours() === 0;
      xLabels += `<line x1="${x.toFixed(1)}" y1="${P.t}" x2="${x.toFixed(1)}" y2="${P.t + cH}" stroke="#e0e0e0" stroke-width="0.5"/>`;
      xLabels += `<text x="${x.toFixed(1)}" y="${P.t + cH + 30}" text-anchor="middle" font-size="10" fill="${isNight ? '#FF9800' : '#757575'}">${lbl}</text>`;
      if (isNight) {
        const nd = new Date(lt);
        xLabels += `<text x="${(x + 2).toFixed(1)}" y="${P.t + cH + 41}" text-anchor="start" font-size="9" fill="#FF9800">${nd.toLocaleDateString('de-DE', { weekday: 'short', day: 'numeric', month: 'short' })}</text>`;
      }
      lt += labelInterval;
    }

    // ── Y-Achsen-Beschriftung ─────────────────────────────────────
    let yLabels = '';
    for (const pct of [0, 20, 40, 60, 80, 100]) {
      const y = yBatt(pct);
      yLabels += `<line x1="${P.l}" y1="${y.toFixed(1)}" x2="${P.l + cW}" y2="${y.toFixed(1)}" stroke="#e0e0e0" stroke-width="${pct === 0 ? 1 : 0.5}"/>`;
      yLabels += `<text x="${(P.l - 5).toFixed(1)}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-size="10" fill="#757575">${pct}%</text>`;
    }

    // Preis-Y-Achse (rechts)
    let yRightLabels = '';
    const priceSteps = [0, maxPrice * 0.25, maxPrice * 0.5, maxPrice * 0.75, maxPrice].map(Math.round);
    for (const ct of priceSteps) {
      const y = yPrice(ct);
      yRightLabels += `<text x="${(P.l + cW + 5).toFixed(1)}" y="${(y + 3).toFixed(1)}" font-size="10" fill="#FF7043">${ct}ct</text>`;
    }

    // ── Aktuellen SOC-Punkt einzeichnen ───────────────────────────
    let currentDot = '';
    if (currentBatt !== null && !isNaN(currentBatt)) {
      const cy = yBatt(currentBatt);
      currentDot = `<circle cx="${nowX.toFixed(1)}" cy="${cy.toFixed(1)}" r="5" fill="#1565C0" stroke="white" stroke-width="1.5"/>
        <text x="${(nowX + 8).toFixed(1)}" y="${(cy - 6).toFixed(1)}" font-size="11" font-weight="bold" fill="#1565C0">${currentBatt.toFixed(0)}%</text>`;
    }

    // ── Legende ───────────────────────────────────────────────────
    const legendItems = [
      { color: '#1565C0', dash: false, label: 'Akku Ist' },
      { color: '#42A5F5', dash: true,  label: 'Akku Prognose' },
      { color: '#FF7043', dash: false, label: 'Preis', box: true },
    ];
    let legendSvg = legendItems.map((item, i) => {
      const lx = 8 + i * 120;
      if (item.box) {
        return `<rect x="${lx}" y="4" width="20" height="10" fill="${item.color}" opacity="0.5"/>
          <text x="${lx + 24}" y="13" font-size="10" fill="var(--primary-text-color)">${item.label}</text>`;
      }
      const dashAttr = item.dash ? ' stroke-dasharray="5,3"' : '';
      return `<line x1="${lx}" y1="9" x2="${lx + 20}" y2="9" stroke="${item.color}" stroke-width="2"${dashAttr}/>
        <text x="${lx + 24}" y="13" font-size="10" fill="var(--primary-text-color)">${item.label}</text>`;
    }).join('');

    // Aktions-Legende
    const actionLegendItems = Object.entries(ACTION_COLOR).map(([k, c]) => ({ key: k, color: c, label: ACTION_LABEL[k] }));
    const aLegRows = [];
    for (let i = 0; i < actionLegendItems.length; i += 4) {
      const row = actionLegendItems.slice(i, i + 4).map((a, j) => {
        const lx = 8 + j * 110;
        return `<rect x="${lx}" y="0" width="12" height="12" fill="${a.color}" rx="2"/>
          <text x="${lx + 15}" y="10" font-size="10" fill="var(--secondary-text-color)">${a.label}</text>`;
      }).join('');
      aLegRows.push(row);
    }

    const cardTitle = 'Batterie & Dispatch Prognose';
    const statusColor = ACTION_COLOR[this._getCurrentAction(planBlocks, nowTs)] || '#607D8B';

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 12px 16px 8px; box-sizing: border-box; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
        .title { font-size: 14px; font-weight: 500; color: var(--primary-text-color); }
        .status-badge {
          font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px;
          color: white; background: ${statusColor};
        }
        svg { width: 100%; height: auto; display: block; }
        .chart-wrap { position: relative; }
        .loading { color: var(--secondary-text-color); font-size: 12px; text-align: center; padding: 8px 0; }
        .action-legend { margin-top: 4px; }
        .action-legend svg { overflow: visible; }
        path { fill: none; }
      </style>
      <ha-card>
        <div class="header">
          <div class="title">⚡ ${cardTitle}</div>
          <div class="status-badge">${this._getCurrentActionLabel(planBlocks, nowTs)}</div>
        </div>
        <div class="chart-wrap">
          <svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg">
            <!-- Hintergrund -->
            <rect x="${P.l}" y="${P.t}" width="${cW}" height="${cH}" fill="var(--card-background-color,#fff)" stroke="#e0e0e0" stroke-width="0.5"/>

            <!-- Preis-Balken (hinter allem) -->
            ${priceBars}

            <!-- Gitterlinien & Achsen-Beschriftung -->
            ${xLabels}
            ${yLabels}
            ${yRightLabels}

            <!-- Aktions-Streifen (unten) -->
            ${actionStrip}
            <text x="${P.l}" y="${stripY + 10}" font-size="9" fill="#757575">Aktion</text>

            <!-- Historische Akku-Linie (solid) -->
            ${histPath ? `<path d="${histPath}" stroke="#1565C0" stroke-width="2.5" stroke-linejoin="round"/>` : ''}

            <!-- Prognose-Akku-Linie (gestrichelt) -->
            ${forecastPath ? `<path d="${forecastPath}" stroke="#42A5F5" stroke-width="2" stroke-dasharray="7,4" stroke-linejoin="round"/>` : ''}

            <!-- Aktueller SOC Punkt -->
            ${currentDot}

            <!-- "Jetzt"-Linie -->
            <line x1="${nowX.toFixed(1)}" y1="${P.t}" x2="${nowX.toFixed(1)}" y2="${P.t + cH + 18}" stroke="#FF5722" stroke-width="1.5" stroke-dasharray="3,2"/>
            <text x="${nowX.toFixed(1)}" y="${P.t - 5}" text-anchor="middle" font-size="9" fill="#FF5722">Jetzt</text>

            <!-- Linienrahmen vorne -->
            <rect x="${P.l}" y="${P.t}" width="${cW}" height="${cH}" fill="none" stroke="#bdbdbd" stroke-width="1"/>

            <!-- Achsentitel -->
            <text x="${P.l - 36}" y="${(P.t + cH/2).toFixed(0)}" text-anchor="middle" font-size="10" fill="#757575" transform="rotate(-90,${P.l - 36},${(P.t + cH/2).toFixed(0)})">Ladestand %</text>
            <text x="${(P.l + cW + 48).toFixed(0)}" y="${(P.t + cH/2).toFixed(0)}" text-anchor="middle" font-size="10" fill="#FF7043" transform="rotate(90,${(P.l + cW + 48).toFixed(0)},${(P.t + cH/2).toFixed(0)})">Preis ct/kWh</text>

            <!-- Legende oben -->
            <svg x="${P.l}" y="0" width="${cW}" height="20" overflow="visible">
              ${legendSvg}
            </svg>
          </svg>
        </div>

        <!-- Aktions-Legende -->
        <div class="action-legend">
          ${aLegRows.map((row, ri) => `
            <svg width="100%" height="16" viewBox="0 0 460 16" style="display:block;margin-top:2px;">${row}</svg>
          `).join('')}
        </div>

        ${!this._history ? '<div class="loading">Lade Verlaufsdaten…</div>' : ''}
      </ha-card>
    `;
  }

  _getCurrentAction(planBlocks, nowTs) {
    const cur = planBlocks.find(b => b.ts <= nowTs && b.ts + 15 * 60 * 1000 > nowTs);
    return cur?.planned_action || 'SAV';
  }

  _getCurrentActionLabel(planBlocks, nowTs) {
    const key = this._getCurrentAction(planBlocks, nowTs);
    return ACTION_LABEL[key] || key;
  }
}

customElements.define('battery-forecast-card', BatteryForecastCard);

// Karte im HA-Dashboard-Editor registrieren
window.customCards = window.customCards || [];
if (!window.customCards.find(c => c.type === 'battery-forecast-card')) {
  window.customCards.push({
    type: 'battery-forecast-card',
    name: 'Smart Battery Optimizer — Prognose',
    description: 'Batterie Ist & Prognose, Tibber-Preis und Dispatch-Plan mit Verlauf',
    preview: false,
    documentationURL: '',
  });
}
