"""Accumulate order book snapshots (lightweight SQLite)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.density import book_range_pct, find_density


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
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(snapshots)")}
            for column in ("wall_share", "wall_dist_pct", "book_range_pct"):
                if column not in existing:
                    conn.execute(f"ALTER TABLE snapshots ADD COLUMN {column} REAL")
            conn.commit()

    @staticmethod
    def parse_book(
        raw: dict[str, Any],
        market_type: str,
        max_wall_dist_pct: float = 0.45,
        *,
        scan_pct: float = 0.6,
        min_wall_dist_pct: float = 0.02,
        min_depth_share: float = 0.15,
        min_wall_ratio: float = 8.0,
    ) -> dict[str, Any]:
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

        density = (
            find_density(
                bids,
                asks,
                mid,
                scan_pct=scan_pct,
                min_dist_pct=min_wall_dist_pct,
                max_dist_pct=max_wall_dist_pct,
                min_depth_share=min_depth_share,
                min_ratio=min_wall_ratio,
            )
            if mid
            else None
        )

        return {
            "symbol": raw.get("s"),
            "market_type": market_type,
            "mid": mid,
            "spread": spread,
            "wall_side": density.side if density else None,
            "wall_price": density.price if density else None,
            "wall_size": density.size if density else None,
            "wall_ratio": density.ratio if density else None,
            "wall_share": density.depth_share if density else None,
            "wall_dist_pct": density.dist_pct if density else None,
            "density": density,
            "book_range_pct": (
                max(book_range_pct(bids, mid), book_range_pct(asks, mid))
                if mid
                else 0.0
            ),
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
                    wall_share, wall_dist_pct, book_range_pct,
                    bids_json, asks_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    snap.get("wall_share"),
                    snap.get("wall_dist_pct"),
                    snap.get("book_range_pct"),
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

    def recent_range_pct(
        self,
        symbol: str,
        window_sec: float = 900.0,
        market_type: str = "perp",
        min_samples: int = 5,
    ) -> float | None:
        """Реальный размах цены за окно, %.

        Единственная честная оценка «есть ли ход, который окупит комиссию»:
        измеряем, сколько символ прошёл на самом деле, а не гадаем по стакану.
        None — недостаточно истории, торговать вслепую нельзя.
        """
        since = time.time() - window_sec
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT MIN(mid) AS lo, MAX(mid) AS hi, COUNT(*) AS n
                FROM snapshots
                WHERE symbol = ? AND market_type = ? AND ts >= ? AND mid IS NOT NULL
                """,
                (symbol, market_type, since),
            ).fetchone()
        if not row or (row["n"] or 0) < min_samples:
            return None
        lo, hi = row["lo"], row["hi"]
        if not lo or lo <= 0 or hi is None:
            return None
        return (hi - lo) / lo * 100.0

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])

    def prune(self, retention_days: float, vacuum: bool = False) -> int:
        """Удалить снимки старше retention_days. 0 — не трогать базу."""
        if retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        with self._connect() as conn:
            removed = conn.execute(
                "DELETE FROM snapshots WHERE ts < ?", (cutoff,)
            ).rowcount
            conn.commit()
        if removed and vacuum:
            # VACUUM возвращает место файловой системе и не работает внутри
            # транзакции, поэтому отдельным соединением.
            conn = self._connect()
            try:
                conn.execute("VACUUM")
            finally:
                conn.close()
        return int(removed or 0)
