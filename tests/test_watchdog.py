"""Сторож против бесшумных зависаний."""

from __future__ import annotations

import time

from app.watchdog import Watchdog


def test_fresh_watchdog_is_not_stalled():
    w = Watchdog(timeout_sec=60)
    assert w.idle_sec() < 1.0


def test_beat_resets_idle():
    w = Watchdog(timeout_sec=60)
    w._last_beat = time.time() - 30
    assert w.idle_sec() >= 29
    w.beat()
    assert w.idle_sec() < 1.0


def test_idle_grows_without_beat():
    w = Watchdog(timeout_sec=60)
    w._last_beat = time.time() - 120
    assert w.idle_sec() >= 119


def test_stall_callback_receives_idle_time():
    """Проверяем колбэк напрямую: запуск потока завершил бы процесс."""
    seen: list[float] = []
    w = Watchdog(timeout_sec=10, on_stall=seen.append)
    w._last_beat = time.time() - 50
    idle = w.idle_sec()
    assert idle > w.timeout_sec
    w.on_stall(idle)
    assert seen and seen[0] > 10
