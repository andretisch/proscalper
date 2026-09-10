"""Сетапы playbook из стакана и тикера.

Ключевые принципы, без которых сетап вырождается в шум:
  * плотность должна отстояться (WallTrack), одиночный снимок — спуфинг;
  * ход должен реально существовать (recent_range_pct), а не быть выведен
    из отношения объёмов;
  * стоп ставится за уровень, а цель обязана окупить round-trip комиссию.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.wall_tracker import WallTrack

# Скальп-цель ограничена реальностью, а не толщиной стакана.
MAX_EXPECTED_MOVE_PCT = 1.2
# Доля недавнего размаха, на которую разумно рассчитывать внутри сделки.
ROOM_CAPTURE = 0.5


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
    risk_pct: float = 0.0
    rr: float = 0.0
    room_pct: float = 0.0
    wall: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return abs(a - b) / b * 100


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def _spread_pct(snap: dict[str, Any]) -> float:
    mid = float(snap.get("mid") or 0)
    spread = float(snap.get("spread") or 0)
    if mid <= 0:
        return 0.0
    return spread / mid * 100.0


def _regime(change24: float) -> str:
    if change24 <= -12:
        return "post_listing_drain"
    if abs(change24) >= 5:
        return "trend_in_play"
    return "range"


def _viable(
    *, expected: float, risk_pct: float, fee_roundtrip_pct: float, min_rr: float
) -> tuple[bool, float]:
    """Сетап проходит, только если цель окупает комиссию и стоп."""
    if risk_pct <= 0:
        return False, 0.0
    rr = expected / risk_pct
    if expected < fee_roundtrip_pct * 3:
        return False, rr
    if rr < min_rr:
        return False, rr
    return True, rr


def detect_signals(
    *,
    symbol: str,
    snap: dict[str, Any],
    ticker: dict[str, Any] | None,
    fee_roundtrip_pct: float = 0.11,
    wall_track: WallTrack | None = None,
    room_pct: float | None = None,
    min_wall_observations: int = 2,
    min_wall_age_sec: float = 45.0,
    min_wall_held_share: float = 0.6,
    wall_approach_pct: float = 0.12,
    min_rr: float = 1.5,
) -> list[Signal]:
    out: list[Signal] = []
    mid = snap.get("mid")
    if not mid:
        return out
    mid = float(mid)

    # Без истории движения символа торговать вслепую нельзя.
    if room_pct is None or room_pct <= 0:
        return out
    capture = _clamp(room_pct * ROOM_CAPTURE, 0.0, MAX_EXPECTED_MOVE_PCT)

    change24 = 0.0
    if ticker and ticker.get("price24hPcnt") is not None:
        try:
            change24 = float(ticker["price24hPcnt"]) * 100
        except (TypeError, ValueError):
            change24 = 0.0
    regime = _regime(change24)
    spread_pct = _spread_pct(snap)

    # S2 — отбой от отстоявшейся лимитной плотности.
    wall_price = snap.get("wall_price")
    wall_side = snap.get("wall_side")
    if (
        regime == "range"
        and wall_side
        and wall_price
        and wall_track is not None
        and wall_track.side == wall_side
        and wall_track.is_mature(min_wall_observations, min_wall_age_sec)
        and wall_track.held_share >= min_wall_held_share
    ):
        wall_price = float(wall_price)
        dist = _pct(mid, wall_price)
        approaching = dist <= wall_approach_pct
        correct_side = (wall_side == "bid" and wall_price < mid) or (
            wall_side == "ask" and wall_price > mid
        )
        if approaching and correct_side:
            side = "long" if wall_side == "bid" else "short"
            # Стоп за уровень: буфер не меньше двух спредов, иначе шум выбьет.
            buffer_pct = max(0.05, spread_pct * 2)
            stop = (
                wall_price * (1 - buffer_pct / 100)
                if side == "long"
                else wall_price * (1 + buffer_pct / 100)
            )
            risk_pct = _pct(mid, stop)
            ok, rr = _viable(
                expected=capture,
                risk_pct=risk_pct,
                fee_roundtrip_pct=fee_roundtrip_pct,
                min_rr=min_rr,
            )
            if ok:
                out.append(
                    Signal(
                        symbol=symbol,
                        setup_id="S2_false_breakout_density",
                        side=side,
                        regime=regime,
                        entry_price=mid,
                        stop_price=stop,
                        reason=(
                            f"S2 ЛП {wall_side}@{wall_price} "
                            f"share={(snap.get('wall_share') or 0) * 100:.0f}% "
                            f"обс={wall_track.observations} возраст={wall_track.age_sec:.0f}с "
                            f"дист={dist:.3f}%"
                        ),
                        wall_ratio=snap.get("wall_ratio"),
                        expected_move_pct=capture,
                        risk_pct=risk_pct,
                        rr=rr,
                        room_pct=room_pct,
                        wall=wall_track.to_dict(),
                    )
                )

    # S4 — продолжение по активному инструменту.
    if regime == "trend_in_play" and abs(change24) >= 8:
        side = "long" if change24 > 0 else "short"
        risk_pct = max(_clamp(room_pct * 0.25, 0.1, 0.8), spread_pct * 3)
        stop = (
            mid * (1 - risk_pct / 100) if side == "long" else mid * (1 + risk_pct / 100)
        )
        ok, rr = _viable(
            expected=capture,
            risk_pct=risk_pct,
            fee_roundtrip_pct=fee_roundtrip_pct,
            min_rr=min_rr,
        )
        if ok:
            out.append(
                Signal(
                    symbol=symbol,
                    setup_id="S4_active_continuation",
                    side=side,
                    regime=regime,
                    entry_price=mid,
                    stop_price=stop,
                    reason=f"S4 in-play change24={change24:.2f}% размах={room_pct:.2f}%",
                    expected_move_pct=capture,
                    risk_pct=risk_pct,
                    rr=rr,
                    room_pct=room_pct,
                )
            )

    # S5 — слив после листинга/вертикали.
    if regime == "post_listing_drain":
        risk_pct = max(_clamp(room_pct * 0.25, 0.15, 0.8), spread_pct * 3)
        stop = mid * (1 + risk_pct / 100)
        ok, rr = _viable(
            expected=capture,
            risk_pct=risk_pct,
            fee_roundtrip_pct=fee_roundtrip_pct,
            min_rr=min_rr,
        )
        if ok:
            out.append(
                Signal(
                    symbol=symbol,
                    setup_id="S5_drain_short",
                    side="short",
                    regime=regime,
                    entry_price=mid,
                    stop_price=stop,
                    reason=f"S5 drain change24={change24:.2f}% размах={room_pct:.2f}%",
                    expected_move_pct=capture,
                    risk_pct=risk_pct,
                    rr=rr,
                    room_pct=room_pct,
                )
            )

    return out
