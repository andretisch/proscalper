"""Отбор символов «в игре» и замер размаха."""

from __future__ import annotations

from app.market import kline_range_pct, rank_candidates


def _ticker(symbol, last, high, low, turnover, change=0.0):
    return {
        "symbol": symbol,
        "lastPrice": str(last),
        "highPrice24h": str(high),
        "lowPrice24h": str(low),
        "turnover24h": str(turnover),
        "price24hPcnt": str(change),
    }


def test_kline_range_uses_high_low_extremes():
    rows = [
        ["0", "10", "12", "9", "11", "1", "1"],
        ["0", "11", "11.5", "8", "10", "1", "1"],
    ]
    assert kline_range_pct(rows) == (12 - 8) / 8 * 100


def test_kline_range_none_without_data():
    assert kline_range_pct([]) is None


def test_ranks_by_range_not_by_change():
    tickers = [
        _ticker("AUSDT", 1, 1.05, 1.0, 5e7, 0.04),  # 5% размах
        _ticker("BUSDT", 1, 1.30, 1.0, 5e7, 0.01),  # 30% размах
    ]
    ranked = rank_candidates(tickers)
    assert [c.symbol for c in ranked] == ["BUSDT", "AUSDT"]


def test_illiquid_symbol_excluded():
    tickers = [_ticker("XUSDT", 1, 2.0, 1.0, 1e6)]
    assert rank_candidates(tickers) == []


def test_flat_symbol_excluded():
    tickers = [_ticker("XUSDT", 1, 1.01, 1.0, 5e7)]
    assert rank_candidates(tickers, min_range_pct=3.0) == []


def test_malformed_ticker_skipped():
    tickers = [{"symbol": "XUSDT"}, _ticker("YUSDT", 1, 1.5, 1.0, 5e7)]
    assert [c.symbol for c in rank_candidates(tickers)] == ["YUSDT"]
