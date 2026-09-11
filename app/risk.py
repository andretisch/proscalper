"""Daily risk limits and position sizing."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.logging_setup import get_logger

log = get_logger("risk")

# Поля, которые обязаны пережить перезапуск процесса.
_PERSISTED = (
    "day_pnl",
    "day_start_ts",
    "paused_until",
    "hard_stopped_until",
    "consecutive_losses",
)


@dataclass
class RiskState:
    deposit: float
    risk_per_trade_pct: float
    daily_loss_limit_pct: float
    soft_pause_pct: float
    day_pnl: float = 0.0
    day_start_ts: float = field(default_factory=time.time)
    paused_until: float = 0.0
    hard_stopped_until: float = 0.0
    consecutive_losses: int = 0
    max_consecutive_losses: int = 3
    # Вызывается после каждого изменения счётчиков — сюда вешается запись в БД.
    on_change: Callable[[], None] | None = None
    # Счётчики меняет торговый поток, а /pause и /resume — поток Telegram.
    # Повторный вход нужен: register_pnl вызывает _roll_day под тем же замком.
    _lock: threading.RLock = field(
        default_factory=threading.RLock, repr=False, compare=False
    )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {name: getattr(self, name) for name in _PERSISTED}

    def restore(self, data: dict[str, Any]) -> None:
        """Поднять счётчики дня из хранилища.

        Депозит и лимиты берутся из .env: они могли измениться, а вот
        накопленный убыток и активные паузы менять нельзя — иначе
        перезапуск превращается в обход дневного стопа.
        """
        with self._lock:
            for name in _PERSISTED:
                value = data.get(name)
                if value is None:
                    continue
                try:
                    current = getattr(self, name)
                    setattr(self, name, type(current)(value))
                except (TypeError, ValueError):
                    log.warning("состояние риска: поле %s испорчено", name)
            self._roll_day()

    def _persist(self) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change()
        except Exception:
            log.exception("не удалось сохранить состояние риска")

    def _roll_day(self) -> bool:
        if time.time() - self.day_start_ts < 86400:
            return False
        self.day_pnl = 0.0
        self.day_start_ts = time.time()
        self.paused_until = 0.0
        self.hard_stopped_until = 0.0
        self.consecutive_losses = 0
        return True

    def status(self) -> str:
        with self._lock:
            rolled = self._roll_day()
            now = time.time()
            if now < self.hard_stopped_until:
                state = "hard_stop"
            elif now < self.paused_until:
                state = "soft_pause"
            else:
                state = "ok"
        if rolled:
            self._persist()
        return state

    def can_open(self) -> tuple[bool, str]:
        st = self.status()
        if st == "hard_stop":
            return False, "дневной жёсткий стоп"
        if st == "soft_pause":
            return False, "мягкая пауза 2ч"
        return True, "ok"

    def size_for_stop(self, entry: float, stop: float) -> float:
        risk_usd = self.deposit * (self.risk_per_trade_pct / 100.0)
        stop_dist = abs(entry - stop)
        if stop_dist <= 0 or entry <= 0:
            return 0.0
        # size in base coin approx
        return max(risk_usd / stop_dist, 0.0)

    def register_pnl(self, pnl: float) -> list[str]:
        events: list[str] = []
        with self._lock:
            self._roll_day()
            self.day_pnl += pnl
            if pnl < 0:
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0

            soft = -self.deposit * (self.soft_pause_pct / 100.0)
            hard = -self.deposit * (self.daily_loss_limit_pct / 100.0)
            if self.day_pnl <= hard and self.hard_stopped_until < time.time():
                self.hard_stopped_until = time.time() + 86400
                events.append("hard_stop")
            elif self.day_pnl <= soft and self.paused_until < time.time():
                self.paused_until = time.time() + 2 * 3600
                events.append("soft_pause")
            elif (
                self.max_consecutive_losses > 0
                and self.consecutive_losses >= self.max_consecutive_losses
                and self.paused_until < time.time()
            ):
                self.paused_until = time.time() + 2 * 3600
                events.append("soft_pause")
        self._persist()
        return events

    def pause(self, seconds: float) -> None:
        with self._lock:
            self.paused_until = time.time() + seconds
        self._persist()

    def manual_resume(self) -> None:
        with self._lock:
            self.paused_until = 0.0
            self.hard_stopped_until = 0.0
        self._persist()
