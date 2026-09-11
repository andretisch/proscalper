#!/usr/bin/env python3
"""One-shot smoke test: connectivity + single scan."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import ProScalpApp  # noqa: E402


def main() -> None:
    app = ProScalpApp()
    print("proxy_enabled", app.settings.proxy_enabled)
    print("bybit_time", app.bybit.server_time().get("timeSecond"))
    wb = app.bybit.wallet_balance()
    print("wallet_ok", bool(wb))
    print(
        "ai_ping",
        app.ai.chat([{"role": "user", "content": "Ответь одним словом: готов"}])[:80],
    )
    books = app.collect_books()
    print("books", list(books))
    print("snaps", app.store.count())
    try:
        c = app.refresh_watchlist_ai()
        print("watchlist", app.watchlist, c[:120])
    except Exception as e:
        print("watchlist_skip", e)
    summary = app.run_once(notify=False)
    print(summary)
    ok = app.tg.send(
        "✅ ProScalp smoke-test пройден.\n"
        f"Режим: {app.settings.mode} (Bybit testnet={app.settings.bybit_testnet})\n"
        f"Watchlist: {', '.join(app.watchlist)}\n"
        f"Снимков стакана: {app.store.count()}\n"
        f"Журнал: {app.paper.journal.stats()}\n"
        "Бот готов. Запуск цикла: python3 -m app\n"
        "Команды: /status /scan /watchlist /help"
    )
    print("telegram_sent", ok)
    print("SMOKE_OK")


if __name__ == "__main__":
    main()
