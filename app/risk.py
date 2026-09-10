"""Daily risk limits and position sizing."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


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

    def _roll_day(self) -> None:
        if time.time() - self.day_start_ts >= 86400:
            self.day_pnl = 0.0
            self.day_start_ts = time.time()
            self.paused_until = 0.0
            self.hard_stopped_until = 0.0
            self.consecutive_losses = 0

    def status(self) -> str:
        self._roll_day()
        now = time.time()
        if now < self.hard_stopped_until:
            return "hard_stop"
        if now < self.paused_until:
            return "soft_pause"
        return "ok"

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
        self._roll_day()
        self.day_pnl += pnl
        events: list[str] = []
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
        return events

    def manual_resume(self) -> None:
        self.paused_until = 0.0
        self.hard_stopped_until = 0.0
