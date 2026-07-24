"""Backtest the LEAD-FLOOR hypothesis on favorite's entries.

The live autopsy (2026-06-20): favorite's losses concentrate in SOFT favorites —
windows priced 0.85-0.91 by the book where BTC has barely moved by entry (a $12-56
mid-window lead on $63k). Reconstructed over 281 windows, reversal rate was 7.8%
when the mid-window move was large vs 31.4% when small — a 4x spread the book's
price does NOT fully capture (it overprices the near-tie favorite).

So: keep favorite's entry, but additionally demand the favorite's lead be ESTABLISHED
at decision time. Three normalizations, because an absolute $ floor is non-stationary
as BTC's price drifts and as vol changes:
  usd  conviction in $        (spot@entry - window_open, signed toward the favorite)
  bps  conviction / open      (scale-invariant to BTC's price level)
  z    conviction / expected-remaining-move = (conv/open)/(vol_per_min*sqrt(min_left))
       — vol-normalized: a storm-favorite is just a small lead vs its own vol, so
       this SUBSUMES favorite_vol's gate instead of bolting a second one on.

Discipline (inherited from the falsified coagula strategy, see FINDINGS): replicate the live decision exactly, hold to settle,
real taker fee + 2c ask haircut, ONE trade per window, split IS/OOS at 06-15, read
the tripwire (realized win-rate must beat the average price paid) on the OOS tranche.

Run: python3 research/backtest_lead.py [5m|15m]
"""
from __future__ import annotations
import json
import math
import os
import sys
import urllib.request

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_favorite import IS_OOS_SPLIT, vol_threshold, SEC, split_stats

KCACHE = os.path.join(os.path.dirname(__file__), "data", "_klines_1m.json")


# ----------------------------------------------------------- spot tape ---
def fetch_klines(start_s: int, end_s: int, cache: str = KCACHE) -> dict[int, float]:
    """1m BTCUSDT close indexed by openTime (sec). Cached to disk so reruns
    are deterministic and offline — the OOS tranche must not drift. `cache` is the
    on-disk path; callers (e.g. backtest_lead5m/backtest_size) pass a DISTINCT file
    so studies over different window spans never clobber each other's cache."""
    if os.path.exists(cache):
        raw = json.load(open(cache))
        idx = {int(k): v for k, v in raw.items()}
        if min(idx) <= start_s and max(idx) >= end_s - 60:
            return idx
    idx: dict[int, float] = {}
    cur = start_s * 1000
    end_ms = end_s * 1000
    while cur < end_ms:
        url = ("https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m"
               f"&startTime={cur}&endTime={end_ms}&limit=1000")
        ks = json.load(urllib.request.urlopen(url, timeout=20))
        if not ks:
            break
        for k in ks:
            idx[k[0] // 1000] = float(k[4])     # close
        cur = ks[-1][0] + 60000
    json.dump(idx, open(cache, "w"))
    return idx


def spot_at(idx: dict[int, float], t: int) -> float | None:
    """Spot as of decision time t, with NO lookahead: the close of the last 1m bar
    that has ALREADY CLOSED at/before t — the bar ending at (t//60)*60. The bar
    *covering* t only closes up to 60s in the FUTURE, so using it (the old code)
    gave the lead gate ~60s of foresight it would not have live — a real lookahead."""
    return idx.get((t // 60) * 60 - 60)


# --------------------------------------------------------- simulation ---
def simulate_lead(recs, idx, sec, frac, min_fav, max_fav, haircut,
                  vol_cap=None, min_lead=0.0, mode="z"):
    """favorite's entry + a floor on the favorite's established lead at entry.
    Returns rows of (pnl_per_$, px, win, r). mode in {usd, bps, z}."""
    rows = []
    elapsed_min = frac * sec / 60.0
    remain_min = (1 - frac) * sec / 60.0
    for r in recs:
        if not (r.get("tape") or r.get("price_track")):
            continue
        if vol_cap is not None and (r.get("vol_per_min") or 0.0) > vol_cap:
            continue
        t = r["window_start"] + int(frac * sec)
        p = up_price(r, t)
        if p is None:
            continue
        up_lead = p >= 0.5
        fav = p if up_lead else 1.0 - p
        if not (min_fav <= fav <= max_fav):
            continue
        # the favorite's established lead, signed toward the side we'd buy
        op = r.get("binance_open")
        spot = spot_at(idx, t)
        if op is None or spot is None:
            continue
        conv = (spot - op) if up_lead else (op - spot)   # >0 when spot agrees
        if mode == "usd":
            lead = conv
        elif mode == "bps":
            lead = conv / op * 1e4
        else:  # z: conviction in remaining-window sigmas
            v = r.get("vol_per_min") or 0.0
            denom = op * v * math.sqrt(max(remain_min, 1e-9))
            lead = conv / denom if denom > 0 else 0.0
        if lead < min_lead:
            continue
        px = min(fav + haircut, 0.99)
        win = r["up_won"] if up_lead else (not r["up_won"])
        sh = 1.0 / px
        fee = taker_fee(sh, px)
        pnl = (sh - sh * px - fee) if win else (-sh * px - fee)
        rows.append((pnl, px, win, r))
    return rows


def report(name, rows, stake=25.0):
    a = split_stats(rows)
    if a is None:
        print(f"  {name:22s}: no qualifying windows")
        return
    ins = split_stats([x for x in rows if day_of(x[3]) < IS_OOS_SPLIT])
    oos = split_stats([x for x in rows if day_of(x[3]) >= IS_OOS_SPLIT])
    ev, n, pos, wr, ap, _ = a
    o = oos and f"OOS EV/$ {oos[0]:+.4f} n={oos[1]:<3d} days+ {oos[2]:.0%} " \
                f"win {oos[3]:.3f}/px {oos[4]:.3f} [{'REAL' if oos[3] > oos[4] else 'DEAD'}]"
    print(f"  {name:22s}: ALL EV/$ {ev:+.4f} n={n:<4d} | {o or 'no OOS'}")


def lapis_curve(recs, idx, sec, base_stake=25.0):
    """favorite_conviction = favorite_vollead's GATES (vol cap + 6bps lead) with the flat stake
    REPLACED by conviction sizing (extremity x excess-lead-z, clamped 0.5-2.0x).
    Reports the flat vs sized DOLLAR curve, IS and OOS, on the SAME entries — so
    the comparison isolates the effect of sizing, not of a different selection.

    There is no greed tilt here — and none in the engine either: a longshot bid-
    pressure tilt was tried and CUT after its flow proxy falsified it (the
    favorite's edge FALLS as longshot greed rises — research/backtest_greed.py).
    So this `mult` matches Engine._conviction_mult exactly. Sizing does not change
    EV/$ (every entry's per-$ payoff is unchanged); it reshapes the $-curve by
    putting more capital on the fatter-premium windows."""
    vhi = vol_threshold(recs, 0.66)
    frac, lo, hi, hc = 0.60, 0.85, 0.95, 0.02
    remain_min = (1 - frac) * sec / 60.0
    rows = []                                    # (pnl_per_$, fav, z, r)
    for r in recs:
        if not (r.get("tape") or r.get("price_track")):
            continue
        if (r.get("vol_per_min") or 0.0) > vhi:          # favorite_vol vol gate
            continue
        t = r["window_start"] + int(frac * sec)
        p = up_price(r, t)
        if p is None:
            continue
        up_lead = p >= 0.5
        fav = p if up_lead else 1.0 - p
        if not (lo <= fav <= hi):
            continue
        op = r.get("binance_open")
        spot = spot_at(idx, t)
        if op is None or spot is None:
            continue
        conv = (spot - op) if up_lead else (op - spot)
        if conv / op * 1e4 < 6.0:                        # favorite_lead 6bps lead floor
            continue
        v = r.get("vol_per_min") or 0.0
        denom = op * v * math.sqrt(max(remain_min, 1e-9))
        z = conv / denom if denom > 0 else 0.0
        px = min(fav + hc, 0.99)
        win = r["up_won"] if up_lead else (not r["up_won"])
        sh = 1.0 / px
        fee = taker_fee(sh, px)
        pnl = (sh - sh * px - fee) if win else (-sh * px - fee)
        rows.append((pnl, fav, z, r))

    def mult(fav, z):                            # mirrors Engine._conviction_mult
        m = 1.0 + 2.0 * max(0.0, hi - fav)       # extremity (nigredo)
        m *= 1.0 + 0.3 * max(0.0, z - 1.0)       # established-lead conviction
        return max(0.5, min(2.0, m))             # matches the engine exactly

    def curve(subset):
        flat = sum(pnl for pnl, _, _, _ in subset) * base_stake
        tot = staked = 0.0
        for pnl, fav, z, _ in subset:
            sz = base_stake * mult(fav, z)
            tot += pnl * sz
            staked += sz
        return flat, tot, staked, len(subset)

    print("\n  -- favorite_conviction: conviction sizing on favorite_vollead's gates ($-curve) --")
    for tag, sub in (("ALL", rows),
                     ("IS ", [x for x in rows if day_of(x[3]) < IS_OOS_SPLIT]),
                     ("OOS", [x for x in rows if day_of(x[3]) >= IS_OOS_SPLIT])):
        if not sub:
            print(f"     {tag}: no qualifying windows")
            continue
        flat, sized, staked, n = curve(sub)
        print(f"     {tag}: flat ${flat:+.0f}  ->  sized ${sized:+.0f}  "
              f"(avg stake ${staked / n:.1f}, n={n})")


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "15m"
    sec = SEC[interval]
    recs = load_all(interval)
    lo_w = min(r["window_start"] for r in recs)
    hi_w = max(r["window_end"] for r in recs)
    idx = fetch_klines(lo_w - 120, hi_w + 120)
    days = sorted({day_of(r) for r in recs})
    vhi = vol_threshold(recs, 0.66)
    HC, FR, LO, HI = 0.02, 0.60, 0.85, 0.95
    print(f"=== backtest LEAD floor  {interval}  ({len(recs)} windows, "
          f"{days[0]}..{days[-1]}, {len(idx)} klines) ===")
    print(f"    IS/OOS @ {IS_OOS_SPLIT} | +2c haircut, hold to settle, taker fee, "
          f"fav {LO}-{HI}, enter {FR:.0%}\n")

    # references (min_lead=0 reproduces the controls)
    report("favorite (lead=0)", simulate_lead(recs, idx, sec, FR, LO, HI, HC))
    report("favorite_vol (vol gate)",
           simulate_lead(recs, idx, sec, FR, LO, HI, HC, vol_cap=vhi))

    sweeps = {
        "usd": [20, 40, 60, 80, 120],
        "bps": [4, 8, 12, 16, 24],
        "z":   [0.25, 0.5, 0.75, 1.0, 1.5],
    }
    for mode, thrs in sweeps.items():
        print(f"\n  -- mode {mode} --")
        for thr in thrs:
            rows = simulate_lead(recs, idx, sec, FR, LO, HI, HC,
                                 min_lead=thr, mode=mode)
            report(f"lead_{mode}>={thr}", rows)

    # the principled combo: vol-z lead floor REPLACING the crude vol cap
    print("\n  -- z-floor as a favorite_vol replacement (no separate vol_cap) --")
    for thr in (0.5, 0.75, 1.0):
        report(f"z>={thr} only", simulate_lead(recs, idx, sec, FR, LO, HI, HC,
                                               min_lead=thr, mode="z"))

    # favorite_conviction: does conviction sizing improve the $-curve on the SAME entries?
    lapis_curve(recs, idx, sec)


if __name__ == "__main__":
    main()
