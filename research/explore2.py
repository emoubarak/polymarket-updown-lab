"""Second wave of strategy search — angles NOT covered by analyze.py/FINDINGS.md.

The first study closed: directional (lookahead artifact), favorite/momentum
(regime noise), tao passive (adverse selection), delta-neutral MM (adverse
selection on the net leg). All netted ≈0 or worse after costs on 5m/15m.

This file tries five genuinely different, latency-free ideas, each held to the
same bar that killed the others:
  - costs in (taker fee  C·0.07·p·(1−p)  on any leg we cross);
  - no lookahead (only info available at decision time);
  - day-by-day sign stability (an edge that flips sign across the 5 days is
    regime noise, not an edge — this is the filter that exposed favorite/tao).

  1  outcome serial-correlation (Markov on the up_won sequence) + streaks
  2  time-of-day skew in P(Up)
  3  early-extreme fade: when the market price is extreme with lots of time
     left, does it mean-revert by settlement?
  4  volatility-conditioned favorite (does the lead hold in calm windows?)
  5  tape order-flow imbalance as a taker-follow signal

Run:  python3 research/explore2.py [5m|15m]
"""
from __future__ import annotations
import datetime as dt
import glob
import json
import math
import os
import sys

CACHE = os.path.join(os.path.dirname(__file__), "data")
# Crypto taker fee coefficient. The 0.07 fee IS charged on-chain — re-verified 2026-06-27
# from raw tx receipts (wallet 0xd630…, NEW pUSD relayer 0xe111…): the wallet debits exactly
# `usdcSize` = size×price + fee at ENTRY, and the fee = 0.07×p×(1−p)×shares is forwarded in
# pUSD to collector 0x115f48dc on BOTH 5m AND 15m (never at settlement). The earlier
# "phantom/fee=0" read (2026-06-26) tracked USDC.e transfers and MISSED the pUSD fee leg —
# the fee was there all along. So `usdcSize` is NOT a display phantom; it's the real debit.
FEE_RATE = 0.07


# ----------------------------------------------------------------- io ---
def load_all(interval: str) -> list[dict]:
    """Every cached window for the interval, outcome-only ones included,
    sorted by window_start."""
    recs = []
    for f in glob.glob(os.path.join(CACHE, f"{interval}_*.json")):
        try:
            recs.append(json.load(open(f)))
        except (json.JSONDecodeError, OSError):
            continue
    recs = [r for r in recs if r.get("up_won") is not None
            and r.get("window_start")]
    recs.sort(key=lambda r: r["window_start"])
    return recs


def taker_fee(shares: float, price: float) -> float:
    return shares * FEE_RATE * price * (1.0 - price)


def up_price(r: dict, t: int) -> float | None:
    """Up price from the most recent executed trade at/before t. The tape is
    sorted NEWEST-FIRST (descending t), so the first trade with t<=decision is
    the last print before the decision — the real, executable price. A Down
    trade at price p maps to Up = 1-p. Falls back to the sparse price_track
    midpoint only if no trade exists yet."""
    for tr in r.get("tape", []):          # descending by t
        if tr["t"] <= t:
            p = tr["price"]
            return p if tr["outcome"] == "Up" else 1.0 - p
    track = r.get("price_track") or []
    out = None
    for ts, p in track:
        if ts <= t:
            out = p
        else:
            break
    return out


def day_of(r: dict) -> str:
    return dt.datetime.utcfromtimestamp(r["window_start"]).strftime("%m-%d")


def per_day(rows, val_fn):
    """rows -> {day: mean(val_fn)} plus the fraction of days with positive mean.
    This is the regime-noise filter."""
    byday: dict[str, list[float]] = {}
    for r in rows:
        v = val_fn(r)
        if v is not None:
            byday.setdefault(day_of(r), []).append(v)
    means = {d: sum(v) / len(v) for d, v in byday.items() if v}
    if not means:
        return means, 0.0
    pos = sum(1 for m in means.values() if m > 0) / len(means)
    return means, pos


def wilson(k: int, n: int) -> tuple[float, float]:
    """95% Wilson interval for a proportion — honest small-sample bounds."""
    if n == 0:
        return (0.0, 1.0)
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


# ----------------------------------------------------- 1 serial / Markov ---
def exp_serial(recs, sec):
    print("\n[1] Serial correlation of outcomes (Markov on up_won)")
    base = sum(r["up_won"] for r in recs) / len(recs)
    print(f"    base P(Up) = {base:.3f}  (n={len(recs)})")
    # transitions on adjacent windows only
    trans = {"U": [0, 0], "D": [0, 0]}  # prev -> [n, n_up]
    runlen = {}  # streak length of prev-same -> [n, n_continue]
    seq = []
    prev = None
    streak = 0
    for r in recs:
        if prev is not None and r["window_start"] - prev["window_start"] == sec:
            ps = "U" if prev["up_won"] else "D"
            trans[ps][0] += 1
            trans[ps][1] += r["up_won"]
            # streak continuation
            cont = (r["up_won"] == prev["up_won"])
            runlen.setdefault(streak, [0, 0])
            runlen[streak][0] += 1
            runlen[streak][1] += cont
            streak = streak + 1 if cont else 1
        else:
            streak = 1
        prev = r
    for s in ("U", "D"):
        n, k = trans[s]
        lo, hi = wilson(k, n)
        print(f"    P(Up | prev {s}) = {k/n:.3f}  n={n}  95%CI[{lo:.3f},{hi:.3f}]")
    print("    streak continuation P(same as run of length L):")
    for L in sorted(runlen):
        n, k = runlen[L]
        if n < 20:
            continue
        lo, hi = wilson(k, n)
        print(f"      L={L}: P(continue)={k/n:.3f} n={n} CI[{lo:.3f},{hi:.3f}]")


# --------------------------------------------------------- 2 time of day ---
def exp_tod(recs):
    print("\n[2] Time-of-day skew in P(Up)  (UTC hour)")
    byh = {}
    for r in recs:
        h = dt.datetime.utcfromtimestamp(r["window_start"]).hour
        byh.setdefault(h, [0, 0])
        byh[h][0] += 1
        byh[h][1] += r["up_won"]
    flagged = 0
    for h in range(24):
        if h not in byh:
            continue
        n, k = byh[h]
        lo, hi = wilson(k, n)
        flag = "  <-- CI excludes 0.5" if (lo > 0.5 or hi < 0.5) else ""
        if flag:
            flagged += 1
        print(f"    {h:02d}h: P(Up)={k/n:.3f} n={n} CI[{lo:.3f},{hi:.3f}]{flag}")
    print(f"    hours whose 95%CI excludes 0.5: {flagged}/24 "
          f"(expect ~1.2 by chance at 5%)")


# ------------------------------------------------ 3 early-extreme fade ---
def exp_fade(recs, sec, frac_in=0.40):
    """At frac_in into the window, if the Up price is extreme, does it revert?
    FADE = buy the cheap side; FOLLOW = buy the rich side. Hold to settle,
    pay one taker fee at entry. Report net EV/$ for several thresholds."""
    print(f"\n[3] Early-extreme fade vs follow  (decision at {frac_in:.0%} into "
          f"window, {(1-frac_in)*sec/60:.1f} min left)")
    rt = [r for r in recs if r.get("tape") or r.get("price_track")]
    print(f"    windows with a price path: {len(rt)}")
    for thr in (0.62, 0.70, 0.78, 0.85):
        fade_rows = []  # (pnl_per_$, day, r) for fade
        foll_rows = []
        for r in rt:
            t = r["window_start"] + int(frac_in * sec)
            p = up_price(r, t)
            if p is None or not (0.02 < p < 0.98):
                continue
            rich = max(p, 1 - p)
            if rich < thr:
                continue
            up_is_rich = p >= 0.5
            # FADE: buy the cheap side at price (1-rich)
            cheap = 1 - rich
            cheap_wins = (not r["up_won"]) if up_is_rich else r["up_won"]
            sh = 1.0 / cheap
            fee = taker_fee(sh, cheap)
            pnl = (sh - sh * cheap - fee) if cheap_wins else (-sh * cheap - fee)
            fade_rows.append((pnl, r))
            # FOLLOW: buy the rich side at price rich
            rich_wins = r["up_won"] if up_is_rich else (not r["up_won"])
            shr = 1.0 / rich
            feer = taker_fee(shr, rich)
            pnlf = (shr - shr * rich - feer) if rich_wins else (-shr * rich - feer)
            foll_rows.append((pnlf, r))
        if not fade_rows:
            print(f"    thr {thr:.2f}: no windows")
            continue
        for label, rows in (("FADE  ", fade_rows), ("FOLLOW", foll_rows)):
            ev = sum(x[0] for x in rows) / len(rows)
            means, pos = per_day([x[1] for x in rows],
                                 lambda r, _m={id(x[1]): x[0] for x in rows}: _m.get(id(r)))
            print(f"    thr {thr:.2f} {label}: EV/$ {ev:+.4f}  n={len(rows)}  "
                  f"days+ {pos:.0%}")


# ----------------------------------------- 4 vol-conditioned favorite ---
def exp_vol_fav(recs, sec, frac_in=0.60):
    """Bet the side leading at frac_in, hold to settle, taker fee. Split by
    realized window volatility tercile — does the lead hold when it's calm?"""
    print(f"\n[4] Volatility-conditioned favorite (enter at {frac_in:.0%}, "
          f"taker-follow the leader)")
    rt = [r for r in recs if (r.get("tape") or r.get("price_track"))
          and r.get("vol_per_min")]
    rt.sort(key=lambda r: r["vol_per_min"])
    n = len(rt)
    if n < 30:
        print("    too few windows with vol")
        return
    terc = [rt[:n // 3], rt[n // 3:2 * n // 3], rt[2 * n // 3:]]
    names = ["LOW vol", "MID vol", "HIGH vol"]
    for name, grp in zip(names, terc):
        rows = []
        for r in grp:
            t = r["window_start"] + int(frac_in * sec)
            p = up_price(r, t)
            if p is None or abs(p - 0.5) < 0.02:
                continue
            up_lead = p > 0.5
            price = p if up_lead else 1 - p
            if not (0.5 < price < 0.97):
                continue
            wins = r["up_won"] if up_lead else (not r["up_won"])
            sh = 1.0 / price
            fee = taker_fee(sh, price)
            pnl = (sh - sh * price - fee) if wins else (-sh * price - fee)
            rows.append((pnl, r))
        if not rows:
            print(f"    {name}: no windows")
            continue
        ev = sum(x[0] for x in rows) / len(rows)
        vlo = grp[0]["vol_per_min"]
        vhi = grp[-1]["vol_per_min"]
        _, pos = per_day([x[1] for x in rows],
                         lambda r, _m={id(x[1]): x[0] for x in rows}: _m.get(id(r)))
        print(f"    {name} [{vlo:.5f},{vhi:.5f}]: EV/$ {ev:+.4f} n={len(rows)} "
              f"days+ {pos:.0%}")


# ------------------------------------------- 5 tape order-flow imbalance ---
def exp_flow(recs, sec, frac_in=0.50):
    """Aggressive flow imbalance up to frac_in: bullish = BUY Up + SELL Down,
    bearish = BUY Down + SELL Up. Does the sign predict outcome, and does a
    taker-follow at frac_in survive fees?"""
    print(f"\n[5] Tape order-flow imbalance, decision at {frac_in:.0%}")
    rt = [r for r in recs if r.get("tape")]
    print(f"    windows with tape: {len(rt)}")
    rows = []
    hits = 0
    n_signal = 0
    for r in rt:
        t = r["window_start"] + int(frac_in * sec)
        bull = bear = 0.0
        for tr in r["tape"]:
            if tr["t"] > t:
                break
            sz = tr["size"]
            o, s = tr["outcome"], tr["side"]
            if (o == "Up" and s == "BUY") or (o == "Down" and s == "SELL"):
                bull += sz
            elif (o == "Down" and s == "BUY") or (o == "Up" and s == "SELL"):
                bear += sz
        tot = bull + bear
        if tot < 1:
            continue
        imb = (bull - bear) / tot
        if abs(imb) < 0.20:          # only act on a clear lean
            continue
        n_signal += 1
        follow_up = imb > 0
        if follow_up == bool(r["up_won"]):
            hits += 1
        p = up_price(r, t)
        if p is None:
            continue
        price = p if follow_up else 1 - p
        if not (0.03 < price < 0.97):
            continue
        wins = r["up_won"] if follow_up else (not r["up_won"])
        sh = 1.0 / price
        fee = taker_fee(sh, price)
        pnl = (sh - sh * price - fee) if wins else (-sh * price - fee)
        rows.append((pnl, r))
    if n_signal:
        print(f"    directional hit-rate of flow sign: {hits/n_signal:.3f} "
              f"(n_signal={n_signal})")
    if rows:
        ev = sum(x[0] for x in rows) / len(rows)
        _, pos = per_day([x[1] for x in rows],
                         lambda r, _m={id(x[1]): x[0] for x in rows}: _m.get(id(r)))
        print(f"    taker-follow EV/$ {ev:+.4f}  n={len(rows)}  days+ {pos:.0%}")


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "5m"
    sec = 300 if interval == "5m" else 900 if interval == "15m" else 3600
    recs = load_all(interval)
    print(f"=== explore2  {interval}  ({len(recs)} windows) ===")
    exp_serial(recs, sec)
    exp_tod(recs)
    exp_fade(recs, sec)
    exp_vol_fav(recs, sec)
    exp_flow(recs, sec)


if __name__ == "__main__":
    main()
