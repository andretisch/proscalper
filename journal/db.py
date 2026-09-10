"""SQLite schema for the trading journal."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "journal" / "journal.sqlite3"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    exchange        TEXT NOT NULL DEFAULT 'bybit',
    side            TEXT NOT NULL CHECK (side IN ('long', 'short')),
    setup_id        TEXT NOT NULL,
    regime          TEXT,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'closed', 'cancelled')),
    entry_ts        TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    size            REAL NOT NULL,
    stop_price      REAL,
    take_price      REAL,
    exit_ts         TEXT,
    exit_price      REAL,
    pnl_usd         REAL,
    pnl_r           REAL,
    exit_reason     TEXT,
    notes           TEXT,
    checklist_json  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trade_fills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id    INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('entry', 'add', 'reduce', 'exit')),
    price       REAL NOT NULL,
    size        REAL NOT NULL,
    note        TEXT
);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER REFERENCES trades(id) ON DELETE SET NULL,
    symbol          TEXT NOT NULL,
    exchange        TEXT NOT NULL DEFAULT 'bybit',
    market_type     TEXT NOT NULL DEFAULT 'perp'
                    CHECK (market_type IN ('spot', 'perp')),
    ts              TEXT NOT NULL,
    mid_price       REAL,
    best_bid        REAL,
    best_ask        REAL,
    spread          REAL,
    -- detected wall / density of interest
    wall_side       TEXT CHECK (wall_side IN ('bid', 'ask', NULL)),
    wall_price      REAL,
    wall_size       REAL,
    wall_age_sec    REAL,
    wall_remaining_pct REAL,
    -- full book + extras
    bids_json       TEXT NOT NULL,
    asks_json       TEXT NOT NULL,
    meta_json       TEXT,
    label           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trade_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id    INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    ts          TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    payload_json TEXT,
    snapshot_id INTEGER REFERENCES orderbook_snapshots(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_setup ON trades(setup_id);
CREATE INDEX IF NOT EXISTS idx_book_trade ON orderbook_snapshots(trade_id);
CREATE INDEX IF NOT EXISTS idx_book_symbol_ts ON orderbook_snapshots(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_events_trade ON trade_events(trade_id);
"""


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | None = None) -> Path:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    return path
