#!/usr/bin/env python3
"""One-shot smoke test: connectivity + single scan."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import ProScalpApp  # noqa: E402
from app.telegram_bot import md_bold, md_code, md_escape  # noqa: E402


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
        "\n".join([
            f"✅ {md_bold('ProScalp smoke-test пройден')}",
            f"Режим: {md_code(app.settings.mode)}"
            f" · testnet {md_code(app.settings.bybit_testnet)}",
            f"Watchlist: {md_code(', '.join(app.watchlist))}",
            f"Снимков стакана: {md_code(app.store.count())}",
            f"Журнал: {md_code(app.paper.journal.stats())}",
            md_escape("Бот готов. Запуск цикла: python3 -m app"),
            md_escape("Команды: /status /scan /watchlist /help"),
        ])
    )
    print("telegram_sent", ok)
    print("SMOKE_OK")


if __name__ == "__main__":
    main()
