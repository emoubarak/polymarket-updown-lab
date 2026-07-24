"""Backtest the LEAD-FLOOR hypothesis on the 5m frame — can it resurrect 5m?

The 5m favorite died OUT-OF-SAMPLE and was falsified live, so 5m was pulled from
prod (MEMORY: edges-are-artifacts-5m). The autopsy that drove favorite_lead on 15m
(lead>=6 bps lifted OOS EV/$ from +0.032 to +0.040) suggests WHY 5m might be
salvageable: favorite's loss mode is the SOFT favorite — a near-coin-flip window the
book overprices at 0.85-0.91 while BTC has barely moved by entry. That failure is
STRUCTURALLY WORSE on 5m: only ~2 min of window remain at entry, so less time for a
real lead to print, yet the book still quotes extreme-favorite prices. If demanding
an ESTABLISHED lead at decision time turns 5m positive AND real (win-rate > price
paid) out-of-sample, the prize is ~3x the 15m volume.

This is backtest_lead.py re-pointed at sec=300, with the 5m geometry made explicit:
  - frac_in default 0.60 == 3 min elapsed / 2 min left (we also sweep 0.5 and 0.7);
  - the lead = (spot@entry - window_open) signed toward the favorite, in bps and in
    vol-normalized z (conviction / expected remaining move);
  - sweep the floor in bps {0,4,6,8,12,16} and z {0.5,1,1.5}, with and without the
    favorite_vol vol gate (66th pct), at each frac_in.

Same discipline as every other study in research/ (inherited from the falsified coagula strategy, see FINDINGS): replicate the
live decision exactly, hold to settle, real taker fee + 2c ask haircut, ONE trade
per window, split IS/OOS at 06-15, and read the tripwire on the OOS tranche — a
favorite-harvester only has an edge if the realized OOS win-rate beats the average
OOS price paid. "days+ on the in-sample tranche" is worth nothing.

Klines are cached to a UNIQUE file so this job never collides with backtest_lead.py
(which uses _klines_1m.json over a different window span).

Run: python3 research/backtest_lead5m.py
"""
from __future__ import annotations
import os
import sys

from explore2 import load_all, day_of
from backtest_favorite import IS_OOS_SPLIT, vol_threshold, split_stats
# Mutualised: spot_at (lookahead-safe) + simulate_lead (favorite entry + lead floor)
# live in backtest_lead — this file is the SAME engine re-pointed at sec=300, so it
# imports them instead of carrying verbatim copies. fetch_klines takes the cache
# path, so we pass a DISTINCT 5m cache below (never collides with the 15m run).
from backtest_lead import fetch_klines, spot_at, simulate_lead  # noqa: F401  (spot_at re-exported)

SEC = 300  # this file is 5m-only by construction
# UNIQUE cache name — must not collide with backtest_lead.py's _klines_1m.json,
# which is fetched over a different (interval-dependent) window span.
KCACHE = os.path.join(os.path.dirname(__file__), "data", "_klines_5m_lead.json")


def report(name, rows):
    """ALL EV/$ + the OOS tranche with the win>px tripwire verdict."""
    a = split_stats(rows)
    if a is None:
        print(f"  {name:24s}: no qualifying windows")
        return
    oos = split_stats([x for x in rows if day_of(x[3]) >= IS_OOS_SPLIT])
    ev, n, pos, wr, ap, _ = a
    if oos:
        verdict = "REAL" if oos[3] > oos[4] else "DEAD"
        o = (f"OOS EV/$ {oos[0]:+.4f} n={oos[1]:<3d} days+ {oos[2]:.0%} "
             f"win {oos[3]:.3f}/px {oos[4]:.3f} [{verdict}]")
    else:
        o = "no OOS"
    print(f"  {name:24s}: ALL EV/$ {ev:+.4f} n={n:<4d} days+ {pos:.0%} | {o}")


def sweep_frac(recs, idx, sec, frac, lo, hi, hc, vhi):
    """Full bps+z floor sweep at a given frac_in, with and without the vol gate."""
    elapsed = frac * sec / 60.0
    remain = (1 - frac) * sec / 60.0
    print(f"\n========== frac_in {frac:.0%}  "
          f"({elapsed:.1f} min elapsed / {remain:.1f} min left) ==========")

    # controls at this frac (min_lead=0 reproduces nu favorite / favorite_vol)
    report("favorite (lead=0)",
           simulate_lead(recs, idx, sec, frac, lo, hi, hc))
    report("favorite_vol (vol gate)",
           simulate_lead(recs, idx, sec, frac, lo, hi, hc, vol_cap=vhi))

    sweeps = {"bps": [4, 6, 8, 12, 16], "z": [0.5, 1.0, 1.5]}
    for mode, thrs in sweeps.items():
        print(f"  -- mode {mode}  (no vol gate) --")
        for thr in thrs:
            report(f"lead_{mode}>={thr}",
                   simulate_lead(recs, idx, sec, frac, lo, hi, hc,
                                 min_lead=thr, mode=mode))
        print(f"  -- mode {mode}  + favorite_vol vol gate --")
        for thr in thrs:
            report(f"lead_{mode}>={thr}+vol",
                   simulate_lead(recs, idx, sec, frac, lo, hi, hc,
                                 min_lead=thr, mode=mode, vol_cap=vhi))


def main():
    sec = SEC
    recs = load_all("5m")
    lo_w = min(r["window_start"] for r in recs)
    hi_w = max(r["window_end"] for r in recs)
    idx = fetch_klines(lo_w - 120, hi_w + 120, cache=KCACHE)
    days = sorted({day_of(r) for r in recs})
    vhi = vol_threshold(recs, 0.66)
    HC, LO, HI = 0.02, 0.85, 0.95
    print(f"=== backtest LEAD floor  5m  ({len(recs)} windows, "
          f"{days[0]}..{days[-1]}, {len(idx)} klines) ===")
    print(f"    IS/OOS @ {IS_OOS_SPLIT} | +2c haircut, hold to settle, taker fee, "
          f"fav {LO}-{HI} | vol gate = {vhi:.5f} (66th pct)")
    print(f"    tripwire: OOS realized win-rate must beat OOS avg price paid "
          f"[REAL] else [DEAD]")

    for frac in (0.50, 0.60, 0.70):
        sweep_frac(recs, idx, sec, frac, LO, HI, HC, vhi)


if __name__ == "__main__":
    main()
