/**
 * forecast-chart-card.js
 * Smart Battery Optimizer – Batterie & Dispatch Prognose
 * Canvas-based chart: battery % + Tibber price, colored action backgrounds.
 * No external dependencies.
 *
 * Config:
 *   type: custom:forecast-chart-card
 *   entity: sensor.smart_battery_optimizer_tagesplan_vorhersage
 *   hours: 24          # optional, default 24
 *   title: "Prognose"  # optional
 */

const ACTION_COLORS = {
  DIS: { bg: "rgba(76,175,80,0.18)",  label: "Dispatch",            dot: "#4caf50" },
  SAV: { bg: "rgba(100,120,160,0.13)", label: "Akku sparen",        dot: "#7986cb" },
  LAD: { bg: "rgba(33,150,243,0.18)", label: "Netzladen",           dot: "#2196f3" },
  NEG: { bg: "rgba(80,80,100,0.18)",  label: "Negativpreis",        dot: "#607d8b" },
  OVF: { bg: "rgba(255,152,0,0.18)",  label: "Überschussvermeidung",dot: "#ff9800" },
  FUL: { bg: "rgba(139,195,74,0.18)", label: "Batterie voll",       dot: "#8bc34a" },
  MIN: { bg: "rgba(244,67,54,0.18)",  label: "Batterie Minimum",    dot: "#f44336" },
  PRE: { bg: "rgba(0,188,212,0.18)",  label: "Laderaum-Vorber.",    dot: "#00bcd4" },
  MAN: { bg: "rgba(255,193,7,0.18)",  label: "Manuell",             dot: "#ffc107" },
};
const DEFAULT_COLOR = { bg: "rgba(150,150,150,0.1)", label: "Unbekannt", dot: "#9e9e9e" };

class ForecastChartCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._resizeObserver = null;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("forecast-chart-card: 'entity' is required");
    this._config = { hours: 24, title: "Batterie & Dispatch Prognose", ...config };
    this._build();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 16px; box-sizing: border-box; }
        .title { font-size: 14px; font-weight: 500; color: var(--secondary-text-color); margin-bottom: 12px; }
        canvas { width: 100%; display: block; }
        .legend { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 10px; }
        .legend-item { display: flex; align-items: center; gap: 5px; font-size: 11px; color: var(--secondary-text-color); }
        .legend-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
        .no-data { color: var(--secondary-text-color); font-size: 13px; text-align: center; padding: 32px 0; }
      </style>
      <ha-card>
        <div class="title">${this._config.title}</div>
        <canvas id="c"></canvas>
        <div class="legend" id="legend"></div>
        <div class="no-data" id="nodata" style="display:none">Keine Daten verfügbar</div>
      </ha-card>
    `;
    this._canvas = this.shadowRoot.getElementById("c");
    this._ctx = this._canvas.getContext("2d");
    this._legend = this.shadowRoot.getElementById("legend");
    this._nodata = this.shadowRoot.getElementById("nodata");

    // Re-render on resize
    this._resizeObserver = new ResizeObserver(() => this._render());
    this._resizeObserver.observe(this.shadowRoot.querySelector("ha-card"));
  }

  disconnectedCallback() {
    if (this._resizeObserver) this._resizeObserver.disconnect();
  }

  _render() {
    if (!this._hass || !this._config.entity) return;

    const stateObj = this._hass.states[this._config.entity];
    if (!stateObj) {
      this._showNoData("Entity nicht gefunden");
      return;
    }

    const attrs = stateObj.attributes || {};
    const rawPlan = attrs.hourly_plan || [];
    if (!rawPlan.length) {
      this._showNoData("Keine Plandaten vorhanden");
      return;
    }

    this._nodata.style.display = "none";
    this._canvas.style.display = "block";

    const plan = rawPlan.slice(0, this._config.hours);
    this._drawChart(plan);
    this._drawLegend(plan);
  }

  _showNoData(msg) {
    this._canvas.style.display = "none";
    this._nodata.style.display = "block";
    this._nodata.textContent = msg;
    this._legend.innerHTML = "";
  }

  _drawChart(plan) {
    const card = this.shadowRoot.querySelector("ha-card");
    const dpr = window.devicePixelRatio || 1;
    const cssW = card.clientWidth - 32; // minus padding
    const cssH = Math.max(180, Math.round(cssW * 0.38));

    this._canvas.width  = cssW * dpr;
    this._canvas.height = cssH * dpr;
    this._canvas.style.height = cssH + "px";

    const ctx = this._ctx;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    // Layout
    const PAD_TOP = 14, PAD_BOT = 28, PAD_LEFT = 42, PAD_RIGHT = 42;
    const W = cssW - PAD_LEFT - PAD_RIGHT;
    const H = cssH - PAD_TOP - PAD_BOT;
    const n = plan.length;

    // Data ranges
    const prices = plan.map(b => b.price);
    const pMin = Math.min(...prices);
    const pMax = Math.max(...prices);
    const pRange = pMax - pMin || 1;

    const isDark = this._isDark();
    const textColor   = isDark ? "rgba(255,255,255,0.7)" : "rgba(0,0,0,0.55)";
    const gridColor   = isDark ? "rgba(255,255,255,0.07)" : "rgba(0,0,0,0.07)";
    const battColor   = isDark ? "#42a5f5" : "#1565c0";
    const priceColor  = isDark ? "#ffd54f" : "#e65100";

    const xOf = i => PAD_LEFT + (i / n) * W;
    const battY = v => PAD_TOP + H - (v / 100) * H;
    const priceY = v => PAD_TOP + H - ((v - pMin) / pRange) * H;

    // ── Action background blocks ──────────────────────────────────────────────
    for (let i = 0; i < n; i++) {
      const action = plan[i].planned_action || "???";
      const key = Object.keys(ACTION_COLORS).find(k => action.startsWith(k)) || "???";
      const col = ACTION_COLORS[key] || DEFAULT_COLOR;
      ctx.fillStyle = col.bg;
      ctx.fillRect(xOf(i), PAD_TOP, W / n + 0.5, H);
    }

    // ── Grid lines ────────────────────────────────────────────────────────────
    ctx.strokeStyle = gridColor;
    ctx.lineWidth = 1;
    [0, 25, 50, 75, 100].forEach(pct => {
      const y = battY(pct);
      ctx.beginPath();
      ctx.moveTo(PAD_LEFT, y);
      ctx.lineTo(PAD_LEFT + W, y);
      ctx.stroke();
    });

    // ── Battery area ──────────────────────────────────────────────────────────
    ctx.beginPath();
    ctx.moveTo(xOf(0) + W / n / 2, battY(plan[0].battery_pct_end ?? 50));
    for (let i = 1; i < n; i++) {
      ctx.lineTo(xOf(i) + W / n / 2, battY(plan[i].battery_pct_end ?? 50));
    }
    // Area fill
    const grad = ctx.createLinearGradient(0, PAD_TOP, 0, PAD_TOP + H);
    grad.addColorStop(0, isDark ? "rgba(66,165,245,0.35)" : "rgba(21,101,192,0.25)");
    grad.addColorStop(1, isDark ? "rgba(66,165,245,0.03)" : "rgba(21,101,192,0.03)");

    ctx.lineTo(xOf(n - 1) + W / n / 2, PAD_TOP + H);
    ctx.lineTo(xOf(0) + W / n / 2, PAD_TOP + H);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // Battery line
    ctx.beginPath();
    ctx.moveTo(xOf(0) + W / n / 2, battY(plan[0].battery_pct_end ?? 50));
    for (let i = 1; i < n; i++) {
      ctx.lineTo(xOf(i) + W / n / 2, battY(plan[i].battery_pct_end ?? 50));
    }
    ctx.strokeStyle = battColor;
    ctx.lineWidth = 2.5;
    ctx.lineJoin = "round";
    ctx.stroke();

    // ── Price line ────────────────────────────────────────────────────────────
    ctx.beginPath();
    ctx.moveTo(xOf(0) + W / n / 2, priceY(prices[0]));
    for (let i = 1; i < n; i++) {
      ctx.lineTo(xOf(i) + W / n / 2, priceY(prices[i]));
    }
    ctx.strokeStyle = priceColor;
    ctx.lineWidth = 1.8;
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.setLineDash([]);

    // ── Left Y-axis labels (battery %) ────────────────────────────────────────
    ctx.fillStyle = battColor;
    ctx.font = `bold 10px system-ui, sans-serif`;
    ctx.textAlign = "right";
    [0, 25, 50, 75, 100].forEach(pct => {
      ctx.fillText(pct + "%", PAD_LEFT - 5, battY(pct) + 4);
    });

    // ── Right Y-axis labels (price) ───────────────────────────────────────────
    ctx.fillStyle = priceColor;
    ctx.textAlign = "left";
    const pTicks = this._niceTicks(pMin, pMax, 4);
    pTicks.forEach(v => {
      const y = priceY(v);
      if (y >= PAD_TOP - 2 && y <= PAD_TOP + H + 2) {
        ctx.fillText(this._fmtPrice(v), PAD_LEFT + W + 5, y + 4);
      }
    });

    // ── X-axis hour labels ────────────────────────────────────────────────────
    ctx.fillStyle = textColor;
    ctx.textAlign = "center";
    ctx.font = `10px system-ui, sans-serif`;
    const step = n <= 24 ? 4 : 8;
    for (let i = 0; i < n; i += step) {
      ctx.fillText(plan[i].hour, xOf(i) + W / n / 2, PAD_TOP + H + 14);
    }

    // ── Axis legend labels ────────────────────────────────────────────────────
    ctx.font = `9px system-ui, sans-serif`;
    ctx.fillStyle = battColor;
    ctx.textAlign = "left";
    ctx.fillText("Akku %", PAD_LEFT, PAD_TOP - 4);
    ctx.fillStyle = priceColor;
    ctx.textAlign = "right";
    ctx.fillText("Preis", PAD_LEFT + W, PAD_TOP - 4);
  }

  _drawLegend(plan) {
    const used = new Set(plan.map(b => {
      const a = b.planned_action || "";
      return Object.keys(ACTION_COLORS).find(k => a.startsWith(k)) || null;
    }).filter(Boolean));

    this._legend.innerHTML = [...used].map(k => {
      const c = ACTION_COLORS[k];
      return `<span class="legend-item">
        <span class="legend-dot" style="background:${c.dot}"></span>${c.label}
      </span>`;
    }).join("");
  }

  _niceTicks(min, max, count) {
    const range = max - min || 1;
    const step = this._niceStep(range / count);
    const start = Math.floor(min / step) * step;
    const ticks = [];
    for (let v = start; v <= max + step * 0.5; v += step) ticks.push(+v.toFixed(4));
    return ticks;
  }

  _niceStep(v) {
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const f = v / p;
    if (f < 1.5) return p;
    if (f < 3)   return 2 * p;
    if (f < 7)   return 5 * p;
    return 10 * p;
  }

  _fmtPrice(v) {
    if (Math.abs(v) >= 0.1) return v.toFixed(2) + "€";
    return (v * 100).toFixed(1) + "ct";
  }

  _isDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  static getConfigElement() {
    return document.createElement("forecast-chart-card-editor");
  }

  static getStubConfig() {
    return { entity: "" };
  }

  getCardSize() { return 4; }
}

customElements.define("forecast-chart-card", ForecastChartCard);

// ── Card picker registration ──────────────────────────────────────────────────
window.customCards = window.customCards || [];
window.customCards.push({
  type: "forecast-chart-card",
  name: "Forecast Chart Card",
  description: "Batterie & Dispatch Prognose – Akkulevel + Tibber-Preis + Aktions-Farbkodierung",
  preview: true,
});
