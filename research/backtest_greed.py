"""Is favorite_conviction's GREED tilt backtestable after all? — a flow proxy for book depth.

The engine's _greed_tilt reads RESTING bid depth on the longshot (dying) side,
which has no retroactive history (the CLOB /book endpoint is live-only). But the
cached `tape` holds executed TRADES (t, size, outcome, side) — so we can measure
the FLOW cousin: how much the crowd is aggressively BUYING the longshot ticket.

Caveat up front: flow as a DIRECTION predictor is already DEAD (explore2.py #5,
backtest_flow.py). This asks a different question — not "who wins" but "is the
favorite's EDGE (win-rate minus price paid) fatter when longshot greed is high?"
That is exactly what the tilt assumes, and it is what flat-direction tests miss.

Method (discipline inherited from the falsified coagula strategy, see FINDINGS): extreme favorite 0.85-0.95 at mid-window (frac 0.60),
longshot greed = BUY volume on the dying side / total BUY volume up to entry. Bin
by greed tercile, report each bin's win-rate vs price paid and EV/$, ALL and OOS.
If edge RISES with greed, the tilt earns real support; if flat, it's decoration.

VERDICT (2026-06-21): edge FALLS with greed — the OPPOSITE of the tilt's premise.
  5m (n=281, the clean sample): edge +0.041 -> +0.020 -> -0.011 across low/mid/high
     greed terciles; the high-greed third is EV-NEGATIVE.
  15m (n=57, small): same sign, +0.082 -> +0.071 -> +0.066.
Aggressive buying of the dying side is informed FADE money, not dumb lottery
demand. The greed tilt was CUT from favorite_conviction. (The inverse — a flow-based fade GATE —
is a live lead, but needs a trades feed in feeds.py; the /book endpoint that favorite_conviction
would have read has no retroactive depth history to test directly.)

Run: python3 research/backtest_greed.py [5m|15m]
"""
from __future__ import annotations
import sys

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_favorite import IS_OOS_SPLIT, SEC


def greed_rows(recs, sec, frac=0.60, lo=0.85, hi=0.95, hc=0.02):
    """One row per qualifying extreme-favorite window: (greed, pnl_per_$, win,
    px, r). greed in [0,1] = share of aggressive BUY flow hitting the LONGSHOT
    (dying) side up to the entry instant; 0.5 balanced, >0.5 = crowd greed."""
    rows = []
    for r in recs:
        if not r.get("tape"):
            continue
        t = r["window_start"] + int(frac * sec)
        p = up_price(r, t)
        if p is None:
            continue
        up_lead = p >= 0.5
        fav = p if up_lead else 1.0 - p
        if not (lo <= fav <= hi):
            continue
        fav_out = "Up" if up_lead else "Down"          # favorite outcome
        long_out = "Down" if up_lead else "Up"         # longshot (dying) outcome
        fav_buy = long_buy = 0.0
        for tr in r["tape"]:
            if tr["t"] > t:
                continue                               # tape is newest-first
            if tr["side"] != "BUY":
                continue
            if tr["outcome"] == fav_out:
                fav_buy += tr["size"]
            elif tr["outcome"] == long_out:
                long_buy += tr["size"]
        tot = fav_buy + long_buy
        if tot < 1:
            continue                                   # too thin to read
        greed = long_buy / tot
        px = min(fav + hc, 0.99)
        win = r["up_won"] if up_lead else (not r["up_won"])
        sh = 1.0 / px
        fee = taker_fee(sh, px)
        pnl = (sh - sh * px - fee) if win else (-sh * px - fee)
        rows.append((greed, pnl, win, px, r))
    return rows


def bin_report(name, rows):
    if len(rows) < 9:
        print(f"  {name}: only n={len(rows)} — too few to bin")
        return
    s = sorted(rows, key=lambda x: x[0])
    k = len(s) // 3
    for label, sub in (("low greed ", s[:k]),
                       ("mid greed ", s[k:2 * k]),
                       ("high greed", s[2 * k:])):
        n = len(sub)
        ev = sum(x[1] for x in sub) / n
        wr = sum(1 for x in sub if x[2]) / n
        apx = sum(x[3] for x in sub) / n
        g = sum(x[0] for x in sub) / n
        flag = "REAL" if wr > apx else "DEAD"
        print(f"    {label} (greed≈{g:.2f}): EV/$ {ev:+.4f}  n={n:<4d} "
              f"win {wr:.3f} vs px {apx:.3f}  edge {wr - apx:+.3f} [{flag}]")


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "15m"
    sec = SEC[interval]
    recs = load_all(interval)
    rows = greed_rows(recs, sec)
    print(f"=== greed tilt backtest (FLOW proxy)  {interval}  "
          f"({len(rows)} extreme-favorite windows with tape) ===")
    print("    longshot greed = crowd BUY flow on the dying side / total BUY flow\n")
    print("  ALL:")
    bin_report("all", rows)
    oos = [x for x in rows if day_of(x[4]) >= IS_OOS_SPLIT]
    print("\n  OOS only:")
    bin_report("oos", oos)


if __name__ == "__main__":
    main()
