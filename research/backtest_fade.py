"""Backtest the FADE / reversal hypothesis (user's idea, 2026-06-21).

Engine buys the FAVORITE at ~0.85 after an established lead and holds. Sometimes
the favorite collapses to ~0.1 (reversal). The idea: BUY THE UNDERDOG (the cheap
side) after the lead — bet ON that reversal. Especially after EXTREME leads
(over-extension → mean reversion), maybe the longshot wins MORE than its price.

This is the mirror of the favorite-longshot harvest, so the prior is strongly
negative (the longshot is the over-priced side). We test it honestly with the
exact same entry machinery as backtest_lead.py, buying the OTHER side. The decisive
number: the UNDERDOG's realized win-rate vs its price. edge = win − price:
  edge > 0  → longshot under-priced → reversal edge exists (user is right)
  edge < 0  → longshot over-priced → favorite-longshot bias holds (favorite's side)
Swept across the lead floor to see if reversal RISES or FALLS with over-extension.

Run: python3 research/backtest_fade.py [5m|15m]
"""
from __future__ import annotations
import math
import sys

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_favorite import IS_OOS_SPLIT, SEC, split_stats
from backtest_lead import fetch_klines, spot_at, simulate_lead


def simulate_fade(recs, idx, sec, frac, min_fav, max_fav, haircut,
                  min_lead=0.0, max_lead=None, mode="z"):
    """Buy the UNDERDOG after the favorite's lead is established (reversal bet).
    Identical entry to simulate_lead, but we take the cheap side and win on a
    reversal. Returns rows of (pnl_per_$, px, win, r)."""
    rows = []
    remain_min = (1 - frac) * sec / 60.0
    for r in recs:
        if not (r.get("tape") or r.get("price_track")):
            continue
        t = r["window_start"] + int(frac * sec)
        p = up_price(r, t)
        if p is None:
            continue
        up_lead = p >= 0.5
        fav = p if up_lead else 1.0 - p
        if not (min_fav <= fav <= max_fav):
            continue
        op = r.get("binance_open")
        spot = spot_at(idx, t)
        if op is None or spot is None:
            continue
        conv = (spot - op) if up_lead else (op - spot)   # lead toward the favorite
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
        if max_lead is not None and lead > max_lead:
            continue
        dog = 1.0 - fav                       # the cheap side
        px = min(dog + haircut, 0.99)
        fav_won = r["up_won"] if up_lead else (not r["up_won"])
        win = not fav_won                     # underdog wins ⇔ favorite reverses
        sh = 1.0 / px
        fee = taker_fee(sh, px)
        pnl = (sh - sh * px - fee) if win else (-sh * px - fee)
        rows.append((pnl, px, win, r))
    return rows


def fade_report(name, rows):
    """All-data view: the underdog's win-rate vs its price (the reversal test),
    plus the OOS tranche so a fluke can't sneak through."""
    a = split_stats(rows)
    if a is None:
        print(f"  {name:20s}: no qualifying windows")
        return
    ev, n, _pos, wr, ap, _ = a
    oos = split_stats([x for x in rows if day_of(x[3]) >= IS_OOS_SPLIT])
    edge = wr - ap
    tag = "REVERSAL" if (oos and oos[3] > oos[4]) else "no edge"
    o = oos and f"OOS n={oos[1]:<4d} win {oos[3]:.3f}/px {oos[4]:.3f}" or "OOS —"
    print(f"  {name:20s}: n={n:<5d} dog-win {wr:.3f} vs px {ap:.3f}  "
          f"edge {edge:+.3f}  EV/$ {ev:+.4f} | {o} [{tag}]")


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "15m"
    sec = SEC[interval]
    recs = load_all(interval)
    lo_w = min(r["window_start"] for r in recs)
    hi_w = max(r["window_end"] for r in recs)
    idx = fetch_klines(lo_w - 120, hi_w + 120)
    HC, FR, LO, HI = 0.02, 0.60, 0.85, 0.95
    print(f"=== backtest FADE / reversal {interval} ({len(recs)} windows) ===")
    print(f"    buy the UNDERDOG (dog {1-HI:.2f}-{1-LO:.2f}) after the favorite's "
          f"lead, enter {FR:.0%}, IS/OOS @ {IS_OOS_SPLIT}")
    print(f"    edge = dog-win − dog-price ; >0 = longshot under-priced (reversal)\n")

    print("  REFERENCE — buy the FAVORITE (favorite, +2c, real fee):")
    a = split_stats(simulate_lead(recs, idx, sec, FR, LO, HI, HC))
    if a:
        print(f"    favorite            : n={a[1]:<5d} fav-win {a[3]:.3f} vs px {a[4]:.3f}  "
              f"edge {a[3]-a[4]:+.3f}  EV/$ {a[0]:+.4f}")

    print("\n  FADE @ FAIR price (haircut=0) — pure mispricing test, swept on the lead:")
    fade_report("fade lead=0", simulate_fade(recs, idx, sec, FR, LO, HI, 0.0))
    for thr in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
        fade_report(f"fade z>={thr}",
                    simulate_fade(recs, idx, sec, FR, LO, HI, 0.0, min_lead=thr, mode="z"))

    print("\n  FADE @ TRADEABLE (haircut=2c + real taker fee):")
    for thr in (0.0, 1.0, 2.0):
        fade_report(f"fade z>={thr}",
                    simulate_fade(recs, idx, sec, FR, LO, HI, HC, min_lead=thr, mode="z"))

    print("\n  bps lead (fair) — same question, scale-invariant floor:")
    for thr in (4, 12, 24, 40):
        fade_report(f"fade bps>={thr}",
                    simulate_fade(recs, idx, sec, FR, LO, HI, 0.0, min_lead=thr, mode="bps"))


if __name__ == "__main__":
    main()
