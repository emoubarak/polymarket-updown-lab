"""Backtest the BINANCE->POLYMARKET lead-lag as a general DIVERGENCE edge.

The lead-floor (backtest_lead5m) is a special case: buy the *extreme favorite*
(CLOB 0.85-0.95) when the spot confirms its lead. But the underlying claim is more
general -- the CLOB price *lags* Binance. If true, the edge should exist at ALL
price levels, not just the tail: whenever a no-lookahead spot model disagrees with
the CLOB price by enough, trade toward the model. That opens the mid-price windows
(0.55-0.85) the favorite harvest SKIPS -- the only place extra *volume* can come from.

Model (no lookahead): resolution is close@end > open. At decision time t we know
spot@t (last CLOSED 1m bar), the window open, the remaining minutes, and the
pre-window EWMA vol. Under a zero-drift GBM the final move is ~Normal(0, sig^2*rem),
so P(Up) = Phi(z), z = ln(spot/open)/(sig_per_min*sqrt(rem_min)). This is exactly
the z the lead-floor already gates on -- here we use its *level* as a probability and
compare it to the price.

Trade rule: gap = model_P_up - clob_P_up.
  gap >= +thr  -> CLOB underprices Up   -> BUY Up   at clob_p (+2c haircut)
  gap <= -thr  -> CLOB underprices Down -> BUY Down at 1-clob_p (+2c)
Hold to settle, real taker fee, ONE trade per window. Split IS/OOS @ 06-15 and read
the tripwire (realized win-rate must beat avg price paid) on the OOS tranche.

The decisive diagnostic is the per-CLOB-price-bucket breakdown: if the edge is REAL
and positive in the 0.55-0.85 buckets (not just 0.85-0.95), it is genuinely NEW
volume. If it only lives in the tail, it's the favorite harvest wearing a new hat.

  *** VERDICT 2026-06-24: FALSIFIED — this is a STALE-MIDPOINT ARTIFACT, not an edge. ***
  The btc 5m run showed an absurd OOS EV/$ +0.40..+0.77, but ONLY at frac 60% (DEAD at
  50%/70%). Cause: the tape is not cached, so up_price() falls back to the sparse
  price_track MIDPOINT (~55-60s between irregular points). On fast 5m markets the CLOB
  reprices hard around +180s; at frac 60% (t=+180) up_price grabs the point ~55s STALE,
  just BEFORE the reprice (e.g. reads 0.305 when the live price is 0.845 five seconds
  later) -> a fantasy fill at a price nobody offers. At 70% it grabs the POST-reprice
  point -> no fake gap -> DEAD (correctly). The midpoint is not executable at mid-range;
  the favorite harvest only survives it because favorites (0.85-0.95) are sticky AND it
  uses real tape + a +2c haircut. To test a real mid-price lead-lag you'd need the
  executable ask (tape/book) at decision time — refetch with dataset.py (with tape).
  Kept as the autopsy of why mid-price divergence can't be tested on this dataset.

Run:  python3 research/backtest_divergence.py [5m|15m] [btc|eth|sol|xrp]
"""
from __future__ import annotations
import json
import math
import os
import sys
import urllib.request

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_favorite import IS_OOS_SPLIT, split_stats
# Mutualised: the lookahead-safe spot_at is canonical in backtest_lead; re-exported
# here (flattery_audit imports it from this module). Only fetch_klines is local —
# it is multi-symbol (per-symbol cache) and so genuinely differs from backtest_lead's.
from backtest_lead import spot_at  # noqa: F401  (re-exported for flattery_audit)

SYMBOL = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}


def kcache(symbol: str) -> str:
    return os.path.join(os.path.dirname(__file__), "data", f"_klines_div_{symbol}.json")


def fetch_klines(symbol: str, start_s: int, end_s: int) -> dict[int, float]:
    """1m close indexed by openTime (sec), cached per symbol so reruns are offline."""
    path = kcache(symbol)
    if os.path.exists(path):
        raw = json.load(open(path))
        idx = {int(k): v for k, v in raw.items()}
        if min(idx) <= start_s and max(idx) >= end_s - 60:
            return idx
    idx: dict[int, float] = {}
    cur, end_ms = start_s * 1000, end_s * 1000
    while cur < end_ms:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m"
               f"&startTime={cur}&endTime={end_ms}&limit=1000")
        ks = json.load(urllib.request.urlopen(url, timeout=20))
        if not ks:
            break
        for k in ks:
            idx[k[0] // 1000] = float(k[4])
        cur = ks[-1][0] + 60000
    json.dump(idx, open(path, "w"))
    return idx


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def simulate(recs, idx, sec, frac, thr, lo_fav=0.0, hi_fav=1.0):
    """Divergence trades. lo_fav/hi_fav optionally restrict the CLOB price of the
    side we BUY (for the bucket breakdown). Returns rows (pnl_per_$, px, win, r)."""
    rows = []
    remain_min = (1 - frac) * sec / 60.0
    for r in recs:
        if not (r.get("tape") or r.get("price_track")):
            continue
        t = r["window_start"] + int(frac * sec)
        p = up_price(r, t)                       # CLOB P(Up) at decision
        if p is None or not (0.02 < p < 0.98):
            continue
        op = r.get("binance_open")
        spot = spot_at(idx, t)
        v = r.get("vol_per_min") or 0.0
        if op is None or spot is None or op <= 0 or v <= 0:
            continue
        denom = v * math.sqrt(max(remain_min, 1e-9))
        z = math.log(spot / op) / denom if denom > 0 else 0.0
        model = norm_cdf(z)                      # model P(Up)
        gap = model - p
        if gap >= thr:                           # buy Up
            buy_p, win = p, r["up_won"]
        elif gap <= -thr:                        # buy Down
            buy_p, win = 1.0 - p, (not r["up_won"])
        else:
            continue
        if not (lo_fav <= buy_p <= hi_fav):
            continue
        px = min(buy_p + 0.02, 0.99)
        sh = 1.0 / px
        fee = taker_fee(sh, px)
        pnl = (sh - sh * px - fee) if win else (-sh * px - fee)
        rows.append((pnl, px, win, r))
    return rows


def report(name, rows):
    a = split_stats(rows)
    if a is None:
        print(f"  {name:26s}: no qualifying windows")
        return
    oos = split_stats([x for x in rows if day_of(x[3]) >= IS_OOS_SPLIT])
    ev, n, pos, wr, ap, _ = a
    if oos:
        verdict = "REAL" if oos[3] > oos[4] else "DEAD"
        o = (f"OOS EV/$ {oos[0]:+.4f} n={oos[1]:<4d} win {oos[3]:.3f}/px {oos[4]:.3f} "
             f"[{verdict}]")
    else:
        o = "no OOS"
    print(f"  {name:26s}: ALL EV/$ {ev:+.4f} n={n:<5d} days+ {pos:.0%} | {o}")


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "5m"
    token = sys.argv[2] if len(sys.argv) > 2 else "btc"
    sec = {"5m": 300, "15m": 900}[interval]
    key = interval if token == "btc" else f"{token}_{interval}"
    recs = load_all(key)
    if not recs:
        print(f"no data for {key}")
        return
    lo_w = min(r["window_start"] for r in recs)
    hi_w = max(r["window_end"] for r in recs)
    idx = fetch_klines(SYMBOL[token], lo_w - 180, hi_w + 120)
    days = sorted({day_of(r) for r in recs})
    print(f"=== DIVERGENCE  {token} {interval}  ({len(recs)} windows, "
          f"{days[0]}..{days[-1]}, {len(idx)} klines) ===")
    print(f"    IS/OOS @ {IS_OOS_SPLIT} | +2c haircut, hold to settle, taker fee, "
          f"symmetric (buy Up or Down)\n")

    for frac in (0.50, 0.60, 0.70):
        print(f"---------- frac_in {frac:.0%} ----------")
        for thr in (0.05, 0.08, 0.12, 0.18):
            report(f"gap>={thr}", simulate(recs, idx, sec, frac, thr))
        # bucket breakdown at the best-known entry, moderate threshold: where does
        # the edge actually live -- the favorite tail or the NEW mid-price zone?
        if frac == 0.60:
            print("   -- by CLOB price of the side bought (thr 0.08) --")
            for lo, hi in ((0.50, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 0.95), (0.95, 0.99)):
                report(f"  buy_p in [{lo},{hi}]",
                       simulate(recs, idx, sec, frac, 0.08, lo_fav=lo, hi_fav=hi))
        print()


if __name__ == "__main__":
    main()
