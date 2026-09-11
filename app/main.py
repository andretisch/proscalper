"""ProScalp runtime: collect books, signals, AI, paper trades, Telegram."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from app.ai import OllamaClient
from app.bybit_client import BybitClient
from app.config import load_settings
from app.logging_setup import get_logger, setup_logging
from app.market import MarketMeter, rank_candidates
from app.orderbook_store import OrderBookStore
from app.paper import PaperBroker
from app.paper_exec import entry_fill_price
from app.risk import RiskState
from app.signals import detect_signals
from app.telegram_bot import TelegramBot
from app.wall_tracker import WallTracker
from app.watchdog import Watchdog

DEFAULT_WATCH = ["SOLUSDT", "DOGEUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT"]


class ProScalpApp:
    def __init__(self) -> None:
        self.settings = load_settings()
        setup_logging(
            self.settings.root,
            level=getattr(logging, self.settings.log_level, logging.INFO),
        )
        self.log = get_logger("app")
        self.bybit = BybitClient(self.settings)
        self.store = OrderBookStore(
            self.settings.root / "data" / "orderbook" / "history.sqlite3"
        )
        self.risk = RiskState(
            deposit=self.settings.deposit_usdt,
            risk_per_trade_pct=self.settings.risk_per_trade_pct,
            daily_loss_limit_pct=self.settings.daily_loss_limit_pct,
            soft_pause_pct=self.settings.soft_pause_pct,
            max_consecutive_losses=self.settings.max_consecutive_losses,
        )
        self.paper = PaperBroker(self.settings, self.risk)
        self.ai = OllamaClient(self.settings)
        self.tg = TelegramBot(self.settings)
        self.walls = WallTracker(
            price_tol_pct=self.settings.wall_min_dist_pct * 2.5,
            ttl_sec=max(300.0, self.settings.room_window_sec),
        )
        self.market = MarketMeter(
            self.bybit, minutes=int(self.settings.room_window_sec // 60) or 15
        )
        self.watchlist = list(DEFAULT_WATCH)
        self.running = True
        self.cycle = 0
        self.watchdog = Watchdog(
            timeout_sec=self.settings.watchdog_timeout_sec,
            on_stall=self._on_stall,
        )
        self._register_commands()

    def _on_stall(self, idle_sec: float) -> None:
        self.tg.send(
            f"🛑 Бот завис: цикл не отвечает {idle_sec / 60:.0f} мин. "
            f"Процесс перезапускается."
        )

    def _register_commands(self) -> None:
        self.tg.command_handlers = {
            "/help": lambda _: (
                "ProScalp команды:\n"
                "/status — состояние и баланс\n"
                "/balance — текущий баланс\n"
                "/mode — paper/live переключение\n"
                "/pause — пауза сигналов\n"
                "/resume — снять hard/soft стоп\n"
                "/watchlist — текущий список\n"
                "/scan — один цикл скана сейчас\n"
                "Или просто пиши вопрос — ИИ ответит"
            ),
            "/status": lambda _: self.status_text(),
            "/balance": lambda _: self.balance_text(),
            "/mode": self._cmd_mode,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/watchlist": lambda _: "Watchlist: " + ", ".join(self.watchlist),
            "/scan": lambda _: self.run_once(notify=False) or "Скан выполнен. /status",
            "/start": lambda _: "ProScalp на связи. /help для списка команд.",
            "_llm_chat": self._cmd_llm_chat,
        }
        # Устанавливаем меню команд в Telegram
        self.tg.set_commands([
            {"command": "status", "description": "Состояние и баланс"},
            {"command": "balance", "description": "Текущий баланс"},
            {"command": "mode", "description": "Переключить paper/live"},
            {"command": "watchlist", "description": "Список символов"},
            {"command": "pause", "description": "Пауза сигналов"},
            {"command": "resume", "description": "Снять паузу"},
            {"command": "scan", "description": "Ручной скан"},
            {"command": "help", "description": "Помощь"},
        ])

    def _cmd_mode(self, text: str) -> str:
        """Переключение paper ↔ live или показ текущего режима."""
        parts = text.strip().split()
        if len(parts) == 1:
            return (
                f"Текущий режим: {self.settings.mode}\n"
                f"Для переключения: /mode paper или /mode live"
            )
        target = parts[1].lower()
        if target not in ("paper", "live"):
            return "Укажи: /mode paper или /mode live"
        if target == self.settings.mode:
            return f"Уже в режиме {target}"
        # Обновляем .env
        env_path = self.settings.root / ".env"
        try:
            lines = env_path.read_text().splitlines()
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("MODE="):
                    new_lines.append(f"MODE={target}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"MODE={target}")
            env_path.write_text("\n".join(new_lines) + "\n")
            return (
                f"✅ Режим переключён: {self.settings.mode} → {target}\n"
                f"⚠️ Перезапусти бота, чтобы изменения вступили в силу:\n"
                f"tmux attach -t proscalp-bot, затем Ctrl+C и перезапуск"
            )
        except Exception as e:
            return f"Ошибка записи .env: {e}"

    def _cmd_pause(self, _text: str) -> str:
        self.risk.paused_until = time.time() + 2 * 3600
        return "Мягкая пауза на 2 часа включена."

    def _cmd_resume(self, _text: str) -> str:
        self.risk.manual_resume()
        return "Паузы сняты. Торговля по правилам разрешена."

    def _cmd_llm_chat(self, question: str) -> str:
        """Диалог с LLM: контекст стратегии + произвольный вопрос."""
        stats = self.paper.journal.stats()
        context = {
            "mode": self.settings.mode,
            "deposit_usdt": self.settings.deposit_usdt,
            "leverage": self.settings.leverage,
            "risk_per_trade_pct": self.settings.risk_per_trade_pct,
            "max_parallel_symbols": self.settings.max_parallel_symbols,
            "max_seconds_without_impulse": self.settings.max_seconds_without_impulse,
            "taker_fee_rate": self.settings.taker_fee_rate_override * 100,
            "watchlist": self.watchlist,
            "open_trades": stats.get("open_trades"),
            "closed_trades": stats.get("closed_trades"),
            "winrate": stats.get("winrate"),
            "pnl_usd_total": stats.get("pnl_usd_total"),
            "day_pnl": self.risk.day_pnl,
            "risk_status": self.risk.status(),
        }
        system = (
            "Ты помощник трейдера, ведущего скальп-бота ProScalp по стратегии playbook S1–S5. "
            "Отвечай на русском, кратко и по делу. "
            "Контекст стратегии:\n"
            f"{json.dumps(context, ensure_ascii=False, indent=2)}\n"
            "Playbook: S1 true breakout, S2 false breakout от плотности, "
            "S3 flip eaten volume, S4 in-play continuation, S5 post-listing drain short. "
            "Ведение: стоп за структурой, BE после первого импульса, окно без импульса 90с, "
            "запрет widen стопа, запрет усреднения, flat при снятой плотности."
        )
        try:
            raw = self.ai.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ])
            return raw[:1500] if raw else "LLM не ответил"
        except Exception as e:
            return f"Ошибка LLM: {e}"

    def balance_text(self) -> str:
        """Баланс с учётом режима: paper показывает deposit, live — реальный."""
        try:
            if self.settings.mode == "paper":
                balance = self.settings.deposit_usdt
                equity = balance + self.risk.day_pnl
                return (
                    f"💼 Баланс (paper):\n"
                    f"Депозит: {balance:.2f} USDT\n"
                    f"Дневной PnL: {self.risk.day_pnl:+.2f} USDT\n"
                    f"Эквити: {equity:.2f} USDT"
                )
            else:
                wallet = self.bybit.wallet_balance()
                balances = wallet.get("list") or []
                if not balances:
                    return "Не удалось получить баланс"
                acc = balances[0]
                total_equity = float(acc.get("totalEquity") or 0)
                wallet_balance = float(acc.get("totalWalletBalance") or 0)
                unrealized = float(acc.get("totalPerpUPL") or 0)
                return (
                    f"💼 Баланс (live testnet):\n"
                    f"Баланс: {wallet_balance:.2f} USDT\n"
                    f"Unrealized PnL: {unrealized:+.2f} USDT\n"
                    f"Эквити: {total_equity:.2f} USDT"
                )
        except Exception as e:
            return f"Ошибка получения баланса: {e}"

    def status_text(self) -> str:
        stats = self.paper.journal.stats()
        wr = stats.get("winrate")
        wr_s = f"{wr * 100:.1f}%" if wr is not None else "—"
        balance_line = ""
        try:
            if self.settings.mode == "paper":
                equity = self.settings.deposit_usdt + self.risk.day_pnl
                balance_line = f"💼 paper equity={equity:.2f} USDT\n"
            else:
                wallet = self.bybit.wallet_balance()
                balances = wallet.get("list") or []
                if balances:
                    total_equity = float(balances[0].get("totalEquity") or 0)
                    balance_line = f"💼 live equity={total_equity:.2f} USDT\n"
        except Exception:
            pass
        return (
            f"📊 ProScalp status\n"
            f"{balance_line}"
            f"mode={self.settings.mode} testnet={self.settings.bybit_testnet}\n"
            f"data={'mainnet' if self.settings.market_data_mainnet else 'testnet'} "
            f"taker={self.settings.taker_fee_rate_override * 100:.3f}%\n"
            f"proxy={'on' if self.settings.proxy_enabled else 'off'}\n"
            f"no_impulse={self.settings.max_seconds_without_impulse}s "
            f"max_open={self.settings.max_parallel_symbols}\n"
            f"risk={self.risk.status()} day_pnl={self.risk.day_pnl:.2f} USDT\n"
            f"watchlist={', '.join(self.watchlist)}\n"
            f"orderbook_snaps={self.store.count()}\n"
            f"journal open={stats.get('open_trades')} closed={stats.get('closed_trades')} "
            f"wr={wr_s} pnl_net={stats.get('pnl_usd_total')}\n"
            f"playbook=v{self.settings.playbook_version}"
        )

    def refresh_watchlist_ai(self) -> str:
        """Watchlist из символов «в игре»: без хода скальп не окупает комиссию."""
        candidates = rank_candidates(
            self.bybit.tickers(category="linear"),
            min_turnover=self.settings.min_turnover_usd,
            min_range_pct=self.settings.min_range24_pct,
        )
        if not candidates:
            return "нет символов в игре, watchlist без изменений"
        pool = [c.symbol for c in candidates]
        decision = self.ai.watchlist_pick([c.to_dict() for c in candidates], limit=5)
        picked = [
            s
            for s in (decision.get("symbols") or [])
            if isinstance(s, str) and s in pool
        ]
        # ИИ может вернуть мусор или пустой список — ранжирование по размаху
        # остаётся источником истины.
        symbols = (picked + pool)[:5] if picked else pool[:5]
        seen: set[str] = set()
        self.watchlist = [s for s in symbols if not (s in seen or seen.add(s))]
        return decision.get("comment") or "watchlist обновлён по размаху"

    def collect_books(self) -> dict[str, dict]:
        books: dict[str, dict] = {}
        for symbol in self.watchlist:
            for category, mtype in (("linear", "perp"), ("spot", "spot")):
                try:
                    raw = self.bybit.orderbook(
                        symbol, category=category, limit=self.settings.book_depth_limit
                    )
                    snap = OrderBookStore.parse_book(
                        raw,
                        mtype,
                        self.settings.wall_max_dist_pct,
                        scan_pct=self.settings.wall_scan_pct,
                        min_wall_dist_pct=self.settings.wall_min_dist_pct,
                        min_depth_share=self.settings.wall_min_depth_share,
                        min_wall_ratio=self.settings.wall_min_ratio,
                    )
                    snap["symbol"] = symbol
                    self.store.save(snap)
                    if mtype == "perp":
                        self.walls.observe(symbol, snap.get("density"))
                        books[symbol] = snap
                    elif symbol in books:
                        books[symbol]["spot_wall"] = {
                            "side": snap.get("wall_side"),
                            "price": snap.get("wall_price"),
                            "size": snap.get("wall_size"),
                        }
                except Exception:
                    continue
                time.sleep(0.05)
        return books

    def run_once(self, notify: bool = True) -> str:
        self.cycle += 1
        can, why = self.risk.can_open()
        books = self.collect_books()
        opened = []
        closed, risk_events = self.paper.manage_open_trades(
            books, decide_manage=self.ai.decide_manage
        )
        skipped = []
        open_symbols = self.paper.open_symbols()

        fee_rt = 0.11  # fallback %; refine per symbol if available
        for symbol, snap in books.items():
            tickers = self.bybit.tickers(category="linear", symbol=symbol)
            ticker = tickers[0] if tickers else None
            taker_rate = self.settings.taker_fee_rate_override
            if not self.settings.market_data_mainnet:
                # testnet fee schedule only makes sense with testnet books
                try:
                    fee = self.bybit.fee_rate(symbol, category="linear")
                    taker_rate = float(fee.get("takerFeeRate") or taker_rate)
                except Exception:
                    pass
            fee_rt = taker_rate * 100 * 2

            momentum = self.market.momentum(symbol)
            room = momentum.range_pct if momentum else None
            if room is None:
                room = self.store.recent_range_pct(
                    symbol, window_sec=self.settings.room_window_sec
                )
            signals = detect_signals(
                symbol=symbol,
                snap=snap,
                ticker=ticker,
                fee_roundtrip_pct=fee_rt,
                wall_track=self.walls.track(symbol),
                room_pct=room,
                momentum=momentum,
                min_wall_observations=self.settings.wall_min_observations,
                min_wall_age_sec=self.settings.wall_min_age_sec,
                min_wall_held_share=self.settings.wall_min_held_share,
                wall_approach_pct=self.settings.wall_approach_pct,
                min_rr=self.settings.min_rr,
                max_fee_share=self.settings.max_fee_share_of_risk,
            )
            for sig in signals[:1]:
                if symbol in open_symbols:
                    skipped.append(f"{symbol}: already open")
                    continue
                cd = self.paper.cooldown_left(symbol)
                if cd > 0:
                    skipped.append(f"{symbol}: cooldown {cd:.0f}s")
                    continue
                if self.paper.open_count() >= self.settings.max_parallel_symbols:
                    skipped.append(f"{symbol}: max_parallel={self.settings.max_parallel_symbols}")
                    continue
                if not can:
                    skipped.append(f"{symbol}: risk block ({why})")
                    continue
                try:
                    decision = self.ai.decide_trade(
                        sig.to_dict(),
                        context=(
                            f"fee_rt%={fee_rt:.4f}; mid={snap.get('mid')}; "
                            f"ЛП={snap.get('wall_side')}@{snap.get('wall_price')} "
                            f"доля_глубины={(snap.get('wall_share') or 0) * 100:.0f}%; "
                            f"размах_15м={room:.3f}%; "
                            f"стакан_виден_на={snap.get('book_range_pct', 0):.3f}%; "
                            f"комиссия_съест={sig.fee_share_of_risk * 100:.0f}%_риска"
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
        self.log.info(
            "цикл #%s books=%s открыто=%s закрыто=%s пропущено=%s",
            self.cycle,
            len(books),
            len(opened),
            len(closed),
            len(skipped),
        )
        for line in opened:
            self.log.info("ОТКРЫТО %s", line)
        for line in closed:
            self.log.info("ЗАКРЫТО %s", line)
        for line in skipped:
            self.log.debug("пропуск %s", line)
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
        self.log.info(
            "старт: mode=%s окно_импульса=%sс комиссия<=%.0f%%_риска watchdog=%.0fс",
            self.settings.mode,
            self.settings.max_seconds_without_impulse,
            self.settings.max_fee_share_of_risk * 100,
            self.settings.watchdog_timeout_sec,
        )
        self.bootstrap()
        self.watchdog.beat()
        self.watchdog.start()

        try:
            comment = self.refresh_watchlist_ai()
            self.log.info("watchlist: %s", ", ".join(self.watchlist))
            self.tg.send(f"Watchlist AI: {', '.join(self.watchlist)}\n{comment[:500]}")
        except Exception as e:
            self.log.exception("watchlist AI недоступен")
            self.tg.send(f"Watchlist AI временно недоступен: {e}. Использую дефолт.")

        try:
            summary = self.run_once(notify=True)
            self.watchdog.beat()
            self.tg.send("Первый скан:\n" + summary[:3500])
        except Exception as e:
            self.log.exception("ошибка первого скана")
            self.tg.send(f"Ошибка первого скана: {e}")

        last_watch = time.time()
        while self.running:
            time.sleep(interval_sec)
            try:
                if time.time() - last_watch > 3600:
                    comment = self.refresh_watchlist_ai()
                    self.log.info("watchlist обновлён: %s", ", ".join(self.watchlist))
                    self.tg.send(f"Watchlist обновлён: {', '.join(self.watchlist)}\n{comment[:400]}")
                    last_watch = time.time()
                self.run_once(notify=True)
                self.watchdog.beat()
            except Exception as e:
                self.log.exception("ошибка цикла")
                self.tg.send(f"Ошибка цикла: {e}")


def main() -> None:
    app = ProScalpApp()
    app.run_forever(interval_sec=90)


if __name__ == "__main__":
    main()
