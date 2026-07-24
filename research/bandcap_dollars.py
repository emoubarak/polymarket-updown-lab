"""Net-$ of capping the favorite band upper bound: does avoiding deep favorites
(0.95+) actually lift cumulative $, or just trade volume for edge? Pooled 15m +
per-coin, IS/OOS. Uses the band control entry (frac 0.60), hold to settle, real fee.
This complements backtest_volregime --key px (which showed deep favs are a trap)
by reporting CUMULATIVE dollars at a fixed stake, the real objective."""
from __future__ import annotations
from explore2 import load_all, taker_fee, up_price, day_of
from backtest_favorite import SEC, IS_OOS_SPLIT

LO = 0.85
HC = 0.02
STAKE = 25.0
CAPS = [0.90, 0.92, 0.93, 0.95]
CELLS = [("btc", "15m"), ("eth", "15m"), ("sol", "15m"), ("xrp", "15m"),
         ("doge", "15m"), ("bnb", "15m")]


def entries(recs, sec, frac=0.60):
    out = []
    for r in recs:
        if not (r.get("tape") or r.get("price_track")):
            continue
        t = r["window_start"] + int(frac * sec)
        p = up_price(r, t)
        if p is None:
            continue
        up = p >= 0.5
        fav = p if up else 1.0 - p
        if fav < LO:
            continue
        px = min(fav + HC, 0.99)
        win = r["up_won"] if up else (not r["up_won"])
        sh = 1.0 / px
        pnl = (sh - sh * px - taker_fee(sh, px)) if win else (-sh * px - taker_fee(sh, px))
        out.append(dict(fav=fav, px=px, win=win, pnl=pnl, day=day_of(r)))
    return out


def dollars(es):
    return sum(e["pnl"] for e in es) * STAKE


def main():
    print("=== BAND-CAP net-$ (stake $25, hold to settle, real fee) | IS/OOS @", IS_OOS_SPLIT, "===")
    print("    cap = max favorite mid accepted (entry still pays +2c). Per-cell + pooled 15m.\n")
    pooled = {c: [] for c in CAPS}
    pooled_all = []
    for coin, frame in CELLS:
        prefix = frame if coin == "btc" else f"{coin}_{frame}"
        es = entries(load_all(prefix), SEC[frame])
        if not es:
            continue
        pooled_all += es
        print(f"  {coin}-{frame:>3} (n0={len(es)}):")
        for cap in CAPS:
            sub = [e for e in es if e["fav"] <= cap]
            oos = [e for e in sub if e["day"] >= IS_OOS_SPLIT]
            pooled[cap] += sub
            wr = sum(e["win"] for e in sub) / len(sub) if sub else 0
            print(f"      cap {cap:.2f}: n={len(sub):4d}  win {wr:.3f}  "
                  f"$ALL {dollars(sub):+7.1f}  $OOS {dollars(oos):+7.1f} (n={len(oos)})")
    print(f"\n  POOLED 15m (all 6 coins, n0={len(pooled_all)}):")
    for cap in CAPS:
        sub = pooled[cap]
        oos = [e for e in sub if e["day"] >= IS_OOS_SPLIT]
        wr = sum(e["win"] for e in sub) / len(sub)
        # $ per 1000 entries — normalizes volume so cap-vs-cap is edge-density, not count
        dp1k = dollars(sub) / len(sub) * 1000
        print(f"      cap {cap:.2f}: n={len(sub):5d}  win {wr:.3f}  $ALL {dollars(sub):+8.1f}  "
              f"$/1k-entries {dp1k:+7.1f}  $OOS {dollars(oos):+8.1f}")


if __name__ == "__main__":
    main()
