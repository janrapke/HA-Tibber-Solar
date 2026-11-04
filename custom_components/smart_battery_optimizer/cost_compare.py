"""Electricity bill in three scenarios: without any system, solar panels only (direct feed-in), real (with battery).

Per interval the household consumption is reconstructed from the Tibber meter:
    house = grid import - export + battery inverter output + balcony output - grid charger input
Scenario costs (Tibber price of the interval, exported energy is not paid):
    without system : house * price
    solar only     : max(0, house - solar) * price, solar = panel DC power * inverter efficiency + balcony
    real           : grid import * price (includes what the grid charger drew)
"solar saves" = without - solar only, "battery saves" = solar only - real (time shifting minus losses).

Live values are integrated every coordinator cycle; on the first start the past is filled from the
hourly recorder statistics (hourly means, so a bit coarser than the live minute values).
"""
from __future__ import annotations

from datetime import datetime, timedelta
import logging

from homeassistant.helpers.storage import Store
import homeassistant.util.dt as dt_util

from .const import (
    CONF_BALCONY_POWER_SENSOR, CONF_GRID_CHARGER_POWER_W, CONF_GRID_CHARGER_SWITCH, CONF_SOLAR_POWER_SENSOR,
    CONF_TIBBER_CONSUMPTION_SENSOR, CONF_TIBBER_EXPORT_SENSOR, CONF_TIBBER_PRICE_SENSOR, DOMAIN,
)
from .cost_model import add, interval, summarize

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
MAX_GAP_S = 300              # longer gaps between cycles are not integrated (restart, outage)
SAVE_EVERY = timedelta(minutes=10)
BACKFILL_START = datetime(2024, 1, 1)


class CostCompare:
    def __init__(self, hass, entry_id: str, config: dict, coordinator) -> None:
        self.hass = hass
        self.config = config
        self.coordinator = coordinator
        self.store = Store(hass, STORE_VERSION, f"{DOMAIN}.cost_compare.{entry_id}")
        self.days: dict[str, list[float]] = {}
        self.live_from: str | None = None     # ISO time from which live values are integrated
        self.summary: dict = {}
        self._last: datetime | None = None
        self._last_save: datetime | None = None
        self._backfill_done = False
        self.listeners: list = []

    async def async_start(self) -> None:
        data = await self.store.async_load() or {}
        self.days = data.get("days", {})
        self.live_from = data.get("live_from")
        self._backfill_done = bool(data.get("backfill_done"))
        self._summarize()

    async def async_save(self) -> None:
        await self.store.async_save({"days": self.days, "live_from": self.live_from,
                                     "backfill_done": self._backfill_done})

    def _summarize(self) -> None:
        self.summary = summarize(self.days, dt_util.now().date())
        for cb in list(self.listeners):
            cb()

    # ------------------------------------------------------------------ helpers
    def charger_sensor(self) -> str | None:
        sw = self.config.get(CONF_GRID_CHARGER_SWITCH)
        if not sw:
            return None
        cand = "sensor." + sw.split(".", 1)[1] + "_power"
        return cand if self.hass.states.get(cand) is not None else None

    def charger_w(self, read_float) -> float:
        sensor = self.charger_sensor()
        if sensor:
            return read_float(sensor) or 0.0
        sw = self.config.get(CONF_GRID_CHARGER_SWITCH)
        st = self.hass.states.get(sw) if sw else None
        return float(self.config.get(CONF_GRID_CHARGER_POWER_W) or 0) if st and st.state == "on" else 0.0

    # ------------------------------------------------------------------ live
    async def async_update(self, now: datetime, price: float | None, imp: float | None, exp: float, inverter: float,
                           balcony: float, charger: float, panel: float | None) -> None:
        if not self._backfill_done:
            await self._backfill(now)
        last, self._last = self._last, now
        if last is None or price is None or imp is None:
            return
        secs = (now - last).total_seconds()
        if secs <= 0 or secs > MAX_GAP_S:
            return
        if self.live_from and now < dt_util.parse_datetime(self.live_from):
            return  # this hour already came from the backfill
        key = dt_util.as_local(now).date().isoformat()
        self.days[key] = add(self.days.get(key), interval(price, secs / 3600.0, imp, exp, inverter, balcony,
                                                            charger, panel or 0.0))
        if self._last_save is None or now - self._last_save >= SAVE_EVERY:
            self._last_save = now
            self._summarize()
            await self.async_save()

    # ------------------------------------------------------------------ backfill
    async def _backfill(self, now: datetime) -> None:
        """Fill the past from hourly recorder statistics (once)."""
        self._backfill_done = True
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistics_during_period
        except ImportError:
            return
        c = self.config
        price_id, imp_id = c.get(CONF_TIBBER_PRICE_SENSOR), c.get(CONF_TIBBER_CONSUMPTION_SENSOR)
        if not price_id or not imp_id:
            return
        exp_id, bal_id = c.get(CONF_TIBBER_EXPORT_SENSOR), c.get(CONF_BALCONY_POWER_SENSOR)
        inv_id, chg_id = self.coordinator._inverter_output_sensor(), self.charger_sensor()
        panels = c.get(CONF_SOLAR_POWER_SENSOR) or []
        panels = [panels] if isinstance(panels, str) else list(panels)
        ids = {i for i in [price_id, imp_id, exp_id, bal_id, inv_id, chg_id, *panels] if i}
        end = dt_util.as_utc(now).replace(minute=0, second=0, microsecond=0)
        start = dt_util.as_utc(BACKFILL_START.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE))
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, start, end, ids, "hour", None, {"mean"})

        def series(eid):
            out = {}
            for row in stats.get(eid, []) if eid else []:
                ts = row.get("start")
                ts = dt_util.utc_from_timestamp(ts) if isinstance(ts, (int, float)) else ts
                if ts is not None and row.get("mean") is not None:
                    out[ts] = float(row["mean"])
            return out

        price, imp, exp, bal, inv, chg = (series(i) for i in (price_id, imp_id, exp_id, bal_id, inv_id, chg_id))
        pan = [series(p) for p in panels]
        first_solar = min((min(s) for s in pan if s), default=None)
        days: dict = {}
        for ts in sorted(price):
            if ts not in imp or (first_solar is not None and ts < first_solar):
                continue  # before the panels were measured the "solar only" case can not be computed
            key = dt_util.as_local(ts).date().isoformat()
            days[key] = add(days.get(key), interval(
                price[ts], 1.0, imp[ts], exp.get(ts, 0.0), max(0.0, inv.get(ts, 0.0)), bal.get(ts, 0.0),
                max(0.0, chg.get(ts, 0.0)), sum(s.get(ts, 0.0) for s in pan)))
        for k, v in days.items():   # live days already recorded keep their own values
            self.days.setdefault(k, v)
        self.live_from = end.isoformat()
        self._summarize()
        await self.async_save()
        _LOGGER.info("Cost comparison filled from recorder: %d days since %s", len(days), min(days) if days else "-")

    async def async_rebuild(self) -> None:
        """Forget everything and fill again from the recorder (button)."""
        self.days, self.live_from, self._backfill_done = {}, None, False
        await self._backfill(dt_util.utcnow())
