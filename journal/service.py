"""Journal business logic: open/close trades, attach order book history."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import connect, init_db

SETUP_IDS = {
    "S1",
    "S1_true_breakout",
    "S2",
    "S2_false_breakout_density",
    "S3",
    "S3_flip_eaten_volume",
    "S4",
    "S4_active_continuation",
    "S5",
    "S5_drain_short",
    "S6",
    "S6_naked_density_micro",
    "other",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_setup(setup_id: str) -> str:
    s = setup_id.strip()
    aliases = {
        "S1": "S1_true_breakout",
        "S2": "S2_false_breakout_density",
        "S3": "S3_flip_eaten_volume",
        "S4": "S4_active_continuation",
        "S5": "S5_drain_short",
        "S6": "S6_naked_density_micro",
    }
    return aliases.get(s, s)


@dataclass
class BookLevel:
    price: float
    size: float


@dataclass
class OrderBookSnapshotIn:
    symbol: str
    bids: list[BookLevel]
    asks: list[BookLevel]
    exchange: str = "bybit"
    market_type: str = "perp"
    ts: str | None = None
    wall_side: str | None = None
    wall_price: float | None = None
    wall_size: float | None = None
    wall_age_sec: float | None = None
    wall_remaining_pct: float | None = None
    label: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    trade_id: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OrderBookSnapshotIn":
        def levels(key: str) -> list[BookLevel]:
            raw = data.get(key) or []
            out: list[BookLevel] = []
            for row in raw:
                if isinstance(row, dict):
                    out.append(BookLevel(float(row["price"]), float(row["size"])))
                else:
                    out.append(BookLevel(float(row[0]), float(row[1])))
            return out

        wall = data.get("wall") or {}
        return cls(
            symbol=str(data["symbol"]).upper(),
            bids=levels("bids"),
            asks=levels("asks"),
            exchange=str(data.get("exchange") or "bybit"),
            market_type=str(data.get("market_type") or "perp"),
            ts=data.get("ts"),
            wall_side=wall.get("side") or data.get("wall_side"),
            wall_price=_maybe_float(wall.get("price") or data.get("wall_price")),
            wall_size=_maybe_float(wall.get("size") or data.get("wall_size")),
            wall_age_sec=_maybe_float(wall.get("age_sec") or data.get("wall_age_sec")),
            wall_remaining_pct=_maybe_float(
                wall.get("remaining_pct") or data.get("wall_remaining_pct")
            ),
            label=data.get("label"),
            meta=dict(data.get("meta") or {}),
            trade_id=data.get("trade_id"),
        )


def _maybe_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)


class Journal:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = init_db(db_path)

    def open_trade(
        self,
        *,
        symbol: str,
        side: str,
        setup_id: str,
        entry_price: float,
        size: float,
        stop_price: float | None = None,
        take_price: float | None = None,
        exchange: str = "bybit",
        regime: str | None = None,
        entry_ts: str | None = None,
        notes: str | None = None,
        checklist: dict[str, Any] | list[Any] | None = None,
    ) -> int:
        side = side.lower()
        if side not in ("long", "short"):
            raise ValueError("side must be long|short")
        setup = normalize_setup(setup_id)
        if setup not in SETUP_IDS and setup_id not in SETUP_IDS:
            # allow unknown but warn via note
            setup = setup_id
        ts = entry_ts or utc_now()
        symbol = symbol.upper()

        with connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO trades (
                    symbol, exchange, side, setup_id, regime, status,
                    entry_ts, entry_price, size, stop_price, take_price,
                    notes, checklist_json
                ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    exchange,
                    side,
                    setup,
                    regime,
                    ts,
                    float(entry_price),
                    float(size),
                    stop_price,
                    take_price,
                    notes,
                    json.dumps(checklist, ensure_ascii=False) if checklist is not None else None,
                ),
            )
            trade_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO trade_fills (trade_id, ts, kind, price, size, note)
                VALUES (?, ?, 'entry', ?, ?, ?)
                """,
                (trade_id, ts, float(entry_price), float(size), "open"),
            )
            conn.execute(
                """
                INSERT INTO trade_events (trade_id, ts, event_type, payload_json)
                VALUES (?, ?, 'open', ?)
                """,
                (
                    trade_id,
                    ts,
                    json.dumps(
                        {
                            "symbol": symbol,
                            "side": side,
                            "setup_id": setup,
                            "entry_price": entry_price,
                            "size": size,
                            "stop_price": stop_price,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()
            return trade_id

    def add_fill(
        self,
        trade_id: int,
        *,
        kind: str,
        price: float,
        size: float,
        ts: str | None = None,
        note: str | None = None,
    ) -> None:
        if kind not in ("entry", "add", "reduce", "exit"):
            raise ValueError("kind must be entry|add|reduce|exit")
        ts = ts or utc_now()
        with connect(self.db_path) as conn:
            trade = conn.execute(
                "SELECT id, status FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            if not trade:
                raise KeyError(f"trade {trade_id} not found")
            if trade["status"] != "open" and kind != "exit":
                raise ValueError(f"trade {trade_id} is {trade['status']}")
            conn.execute(
                """
                INSERT INTO trade_fills (trade_id, ts, kind, price, size, note)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (trade_id, ts, kind, float(price), float(size), note),
            )
            conn.execute(
                """
                INSERT INTO trade_events (trade_id, ts, event_type, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    trade_id,
                    ts,
                    f"fill_{kind}",
                    json.dumps(
                        {"price": price, "size": size, "note": note},
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.execute(
                "UPDATE trades SET updated_at = datetime('now') WHERE id = ?",
                (trade_id,),
            )
            conn.commit()

    def close_trade(
        self,
        trade_id: int,
        *,
        exit_price: float,
        exit_ts: str | None = None,
        exit_reason: str | None = None,
        notes: str | None = None,
        fees_usd: float = 0.0,
    ) -> dict[str, Any]:
        ts = exit_ts or utc_now()
        fees_usd = max(float(fees_usd), 0.0)
        with connect(self.db_path) as conn:
            trade = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            if not trade:
                raise KeyError(f"trade {trade_id} not found")
            if trade["status"] != "open":
                raise ValueError(f"trade {trade_id} already {trade['status']}")

            entry = float(trade["entry_price"])
            size = float(trade["size"])
            side = trade["side"]
            if side == "long":
                gross_pnl = (float(exit_price) - entry) * size
            else:
                gross_pnl = (entry - float(exit_price)) * size
            pnl = gross_pnl - fees_usd

            pnl_r = None
            if trade["stop_price"] is not None:
                stop = float(trade["stop_price"])
                risk = abs(entry - stop) * size
                if risk > 0:
                    pnl_r = pnl / risk

            conn.execute(
                """
                UPDATE trades SET
                    status = 'closed',
                    exit_ts = ?,
                    exit_price = ?,
                    pnl_usd = ?,
                    pnl_r = ?,
                    exit_reason = ?,
                    notes = COALESCE(?, notes),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (ts, float(exit_price), pnl, pnl_r, exit_reason, notes, trade_id),
            )
            conn.execute(
                """
                INSERT INTO trade_fills (trade_id, ts, kind, price, size, note)
                VALUES (?, ?, 'exit', ?, ?, ?)
                """,
                (trade_id, ts, float(exit_price), size, exit_reason),
            )
            conn.execute(
                """
                INSERT INTO trade_events (trade_id, ts, event_type, payload_json)
                VALUES (?, ?, 'close', ?)
                """,
                (
                    trade_id,
                    ts,
                    json.dumps(
                        {
                            "exit_price": exit_price,
                            "gross_pnl_usd": gross_pnl,
                            "fees_usd": fees_usd,
                            "pnl_usd": pnl,
                            "pnl_r": pnl_r,
                            "exit_reason": exit_reason,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()
            return {
                "trade_id": trade_id,
                "gross_pnl_usd": gross_pnl,
                "fees_usd": fees_usd,
                "pnl_usd": pnl,
                "pnl_r": pnl_r,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
            }

    def save_orderbook(self, snap: OrderBookSnapshotIn) -> int:
        if not snap.bids or not snap.asks:
            raise ValueError("bids and asks must be non-empty")
        ts = snap.ts or utc_now()
        best_bid = max(l.price for l in snap.bids)
        best_ask = min(l.price for l in snap.asks)
        mid = (best_bid + best_ask) / 2
        spread = best_ask - best_bid

        with connect(self.db_path) as conn:
            if snap.trade_id is not None:
                row = conn.execute(
                    "SELECT id FROM trades WHERE id = ?", (snap.trade_id,)
                ).fetchone()
                if not row:
                    raise KeyError(f"trade {snap.trade_id} not found")

            cur = conn.execute(
                """
                INSERT INTO orderbook_snapshots (
                    trade_id, symbol, exchange, market_type, ts,
                    mid_price, best_bid, best_ask, spread,
                    wall_side, wall_price, wall_size, wall_age_sec, wall_remaining_pct,
                    bids_json, asks_json, meta_json, label
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap.trade_id,
                    snap.symbol.upper(),
                    snap.exchange,
                    snap.market_type,
                    ts,
                    mid,
                    best_bid,
                    best_ask,
                    spread,
                    snap.wall_side,
                    snap.wall_price,
                    snap.wall_size,
                    snap.wall_age_sec,
                    snap.wall_remaining_pct,
                    json.dumps([asdict(x) for x in snap.bids], ensure_ascii=False),
                    json.dumps([asdict(x) for x in snap.asks], ensure_ascii=False),
                    json.dumps(snap.meta, ensure_ascii=False) if snap.meta else None,
                    snap.label,
                ),
            )
            snapshot_id = int(cur.lastrowid)
            if snap.trade_id is not None:
                conn.execute(
                    """
                    INSERT INTO trade_events (trade_id, ts, event_type, payload_json, snapshot_id)
                    VALUES (?, ?, 'orderbook', ?, ?)
                    """,
                    (
                        snap.trade_id,
                        ts,
                        json.dumps(
                            {
                                "snapshot_id": snapshot_id,
                                "label": snap.label,
                                "wall_side": snap.wall_side,
                                "wall_price": snap.wall_price,
                                "wall_size": snap.wall_size,
                            },
                            ensure_ascii=False,
                        ),
                        snapshot_id,
                    ),
                )
            conn.commit()
            return snapshot_id

    def list_trades(self, status: str | None = None) -> list[dict[str, Any]]:
        with connect(self.db_path) as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE status = ? ORDER BY id DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades ORDER BY id DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_trade(self, trade_id: int) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            trade = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            if not trade:
                raise KeyError(f"trade {trade_id} not found")
            fills = conn.execute(
                "SELECT * FROM trade_fills WHERE trade_id = ? ORDER BY id",
                (trade_id,),
            ).fetchall()
            books = conn.execute(
                "SELECT id, ts, market_type, mid_price, spread, wall_side, wall_price, "
                "wall_size, wall_age_sec, wall_remaining_pct, label "
                "FROM orderbook_snapshots WHERE trade_id = ? ORDER BY id",
                (trade_id,),
            ).fetchall()
            events = conn.execute(
                "SELECT * FROM trade_events WHERE trade_id = ? ORDER BY id",
                (trade_id,),
            ).fetchall()
            return {
                "trade": dict(trade),
                "fills": [dict(r) for r in fills],
                "orderbook_snapshots": [dict(r) for r in books],
                "events": [dict(r) for r in events],
            }

    def stats(self) -> dict[str, Any]:
        with connect(self.db_path) as conn:
            closed = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(pnl_usd),0) AS pnl "
                "FROM trades WHERE status = 'closed'"
            ).fetchone()
            wins = conn.execute(
                "SELECT COUNT(*) AS n FROM trades "
                "WHERE status = 'closed' AND pnl_usd > 0"
            ).fetchone()["n"]
            n = closed["n"]
            by_setup = conn.execute(
                """
                SELECT setup_id, COUNT(*) AS n,
                       COALESCE(SUM(pnl_usd),0) AS pnl,
                       AVG(pnl_r) AS avg_r
                FROM trades WHERE status = 'closed'
                GROUP BY setup_id ORDER BY n DESC
                """
            ).fetchall()
            open_n = conn.execute(
                "SELECT COUNT(*) AS n FROM trades WHERE status = 'open'"
            ).fetchone()["n"]
            books = conn.execute(
                "SELECT COUNT(*) AS n FROM orderbook_snapshots"
            ).fetchone()["n"]
            return {
                "open_trades": open_n,
                "closed_trades": n,
                "wins": wins,
                "winrate": (wins / n) if n else None,
                "pnl_usd_total": closed["pnl"],
                "orderbook_snapshots": books,
                "by_setup": [dict(r) for r in by_setup],
            }
