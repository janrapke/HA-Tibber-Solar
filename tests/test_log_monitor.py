"""Log sensor source: warnings/errors of the watched loggers, last 24 h only."""
import importlib.util
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "smart_battery_optimizer"
spec = importlib.util.spec_from_file_location("sbo_log_monitor", BASE / "log_monitor.py")
lm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lm)


def test_counts_warnings_and_errors_of_watched_loggers_only():
    mon = lm.get_monitor()
    assert lm.get_monitor() is mon  # attached once
    mon.records.clear()
    log = logging.getLogger("custom_components.smart_battery_optimizer.coordinator")
    log.info("not counted")
    log.warning("Grid charger %s", "stuck")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("Step failed")
    logging.getLogger("some.other.integration").error("ignored")
    s = mon.summary()
    assert s["fehler_24h"] == 1 and s["warnungen_24h"] == 1
    assert s["letzte"][0]["text"] == "Grid charger stuck"
    assert s["letzte"][1]["quelle"] == "coordinator" and "ValueError: boom" in s["letzte"][1]["text"]


def test_old_records_drop_out_of_the_window():
    mon = lm.get_monitor()
    mon.records.clear()
    now = datetime.now(timezone.utc)
    mon.records.append((now - timedelta(hours=25), "ERROR", "x", "old"))
    mon.records.append((now - timedelta(hours=1), "WARNING", "x", "new"))
    s = mon.summary(now)
    assert s["fehler_24h"] == 0 and s["warnungen_24h"] == 1
