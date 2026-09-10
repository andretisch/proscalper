"""Paper/shadow execution into journal."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from journal.service import Journal, OrderBookSnapshotIn, BookLevel

from app.config import Settings
from app.paper_exec import (
    entry_fill_price,
    evaluate_exit,
    round_trip_fees_usd,
    take_price,
)
from app.risk import RiskState
from app.signals import Signal


class PaperBroker:
    def __init__(self, settings: Settings, risk: RiskState) -> None:
        self.s = settings
        self.risk = risk
        self.journal = Journal(settings.root / "data" / "journal" / "journal.sqlite3")

    def open_symbols(self) -> set[str]:
        return {t["symbol"] for t in self.journal.list_trades(status="open")}

    def open_from_signal(
        self,
        signal: Signal,
        *,
        size: float,
        ai_comment: str,
        book_snap: dict[str, Any],
        fee_roundtrip_pct: float,
        taker_fee_rate: float,
    ) -> int:
        fill = entry_fill_price(book_snap, signal.side)
        tp = take_price(fill, signal.side, signal.expected_move_pct, fee_roundtrip_pct)
        checklist = {
            "playbook_version": self.s.playbook_version,
            "mode": self.s.mode,
            "ai_comment": ai_comment,
            "signal": signal.to_dict(),
            "paper_exec": {
                "opened_at": time.time(),
                "taker_fee_rate": taker_fee_rate,
                "fee_rt_pct": fee_roundtrip_pct,
                "expected_move_pct": signal.expected_move_pct,
                "take_price": tp,
                "entry_fill": fill,
            },
        }
        trade_id = self.journal.open_trade(
            symbol=signal.symbol,
            side=signal.side,
            setup_id=signal.setup_id,
            entry_price=fill,
            size=size,
            stop_price=signal.stop_price,
            take_price=tp,
            exchange="bybit",
            regime=signal.regime,
            notes=f"[paper] {signal.reason}",
            checklist=checklist,
        )
        bids = [BookLevel(p, s) for p, s in (book_snap.get("bids") or [])[:25]]
        asks = [BookLevel(p, s) for p, s in (book_snap.get("asks") or [])[:25]]
        if bids and asks:
            self.journal.save_orderbook(
                OrderBookSnapshotIn(
                    symbol=signal.symbol,
                    bids=bids,
                    asks=asks,
                    market_type=book_snap.get("market_type") or "perp",
                    wall_side=book_snap.get("wall_side"),
                    wall_price=book_snap.get("wall_price"),
                    wall_size=book_snap.get("wall_size"),
                    label="signal_entry",
                    meta={"ai_comment": ai_comment, "entry_fill": fill},
                    trade_id=trade_id,
                )
            )
        return trade_id

    def manage_open_trades(
        self, books: dict[str, dict[str, Any]]
    ) -> tuple[list[str], list[str]]:
        """Check stops, targets, timeouts; close with fees."""
        closed: list[str] = []
        risk_events: list[str] = []
        now = time.time()
        for trade in self.journal.list_trades(status="open"):
            symbol = trade["symbol"]
            snap = books.get(symbol)
            if not snap:
                continue
            meta = self._exec_meta(trade)
            age = now - float(meta["opened_at"])
            decision = evaluate_exit(
                side=trade["side"],
                entry_price=float(trade["entry_price"]),
                stop_price=trade.get("stop_price"),
                take_px=trade.get("take_price") or meta.get("take_price"),
                snap=snap,
                age_sec=age,
                min_hold_sec=self.s.paper_min_hold_sec,
                max_hold_sec=self.s.paper_max_hold_sec,
                fee_roundtrip_pct=float(meta["fee_rt_pct"]),
            )
            if not decision:
                continue
            exit_px, reason = decision
            fees = round_trip_fees_usd(
                float(trade["entry_price"]),
                exit_px,
                float(trade["size"]),
                float(meta["taker_fee_rate"]),
            )
            result = self.close_trade(
                int(trade["id"]),
                exit_price=exit_px,
                reason=reason,
                fees_usd=fees,
            )
            risk_events.extend(result.get("risk_events") or [])
            closed.append(
                f"#{trade['id']} {symbol} {trade['setup_id']} {trade['side']} "
                f"pnl={result.get('pnl_usd'):.4f} fee={fees:.4f} ({reason})"
            )
        return closed, risk_events

    def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        reason: str,
        *,
        fees_usd: float = 0.0,
        ai_comment: str = "",
    ) -> dict[str, Any]:
        notes = json.dumps(
            {"ai_exit_comment": ai_comment, "fees_usd": fees_usd},
            ensure_ascii=False,
        )
        result = self.journal.close_trade(
            trade_id,
            exit_price=exit_price,
            exit_reason=reason,
            notes=notes,
            fees_usd=fees_usd,
        )
        events = self.risk.register_pnl(float(result.get("pnl_usd") or 0))
        result["risk_events"] = events
        return result

    @staticmethod
    def _exec_meta(trade: dict[str, Any]) -> dict[str, Any]:
        checklist = {}
        raw = trade.get("checklist_json")
        if raw:
            try:
                checklist = json.loads(raw)
            except Exception:
                checklist = {}
        meta = dict(checklist.get("paper_exec") or {})
        if "opened_at" not in meta:
            try:
                ts = trade.get("entry_ts") or ""
                meta["opened_at"] = datetime.fromisoformat(ts).timestamp()
            except Exception:
                meta["opened_at"] = time.time()
        meta.setdefault("taker_fee_rate", 0.00055)
        meta.setdefault("fee_rt_pct", 0.11)
        meta.setdefault("take_price", trade.get("take_price"))
        return meta
