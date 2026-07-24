"""Diffusion model for P(close > open) — the only survivor of the falsified esoteric era.

This file once held a three-forces ("Law of Three") gate for the gurdjieff brain. That
brain — and the whole esoteric family — was falsified and retired (research/FINDINGS.md):
the live edge is in the favorite PRICE (the longshot-favorite premium), NOT in a model of
BTC direction. All that survives is `model_p_up`, which the runner uses only to populate
`ctx.p_up` (telemetry / a reference probability) — the zlead family ignores it for the
decision. Kept here (not deleted) because the runner and research/analyze.py both read it.
"""

from __future__ import annotations

import math


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


SIGMA_FLOOR = 1.5e-4   # 1.5 bp/√min: a frozen tape must not saturate the model
P_SHRINK = 0.88        # pull p toward 0.5: model + oracle-proxy overconfidence guard


def model_p_up(spot: float, window_open: float, sigma_min: float, tau_min: float) -> float:
    """P(close > open) under a driftless diffusion of the remaining tau minutes.

    p = Phi( (spot - open) / (spot * sigma_min * sqrt(tau)) ), with a floored
    sigma so a quiet tape can't saturate p to 0/1, then shrunk toward 0.5 —
    our window-open price is a Binance proxy for the resolution oracle, so
    raw confidence is always overstated.
    """
    if tau_min <= 0:
        return 1.0 if spot > window_open else 0.0
    sigma = max(sigma_min, SIGMA_FLOOR)
    denom = spot * sigma * math.sqrt(tau_min)
    if denom <= 0:
        return 0.5
    p = _phi((spot - window_open) / denom)
    return 0.5 + (p - 0.5) * P_SHRINK
