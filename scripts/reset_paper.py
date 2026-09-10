#!/usr/bin/env python3
"""Wipe paper journal and orderbook history for a fresh start."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from journal.db import init_db  # noqa: E402
from app.orderbook_store import OrderBookStore  # noqa: E402


def main() -> None:
    journal_path = ROOT / "data" / "journal" / "journal.sqlite3"
    ob_path = ROOT / "data" / "orderbook" / "history.sqlite3"
    if journal_path.exists():
        journal_path.unlink()
    if ob_path.exists():
        ob_path.unlink()
    init_db(journal_path)
    OrderBookStore(ob_path)
    print(f"reset ok journal={journal_path} orderbook={ob_path}")


if __name__ == "__main__":
    main()
