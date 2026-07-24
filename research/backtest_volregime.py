"""Vol-regime sweep: where (if anywhere) does the zlead favorite edge flip sign?

The z-floor already vol-normalizes the LEAD (z = conviction / expected-remaining-
move), and a raw vol CAP (favorite_vol) was rejected OOS. But the user's direct
question deserves a direct picture: bucket the entered favorites by the pre-window
EWMA vol (vol_per_min, the coin's OWN vol) and read the tripwire EDGE = realized
win-rate - average price paid, per bucket. Edge<0 => the book OVERprices the
favorite in that regime => the harvest is dead there.

Entry = the band control (frac, fav 0.85-0.95, +2c haircut, taker fee, hold to
settle) — same as backtest_exit/revexit so the only moving part is the vol bucket.
We DON'T pre-apply the z-floor here: we want to see raw vol's effect un-gated.

Buckets = vol quintiles WITHIN each cell (equal-n, so 'high vol' is self-relative
per coin). Reported per bucket: n, vol range, avg entry px, win-rate, EDGE
(win - px, the tripwire), EV/$ hold, and the OOS edge (the honest tranche).

Run: python3 research/backtest_volregime.py            # all cells
     python3 research/backtest_volregime.py 15m btc     # one cell
     python3 research/backtest_volregime.py --pool15m   # pooled across coins, 15m
"""
from __future__ import annotations
import sys

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_favorite import SEC, IS_OOS_SPLIT

MIN_FAV, MAX_FAV = 0.85, 0.95
HAIRCUT = 0.02
NB = 5                    # quintiles

CELLS = [("btc", "15m"), ("eth", "15m"), ("sol", "15m"), ("xrp", "15m"),
         ("doge", "15m"), ("bnb", "15m"),
         ("btc", "5m"), ("eth", "5m"), ("sol", "5m"), ("xrp", "5m")]


def entries(recs, sec, frac):
    """Band entries with vol attached: dict(px, win, hold, vol, day)."""
    out = []
    for r in recs:
        if not (r.get("tape") or r.get("price_track")):
            continue
        vol = r.get("vol_per_min")
        if vol is None:
            continue
        t = r["window_start"] + int(frac * sec)
        p = up_price(r, t)
        if p is None:
            continue
        up_lead = p >= 0.5
        fav = p if up_lead else 1.0 - p
        if not (MIN_FAV <= fav <= MAX_FAV):
            continue
        px = min(fav + HAIRCUT, 0.99)
        win = r["up_won"] if up_lead else (not r["up_won"])
        sh = 1.0 / px
        fee = taker_fee(sh, px)
        hold = (sh - sh * px - fee) if win else (-sh * px - fee)
        out.append(dict(px=px, win=win, hold=hold, vol=vol, day=day_of(r)))
    return out


def quantile_edges(vals, nb):
    s = sorted(vals)
    return [s[min(len(s) - 1, int(round(k * len(s) / nb)))] for k in range(1, nb)]


def stat(es):
    if not es:
        return None
    n = len(es)
    win = sum(1 for e in es if e["win"]) / n
    px = sum(e["px"] for e in es) / n
    ev = sum(e["hold"] for e in es) / n
    return dict(n=n, win=win, px=px, edge=win - px, ev=ev)


def report_buckets(title, es, key="vol"):
    if len(es) < NB * 10:
        print(f"\n### {title}: too few entries (n={len(es)})")
        return
    cuts = quantile_edges([e[key] for e in es], NB)
    def bucket(v):
        for i, c in enumerate(cuts):
            if v <= c:
                return i
        return NB - 1
    groups = [[] for _ in range(NB)]
    for e in es:
        groups[bucket(e[key])].append(e)
    a = stat(es)
    unit = "%/m" if key == "vol" else "   "
    scale = 100.0 if key == "vol" else 1.0
    hdr = "vol/min range" if key == "vol" else "entry-px range"
    print(f"\n### {title}  (n={a['n']}, overall edge {a['edge']:+.4f}, "
          f"EV/$ {a['ev']:+.4f})")
    print(f"  {'quintile':>13}  {hdr:>17}   n     win    px     "
          f"{'EDGE':>7}   {'EV/$':>8}   OOS-edge")
    for i, g in enumerate(groups):
        s = stat(g)
        lo = min(e[key] for e in g) * scale
        hi = max(e[key] for e in g) * scale
        oos = stat([e for e in g if e["day"] >= IS_OOS_SPLIT])
        oe = f"{oos['edge']:+.4f} (n={oos['n']})" if oos else "   -"
        flag = "  <-- edge<0" if s["edge"] < 0 else ""
        rng = (f"{lo:6.3f}-{hi:6.3f}{unit}" if key == "vol"
               else f"{lo:6.3f}-{hi:6.3f}   ")
        print(f"  {('Q'+str(i+1)+(' (lo)' if i==0 else ' (hi)' if i==NB-1 else '')):>13}"
              f"  {rng}  {s['n']:4d}  {s['win']:.3f}  "
              f"{s['px']:.3f}  {s['edge']:+.4f}   {s['ev']:+.4f}   {oe}{flag}")


def main():
    frac = 0.60
    key = "vol"
    args = list(sys.argv[1:])
    if "--frac" in args:
        k = args.index("--frac"); frac = float(args[k + 1]); del args[k:k + 2]
    if "--key" in args:
        k = args.index("--key"); key = args[k + 1]; del args[k:k + 2]
    print("=== VOL-REGIME SWEEP — does the favorite edge flip sign in high vol? ===")
    print(f"    entry = band control (frac {frac:.2f}, fav {MIN_FAV}-{MAX_FAV}, "
          f"+2c haircut, hold) | EDGE = win-rate - avg px (tripwire) | OOS @ {IS_OOS_SPLIT}")
    print("    vol = pre-window EWMA log-ret/min, the coin's OWN vol. Quintiles within each cell.")

    if "--pool15m" in args:
        pooled = []
        for coin, frame in [c for c in CELLS if c[1] == "15m"]:
            prefix = frame if coin == "btc" else f"{coin}_{frame}"
            pooled += entries(load_all(prefix), SEC[frame], frac)
        report_buckets("POOLED 15m (all coins)", pooled, key)
        return
    if len(args) >= 2:
        a, b = args[0], args[1]
        coin, frame = (a, b) if b in SEC else (b, a)
        prefix = frame if coin == "btc" else f"{coin}_{frame}"
        report_buckets(f"{coin}-{frame}", entries(load_all(prefix), SEC[frame], frac), key)
        return
    for coin, frame in CELLS:
        prefix = frame if coin == "btc" else f"{coin}_{frame}"
        report_buckets(f"{coin}-{frame}", entries(load_all(prefix), SEC[frame], frac), key)


if __name__ == "__main__":
    main()
