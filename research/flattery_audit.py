"""Backtest FLATTERY AUDIT — the institutional memory of 2026-06-25.

WHY THIS EXISTS. Every research/ backtest scores entries at `up_price()`, which falls
back to the sparse CLOB price_track MIDPOINT (~1 point/min, ~55s stale) when the real
executed-trade tape isn't cached. On fast frames that midpoint LAGS the real price, so
the backtest "buys" at a stale low the market has already left — a fantasy fill. This
flattered the entire 5m lead-floor family (+0.04..+0.09 on track) into looking like an
edge when the tape truth was ~0 (favorite already ~0.96 at entry). The same trap also
made a MAKER variant look like +0.05 in a tape backtest, until the LIVE PAPER runners
showed it anti-selected and breakeven (winrate 0.96→0.90). Lesson learned seven times in
one session: a backtest number is only trustworthy if it survives the REAL tape, and the
final arbiter is the live forward-test.

WHAT IT DOES. For windows that carry the real tape, recompute a strategy's edge at the
tape price (an actually-executed fill) vs the stale price_track price, and report the
FLATTERY GAP. Verdict:
  - FLATTERED  if track_edge - tape_edge exceeds `gap_max` (the backtest is lying), or
  - DEAD       if the tape edge itself isn't REAL (winrate <= price paid), or
  - SURVIVES   if the tape edge is REAL and close to the track edge.
Run this on a tape sample BEFORE trusting any new backtest, and never deploy real money
on a config that FLATTERS or is DEAD on tape — paper-forward-test it first.

Get tape: python3 research/dataset.py --count 200 --interval 5m --force   (and 15m, eth_…)
Run:      python3 research/flattery_audit.py [5m|15m] [btc|eth|sol|xrp]
"""
from __future__ import annotations
import math
import sys

from explore2 import load_all, up_price, taker_fee
from backtest_divergence import fetch_klines, spot_at, SYMBOL

GAP_MAX = 0.015   # track may exceed tape by at most this before we call it FLATTERED


def _track_price(r, t):
    """The stale midpoint read every backtest uses: last price_track point <= t."""
    out = None
    for ts, p in (r.get("price_track") or []):
        if ts <= t:
            out = p
        else:
            break
    return out


def edge_at(recs, idx, sec, frac, floor, z_thr, source):
    """Lead-floor taker edge using either the 'tape' (real fills) or 'track' (stale)
    price. Returns (EV/$, n, winrate, avg_px) or None."""
    remain = (1 - frac) * sec / 60.0
    rows = []
    for r in recs:
        ws = r["window_start"]; t = ws + int(frac * sec)
        p = up_price(r, t) if source == "tape" else _track_price(r, t)
        if p is None or not (0.02 < p < 0.98):
            continue
        op = r.get("binance_open"); spot = spot_at(idx, t); v = r.get("vol_per_min") or 0
        if op is None or spot is None or op <= 0 or v <= 0:
            continue
        up_lead = p >= 0.5
        fav = p if up_lead else 1 - p
        if not (floor <= fav <= 0.97):
            continue
        z = math.log(spot / op) / (v * math.sqrt(remain)) * (1 if up_lead else -1)
        if z < z_thr:
            continue
        px = min(fav + 0.02, 0.99)
        win = r["up_won"] if up_lead else (not r["up_won"])
        sh = 1.0 / px; fee = taker_fee(sh, px)
        rows.append(((sh - sh * px - fee) if win else (-sh * px - fee), px, win))
    if not rows:
        return None
    n = len(rows)
    return (sum(x[0] for x in rows) / n, n,
            sum(x[2] for x in rows) / n, sum(x[1] for x in rows) / n)


def audit(frame="5m", token="btc", frac=0.60, floor=0.85, z_thr=1.5):
    key = frame if token == "btc" else f"{token}_{frame}"
    recs = [r for r in load_all(key) if r.get("has_tape") and r.get("tape")]
    if not recs:
        print(f"{token} {frame}: NO tape windows — run dataset.py --force first"); return
    sec = {"5m": 300, "15m": 900}[frame]
    lo = min(r["window_start"] for r in recs); hi = max(r["window_end"] for r in recs)
    idx = fetch_klines(SYMBOL[token], lo - 180, hi + 120)
    print(f"=== flattery audit  {token} {frame}  ({len(recs)} tape windows, "
          f"frac {frac:.0%}, fav>={floor}, z>={z_thr}) ===")
    trk = edge_at(recs, idx, sec, frac, floor, z_thr, "track")
    tap = edge_at(recs, idx, sec, frac, floor, z_thr, "tape")
    if not trk or not tap:
        print("  not enough qualifying windows"); return
    gap = trk[0] - tap[0]
    real = tap[2] > tap[3]
    verdict = ("FLATTERED" if gap > GAP_MAX else
               "DEAD" if not real else "SURVIVES")
    print(f"  track : EV/$ {trk[0]:+.4f} n={trk[1]:<4d} win {trk[2]:.3f}/px {trk[3]:.3f}")
    print(f"  tape  : EV/$ {tap[0]:+.4f} n={tap[1]:<4d} win {tap[2]:.3f}/px {tap[3]:.3f}  <- the real number")
    print(f"  FLATTERY GAP (track-tape) = {gap:+.4f}  (max {GAP_MAX})  ->  [{verdict}]")
    if verdict == "DEAD":
        print("  !! NO executable edge on tape — do NOT deploy real money.")
    elif verdict == "FLATTERED":
        print(f"  !! backtest is INFLATED — ignore the track {trk[0]:+.4f}; the tape {tap[0]:+.4f} is the floor. "
              "Confirm on the LIVE forward-test (exec_book may beat the tape proxy) before sizing.")
    else:
        print("  backtest is trustworthy (track ~= tape).")


def main():
    frame = sys.argv[1] if len(sys.argv) > 1 else "5m"
    token = sys.argv[2] if len(sys.argv) > 2 else "btc"
    # audit both the deployed favorite band and the lower floor that looked too good
    for floor in (0.85, 0.70):
        audit(frame, token, floor=floor)
        print()


if __name__ == "__main__":
    main()
