"""Сторож против бесшумных зависаний.

`requests` ограничивает паузу между байтами, а не длительность операции:
медленный прокси может держать соединение открытым бесконечно. Процесс при
этом жив и выглядит рабочим, но циклы не идут — именно так бот простоял
3 часа 43 минуты. Сторож отслеживает время последнего успешного цикла и
завершает процесс, чтобы обёртка перезапуска подняла его заново.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

from app.logging_setup import get_logger

log = get_logger("watchdog")


class Watchdog:
    def __init__(
        self,
        timeout_sec: float,
        on_stall: Callable[[float], None] | None = None,
        check_interval_sec: float = 30.0,
    ) -> None:
        self.timeout_sec = timeout_sec
        self.check_interval_sec = check_interval_sec
        self.on_stall = on_stall
        self._last_beat = time.time()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        """Отметить успешное завершение цикла."""
        with self._lock:
            self._last_beat = time.time()

    def idle_sec(self) -> float:
        with self._lock:
            return time.time() - self._last_beat

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(self.check_interval_sec):
                idle = self.idle_sec()
                if idle < self.timeout_sec:
                    continue
                log.critical(
                    "Зависание: цикл не отвечает %.0f с (лимит %.0f). Перезапуск.",
                    idle,
                    self.timeout_sec,
                )
                if self.on_stall is not None:
                    try:
                        self.on_stall(idle)
                    except Exception:
                        log.exception("Ошибка уведомления о зависании")
                # Поток завис на сетевом вызове: штатный выход недостижим,
                # нужен именно немедленный выход процесса.
                os._exit(75)

        self._thread = threading.Thread(target=loop, name="watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
