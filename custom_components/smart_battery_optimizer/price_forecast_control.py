"""Daily Tibber price forecast in shadow mode: forecast day 2-4, score against real prices later.

Runs once a day after tomorrow's Tibber prices are known (~13:00). Price history comes from
the recorder statistics of the Tibber price sensor plus the coordinator's current price list,
weather from Open-Meteo (archive for training, forecast for the target days). The switch
"Preisprognose nutzen" is only flipped by the nightly agent once the quality sensor reports
that the release rule is met; nothing uses the forecast before that.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
import homeassistant.util.dt as dt_util

from .const import CONF_TIBBER_PRICE_SENSOR, DOMAIN
from .price_forecast import (
    POINTS, TRAIN_DAYS, Weather, cheap_level, day_hours, make_forecast, parse_open_meteo, quality, score,
    wait_signal,
)

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
KEEP_RESULTS_DAYS = 90
QUALITY_DAYS = 30
RETRY = timedelta(minutes=30)
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY = "wind_speed_100m,shortwave_radiation,temperature_2m"


class PriceForecaster:
    def __init__(self, hass, entry_id: str, config: dict, coordinator) -> None:
        self.hass = hass
        self.config = config
        self.coordinator = coordinator
        self.store = Store(hass, STORE_VERSION, f"{DOMAIN}.price_forecast.{entry_id}")
        self.forecasts: dict = {}   # target date -> list of {issued, horizon, hours, day1_cheap}
        self.results: list[dict] = []
        self.use_enabled = False
        self.done_for: str | None = None  # issue date of the last run
        self._next_try: datetime | None = None
        self.listeners: list = []

    async def async_start(self) -> None:
        data = await self.store.async_load() or {}
        self.forecasts = data.get("forecasts", {})
        self.results = data.get("results", [])
        self.use_enabled = bool(data.get("use_enabled"))
        self.done_for = data.get("done_for")

    async def async_save(self) -> None:
        await self.store.async_save({"forecasts": self.forecasts, "results": self.results,
                                     "use_enabled": self.use_enabled, "done_for": self.done_for})

    def _changed(self) -> None:
        for cb in list(self.listeners):
            cb()

    async def async_set_use(self, on: bool) -> None:
        self.use_enabled = on
        await self.async_save()
        self._changed()

    # ------------------------------------------------------------------ data
    async def _price_history(self, today: date, tz) -> dict:
        """Hourly prices (UTC hour -> EUR/kWh): recorder statistics plus the current Tibber list."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        sensor = self.config.get(CONF_TIBBER_PRICE_SENSOR)
        prices: dict = {}
        if sensor:
            start = dt_util.as_utc(datetime(today.year, today.month, today.day, tzinfo=tz)) - timedelta(days=TRAIN_DAYS + 10)
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period, self.hass, start, None, {sensor}, "hour", None, {"mean"})
            for row in stats.get(sensor, []):
                t = row["start"]
                t = datetime.fromtimestamp(t, tz=timezone.utc) if isinstance(t, (int, float)) else dt_util.as_utc(t)
                if row.get("mean") is not None:
                    prices[t.replace(minute=0, second=0, microsecond=0)] = float(row["mean"])
        sums: dict = {}
        for p in self.coordinator.tibber_prices:
            t = dt_util.as_utc(p["datetime"]).replace(minute=0, second=0, microsecond=0)
            sums.setdefault(t, []).append(float(p.get("total") or 0.0))
        for t, v in sums.items():
            prices[t] = sum(v) / len(v)  # the published price wins over the recorded mean
        return prices

    async def _weather(self, today: date) -> Weather:
        session = async_get_clientsession(self.hass)
        lat = ",".join(str(p[0]) for p in POINTS)
        lon = ",".join(str(p[1]) for p in POINTS)
        w = Weather()
        archive = {"latitude": lat, "longitude": lon, "hourly": HOURLY, "timezone": "UTC",
                   "start_date": (today - timedelta(days=TRAIN_DAYS + 10)).isoformat(),
                   "end_date": (today - timedelta(days=6)).isoformat()}
        async with session.get(ARCHIVE_URL, params=archive, timeout=60) as r:
            r.raise_for_status()
            w.merge(parse_open_meteo(await r.json()))
        recent = {"latitude": lat, "longitude": lon, "hourly": HOURLY, "timezone": "UTC",
                  "past_days": 10, "forecast_days": 6}
        async with session.get(FORECAST_URL, params=recent, timeout=60) as r:
            r.raise_for_status()
            w.merge(parse_open_meteo(await r.json()))
        return w

    # ------------------------------------------------------------------ daily run
    async def async_update(self, now: datetime) -> None:
        tz = dt_util.DEFAULT_TIME_ZONE
        local = dt_util.as_local(now)
        today = local.date()
        tomorrow_end = dt_util.as_utc(datetime(today.year, today.month, today.day, tzinfo=tz)) + timedelta(days=2)
        latest = max((p["datetime"] for p in self.coordinator.tibber_prices), default=None)
        if (self.done_for == today.isoformat() or latest is None
                or dt_util.as_utc(latest) < tomorrow_end - timedelta(hours=1)
                or (self._next_try and now < self._next_try)):
            return
        self._next_try = now + RETRY
        prices = await self._price_history(today, tz)
        weather = await self._weather(today)
        self._score(prices, today, tz)
        fc = await self.hass.async_add_executor_job(make_forecast, prices, weather, today, tz)
        day1 = [prices[t] for t in day_hours(today + timedelta(days=1), tz) if t in prices]
        for target, f in fc.items():
            f["issued"] = today.isoformat()
            f["day1_cheap"] = round(cheap_level(day1), 4) if len(day1) >= 20 else None
            self.forecasts.setdefault(target, []).append(f)
        self.done_for = today.isoformat()
        await self.async_save()
        self._changed()
        _LOGGER.info("Price forecast for %s issued", ", ".join(sorted(fc)))

    def _score(self, prices: dict, today: date, tz) -> None:
        """Score forecasts whose target day is fully known (up to tomorrow)."""
        known = today + timedelta(days=1)
        for target in sorted(self.forecasts):
            d = date.fromisoformat(target)
            if d > known:
                continue
            actual = [prices[t] for t in day_hours(d, tz) if t in prices]
            for f in self.forecasts.pop(target):
                s = score(f["hours"], prices)
                if s is None:
                    continue
                r = {"target": target, "issued": f["issued"], "horizon": f["horizon"],
                     **{k: round(v, 4) for k, v in s.items()}}
                if f["horizon"] == 2 and f.get("day1_cheap") is not None and len(actual) >= 20:
                    r["wait"] = wait_signal([f["day1_cheap"]] * 6, list(f["hours"].values()))
                    r["gain"] = round(f["day1_cheap"] - cheap_level(actual), 4)
                self.results.append(r)
        cutoff = (today - timedelta(days=KEEP_RESULTS_DAYS)).isoformat()
        self.results = [r for r in self.results if r["target"] >= cutoff]

    # ------------------------------------------------------------------ views
    def quality(self, today: date | None = None) -> dict:
        today = today or dt_util.now().date()
        cutoff = (today - timedelta(days=QUALITY_DAYS)).isoformat()
        return quality([r for r in self.results if r["target"] >= cutoff])

    def upcoming(self) -> list[dict]:
        """Latest forecast per target day (day 2-4), summary plus hourly values."""
        out = []
        for target in sorted(self.forecasts):
            f = max(self.forecasts[target], key=lambda x: x["issued"])
            vals = list(f["hours"].values())
            out.append({"datum": target, "horizont_tage": f["horizon"], "ausgestellt": f["issued"],
                        "guenstigste_6h": round(cheap_level(vals), 4), "mittel": round(sum(vals) / len(vals), 4),
                        "stunden": {dt_util.as_local(datetime.fromisoformat(t)).strftime("%H"): p
                                    for t, p in f["hours"].items()}})
        return out
