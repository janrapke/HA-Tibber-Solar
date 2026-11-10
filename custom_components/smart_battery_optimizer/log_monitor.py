"""Keeps the integration's own warnings and errors for a sensor.

Pure Python (logging only, no Home Assistant imports), so the tests can run it.
Since HA 2026.10 `GET /api/error_log` answers 404, and the nightly agent only has REST:
this handler collects WARNING and above from the watched loggers, and a sensor shows the
count of the last 24 hours with the latest messages as attributes.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timedelta, timezone

WATCHED_LOGGERS = (
    "custom_components.smart_battery_optimizer",
    "homeassistant.components.tesla_fleet",
)
KEEP = 200
WINDOW = timedelta(hours=24)
MSG_LEN = 300


class LogMonitor(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self._lock_rec = threading.Lock()
        self.records: deque[tuple[datetime, str, str, str]] = deque(maxlen=KEEP)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                msg += f" | {type(record.exc_info[1]).__name__}: {record.exc_info[1]}"
            when = datetime.fromtimestamp(record.created, timezone.utc)
            with self._lock_rec:
                self.records.append((when, record.levelname, record.name, msg[:MSG_LEN]))
        except Exception:  # noqa: BLE001  (a log handler must never raise)
            pass

    def recent(self, now: datetime | None = None) -> list[tuple[datetime, str, str, str]]:
        now = now or datetime.now(timezone.utc)
        with self._lock_rec:
            return [r for r in self.records if now - r[0] <= WINDOW]

    def summary(self, now: datetime | None = None, last: int = 10) -> dict:
        rows = self.recent(now)
        errors = sum(1 for r in rows if r[1] in ("ERROR", "CRITICAL"))
        return {
            "fehler_24h": errors,
            "warnungen_24h": len(rows) - errors,
            "letzte": [
                {"zeit": r[0].isoformat(timespec="seconds"), "stufe": r[1],
                 "quelle": r[2].rsplit(".", 1)[-1], "text": r[3]}
                for r in rows[-last:]
            ],
        }


_MONITOR: LogMonitor | None = None


def get_monitor() -> LogMonitor:
    """One handler per process, attached once (survives reloads of the config entry)."""
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = LogMonitor()
        for name in WATCHED_LOGGERS:
            logging.getLogger(name).addHandler(_MONITOR)
    return _MONITOR
