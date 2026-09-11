"""Bybit REST client (testnet/mainnet)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

from app.config import Settings
from app.http_client import SessionPool, request_with_retry
from app.logging_setup import get_logger

log = get_logger("bybit")


class BybitClient:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self._sessions = SessionPool(settings, {"Content-Type": "application/json"})

    @property
    def session(self):
        return self._sessions.get()

    def _request(self, method: str, url: str, *, label: str, **kwargs):
        return request_with_retry(
            self.session,
            method,
            url,
            timeout=self.s.http_timeout,
            attempts=self.s.http_retry_attempts,
            deadline=kwargs.pop("deadline", None),
            label=label,
            **kwargs,
        )

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

    def get_public(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        deadline: float | None = None,
    ) -> dict:
        r = self._request(
            "GET",
            f"{self.s.bybit_public_base}{path}",
            label=path,
            params=params or {},
            deadline=deadline,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit public error: {data}")
        return data["result"]

    def get_private(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        deadline: float | None = None,
    ) -> dict:
        params = params or {}
        query = urlencode(params)
        r = self._request(
            "GET",
            f"{self.s.bybit_base}{path}?{query}",
            label=path,
            headers=self._headers(query),
            deadline=deadline,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            raise RuntimeError(f"bybit private error: {data}")
        return data["result"]

    def server_time(self) -> dict:
        return self.get_public("/v5/market/time")

    def orderbook(
        self,
        symbol: str,
        category: str = "linear",
        limit: int = 25,
        deadline: float | None = None,
    ) -> dict:
        return self.get_public(
            "/v5/market/orderbook",
            {"category": category, "symbol": symbol, "limit": limit},
            deadline=deadline,
        )

    def klines(
        self,
        symbol: str,
        category: str = "linear",
        interval: str = "1",
        limit: int = 15,
        deadline: float | None = None,
    ) -> list[list[str]]:
        result = self.get_public(
            "/v5/market/kline",
            {
                "category": category,
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
            },
            deadline=deadline,
        )
        return result.get("list") or []

    def tickers(
        self,
        category: str = "linear",
        symbol: str | None = None,
        deadline: float | None = None,
    ) -> list:
        params: dict[str, Any] = {"category": category}
        if symbol:
            params["symbol"] = symbol
        result = self.get_public("/v5/market/tickers", params, deadline=deadline)
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
