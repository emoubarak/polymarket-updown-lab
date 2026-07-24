"""Re-validate the favorite-longshot configs on the EXTENDED cache (≈40 days,
harvested 2026-06-20) with a robust split — the payoff of more data.

The original validation used a 10-day window (06-09..18, OOS 06-15..18 ≈ 4 days).
That window turned out FAVORABLE: on the full ≈40 days the edge is far thinner.
This script re-runs each config over the whole cache and reports the OOS tranche for
a configurable split (default 05-29 → ≈20 days OOS, n in the hundreds). It is the
honest reckoning: a 10-day apparent edge can be period-specific (the lesson from the falsified coagula strategy).

STATUS (2026-06-26): HISTORICAL. The `configs` below are the PRE-zlead lineage
(favorite / favorite_vol / favorite_wide / favorite_lead / favorite_vollead / favorite_cheap) — all since
retired and renamed. The dashboard's BACKTEST numbers are NO LONGER taken from
here; they now come from gen_backtest_curves.py (the zlead family →
research/backtest_curves.json). Kept as a re-runnable robustness probe of the
underlying lead-floor edge on the long cache; it does NOT drive any live number.

Run: python3 research/revalidate.py [SPLIT_MMDD]   (default 05-29)
"""
from __future__ import annotations
import sys

from explore2 import day_of, load_all
from backtest_favorite import split_stats, vol_threshold
import backtest_lead as BL


def main():
    split = sys.argv[1] if len(sys.argv) > 1 else "05-29"
    recs = load_all("15m")
    days = sorted({day_of(r) for r in recs})
    lo = min(r["window_start"] for r in recs) - 120
    hi = max(r["window_end"] for r in recs) + 120
    idx = BL.fetch_klines(lo, hi)
    vhi = vol_threshold(recs, 0.66)
    ndays = len(days)
    print(f"=== re-validation  {len(recs)} fenêtres, {days[0]}..{days[-1]} "
          f"({ndays} jours) · OOS @ {split} ===\n")
    # Legacy (pre-zlead) named variants — see the module docstring: these names are
    # retired from prod, kept here only as a long-cache robustness probe of the edge.
    configs = {
        "favorite":     dict(lo=0.85, hi=0.95),
        "favorite_vol": dict(lo=0.85, hi=0.95, vol_cap=vhi),
        "favorite_wide": dict(lo=0.80, hi=0.95, vol_cap=vhi),
        "favorite_lead":    dict(lo=0.85, hi=0.95, min_lead=6, mode="bps"),
        "favorite_vollead": dict(lo=0.85, hi=0.95, vol_cap=vhi, min_lead=6, mode="bps"),
        "favorite_cheap":      dict(lo=0.85, hi=0.88),
    }
    for nm, kw in configs.items():
        rows = BL.simulate_lead(recs, idx, 900, 0.60, kw["lo"], kw["hi"], 0.02,
                                vol_cap=kw.get("vol_cap"),
                                min_lead=kw.get("min_lead", 0.0), mode=kw.get("mode", "z"))
        full = split_stats(rows)
        oos = split_stats([x for x in rows if day_of(x[3]) >= split])
        if not (full and oos):
            print(f"  {nm:12s}: insufficient")
            continue
        fev, fn = full[0], full[1]
        ev, n, dp, wr, px, _ = oos
        print(f"  {nm:12s}: OOS ev {ev:+.4f} n={n:<4d} days+ {dp:.0%} "
              f"win {wr:.3f}/px {px:.3f} [{'REAL' if wr > px else 'DEAD'}] "
              f"| full ev {fev:+.4f} n={fn} tpd {fn/ndays:.1f}")


if __name__ == "__main__":
    main()
