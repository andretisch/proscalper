"""Bybit REST client (testnet/mainnet)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

from app.config import Settings
from app.http_client import make_session


class BybitClient:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.session = make_session(settings)
        self.session.headers.update({"Content-Type": "application/json"})

    def _sign(self, payload: str, ts: str, recv: str) -> str:
        raw = f"{ts}{self.s.bybit_key}{recv}{payload}"
        return hmac.new(
            self.s.bybit_secret.encode(), raw.encode(), hashlib.sha256
        ).hexdigest()

    def _headers(self, payload: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        return {
            "X-BAPI-API-KEY": self.s.bybit_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": self._sign(payload, ts, recv),
        }

    def get_public(self, path: str, params: dict[str, Any] | None = None) -> dict:
        r = self.session.get(
            f"{self.s.bybit_public_base}{path}", params=params or {}, timeout=20
        )
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit public error: {data}")
        return data["result"]

    def get_private(self, path: str, params: dict[str, Any] | None = None) -> dict:
        params = params or {}
        query = urlencode(params)
        r = self.session.get(
            f"{self.s.bybit_base}{path}?{query}",
            headers=self._headers(query),
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit private error: {data}")
        return data["result"]

    def server_time(self) -> dict:
        return self.get_public("/v5/market/time")

    def orderbook(
        self, symbol: str, category: str = "linear", limit: int = 25
    ) -> dict:
        return self.get_public(
            "/v5/market/orderbook",
            {"category": category, "symbol": symbol, "limit": limit},
        )

    def klines(
        self,
        symbol: str,
        category: str = "linear",
        interval: str = "1",
        limit: int = 15,
    ) -> list[list[str]]:
        result = self.get_public(
            "/v5/market/kline",
            {
                "category": category,
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
        )
        return result.get("list") or []

    def tickers(self, category: str = "linear", symbol: str | None = None) -> list:
        params: dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        result = self.get_public("/v5/market/tickers", params)
        return result.get("list") or []

    def wallet_balance(self) -> dict:
        return self.get_private(
            "/v5/account/wallet-balance", {"accountType": "UNIFIED"}
        )

    def fee_rate(self, symbol: str, category: str = "linear") -> dict:
        result = self.get_private(
            "/v5/account/fee-rate",
            {"category": category, "symbol": symbol},
        )
        rows = result.get("list") or []
        return rows[0] if rows else {}
