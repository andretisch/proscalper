"""CLI for ProScalp trading journal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from journal.db import DEFAULT_DB_PATH
from journal.service import Journal, OrderBookSnapshotIn


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="journal",
        description="Журнал сделок ProScalp: вход/выход + история стакана",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"путь к sqlite (default: {DEFAULT_DB_PATH})",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="создать БД и таблицы")

    open_p = sub.add_parser("open", help="открыть сделку")
    open_p.add_argument("--symbol", required=True)
    open_p.add_argument("--side", required=True, choices=["long", "short"])
    open_p.add_argument("--setup", required=True, help="S1..S6 или полный id")
    open_p.add_argument("--price", type=float, required=True)
    open_p.add_argument("--size", type=float, required=True)
    open_p.add_argument("--stop", type=float, default=None)
    open_p.add_argument("--take", type=float, default=None)
    open_p.add_argument("--exchange", default="bybit")
    open_p.add_argument("--regime", default=None, help="range|trend_in_play|post_listing_drain")
    open_p.add_argument("--notes", default=None)
    open_p.add_argument("--checklist-json", type=Path, default=None)

    close_p = sub.add_parser("close", help="закрыть сделку")
    close_p.add_argument("--id", type=int, required=True)
    close_p.add_argument("--price", type=float, required=True)
    close_p.add_argument("--reason", default=None)
    close_p.add_argument("--notes", default=None)

    add_p = sub.add_parser("add", help="добор / частичное сокращение")
    add_p.add_argument("--id", type=int, required=True)
    add_p.add_argument("--kind", required=True, choices=["add", "reduce"])
    add_p.add_argument("--price", type=float, required=True)
    add_p.add_argument("--size", type=float, required=True)
    add_p.add_argument("--note", default=None)

    book_p = sub.add_parser("book", help="сохранить снимок стакана")
    book_p.add_argument("--file", type=Path, required=True, help="JSON снимок стакана")
    book_p.add_argument("--trade-id", type=int, default=None)
    book_p.add_argument("--label", default=None)

    list_p = sub.add_parser("list", help="список сделок")
    list_p.add_argument("--status", choices=["open", "closed", "cancelled"], default=None)

    show_p = sub.add_parser("show", help="карточка сделки + стаканы")
    show_p.add_argument("id", type=int)

    sub.add_parser("stats", help="сводка PnL / WR / по сетапам")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    j = Journal(args.db)

    if args.cmd == "init":
        print(f"ok db={j.db_path}")
        return 0

    if args.cmd == "open":
        checklist = None
        if args.checklist_json:
            checklist = json.loads(args.checklist_json.read_text(encoding="utf-8"))
        trade_id = j.open_trade(
            symbol=args.symbol,
            side=args.side,
            setup_id=args.setup,
            entry_price=args.price,
            size=args.size,
            stop_price=args.stop,
            take_price=args.take,
            exchange=args.exchange,
            regime=args.regime,
            notes=args.notes,
            checklist=checklist,
        )
        print(json.dumps({"trade_id": trade_id}, ensure_ascii=False))
        return 0

    if args.cmd == "close":
        result = j.close_trade(
            args.id,
            exit_price=args.price,
            exit_reason=args.reason,
            notes=args.notes,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "add":
        j.add_fill(
            args.id,
            kind=args.kind,
            price=args.price,
            size=args.size,
            note=args.note,
        )
        print(json.dumps({"ok": True, "trade_id": args.id}, ensure_ascii=False))
        return 0

    if args.cmd == "book":
        data = json.loads(args.file.read_text(encoding="utf-8"))
        snap = OrderBookSnapshotIn.from_dict(data)
        if args.trade_id is not None:
            snap.trade_id = args.trade_id
        if args.label:
            snap.label = args.label
        snapshot_id = j.save_orderbook(snap)
        print(
            json.dumps(
                {"snapshot_id": snapshot_id, "trade_id": snap.trade_id},
                ensure_ascii=False,
            )
        )
        return 0

    if args.cmd == "list":
        rows = j.list_trades(status=args.status)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "show":
        data = j.get_trade(args.id)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "stats":
        print(json.dumps(j.stats(), ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:  # noqa: BLE001 — CLI surface
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(1)
