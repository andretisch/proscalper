"""Оценка реального хода цены и отбор символов «в игре».

Скальп по плотности окупается только там, где цена действительно ходит.
Размах берём из минутных свечей: это доступно сразу, в отличие от
собственной истории снимков, которая обнуляется при ротации watchlist.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.bybit_client import BybitClient


@dataclass(frozen=True)
class Candidate:
    symbol: str
    change24_pct: float
    range24_pct: float
    turnover24: float
    last_price: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "change24_pct": round(self.change24_pct, 2),
            "range24_pct": round(self.range24_pct, 2),
            "turnover24_musd": round(self.turnover24 / 1e6, 1),
        }


@dataclass(frozen=True)
class Momentum:
    """Краткосрочный контекст окна: куда идёт цена и где она внутри диапазона."""

    change_pct: float  # закрытие против открытия окна
    range_pct: float  # размах окна
    position: float  # 0 — у минимума окна, 1 — у максимума

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_pct": round(self.change_pct, 3),
            "range_pct": round(self.range_pct, 3),
            "position": round(self.position, 3),
        }


def momentum_from_klines(rows: list[list[str]]) -> Momentum | None:
    """Bybit отдаёт свечи от новых к старым: rows[0] — текущая минута."""
    highs: list[float] = []
    lows: list[float] = []
    for row in rows:
        try:
            highs.append(float(row[2]))
            lows.append(float(row[3]))
        except (IndexError, TypeError, ValueError):
            continue
    if not highs or not lows:
        return None
    try:
        last_close = float(rows[0][4])
        first_open = float(rows[-1][1])
    except (IndexError, TypeError, ValueError):
        return None
    hi, lo = max(highs), min(lows)
    if lo <= 0 or first_open <= 0:
        return None
    span = hi - lo
    return Momentum(
        change_pct=(last_close - first_open) / first_open * 100.0,
        range_pct=span / lo * 100.0,
        position=(last_close - lo) / span if span > 0 else 0.5,
    )


def kline_range_pct(rows: list[list[str]]) -> float | None:
    m = momentum_from_klines(rows)
    return m.range_pct if m else None


class MarketMeter:
    """Окно последних минут с коротким кэшем (экономия REST-лимита)."""

    def __init__(
        self, client: BybitClient, minutes: int = 15, ttl_sec: float = 60.0
    ) -> None:
        self.client = client
        self.minutes = minutes
        self.ttl_sec = ttl_sec
        self._cache: dict[str, tuple[float, Momentum | None]] = {}

    def momentum(self, symbol: str) -> Momentum | None:
        now = time.time()
        hit = self._cache.get(symbol)
        if hit and now - hit[0] < self.ttl_sec:
            return hit[1]
        try:
            rows = self.client.klines(
                symbol, category="linear", interval="1", limit=self.minutes
            )
            value = momentum_from_klines(rows)
        except Exception:
            value = hit[1] if hit else None
        self._cache[symbol] = (now, value)
        return value

    def range_pct(self, symbol: str) -> float | None:
        m = self.momentum(symbol)
        return m.range_pct if m else None


def rank_candidates(
    tickers: list[dict[str, Any]],
    *,
    min_turnover: float = 20_000_000.0,
    min_range_pct: float = 3.0,
    limit: int = 20,
) -> list[Candidate]:
    """Символы «в игре»: широкий суточный диапазон при живом обороте."""
    out: list[Candidate] = []
    for t in tickers:
        try:
            turnover = float(t.get("turnover24h") or 0)
            high = float(t["highPrice24h"])
            low = float(t["lowPrice24h"])
            last = float(t["lastPrice"])
            symbol = str(t["symbol"])
        except (KeyError, TypeError, ValueError):
            continue
        if turnover < min_turnover or low <= 0 or not symbol.endswith("USDT"):
            continue
        range_pct = (high - low) / low * 100.0
        if range_pct < min_range_pct:
            continue
        change = float(t.get("price24hPcnt") or 0) * 100
        out.append(
            Candidate(
                symbol=symbol,
                change24_pct=change,
                range24_pct=range_pct,
                turnover24=turnover,
                last_price=last,
            )
        )
    out.sort(key=lambda c: c.range24_pct, reverse=True)
    return out[:limit]
