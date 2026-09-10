"""Realistic paper fill / exit simulation (fees, spread, stops)."""

from __future__ import annotations

from typing import Any


def best_prices(snap: dict[str, Any]) -> tuple[float, float, float]:
    bids = snap.get("bids") or []
    asks = snap.get("asks") or []
    bid = float(bids[0][0]) if bids else float(snap.get("mid") or 0)
    ask = float(asks[0][0]) if asks else float(snap.get("mid") or 0)
    mid = float(snap.get("mid") or (bid + ask) / 2)
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
    entry_notional = abs(entry_price * size)
    exit_notional = abs(exit_price * size)
    return (entry_notional + exit_notional) * taker_rate


def take_price(
    entry_price: float,
    side: str,
    expected_move_pct: float,
    fee_roundtrip_pct: float,
) -> float:
    move_pct = max(expected_move_pct, fee_roundtrip_pct * 2.0) / 100.0
    if side == "long":
        return entry_price * (1.0 + move_pct)
    return entry_price * (1.0 - move_pct)


def passes_fee_filter(
    expected_move_pct: float, fee_roundtrip_pct: float, buffer_mult: float = 3.0
) -> bool:
    return expected_move_pct >= fee_roundtrip_pct * buffer_mult


def evaluate_exit(
    *,
    side: str,
    entry_price: float,
    stop_price: float | None,
    take_px: float | None,
    snap: dict[str, Any],
    age_sec: float,
    min_hold_sec: float,
    max_hold_sec: float,
    fee_roundtrip_pct: float,
) -> tuple[float, str] | None:
    """Return (exit_price, reason) or None while position stays open."""
    bid, ask, _ = best_prices(snap)
    side = side.lower()

    if stop_price is not None:
        stop = float(stop_price)
        if side == "long" and bid <= stop:
            return stop, "stop_loss"
        if side == "short" and ask >= stop:
            return stop, "stop_loss"

    if age_sec >= min_hold_sec and take_px is not None:
        take = float(take_px)
        if side == "long" and bid >= take:
            return exit_fill_price(snap, side), "take_profit"
        if side == "short" and ask <= take:
            return exit_fill_price(snap, side), "take_profit"

    if age_sec >= min_hold_sec:
        min_move = entry_price * (fee_roundtrip_pct / 100.0)
        mark = bid if side == "long" else ask
        favorable = (mark - entry_price) if side == "long" else (entry_price - mark)
        halfway = max_hold_sec * 0.5
        if age_sec >= halfway and favorable < min_move * 1.5:
            return exit_fill_price(snap, side), "no_impulse_exit"

    if age_sec >= max_hold_sec:
        return exit_fill_price(snap, side), "timeout_exit"

    return None
