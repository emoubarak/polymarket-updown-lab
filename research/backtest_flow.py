"""Backtest the ORDER-FLOW / "ego footprint" signal (user's idea, 2026-06-21).

The mid price is efficient (every directional test on it died). But the raw
TRADE TAPE carries the emotion the mid may have already absorbed — or NOT:
aggressive buying of a side = greed/pride on it, aggressive selling = fear.

Signed Up-flow of a trade (normalise everything to pressure ON Up):
  BUY Up  or SELL Down  → +size   (bullish Up)
  SELL Up or BUY Down   → −size   (bearish Up)
imbalance = net / volume ∈ [−1, 1] over a lookback before entry.

Decisive question: does the flow predict the outcome BEYOND the mid?
  residual = up_won − up_mid   (did Up over/under-perform its price?)
  residual FALLS as imbalance RISES → the aggressively-bought side OVER-shoots →
       fade-able (the user's "price the unfounded pride/fear" — it WORKS)
  residual RISES with imbalance → flow is INFORMED → follow it
  residual ⊥ imbalance → the emotion is already in the mid → no edge

Only ~5%/19% of windows carry a full tape (the live-collected ones), so this is
exploratory — a smaller, possibly time-clustered sample. Read it as a probe.

Run: python3 research/backtest_flow.py [5m|15m] [lookback_s]
"""
from __future__ import annotations
import sys

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_favorite import IS_OOS_SPLIT, SEC


def signed_up(tr):
    s = tr["size"]
    bull = (tr["side"] == "BUY" and tr["outcome"] == "Up") or \
           (tr["side"] == "SELL" and tr["outcome"] == "Down")
    return s if bull else -s


def collect(recs, interval, frac=0.6, lookback=120):
    sec = SEC[interval]
    rows = []                                   # (mid, imbalance, up_won, r)
    for r in recs:
        tape = r.get("tape") or []
        if len(tape) < 30:
            continue
        ws = r["window_start"]
        t = ws + int(frac * sec)
        mid = up_price(r, t)
        if mid is None:
            continue
        net = vol = 0.0
        for tr in tape:                         # tape is descending by t
            if tr["t"] > t:
                continue
            if tr["t"] < t - lookback:
                break
            net += signed_up(tr)
            vol += tr["size"]
        if vol < 1.0:
            continue
        rows.append((mid, net / vol, r["up_won"], r))
    return rows


def corr(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else 0.0


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "5m"
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    recs = load_all(interval)
    rows = collect(recs, interval, lookback=lookback)
    print(f"=== backtest ORDER-FLOW / ego footprint {interval} "
          f"(lookback {lookback}s, entry 60%, n={len(rows)}) ===")
    print(f"    residual = up_won − up_mid ; vs flow imbalance ∈ [−1,1]\n")

    imbs = [im for _, im, _, _ in rows]
    res = [w - m for m, _, w, _ in rows]
    c = corr(imbs, res)
    sign = "FADE (overshoot)" if c < -0.05 else "FOLLOW (informed)" if c > 0.05 else "PRICED (⊥)"
    print(f"  corr(imbalance, residual) = {c:+.3f}   →  {sign}\n")

    print(f"      {'flow imbalance':>16}{'n':>6}{'mid':>7}{'win':>7}{'residual':>10}")
    edges = [-1.01, -0.5, -0.2, 0.2, 0.5, 1.01]
    for lo, hi in zip(edges, edges[1:]):
        sub = [(m, w) for m, im, w, _ in rows if lo <= im < hi]
        if len(sub) < 8:
            print(f"      [{lo:+.1f},{hi:+.1f})  n={len(sub):<4d} (trop peu)")
            continue
        mm = sum(m for m, _ in sub) / len(sub)
        wr = sum(w for _, w in sub) / len(sub)
        print(f"      [{lo:+.1f},{hi:+.1f})  {len(sub):>5d}  {mm:>6.3f} {wr:>6.3f} {wr-mm:>+9.3f}")

    # crude tradeable read: fade the EXTREME flow (buy the side the herd is
    # dumping). Buy Up when imbalance very negative (panic-sold), Down when very
    # positive (euphoria-bought). +2c taker haircut, hold to settle.
    print(f"\n  TRADEABLE fade of extreme flow (|imb|>=0.5, +2c, real fee):")
    for thr in (0.5, 0.7):
        ev = []
        oev = []
        for m, im, w, r in rows:
            if abs(im) < thr:
                continue
            buy_up = im < 0                      # fade: buy what's being dumped
            px = min((m if buy_up else 1 - m) + 0.02, 0.99)
            win = w if buy_up else (not w)
            sh = 1.0 / px
            pnl = (sh - sh * px - taker_fee(sh, px)) if win else (-sh * px - taker_fee(sh, px))
            ev.append(pnl)
            if day_of(r) >= IS_OOS_SPLIT:
                oev.append(pnl)
        if ev:
            print(f"    |imb|>={thr}: n={len(ev):<4d} EV/$ {sum(ev)/len(ev):+.4f}"
                  f"  | OOS n={len(oev):<3d} EV/$ {sum(oev)/len(oev):+.4f}" if oev else
                  f"    |imb|>={thr}: n={len(ev):<4d} EV/$ {sum(ev)/len(ev):+.4f} | no OOS")


if __name__ == "__main__":
    main()
