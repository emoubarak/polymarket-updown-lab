#!/usr/bin/env python3
"""Measure per-(coin, frame) book CAPACITY for a SINGLE actor, from Polymarket trade history.

The user-true per-bet ceiling: the max stake YOU can deploy to enter the favorite in [0.85, 0.95]
WHILE OTHER buyers compete for the same asks. Polymarket serves no historical order BOOK (/book is
live-only) but serves historical TRADES (data-api /trades?market=<cond>, no auth). A BUY at a price
in [0.85, 0.95] is necessarily on the favorite (the longshot trades ~0.05-0.15).

Two reads per window:
  • AGGREGATE = Σ price·size of ALL in-band favorite-BUYs = what EVERYONE bought together. This
    OVERSTATES a single actor's capacity — the book is split across many simultaneous buyers.
  • SINGLE-WALLET = group those BUYs by proxyWallet, take the LARGEST single wallet's total = what
    ONE real actor accumulated in-band WHILE competing. THIS is the realistic per-bet cap (it bakes
    in simultaneity). The committed coins.depth uses the MEDIAN of this across windows (conservative:
    the typical top buyer, not a p90 whale — and still a lower bound, it's realized volume not resting
    depth; an actor adding fresh demand could push price, so don't size above it).

Usage:  python3 tools/measure_capacity.py [n_windows_5m] [n_windows_15m]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pmlab import feeds
from pmlab.coins import COIN_KEYS, COIN_DEPTH

LO, HI = 0.85, 0.95
PAGE, MAX_PAGES = 500, 8
N5 = int(sys.argv[1]) if len(sys.argv) > 1 else 60
N15 = int(sys.argv[2]) if len(sys.argv) > 2 else 40
FRAMES = {"5m": (300, N5), "15m": (900, N15)}


def all_trades(cond):
    """Every trade for a market, paginating by offset until a short page."""
    out = []
    for pg in range(MAX_PAGES):
        try:
            chunk = feeds._get(f"{feeds.DATA_API}/trades", market=cond, limit=PAGE, offset=pg * PAGE)
        except Exception:
            break
        if not isinstance(chunk, list) or not chunk:
            break
        out.extend(chunk)
        if len(chunk) < PAGE:
            break
    return out


def window_stats(coin, frame, ws):
    """(aggregate, max-single-wallet, n_distinct_buyers) of in-band favorite-BUYs, or None."""
    slug = f"{coin}-updown-{frame}-{ws}"
    try:
        ev = feeds._get(f"{feeds.GAMMA}/events", slug=slug)
        cond = ev[0]["markets"][0]["conditionId"] if ev else None
    except Exception:
        cond = None
    if not cond:
        return None
    by_wallet = {}
    for t in all_trades(cond):
        try:
            if t.get("side") == "BUY":
                p, s = float(t["price"]), float(t["size"])
                if LO <= p <= HI:
                    w = t.get("proxyWallet", "?")
                    by_wallet[w] = by_wallet.get(w, 0.0) + p * s
        except (TypeError, ValueError, KeyError):
            pass
    if not by_wallet:
        return (0.0, 0.0, 0)
    return (sum(by_wallet.values()), max(by_wallet.values()), len(by_wallet))


def qtile(xs, f):
    s = sorted(xs)
    return s[min(len(s) - 1, int(f * len(s)))] if s else 0.0


print(f"in-band (0.85-0.95) favorite-BUY — n5m={N5} n15m={N15}")
print(f"{'coin':5} {'frame':5} {'win':>4} {'agg(med)':>9} {'1wallet(med)':>13} {'1wallet(p75)':>13} "
      f"{'buyers':>7} {'share':>6}  committed")
print("-" * 92)
suggestion = {}
for c in COIN_KEYS:
    for frame, (sec, nwin) in FRAMES.items():
        now = time.time()
        latest = int((now - sec - 90) // sec) * sec
        aggs, maxs, nbs = [], [], []
        for i in range(nwin):
            r = window_stats(c, frame, latest - i * sec)
            if r is not None:
                aggs.append(r[0]); maxs.append(r[1]); nbs.append(r[2])
        if not aggs:
            print(f"{c:5} {frame:5}  (no data)")
            continue
        magg, mmax = qtile(aggs, .5), qtile(maxs, .5)
        suggestion[(c, frame)] = round(mmax)
        share = (mmax / magg) if magg > 0 else 0
        cur = (COIN_DEPTH.get(c) or {}).get(frame, "?")
        print(f"{c:5} {frame:5} {len(aggs):4d} {magg:8.0f}$ {mmax:12.0f}$ {qtile(maxs,.75):12.0f}$ "
              f"{qtile(nbs,.5):7.0f} {share*100:5.0f}%  {cur}")
    print()

print("\nSuggested per-(coin,frame) depth dict (SINGLE-actor median, for coins.py):")
for c in COIN_KEYS:
    row = {f: suggestion.get((c, f)) for f in FRAMES if (c, f) in suggestion}
    print(f'    "{c}": {row},')
