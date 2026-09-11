"""Прогон нескольких циклов без Telegram — проверка поведения стратегии."""

from __future__ import annotations

import sys
import time

from app.main import ProScalpApp


def main() -> None:
    cycles = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    app = ProScalpApp()
    app.tg.send = lambda *_a, **_k: True  # type: ignore[assignment]
    print(app.refresh_watchlist_ai(), flush=True)
    print("watchlist:", ", ".join(app.watchlist), flush=True)
    for i in range(cycles):
        started = time.time()
        print(app.run_once(notify=False), flush=True)
        print("-" * 60, flush=True)
        if i < cycles - 1:
            time.sleep(max(0.0, interval - (time.time() - started)))
    print(app.paper.journal.stats(), flush=True)


if __name__ == "__main__":
    main()
