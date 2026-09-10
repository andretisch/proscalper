"""ProScalp runtime: collect books, signals, AI, paper trades, Telegram."""

from __future__ import annotations

import json
import time
from pathlib import Path

from app.ai import OllamaClient
from app.bybit_client import BybitClient
from app.config import load_settings
from app.orderbook_store import OrderBookStore
from app.paper import PaperBroker
from app.paper_exec import entry_fill_price
from app.risk import RiskState
from app.signals import detect_signals
from app.telegram_bot import TelegramBot

DEFAULT_WATCH = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]


class ProScalpApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.bybit = BybitClient(self.settings)
        self.store = OrderBookStore(
            self.settings.root / "data" / "orderbook" / "history.sqlite3"
        )
        self.risk = RiskState(
            deposit=self.settings.deposit_usdt,
            risk_per_trade_pct=self.settings.risk_per_trade_pct,
            daily_loss_limit_pct=self.settings.daily_loss_limit_pct,
            soft_pause_pct=self.settings.soft_pause_pct,
        )
        self.paper = PaperBroker(self.settings, self.risk)
        self.ai = OllamaClient(self.settings)
        self.tg = TelegramBot(self.settings)
        self.watchlist = list(DEFAULT_WATCH)
        self.running = True
        self.cycle = 0
        self._register_commands()

    def _register_commands(self) -> None:
        self.tg.command_handlers = {
            "/help": lambda _: (
                "ProScalp команды:\n"
                "/status — состояние\n"
                "/mode — paper/live\n"
                "/pause — пауза сигналов\n"
                "/resume — снять hard/soft стоп\n"
                "/watchlist — текущий список\n"
                "/scan — один цикл скана сейчас"
            ),
            "/status": lambda _: self.status_text(),
            "/mode": lambda _: f"Режим: {self.settings.mode} (переключение live — вручную в .env пока)",
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/watchlist": lambda _: "Watchlist: " + ", ".join(self.watchlist),
            "/scan": lambda _: self.run_once(notify=False) or "Скан выполнен. /status",
            "/start": lambda _: "ProScalp на связи. /help",
        }

    def _cmd_pause(self, _text: str) -> str:
        self.risk.paused_until = time.time() + 2 * 3600
        return "Мягкая пауза на 2 часа включена."

    def _cmd_resume(self, _text: str) -> str:
        self.risk.manual_resume()
        return "Паузы сняты. Торговля по правилам разрешена."

    def status_text(self) -> str:
        stats = self.paper.journal.stats()
        wr = stats.get("winrate")
        wr_s = f"{wr * 100:.1f}%" if wr is not None else "—"
        return (
            f"ProScalp status\n"
            f"mode={self.settings.mode} testnet={self.settings.bybit_testnet}\n"
            f"proxy={'on' if self.settings.proxy_enabled else 'off'}\n"
            f"paper_hold={self.settings.paper_min_hold_sec}-{self.settings.paper_max_hold_sec}s\n"
            f"risk={self.risk.status()} day_pnl={self.risk.day_pnl:.2f} USDT\n"
            f"watchlist={', '.join(self.watchlist)}\n"
            f"orderbook_snaps={self.store.count()}\n"
            f"journal open={stats.get('open_trades')} closed={stats.get('closed_trades')} "
            f"wr={wr_s} pnl_net={stats.get('pnl_usd_total')}\n"
            f"playbook=v{self.settings.playbook_version}"
        )

    def refresh_watchlist_ai(self) -> str:
        tickers = self.bybit.tickers(category="linear")
        scored = []
        for t in tickers:
            try:
                turn = float(t.get("turnover24h") or 0)
                ch = float(t.get("price24hPcnt") or 0) * 100
                if turn < 1_000_000:
                    continue
                scored.append(
                    {
                        "symbol": t.get("symbol"),
                        "change24h_pct": round(ch, 2),
                        "turnover24h": round(turn, 0),
                        "last": t.get("lastPrice"),
                    }
                )
            except Exception:
                continue
        scored.sort(key=lambda x: abs(x["change24h_pct"]), reverse=True)
        decision = self.ai.watchlist_pick(scored[:40], limit=5)
        symbols = [
            s for s in (decision.get("symbols") or []) if isinstance(s, str) and s.endswith("USDT")
        ]
        # always keep BTC/ETH if present in market
        for must in ("BTCUSDT", "ETHUSDT"):
            if must not in symbols:
                symbols.insert(0, must)
        symbols = symbols[:5]
        if symbols:
            self.watchlist = symbols
        return decision.get("comment") or "watchlist updated"

    def collect_books(self) -> dict[str, dict]:
        books: dict[str, dict] = {}
        for symbol in self.watchlist:
            for category, mtype in (("linear", "perp"), ("spot", "spot")):
                try:
                    raw = self.bybit.orderbook(symbol, category=category, limit=25)
                    snap = OrderBookStore.parse_book(raw, mtype)
                    snap["symbol"] = symbol
                    self.store.save(snap)
                    if mtype == "perp":
                        books[symbol] = snap
                except Exception:
                    continue
                time.sleep(0.05)
        return books

    def run_once(self, notify: bool = True) -> str:
        self.cycle += 1
        can, why = self.risk.can_open()
        books = self.collect_books()
        opened = []
        closed, risk_events = self.paper.manage_open_trades(books)
        skipped = []
        open_symbols = self.paper.open_symbols()

        fee_rt = 0.11  # fallback %; refine per symbol if available
        for symbol, snap in books.items():
            tickers = self.bybit.tickers(category="linear", symbol=symbol)
            ticker = tickers[0] if tickers else None
            taker_rate = 0.00055
            try:
                fee = self.bybit.fee_rate(symbol, category="linear")
                taker_rate = float(fee.get("takerFeeRate") or 0.00055)
                taker = taker_rate * 100
                fee_rt = taker * 2
            except Exception:
                pass

            signals = detect_signals(
                symbol=symbol, snap=snap, ticker=ticker, fee_roundtrip_pct=fee_rt
            )
            for sig in signals[:1]:
                if symbol in open_symbols:
                    skipped.append(f"{symbol}: already open")
                    continue
                if not can:
                    skipped.append(f"{symbol}: risk block ({why})")
                    continue
                try:
                    decision = self.ai.decide_trade(
                        sig.to_dict(),
                        context=(
                            f"fee_rt%={fee_rt:.4f}; mid={snap.get('mid')}; "
                            f"wall={snap.get('wall_side')}@{snap.get('wall_price')} "
                            f"ratio={snap.get('wall_ratio')}"
                        ),
                    )
                except Exception as e:
                    skipped.append(f"{symbol}: AI timeout/error → skip ({e})")
                    continue

                if str(decision.get("decision", "")).lower() not in {"go", "yes", "approve"}:
                    skipped.append(
                        f"{symbol} {sig.setup_id}: no_go — {decision.get('comment')}"
                    )
                    continue

                fill = entry_fill_price(snap, sig.side)
                size = self.risk.size_for_stop(fill, sig.stop_price)
                notional = size * fill
                max_notional = self.settings.deposit_usdt * 0.2 * self.settings.leverage
                if notional > max_notional and fill > 0:
                    size = max_notional / fill
                if size <= 0:
                    skipped.append(f"{symbol}: size=0")
                    continue

                trade_id = self.paper.open_from_signal(
                    sig,
                    size=size,
                    ai_comment=str(decision.get("comment") or ""),
                    book_snap=snap,
                    fee_roundtrip_pct=fee_rt,
                    taker_fee_rate=taker_rate,
                )
                open_symbols.add(symbol)
                opened.append(
                    f"#{trade_id} {symbol} {sig.setup_id} {sig.side} entry={fill:.6g}"
                )

        if notify:
            for ev in risk_events:
                if ev == "soft_pause":
                    self.tg.send(
                        "⚠️ Мягкая пауза: достигнут 50% дневного лимита (−1%). Пауза 2ч."
                    )
                if ev == "hard_stop":
                    self.tg.send(
                        "🛑 Жёсткий дневной стоп (−2%). Торговля остановлена на 24ч или /resume."
                    )

        summary = (
            f"Цикл #{self.cycle}\n"
            f"books={len(books)} snaps_total={self.store.count()}\n"
            f"closed: {closed or ['—']}\n"
            f"opened: {opened or ['—']}\n"
            f"notes: {skipped[:5] or ['—']}"
        )
        if notify and (opened or closed):
            parts = []
            if closed:
                parts.append("Закрыто paper:\n" + "\n".join(closed))
            if opened:
                parts.append("Открыто paper:\n" + "\n".join(opened))
            self.tg.send("\n\n".join(parts))
        return summary

    def bootstrap(self) -> None:
        # connectivity checks
        self.bybit.server_time()
        self.bybit.wallet_balance()
        self.tg.start_polling()
        msg = (
            "✅ ProScalp запущен\n"
            f"mode={self.settings.mode}, testnet={self.settings.bybit_testnet}\n"
            f"deposit≈{self.settings.deposit_usdt}$ leverage={self.settings.leverage}x\n"
            f"AI={self.settings.ollama_model}\n"
            f"watchlist={', '.join(self.watchlist)}\n"
            "Команды: /status /scan /watchlist /help"
        )
        self.tg.send(msg)

    def run_forever(self, interval_sec: int = 60) -> None:
        self.bootstrap()
        # initial watchlist AI (best-effort)
        try:
            comment = self.refresh_watchlist_ai()
            self.tg.send(f"Watchlist AI: {', '.join(self.watchlist)}\n{comment[:500]}")
        except Exception as e:
            self.tg.send(f"Watchlist AI временно недоступен: {e}. Использую дефолт.")

        # first scan immediately
        try:
            summary = self.run_once(notify=True)
            self.tg.send("Первый скан:\n" + summary[:3500])
        except Exception as e:
            self.tg.send(f"Ошибка первого скана: {e}")

        last_watch = time.time()
        while self.running:
            time.sleep(interval_sec)
            try:
                if time.time() - last_watch > 3600:
                    comment = self.refresh_watchlist_ai()
                    self.tg.send(f"Watchlist обновлён: {', '.join(self.watchlist)}\n{comment[:400]}")
                    last_watch = time.time()
                self.run_once(notify=True)
            except Exception as e:
                self.tg.send(f"Ошибка цикла: {e}")


def main() -> None:
    app = ProScalpApp()
    app.run_forever(interval_sec=90)


if __name__ == "__main__":
    main()
