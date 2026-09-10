"""Правила ведения позиции по playbook."""

from __future__ import annotations

import pytest

from app.paper_exec import (
    ManageState,
    be_stop_price,
    entry_fill_price,
    evaluate_playbook,
    exit_fill_price,
    net_pnl_usd,
    round_trip_fees_usd,
    wall_invalidated,
)

TAKER = 0.00055
FEE_RT_PCT = TAKER * 100 * 2  # 0.11%


def _snap(bid: float, ask: float, wall_price: float | None = 99.0,
          wall_size: float = 100.0, wall_side: str = "bid"):
    return {
        "bids": [[bid, 5.0]],
        "asks": [[ask, 5.0]],
        "mid": (bid + ask) / 2,
        "wall_side": wall_side if wall_price else None,
        "wall_price": wall_price,
        "wall_size": wall_size if wall_price else None,
        "wall_ratio": 50.0,
    }


WALL_AT_ENTRY = {"side": "bid", "price": 99.0, "size": 100.0, "ratio": 50.0}


def _eval(setup_id="S2_false_breakout_density", side="long", entry=100.0,
          stop=98.9, snap=None, age=10.0, wall=None, state=None):
    return evaluate_playbook(
        setup_id=setup_id,
        side=side,
        entry_price=entry,
        stop_price=stop,
        snap=snap if snap is not None else _snap(100.0, 100.02),
        age_sec=age,
        wall_at_entry=WALL_AT_ENTRY if wall is None else wall,
        state=state or ManageState(),
        fee_roundtrip_pct=FEE_RT_PCT,
        taker_rate=TAKER,
        max_seconds_without_impulse=90.0,
    )


def test_fills_cross_the_spread():
    snap = _snap(99.98, 100.02)
    assert entry_fill_price(snap, "long") == 100.02
    assert exit_fill_price(snap, "long") == 99.98
    assert entry_fill_price(snap, "short") == 99.98
    assert exit_fill_price(snap, "short") == 100.02


def test_round_trip_fees_count_both_sides():
    assert round_trip_fees_usd(100.0, 101.0, 2.0, TAKER) == pytest.approx(
        (200.0 + 202.0) * TAKER
    )


def test_net_pnl_is_gross_minus_fees():
    net = net_pnl_usd(side="long", entry_price=100.0, exit_price=101.0,
                      size=2.0, taker_rate=TAKER)
    assert net == pytest.approx(2.0 - (200.0 + 202.0) * TAKER)


def test_be_stop_covers_round_trip_fee():
    assert be_stop_price("long", 100.0, TAKER) > 100.0
    assert be_stop_price("short", 100.0, TAKER) < 100.0


def test_stop_loss_has_priority():
    d = _eval(snap=_snap(98.5, 98.52), stop=98.9)
    assert d.action == "flatten"
    assert d.reason == "stop_loss"


def test_pulled_wall_flattens_density_setup():
    d = _eval(snap=_snap(100.0, 100.02, wall_price=None))
    assert d.action == "flatten"
    assert d.reason == "wall_pulled"


def test_moved_wall_flattens():
    d = _eval(snap=_snap(100.0, 100.02, wall_price=99.5))
    assert d.action == "flatten"
    assert d.reason == "wall_moved"


def test_eaten_wall_flattens():
    d = _eval(snap=_snap(100.0, 100.02, wall_size=20.0))
    assert d.action == "flatten"
    assert d.reason == "wall_eaten"


def test_intact_wall_holds():
    assert _eval().action == "hold"


def test_impulse_moves_density_setup_to_breakeven():
    d = _eval(snap=_snap(100.3, 100.32))
    assert d.action == "be"
    assert d.reason == "auto_be_after_impulse"
    assert d.new_stop > 100.0


def test_impulse_takes_profit_on_breakout_setup():
    d = _eval(setup_id="S1_true_breakout", snap=_snap(100.3, 100.32))
    assert d.action == "flatten"
    assert d.reason == "first_impulse"


def test_no_impulse_within_window_flattens():
    d = _eval(age=120.0)
    assert d.action == "flatten"
    assert d.reason == "no_impulse"


def test_window_not_expired_still_holds():
    assert _eval(age=80.0).action == "hold"


def test_retest_flattens_after_impulse():
    state = ManageState(impulse_seen=True, be_done=True)
    d = _eval(state=state, snap=_snap(99.99, 100.01))
    assert d.action == "flatten"
    assert d.reason == "retest_flat"


def test_wall_invalidated_none_when_stable():
    assert wall_invalidated(WALL_AT_ENTRY, _snap(100.0, 100.02)) is None


def test_non_density_setup_ignores_wall():
    d = _eval(setup_id="S4_active_continuation",
              snap=_snap(100.0, 100.02, wall_price=None))
    assert d.action == "hold"
