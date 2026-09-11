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

from app.market import Momentum
from app.wall_tracker import WallTrack

# Скальп-цель ограничена реальностью, а не толщиной стакана.
MAX_EXPECTED_MOVE_PCT = 1.2
# Доля недавнего размаха, на которую разумно рассчитывать внутри сделки.
ROOM_CAPTURE = 0.5
# Насколько близко к краю окна ещё можно входить по импульсу.
CHASE_LIMIT = 0.85
# S5: шорт от отскока внутри слива, а не с минимума.
DRAIN_MIN_POSITION = 0.25


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
    rr: float = 0.0  # чистый RR, комиссия уже учтена
    room_pct: float = 0.0
    fee_share_of_risk: float = 0.0
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


def _impulse_risk_pct(
    *,
    capture: float,
    fee_roundtrip_pct: float,
    min_rr: float,
    technical_pct: float,
    floor_pct: float,
) -> float:
    """Стоп импульсного сетапа — не дальше, чем позволяет требуемый чистый RR.

    Раньше риск брался как доля размаха, а цель упиралась в потолок: чем
    волатильнее символ, тем хуже становилось соотношение. Возвращает 0, если
    даже минимально осмысленный стоп не укладывается в требуемый RR.
    """
    affordable = (capture - fee_roundtrip_pct) / min_rr - fee_roundtrip_pct
    risk = min(technical_pct, affordable)
    return risk if risk >= floor_pct else 0.0


def _trigger(momentum: Momentum | None, change24: float) -> bool:
    """Триггер импульсного сетапа.

    Суточное изменение — это контекст, а не повод для входа. Нужен ход прямо
    сейчас в ту же сторону, и цена не должна стоять у самого края окна:
    покупка на вершине выноса — это вход в чужой профит.
    """
    if momentum is None or momentum.range_pct <= 0:
        return False
    if change24 > 0:
        return momentum.change_pct > 0 and momentum.position <= CHASE_LIMIT
    return momentum.change_pct < 0 and momentum.position >= 1.0 - CHASE_LIMIT


def _viable(
    *,
    expected: float,
    risk_pct: float,
    fee_roundtrip_pct: float,
    min_rr: float,
    max_fee_share: float = 0.20,
) -> tuple[bool, float, float]:
    """Сетап проходит, только если цель окупает комиссию и стоп.

    RR считается по ЧИСТЫМ величинам: комиссия уменьшает прибыль и
    одновременно увеличивает убыток, поэтому номинальный RR всегда льстит.
    За ночь средний риск был 0.362% при round-trip 0.11% и номинальном RR 2.0 —
    чистый RR такой сделки всего 1.29, и именно на этом разрыве стратегия
    теряла деньги при формально проходящем фильтре.
    """
    if risk_pct <= 0:
        return False, 0.0, 1.0
    fee_share = fee_roundtrip_pct / risk_pct
    net_gain = expected - fee_roundtrip_pct
    net_loss = risk_pct + fee_roundtrip_pct
    if net_gain <= 0 or net_loss <= 0:
        return False, 0.0, fee_share
    net_rr = net_gain / net_loss
    if net_rr < min_rr:
        return False, net_rr, fee_share
    return True, net_rr, fee_share


def detect_signals(
    *,
    symbol: str,
    snap: dict[str, Any],
    ticker: dict[str, Any] | None,
    fee_roundtrip_pct: float = 0.11,
    wall_track: WallTrack | None = None,
    room_pct: float | None = None,
    momentum: Momentum | None = None,
    min_wall_observations: int = 2,
    min_wall_age_sec: float = 45.0,
    min_wall_held_share: float = 0.6,
    wall_approach_pct: float = 0.12,
    min_rr: float = 1.5,
    max_fee_share: float = 0.20,
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
            ok, rr, fee_share = _viable(
                expected=capture,
                risk_pct=risk_pct,
                fee_roundtrip_pct=fee_roundtrip_pct,
                min_rr=min_rr,
                max_fee_share=max_fee_share,
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
                        fee_share_of_risk=fee_share,
                        wall=wall_track.to_dict(),
                    )
                )

    # S4 — продолжение по активному инструменту.
    if regime == "trend_in_play" and abs(change24) >= 8 and _trigger(momentum, change24):
        side = "long" if change24 > 0 else "short"
        risk_pct = _impulse_risk_pct(
            capture=capture,
            fee_roundtrip_pct=fee_roundtrip_pct,
            min_rr=min_rr,
            technical_pct=_clamp(room_pct * 0.25, 0.1, 0.8),
            floor_pct=max(spread_pct * 3, 0.05),
        )
        stop = (
            mid * (1 - risk_pct / 100) if side == "long" else mid * (1 + risk_pct / 100)
        )
        ok, rr, fee_share = _viable(
            expected=capture,
            risk_pct=risk_pct,
            fee_roundtrip_pct=fee_roundtrip_pct,
            min_rr=min_rr,
            max_fee_share=max_fee_share,
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
                    reason=(
                        f"S4 in-play change24={change24:.2f}% "
                        f"ход={momentum.change_pct:+.2f}% "
                        f"позиция={momentum.position:.2f} размах={room_pct:.2f}%"
                    ),
                    expected_move_pct=capture,
                    risk_pct=risk_pct,
                    rr=rr,
                    room_pct=room_pct,
                    fee_share_of_risk=fee_share,
                )
            )

    # S5 — слив после листинга/вертикали: шортим продолжение слива, не дно.
    if (
        regime == "post_listing_drain"
        and momentum is not None
        and momentum.change_pct < 0
        and momentum.position >= DRAIN_MIN_POSITION
    ):
        risk_pct = _impulse_risk_pct(
            capture=capture,
            fee_roundtrip_pct=fee_roundtrip_pct,
            min_rr=min_rr,
            technical_pct=_clamp(room_pct * 0.25, 0.15, 0.8),
            floor_pct=max(spread_pct * 3, 0.05),
        )
        stop = mid * (1 + risk_pct / 100)
        ok, rr, fee_share = _viable(
            expected=capture,
            risk_pct=risk_pct,
            fee_roundtrip_pct=fee_roundtrip_pct,
            min_rr=min_rr,
            max_fee_share=max_fee_share,
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
                    reason=(
                        f"S5 drain change24={change24:.2f}% "
                        f"ход={momentum.change_pct:+.2f}% "
                        f"позиция={momentum.position:.2f} размах={room_pct:.2f}%"
                    ),
                    expected_move_pct=capture,
                    risk_pct=risk_pct,
                    rr=rr,
                    room_pct=room_pct,
                    fee_share_of_risk=fee_share,
                )
            )

    return out
