"""S2 срабатывает только на отстоявшейся ЛП с окупаемой целью."""

from __future__ import annotations

import pytest

from app.signals import detect_signals
from app.wall_tracker import WallTrack

FEE_RT = 0.11
TICKER = {"price24hPcnt": "0.01"}  # +1% — боковик


def _snap(mid: float = 100.0, wall_price: float = 99.9, wall_side: str = "bid"):
    return {
        "mid": mid,
        "spread": 0.01,
        "wall_side": wall_side,
        "wall_price": wall_price,
        "wall_size": 80.0,
        "wall_share": 0.3,
        "wall_ratio": 80.0,
        "bids": [[99.99, 1.0]],
        "asks": [[100.01, 1.0]],
    }


def _mature(side: str = "bid", price: float = 99.9, held: float = 1.0) -> WallTrack:
    return WallTrack(
        side=side,
        price=price,
        first_seen=0.0,
        last_seen=120.0,
        observations=3,
        max_size=100.0,
        last_size=100.0 * held,
    )


def _signals(**kw):
    params = {
        "symbol": "TESTUSDT",
        "snap": _snap(),
        "ticker": TICKER,
        "fee_roundtrip_pct": FEE_RT,
        "wall_track": _mature(),
        "room_pct": 1.5,
    }
    params.update(kw)
    return detect_signals(**params)


def test_mature_wall_gives_s2_long():
    sigs = _signals()
    assert len(sigs) == 1
    s = sigs[0]
    assert s.setup_id == "S2_false_breakout_density"
    assert s.side == "long"
    assert s.stop_price < 99.9  # стоп за уровнем
    assert s.rr >= 1.5


def test_ask_wall_gives_short():
    sigs = _signals(
        snap=_snap(wall_price=100.1, wall_side="ask"),
        wall_track=_mature(side="ask", price=100.1),
    )
    assert sigs[0].side == "short"
    assert sigs[0].stop_price > 100.1


def test_no_history_no_trade():
    assert _signals(room_pct=None) == []


def test_dead_symbol_cannot_pay_fees():
    # размах 0.05% — половина не покроет round-trip 0.11%
    assert _signals(room_pct=0.05) == []


def test_fresh_wall_rejected():
    fresh = WallTrack(
        side="bid", price=99.9, first_seen=0.0, last_seen=10.0, observations=1,
        max_size=100.0, last_size=100.0,
    )
    assert _signals(wall_track=fresh) == []


def test_eaten_wall_rejected():
    assert _signals(wall_track=_mature(held=0.3)) == []


def test_price_far_from_wall_rejected():
    # цена ещё не подошла к плотности: 1% выше
    assert _signals(snap=_snap(mid=101.0, wall_price=99.9)) == []


def test_wall_on_wrong_side_rejected():
    # «бид»-плотность выше цены — структурно невозможно, не входим
    assert (
        _signals(
            snap=_snap(mid=99.85, wall_price=99.9),
            wall_track=_mature(price=99.9),
        )
        == []
    )


@pytest.mark.parametrize("change", ["0.09", "-0.15"])
def test_trending_symbol_is_not_s2(change):
    sigs = _signals(ticker={"price24hPcnt": change})
    assert all(s.setup_id != "S2_false_breakout_density" for s in sigs)
