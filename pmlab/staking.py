"""Weighted staking: the single sizing rule shared by the paper engine, the
retroactive recompute and the backtests. Pure stdlib, no I/O.
"""
from __future__ import annotations


def weighted_clip(capital: float, frac: float, bet_max: float,
                  min_clip: float = 5.0) -> float:
    """USD to stake = `frac` of capital, floored at min_clip, capped at the book-depth
    bet_max, and never more than the capital on hand. The SINGLE definition of the
    weighted sizing — reused by the paper engine (favorite), the retroactive
    recompute and the backtest so they all size identically (cf. always-modularize)."""
    return round(min(max(min_clip, frac * capital), bet_max, capital), 2)
