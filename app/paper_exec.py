"""Paper fills + playbook position management (fees, structure, impulse)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DENSITY_SETUPS = {
    "S2",
    "S2_false_breakout_density",
    "S3",
    "S3_flip_eaten_volume",
}
IMPULSE_TAKE_SETUPS = {
    "S1",
    "S1_true_breakout",
    "S4",
    "S4_active_continuation",
    "S5",
    "S5_drain_short",
}


def best_prices(snap: dict[str, Any]) -> tuple[float, float, float]:
    bids = snap.get("bids") or []
    asks = snap.get("asks") or []
    bid = float(bids[0][0]) if bids else float(snap.get("mid") or 0)
    ask = float(asks[0][0]) if asks else float(snap.get("mid") or 0)
    mid = float(snap.get("mid") or ((bid + ask) / 2 if bid and ask else 0))
    return bid, ask, mid


def entry_fill_price(snap: dict[str, Any], side: str) -> float:
    bid, ask, mid = best_prices(snap)
    if side == "long":
        return ask if ask > 0 else mid
    return bid if bid > 0 else mid


def exit_fill_price(snap: dict[str, Any], side: str) -> float:
    bid, ask, mid = best_prices(snap)
    if side == "long":
        return bid if bid > 0 else mid
    return ask if ask > 0 else mid


def round_trip_fees_usd(
    entry_price: float, exit_price: float, size: float, taker_rate: float
) -> float:
    return (abs(entry_price * size) + abs(exit_price * size)) * taker_rate


def net_pnl_usd(
    *,
    side: str,
    entry_price: float,
    exit_price: float,
    size: float,
    taker_rate: float,
) -> float:
    if side == "long":
        gross = (exit_price - entry_price) * size
    else:
        gross = (entry_price - exit_price) * size
    return gross - round_trip_fees_usd(entry_price, exit_price, size, taker_rate)


def favorable_pct(side: str, entry_price: float, snap: dict[str, Any]) -> float:
    bid, ask, _ = best_prices(snap)
    if entry_price <= 0:
        return 0.0
    if side == "long":
        return (bid - entry_price) / entry_price * 100.0
    return (entry_price - ask) / entry_price * 100.0


def be_stop_price(side: str, entry_price: float, taker_rate: float) -> float:
    """Stop at fee-adjusted breakeven (round-trip taker). Never worse than entry."""
    adj = 2.0 * taker_rate
    if side == "long":
        return entry_price * (1.0 + adj)
    return entry_price * (1.0 - adj)


def impulse_threshold_pct(fee_roundtrip_pct: float) -> float:
    return max(float(fee_roundtrip_pct), 0.12)


def wall_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
    return {
        "side": snap.get("wall_side"),
        "price": snap.get("wall_price"),
        "size": snap.get("wall_size"),
        "ratio": snap.get("wall_ratio"),
    }


def wall_invalidated(
    wall_at_entry: dict[str, Any] | None, snap: dict[str, Any]
) -> str | None:
    """S2/S3: volume moved/cancelled/eaten → flatten, do not trail."""
    if not wall_at_entry or not wall_at_entry.get("price"):
        return None
    entry_side = wall_at_entry.get("side")
    entry_price = float(wall_at_entry["price"])
    entry_size = float(wall_at_entry.get("size") or 0)
    now = wall_snapshot(snap)
    now_price = now.get("price")
    now_size = float(now.get("size") or 0)
    if not now_price or now.get("side") != entry_side:
        return "wall_pulled"
    if abs(float(now_price) - entry_price) / entry_price >= 0.0005:
        return "wall_moved"
    if entry_size > 0 and now_size / entry_size <= 0.35:
        return "wall_eaten"
    return None


def stop_hit(side: str, stop_price: float | None, snap: dict[str, Any]) -> bool:
    if stop_price is None:
        return False
    bid, ask, _ = best_prices(snap)
    stop = float(stop_price)
    if side == "long":
        return bid <= stop
    return ask >= stop


def s2_retest(side: str, entry_price: float, snap: dict[str, Any]) -> bool:
    """After bounce, price returned to entry (classic LP full-flat on retest)."""
    bid, ask, _ = best_prices(snap)
    tol = entry_price * 0.0004
    if side == "long":
        return bid <= entry_price + tol
    return ask >= entry_price - tol


@dataclass
class ManageState:
    impulse_seen: bool = False
    be_done: bool = False


@dataclass
class ManageDecision:
    action: str  # hold | flatten | be
    reason: str
    exit_price: float | None = None
    new_stop: float | None = None
    mechanical: bool = True


def evaluate_playbook(
    *,
    setup_id: str,
    side: str,
    entry_price: float,
    stop_price: float | None,
    snap: dict[str, Any],
    age_sec: float,
    wall_at_entry: dict[str, Any] | None,
    state: ManageState,
    fee_roundtrip_pct: float,
    taker_rate: float,
    max_seconds_without_impulse: float,
    unattended_sec: float = 4 * 3600,
) -> ManageDecision:
    """Hard playbook rules. LLM is only consulted on hold."""
    side = side.lower()
    mark_exit = exit_fill_price(snap, side)
    fav = favorable_pct(side, entry_price, snap)
    impulse_need = impulse_threshold_pct(fee_roundtrip_pct)

    if stop_hit(side, stop_price, snap):
        return ManageDecision("flatten", "stop_loss", float(stop_price), mechanical=True)

    if setup_id in DENSITY_SETUPS:
        inv = wall_invalidated(wall_at_entry, snap)
        if inv:
            return ManageDecision("flatten", inv, mark_exit, mechanical=True)

    if (not state.impulse_seen) and fav >= impulse_need:
        state.impulse_seen = True
        be = be_stop_price(side, entry_price, taker_rate)
        if setup_id in IMPULSE_TAKE_SETUPS:
            if net_pnl_usd(
                side=side,
                entry_price=entry_price,
                exit_price=mark_exit,
                size=1.0,
                taker_rate=taker_rate,
            ) > 0:
                return ManageDecision(
                    "flatten", "first_impulse", mark_exit, mechanical=True
                )
        if not state.be_done:
            state.be_done = True
            return ManageDecision("be", "auto_be_after_impulse", new_stop=be)

    if state.impulse_seen and setup_id in DENSITY_SETUPS and s2_retest(
        side, entry_price, snap
    ):
        return ManageDecision("flatten", "retest_flat", mark_exit, mechanical=True)

    if (not state.impulse_seen) and age_sec >= max_seconds_without_impulse:
        return ManageDecision("flatten", "no_impulse", mark_exit, mechanical=True)

    if age_sec >= unattended_sec:
        return ManageDecision("flatten", "unattended_flatten", mark_exit, mechanical=True)

    return ManageDecision("hold", "manage")
