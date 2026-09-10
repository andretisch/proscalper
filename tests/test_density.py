"""Детектор плотности не должен принимать обычный топ стакана за ЛП."""

from __future__ import annotations

from app.density import book_range_pct, find_density


def _flat_side(start: float, step: float, size: float, n: int = 60):
    return [[start + step * i, size] for i in range(n)]


def test_uniform_book_has_no_density():
    bids = _flat_side(100.0, -0.01, 1.0)
    asks = _flat_side(100.01, 0.01, 1.0)
    assert find_density(bids, asks, 100.005) is None


def test_top_of_book_size_is_not_a_wall():
    # лучший бид крупнее остальных, но стоит вплотную к цене
    bids = _flat_side(100.0, -0.01, 1.0)
    bids[0] = [100.0, 60.0]
    asks = _flat_side(100.01, 0.01, 1.0)
    assert find_density(bids, asks, 100.005) is None


def test_real_wall_is_found_with_depth_share():
    bids = _flat_side(100.0, -0.01, 1.0)
    bids[10] = [99.90, 80.0]  # ~0.1% ниже mid, держит большую долю глубины
    asks = _flat_side(100.01, 0.01, 1.0)
    d = find_density(bids, asks, 100.005)
    assert d is not None
    assert d.side == "bid"
    assert d.price == 99.90
    assert d.depth_share > 0.15
    assert 0.09 < d.dist_pct < 0.12


def test_wall_beyond_max_distance_ignored():
    bids = _flat_side(100.0, -0.01, 1.0)
    bids[50] = [99.50, 80.0]  # ~0.5% — вне окна сетапа
    asks = _flat_side(100.01, 0.01, 1.0)
    assert find_density(bids, asks, 100.005, max_dist_pct=0.45) is None


def test_thin_book_is_rejected():
    assert find_density([[100.0, 5.0]], [[100.1, 5.0]], 100.05) is None


def test_book_range_pct():
    bids = _flat_side(100.0, -0.01, 1.0, n=11)
    assert round(book_range_pct(bids, 100.0), 2) == 0.10
