"""Accumulate order book snapshots (lightweight SQLite)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class OrderBookStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    mid REAL,
                    spread REAL,
                    wall_side TEXT,
                    wall_price REAL,
                    wall_size REAL,
                    wall_ratio REAL,
                    bids_json TEXT NOT NULL,
                    asks_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ob_sym_ts ON snapshots(symbol, ts)"
            )
            conn.commit()

    @staticmethod
    def parse_book(raw: dict[str, Any], market_type: str) -> dict[str, Any]:
        bids = [[float(p), float(s)] for p, s in (raw.get("b") or [])]
        asks = [[float(p), float(s)] for p, s in (raw.get("a") or [])]
        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        mid = (
            (best_bid + best_ask) / 2
            if best_bid is not None and best_ask is not None
            else None
        )
        spread = (
            (best_ask - best_bid)
            if best_bid is not None and best_ask is not None
            else None
        )

        sizes = [s for _, s in bids[:10]] + [s for _, s in asks[:10]]
        median = sorted(sizes)[len(sizes) // 2] if sizes else 0.0
        wall_side = wall_price = wall_size = wall_ratio = None
        threshold = median * 3 if median > 0 else 0
        candidates = [("bid", p, s) for p, s in bids[:25] if s >= threshold] + [
            ("ask", p, s) for p, s in asks[:25] if s >= threshold
        ]
        if candidates:
            # pick largest wall
            side, price, size = max(candidates, key=lambda x: x[2])
            wall_side, wall_price, wall_size = side, price, size
            wall_ratio = (size / median) if median else None

        return {
            "symbol": raw.get("s"),
            "market_type": market_type,
            "mid": mid,
            "spread": spread,
            "wall_side": wall_side,
            "wall_price": wall_price,
            "wall_size": wall_size,
            "wall_ratio": wall_ratio,
            "bids": bids,
            "asks": asks,
            "ts": time.time(),
        }

    def save(self, snap: dict[str, Any]) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO snapshots (
                    ts, symbol, market_type, mid, spread,
                    wall_side, wall_price, wall_size, wall_ratio,
                    bids_json, asks_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap["ts"],
                    snap["symbol"],
                    snap["market_type"],
                    snap.get("mid"),
                    snap.get("spread"),
                    snap.get("wall_side"),
                    snap.get("wall_price"),
                    snap.get("wall_size"),
                    snap.get("wall_ratio"),
                    json.dumps(snap.get("bids") or []),
                    json.dumps(snap.get("asks") or []),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def latest(self, symbol: str, market_type: str = "perp") -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM snapshots
                WHERE symbol = ? AND market_type = ?
                ORDER BY id DESC LIMIT 1
                """,
                (symbol, market_type),
            ).fetchone()
            return dict(row) if row else None

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
