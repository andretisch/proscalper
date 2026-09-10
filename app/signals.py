"""Rule-based signal candidates from order book + tickers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Signal:
    symbol: str
    setup_id: str
    side: str  # long|short
    regime: str
    entry_price: float
    stop_price: float
    reason: str
    wall_ratio: float | None = None
    expected_move_pct: float = 0.2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return abs(a - b) / b * 100


def detect_signals(
    *,
    symbol: str,
    snap: dict[str, Any],
    ticker: dict[str, Any] | None,
    fee_roundtrip_pct: float = 0.11,
) -> list[Signal]:
    """Lightweight detectors for paper MVP (S2 density + S4 in-play hint)."""
    out: list[Signal] = []
    mid = snap.get("mid")
    if not mid:
        return out

    change24 = float(ticker.get("price24hPcnt") or 0) * 100 if ticker else 0.0
    # Bybit returns fraction sometimes as string already percent-like; handle both
    if abs(change24) < 1 and ticker and ticker.get("price24hPcnt"):
        try:
            change24 = float(ticker["price24hPcnt"]) * 100
        except Exception:
            pass

    regime = "range"
    if abs(change24) >= 5:
        regime = "trend_in_play"
    if change24 <= -12:
        regime = "post_listing_drain"

    wall_side = snap.get("wall_side")
    wall_price = snap.get("wall_price")
    wall_ratio = snap.get("wall_ratio") or 0

    # S2: bounce from aged-enough thick wall (age not available on REST snapshot → ratio filter)
    if (
        regime == "range"
        and wall_side
        and wall_price
        and wall_ratio >= 3.0
        and _pct(mid, float(wall_price)) <= 0.15
    ):
        if wall_side == "bid":
            # long from bid wall, stop below
            stop = float(wall_price) * 0.999
            side = "long"
        else:
            stop = float(wall_price) * 1.001
            side = "short"
        expected = max(0.2, wall_ratio * 0.05)
        if expected >= fee_roundtrip_pct * 3:
            out.append(
                Signal(
                    symbol=symbol,
                    setup_id="S2_false_breakout_density",
                    side=side,
                    regime=regime,
                    entry_price=float(mid),
                    stop_price=stop,
                    reason=f"S2 wall {wall_side}@{wall_price} ratio={wall_ratio:.1f}",
                    wall_ratio=wall_ratio,
                    expected_move_pct=expected,
                )
            )

    # S4: active coin continuation hint (no full level engine yet — candidate for AI)
    if regime == "trend_in_play" and abs(change24) >= 8:
        side = "long" if change24 > 0 else "short"
        stop = mid * (0.997 if side == "long" else 1.003)
        expected = max(0.25, abs(change24) * 0.05)
        if expected >= fee_roundtrip_pct * 3:
            out.append(
                Signal(
                    symbol=symbol,
                    setup_id="S4_active_continuation",
                    side=side,
                    regime=regime,
                    entry_price=float(mid),
                    stop_price=float(stop),
                    reason=f"S4 in-play change24={change24:.2f}%",
                    expected_move_pct=expected,
                )
            )

    # S5 drain
    if regime == "post_listing_drain":
        expected = 0.35
        if expected >= fee_roundtrip_pct * 3:
            out.append(
                Signal(
                    symbol=symbol,
                    setup_id="S5_drain_short",
                    side="short",
                    regime=regime,
                    entry_price=float(mid),
                    stop_price=float(mid) * 1.004,
                    reason=f"S5 drain change24={change24:.2f}%",
                    expected_move_pct=expected,
                )
            )

    return out
