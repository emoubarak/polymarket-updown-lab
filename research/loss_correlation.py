"""What predicts a LOSS? Deep-cache loss-rate by feature, to find GUARDRAILS.

Discipline (the trap to avoid — see memory untested-signals): the real-money loss
sample is tiny (~55), so DISCOVERY runs on the deep cache (thousands of windows,
research/data) where loss-rate buckets have power; the real losses only CONFIRM.

A guardrail must cut windows that are BOTH high-loss AND low/neg-EDGE — cutting a
high-loss bucket that is still +EV (a cheap favorite whose price already pays for the
risk) would just shrink the book. So every bucket prints loss-rate AND edge (win−px)
AND EV/$, so we never trade away profit to dodge a loss.

Tier 1 (this file): the two features available in the cache WITHOUT extra klines —
entry PRICE and pre-window VOL — per coin/frame + pooled. z/bps/btc-align need klines
(Tier 2, loss_correlation_z.py).

Run: python3 research/loss_correlation.py [5m|15m|all]
"""
from __future__ import annotations
import sys
from collections import defaultdict

from explore2 import load_all, taker_fee, up_price, day_of  # noqa
from backtest_favorite import SEC

FRAC = 0.60
LO, HI, HC = 0.85, 0.95, 0.02
COINS = ["btc", "eth", "sol", "xrp", "doge", "bnb"]


def entered(prefix, sec):
    """Band entries (the zlead superset minus the z-floor, which needs klines): one
    dict per window with px, vol, won, day."""
    out = []
    for r in load_all(prefix):
        if not (r.get("tape") or r.get("price_track")):
            continue
        vol = r.get("vol_per_min")
        t = r["window_start"] + int(FRAC * sec)
        p = up_price(r, t)
        if p is None:
            continue
        up = p >= 0.5
        fav = p if up else 1.0 - p
        if not (LO <= fav <= HI):
            continue
        px = min(fav + HC, 0.99)
        won = (r["up_won"] if up else (not r["up_won"]))
        out.append(dict(px=px, vol=vol, won=won, day=day_of(r)))
    return out


def buckets_report(rows, feat, edges, labels, unit=""):
    """Print loss-rate + edge + EV/$ per feature bucket."""
    grp = defaultdict(list)
    for r in rows:
        v = r.get(feat)
        if v is None:
            continue
        bi = len(edges)
        for i, e in enumerate(edges):
            if v <= e:
                bi = i
                break
        grp[bi].append(r)
    print(f"  by {feat}:")
    print(f"    {'bucket':>14}   n     loss%   win    avg-px   EDGE     EV/$")
    for bi in range(len(edges) + 1):
        g = grp.get(bi, [])
        if not g:
            continue
        n = len(g)
        loss = sum(1 for r in g if not r["won"]) / n
        win = 1 - loss
        px = sum(r["px"] for r in g) / n
        ev = sum(((1/r["px"] - 1) if r["won"] else -1.0) - taker_fee(1/r["px"], r["px"]) for r in g) / n
        flag = "  <<" if (loss > 0.12 and (win - px) < 0) else ""
        print(f"    {labels[bi]:>14}  {n:5d}  {loss:5.1%}  {win:.3f}  {px:.4f}  {win-px:+.4f}  {ev:+.4f}{flag}")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    frames = ["5m", "15m"] if which == "all" else [which]
    pooled = {f: [] for f in ("5m", "15m")}
    for frame in frames:
        for coin in COINS:
            prefix = frame if coin == "btc" else f"{coin}_{frame}"
            rows = entered(prefix, SEC[frame])
            if rows:
                pooled[frame] += rows
    PX_EDGES = [0.88, 0.90, 0.92, 0.94]
    PX_LAB = ["0.85-0.88", "0.88-0.90", "0.90-0.92", "0.92-0.94", "0.94-0.95"]
    # vol buckets (log-ret/min); coin-relative quintiles would be better but absolute is comparable across the pool
    VOL_EDGES = [0.0004, 0.0006, 0.0009, 0.0014]
    VOL_LAB = ["<.04%", ".04-.06%", ".06-.09%", ".09-.14%", ">.14%"]
    for frame in frames:
        rows = pooled[frame]
        if not rows:
            continue
        n = len(rows); loss = sum(1 for r in rows if not r["won"]) / n
        print(f"\n### {frame} pooled (all coins)  n={n}  base loss-rate {loss:.1%}")
        buckets_report(rows, "px", PX_EDGES, PX_LAB)
        buckets_report(rows, "vol", VOL_EDGES, VOL_LAB)
    print("\n  << = high loss (>12%) AND negative edge (a guardrail candidate: cut it, it's not paying for its risk)")


if __name__ == "__main__":
    main()
