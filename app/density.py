"""Обнаружение лимитной плотности (ЛП) в стакане.

Наивный критерий «размер / медиана уровня» бесполезен: на BTC обычный
топ-стакана даёт ratio ~600, потому что медиана уровня почти нулевая.
Плотность — это уровень, который держит заметную ДОЛЮ видимой глубины
стороны и стоит на дистанции от цены, а не является лучшим бид/аском.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import median
from typing import Any, Sequence

Level = Sequence[float]


@dataclass(frozen=True)
class Density:
    side: str  # bid | ask
    price: float
    size: float
    dist_pct: float  # расстояние от mid, %
    depth_share: float  # доля глубины стороны в полосе сканирования
    ratio: float  # размер / медиана остальных уровней полосы

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def book_range_pct(levels: Sequence[Level], mid: float) -> float:
    """Насколько далеко от mid дотягивается видимая часть стакана."""
    if not levels or mid <= 0:
        return 0.0
    return abs(float(levels[-1][0]) - mid) / mid * 100.0


def _side_density(
    levels: Sequence[Level],
    side: str,
    mid: float,
    *,
    scan_pct: float,
    min_dist_pct: float,
    max_dist_pct: float,
    min_depth_share: float,
    min_ratio: float,
) -> Density | None:
    band = [
        (float(p), float(s))
        for p, s in levels
        if abs(float(p) - mid) / mid * 100.0 <= scan_pct
    ]
    if len(band) < 5:
        return None
    depth = sum(s for _, s in band)
    if depth <= 0:
        return None

    best: Density | None = None
    for price, size in band:
        dist = abs(price - mid) / mid * 100.0
        if dist < min_dist_pct or dist > max_dist_pct:
            continue
        share = size / depth
        if share < min_depth_share:
            continue
        others = [s for p, s in band if p != price]
        base = median(others) if others else 0.0
        ratio = size / base if base > 0 else float("inf")
        if ratio < min_ratio:
            continue
        if best is None or share > best.depth_share:
            best = Density(
                side=side,
                price=price,
                size=size,
                dist_pct=dist,
                depth_share=share,
                ratio=ratio,
            )
    return best


def find_density(
    bids: Sequence[Level],
    asks: Sequence[Level],
    mid: float,
    *,
    scan_pct: float = 0.6,
    min_dist_pct: float = 0.02,
    max_dist_pct: float = 0.45,
    min_depth_share: float = 0.15,
    min_ratio: float = 8.0,
) -> Density | None:
    """Самая крупная плотность по обе стороны; None — плотности нет."""
    if not mid or mid <= 0:
        return None
    found = [
        d
        for d in (
            _side_density(
                bids,
                "bid",
                mid,
                scan_pct=scan_pct,
                min_dist_pct=min_dist_pct,
                max_dist_pct=max_dist_pct,
                min_depth_share=min_depth_share,
                min_ratio=min_ratio,
            ),
            _side_density(
                asks,
                "ask",
                mid,
                scan_pct=scan_pct,
                min_dist_pct=min_dist_pct,
                max_dist_pct=max_dist_pct,
                min_depth_share=min_depth_share,
                min_ratio=min_ratio,
            ),
        )
        if d is not None
    ]
    if not found:
        return None
    return max(found, key=lambda d: d.depth_share)
