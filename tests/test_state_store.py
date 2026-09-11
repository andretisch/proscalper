"""Перезапуск не должен обнулять дневной риск."""

from __future__ import annotations

import time
from types import SimpleNamespace

from app.paper import PaperBroker
from app.risk import RiskState
from app.state_store import StateStore


def _risk(**kw) -> RiskState:
    params = dict(
        deposit=100.0,
        risk_per_trade_pct=0.5,
        daily_loss_limit_pct=2.0,
        soft_pause_pct=1.0,
        max_consecutive_losses=3,
    )
    params.update(kw)
    return RiskState(**params)


def test_save_and_load_roundtrip(tmp_path):
    store = StateStore(tmp_path / "j.sqlite3")
    store.save("risk", {"day_pnl": -1.25, "consecutive_losses": 2})
    assert store.load("risk") == {"day_pnl": -1.25, "consecutive_losses": 2}


def test_missing_key_is_none(tmp_path):
    store = StateStore(tmp_path / "j.sqlite3")
    assert store.load("risk") is None


def test_save_overwrites(tmp_path):
    store = StateStore(tmp_path / "j.sqlite3")
    store.save("risk", {"day_pnl": -1.0})
    store.save("risk", {"day_pnl": -2.0})
    assert store.load("risk") == {"day_pnl": -2.0}


def test_corrupted_state_does_not_crash(tmp_path):
    store = StateStore(tmp_path / "j.sqlite3")
    store.save("risk", {"day_pnl": -1.0})
    with store._connect() as conn:
        conn.execute("UPDATE runtime_state SET value_json = '{не json' WHERE key='risk'")
    assert store.load("risk") is None


def test_hard_stop_survives_restart(tmp_path):
    store = StateStore(tmp_path / "j.sqlite3")
    before = _risk()
    before.on_change = lambda: store.save("risk", before.snapshot())
    before.register_pnl(-2.5)  # глубже дневного лимита в 2% от 100 USDT
    assert before.status() == "hard_stop"

    # Новый процесс: лимиты из .env, счётчики — из хранилища.
    after = _risk()
    after.restore(store.load("risk"))
    assert after.status() == "hard_stop"
    assert after.day_pnl == -2.5
    assert after.can_open()[0] is False


def test_day_pnl_is_not_reset_by_restart(tmp_path):
    store = StateStore(tmp_path / "j.sqlite3")
    before = _risk()
    before.on_change = lambda: store.save("risk", before.snapshot())
    before.register_pnl(-0.4)
    before.register_pnl(-0.3)

    after = _risk()
    after.restore(store.load("risk"))
    assert round(after.day_pnl, 4) == -0.7
    assert after.consecutive_losses == 2


def test_stale_day_is_rolled_on_restore(tmp_path):
    store = StateStore(tmp_path / "j.sqlite3")
    stale = _risk(day_start_ts=time.time() - 90_000, day_pnl=-1.9)
    stale.paused_until = time.time() + 3600
    store.save("risk", stale.snapshot())

    after = _risk()
    after.restore(store.load("risk"))
    # Сутки прошли — счётчики и паузы обнуляются, это не обход лимита.
    assert after.day_pnl == 0.0
    assert after.status() == "ok"


def test_manual_resume_is_persisted(tmp_path):
    store = StateStore(tmp_path / "j.sqlite3")
    risk = _risk()
    risk.on_change = lambda: store.save("risk", risk.snapshot())
    risk.register_pnl(-2.5)
    risk.manual_resume()

    after = _risk()
    after.restore(store.load("risk"))
    assert after.status() == "ok"


def test_pause_is_persisted(tmp_path):
    store = StateStore(tmp_path / "j.sqlite3")
    risk = _risk()
    risk.on_change = lambda: store.save("risk", risk.snapshot())
    risk.pause(2 * 3600)

    after = _risk()
    after.restore(store.load("risk"))
    assert after.status() == "soft_pause"


def _broker(tmp_path, store) -> PaperBroker:
    settings = SimpleNamespace(root=tmp_path, symbol_cooldown_sec=600)
    return PaperBroker(settings, _risk(), state=store)


def test_cooldown_survives_restart(tmp_path):
    store = StateStore(tmp_path / "data" / "journal" / "journal.sqlite3")
    broker = _broker(tmp_path, store)
    broker._remember_cooldown("SOLUSDT")
    assert broker.cooldown_left("SOLUSDT") > 0

    restarted = _broker(tmp_path, store)
    assert restarted.cooldown_left("SOLUSDT") > 0


def test_expired_cooldown_is_dropped_on_load(tmp_path):
    store = StateStore(tmp_path / "data" / "journal" / "journal.sqlite3")
    store.save("cooldown", {"SOLUSDT": time.time() - 10, "XRPUSDT": "мусор"})
    broker = _broker(tmp_path, store)
    assert broker.cooldown_left("SOLUSDT") == 0
    assert broker.cooldown_left("XRPUSDT") == 0
