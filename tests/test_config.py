import os

from app.config import load_settings


def test_orderbook_defaults(monkeypatch):
    for key in (
        "ORDERBOOK_SNAPSHOT_INTERVAL_SEC",
        "ORDERBOOK_RETENTION_DAYS",
    ):
        monkeypatch.delenv(key, raising=False)
    s = load_settings()
    assert s.orderbook_snapshot_interval_sec == 15.0
    assert s.orderbook_retention_days == 0.0
