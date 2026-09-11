"""Paper/shadow execution into journal — playbook open/manage/close."""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable

from journal.service import Journal, OrderBookSnapshotIn, BookLevel

from app.config import Settings
from app.paper_exec import (
    ManageState,
    be_stop_price,
    entry_fill_price,
    evaluate_playbook,
    exit_fill_price,
    favorable_pct,
    net_pnl_usd,
    round_trip_fees_usd,
    wall_snapshot,
)
from app.risk import RiskState
from app.signals import Signal


class PaperBroker:
    def __init__(self, settings: Settings, risk: RiskState) -> None:
        self.s = settings
        self.risk = risk
        self.journal = Journal(settings.root / "data" / "journal" / "journal.sqlite3")
        self._cooldown: dict[str, float] = {}

    def open_symbols(self) -> set[str]:
        return {t["symbol"] for t in self.journal.list_trades(status="open")}

    def cooldown_left(self, symbol: str) -> float:
        """Anti-churn: no immediate re-entry into the same level after an exit."""
        until = self._cooldown.get(symbol, 0.0)
        return max(0.0, until - time.time())

    def open_count(self) -> int:
        return len(self.journal.list_trades(status="open"))

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
        checklist = {
            "playbook_version": self.s.playbook_version,
            "mode": self.s.mode,
            "ai_comment": ai_comment,
            "signal": signal.to_dict(),
            "paper_exec": {
                "opened_at": time.time(),
                "taker_fee_rate": taker_fee_rate,
                "fee_rt_pct": fee_roundtrip_pct,
                "entry_fill": fill,
                "wall_at_entry": wall_snapshot(book_snap),
                "impulse_seen": False,
                "be_done": False,
            },
        }
        trade_id = self.journal.open_trade(
            symbol=signal.symbol,
            side=signal.side,
            setup_id=signal.setup_id,
            entry_price=fill,
            size=size,
            stop_price=signal.stop_price,
            take_price=None,
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
        self,
        books: dict[str, dict[str, Any]],
        *,
        decide_manage: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> tuple[list[str], list[str]]:
        closed: list[str] = []
        risk_events: list[str] = []
        now = time.time()
        for trade in self.journal.list_trades(status="open"):
            symbol = trade["symbol"]
            snap = books.get(symbol)
            if not snap:
                continue
            checklist, meta = self._checklist(trade)
            age = now - float(meta["opened_at"])
            state = ManageState(
                impulse_seen=bool(meta.get("impulse_seen")),
                be_done=bool(meta.get("be_done")),
            )
            taker = float(meta["taker_fee_rate"])
            fee_rt = float(meta["fee_rt_pct"])
            decision = evaluate_playbook(
                setup_id=str(trade["setup_id"]),
                side=trade["side"],
                entry_price=float(trade["entry_price"]),
                stop_price=trade.get("stop_price"),
                snap=snap,
                age_sec=age,
                wall_at_entry=meta.get("wall_at_entry"),
                state=state,
                fee_roundtrip_pct=fee_rt,
                taker_rate=taker,
                max_seconds_without_impulse=self.s.max_seconds_without_impulse,
            )

            if decision.action == "be" and decision.new_stop is not None:
                self.journal.tighten_stop(
                    int(trade["id"]), decision.new_stop, side=trade["side"]
                )
                meta["impulse_seen"] = True
                meta["be_done"] = True
                checklist["paper_exec"] = meta
                self.journal.update_checklist(int(trade["id"]), checklist)
                continue

            # Playbook gives a setup its impulse window before discretionary exit;
            # structural invalidation above already handled the urgent cases.
            llm_gate = age >= self.s.max_seconds_without_impulse * 0.5
            if decision.action == "hold" and decide_manage is not None and llm_gate:
                mark = exit_fill_price(snap, trade["side"])
                view = {
                    "trade_id": trade["id"],
                    "symbol": symbol,
                    "setup_id": trade["setup_id"],
                    "side": trade["side"],
                    "regime": trade.get("regime"),
                    "entry": trade["entry_price"],
                    "stop": trade.get("stop_price"),
                    "age_sec": round(age, 1),
                    "impulse_seen": state.impulse_seen,
                    "be_done": state.be_done,
                    "favorable_pct": round(
                        favorable_pct(trade["side"], float(trade["entry_price"]), snap),
                        4,
                    ),
                    "wall_now": wall_snapshot(snap),
                    "wall_at_entry": meta.get("wall_at_entry"),
                    "fee_rt_pct": fee_rt,
                    "no_impulse_window_sec": self.s.max_seconds_without_impulse,
                    "pnl_if_exit_now": round(
                        net_pnl_usd(
                            side=trade["side"],
                            entry_price=float(trade["entry_price"]),
                            exit_price=mark,
                            size=float(trade["size"]),
                            taker_rate=taker,
                        ),
                        4,
                    ),
                }
                try:
                    llm = decide_manage(view)
                except Exception as e:
                    llm = {"action": "hold", "comment": f"llm_error:{e}"}
                action = str(llm.get("action") or "hold").lower()
                comment = str(llm.get("comment") or "")
                if action in {"flatten", "close", "exit", "flat"}:
                    decision.action = "flatten"
                    decision.reason = f"llm_flatten:{comment[:80]}"
                    decision.exit_price = mark
                    decision.mechanical = False
                elif action in {"be", "breakeven"} and not state.be_done:
                    be = be_stop_price(trade["side"], float(trade["entry_price"]), taker)
                    self.journal.tighten_stop(int(trade["id"]), be, side=trade["side"])
                    meta["be_done"] = True
                    meta["impulse_seen"] = True
                    checklist["paper_exec"] = meta
                    self.journal.update_checklist(int(trade["id"]), checklist)
                    continue

            if decision.action != "flatten" or decision.exit_price is None:
                meta["impulse_seen"] = state.impulse_seen
                meta["be_done"] = state.be_done
                checklist["paper_exec"] = meta
                self.journal.update_checklist(int(trade["id"]), checklist)
                continue

            fees = round_trip_fees_usd(
                float(trade["entry_price"]),
                float(decision.exit_price),
                float(trade["size"]),
                taker,
            )
            result = self.close_trade(
                int(trade["id"]),
                exit_price=float(decision.exit_price),
                reason=decision.reason,
                fees_usd=fees,
            )
            risk_events.extend(result.get("risk_events") or [])
            self._cooldown[symbol] = time.time() + self.s.symbol_cooldown_sec
            closed.append(
                f"#{trade['id']} {symbol} {trade['setup_id']} {trade['side']} "
                f"pnl={float(result.get('pnl_usd') or 0):.4f} fee={fees:.4f} "
                f"({decision.reason})"
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

    def _checklist(self, trade: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        checklist: dict[str, Any] = {}
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
        meta.setdefault("impulse_seen", False)
        meta.setdefault("be_done", False)
        meta.setdefault("wall_at_entry", None)
        checklist["paper_exec"] = meta
        return checklist, meta
