"""Paper/shadow execution into journal."""

from __future__ import annotations

import json
from typing import Any

from journal.service import Journal, OrderBookSnapshotIn, BookLevel

from app.config import Settings
from app.risk import RiskState
from app.signals import Signal


class PaperBroker:
    def __init__(self, settings: Settings, risk: RiskState) -> None:
        self.s = settings
        self.risk = risk
        self.journal = Journal(settings.root / "data" / "journal" / "journal.sqlite3")
        self.open_ids: list[int] = []

    def open_from_signal(
        self,
        signal: Signal,
        *,
        size: float,
        ai_comment: str,
        book_snap: dict[str, Any] | None = None,
    ) -> int:
        checklist = {
            "playbook_version": self.s.playbook_version,
            "mode": self.s.mode,
            "ai_comment": ai_comment,
            "signal": signal.to_dict(),
        }
        trade_id = self.journal.open_trade(
            symbol=signal.symbol,
            side=signal.side,
            setup_id=signal.setup_id,
            entry_price=signal.entry_price,
            size=size,
            stop_price=signal.stop_price,
            exchange="bybit",
            regime=signal.regime,
            notes=f"[paper] {signal.reason}",
            checklist=checklist,
        )
        if book_snap:
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
                        meta={"ai_comment": ai_comment},
                        trade_id=trade_id,
                    )
                )
        self.open_ids.append(trade_id)
        return trade_id

    def close_trade(
        self, trade_id: int, exit_price: float, reason: str, ai_comment: str = ""
    ) -> dict[str, Any]:
        result = self.journal.close_trade(
            trade_id,
            exit_price=exit_price,
            exit_reason=reason,
            notes=json.dumps({"ai_exit_comment": ai_comment}, ensure_ascii=False),
        )
        events = self.risk.register_pnl(float(result.get("pnl_usd") or 0))
        result["risk_events"] = events
        if trade_id in self.open_ids:
            self.open_ids.remove(trade_id)
        return result
