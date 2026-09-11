"""Торговый цикл и Telegram работают в разных потоках — гонок быть не должно."""

from __future__ import annotations

import threading
import time

from app.main import ProScalpApp
from app.risk import RiskState


def _risk() -> RiskState:
    return RiskState(
        deposit=100.0,
        risk_per_trade_pct=0.5,
        daily_loss_limit_pct=2.0,
        soft_pause_pct=1.0,
        max_consecutive_losses=0,
    )


def test_parallel_pnl_is_not_lost():
    risk = _risk()

    def worker() -> None:
        for _ in range(200):
            risk.register_pnl(0.01)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert round(risk.day_pnl, 6) == round(4 * 200 * 0.01, 6)


def test_pause_from_another_thread_is_not_overwritten():
    risk = _risk()
    stop = threading.Event()

    def trader() -> None:
        while not stop.is_set():
            risk.register_pnl(0.001)

    t = threading.Thread(target=trader)
    t.start()
    try:
        time.sleep(0.05)
        risk.pause(3600)
        # Прибыльные сделки не должны снимать паузу, поставленную вручную.
        time.sleep(0.05)
        assert risk.status() == "soft_pause"
    finally:
        stop.set()
        t.join()


def _app() -> ProScalpApp:
    app = object.__new__(ProScalpApp)
    app._trade_lock = threading.RLock()
    return app


def test_manual_scan_is_skipped_while_cycle_runs():
    app = _app()
    running = threading.Event()
    release = threading.Event()

    def slow_cycle(notify: bool = True) -> str:
        with app._trade_lock:
            running.set()
            release.wait(5)
            return "цикл"

    app.run_once = slow_cycle
    cycle = threading.Thread(target=slow_cycle)
    cycle.start()
    try:
        assert running.wait(2)
        reply = app._cmd_scan("/scan")
        assert "пропущен" in reply
    finally:
        release.set()
        cycle.join()


def test_manual_scan_runs_when_free():
    app = _app()
    app.run_once = lambda notify=True: "Цикл #7"
    assert "Цикл #7" in app._cmd_scan("/scan")
