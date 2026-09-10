"""Плотность засчитывается только после выдержки во времени."""

from __future__ import annotations

from app.density import Density
from app.wall_tracker import WallTracker


def _d(price: float = 99.9, size: float = 80.0, side: str = "bid") -> Density:
    return Density(
        side=side, price=price, size=size, dist_pct=0.1, depth_share=0.3, ratio=80.0
    )


def test_single_observation_is_not_mature():
    t = WallTracker()
    track = t.observe("X", _d(), now=1000.0)
    assert track is not None
    assert not track.is_mature(2, 45.0)


def test_repeated_observations_mature_the_wall():
    t = WallTracker()
    t.observe("X", _d(), now=1000.0)
    track = t.observe("X", _d(price=99.91), now=1060.0)
    assert track.observations == 2
    assert track.age_sec == 60.0
    assert track.is_mature(2, 45.0)


def test_pulled_wall_resets_maturity():
    t = WallTracker()
    t.observe("X", _d(), now=1000.0)
    t.observe("X", _d(), now=1060.0)
    assert t.observe("X", None, now=1120.0) is None
    track = t.observe("X", _d(), now=1180.0)
    assert track.observations == 1
    assert not track.is_mature(2, 45.0)


def test_moved_wall_starts_new_track():
    t = WallTracker(price_tol_pct=0.05)
    t.observe("X", _d(price=99.9), now=1000.0)
    track = t.observe("X", _d(price=99.0), now=1060.0)
    assert track.observations == 1
    assert track.price == 99.0


def test_eaten_wall_loses_held_share():
    t = WallTracker()
    t.observe("X", _d(size=100.0), now=1000.0)
    track = t.observe("X", _d(size=20.0), now=1060.0)
    assert track.held_share == 0.2
    assert track.is_mature(2, 45.0)
