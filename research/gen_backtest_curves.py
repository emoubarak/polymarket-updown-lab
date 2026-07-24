#!/usr/bin/env python3
"""Generate the per-strategy BACKTEST EQUITY CURVE for the deployed lineup.

Replays the SAME gate that produced the published EV numbers — backtest_lead.py's
`simulate_lead` (symbol-agnostic) over the cached tape + spot klines — in
chronological order, and accumulates the cumulative DOLLAR P&L at the reference
stake. The IS/OOS boundary is marked so the dashboard can colour the out-of-sample
tranche (the only one that counts) distinctly.

This is faithful by construction: it imports the exact research functions, it does
NOT re-implement any gate. Output → research/backtest_curves.json, baked into the
dashboard (webdash serves it; the runner code never touches it). Re-run after any
change to a deployed strategy's params or the cache.

    python3 research/gen_backtest_curves.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest_favorite import SEC, IS_OOS_SPLIT, day_of, split_stats
from backtest_lead import simulate_lead
from backtest_eth import load_alt, fetch_klines, SYMBOL
from pmlab.presets import COIN_BET_MAX, WEIGHT_PCT, START_CAPITAL
from pmlab.staking import weighted_clip

LO, HI, HC, FRAC = 0.85, 0.95, 0.02, 0.60
MAXPTS = 120           # downsample the curve to keep /config light

# Deployed TAKER variants (zlead family) × coins × frames, keyed by the dashboard strat
# name so Strategy.js looks the curve up directly. Params mirror pmlab.presets:
# zlead z>=1 band 0.85-0.95 · zleadn narrow band 0.85-0.90 · zleadx strict z>=1.5.
# Maker (zleadmk) has no own backtest — it borrows its taker twin (front-end handles it).
# Only btc/eth/sol/xrp have a kline feed in backtest_eth.SYMBOL; doge/bnb skip gracefully.
_VARIANTS = {
    "zlead":  dict(min_lead=1.0, mode="z", lo=LO, hi=HI),
    "zleadn": dict(min_lead=1.0, mode="z", lo=LO, hi=0.90),
    "zleadx": dict(min_lead=1.5, mode="z", lo=LO, hi=HI),
}
SPECS = {
    f"{v}-{u}-{i}": dict(u=u, i=i, vol_cap=None, **p)
    for v, p in _VARIANTS.items()
    for u in ("btc", "eth", "sol", "xrp")
    for i in ("5m", "15m")
}


def downsample(pts: list[list[float]]) -> list[list[float]]:
    if len(pts) <= MAXPTS:
        return pts
    step = max(1, len(pts) // MAXPTS)
    out = pts[::step]
    if out[-1] is not pts[-1]:
        out.append(pts[-1])
    return out


def curve_for(spec: dict) -> dict | None:
    recs = load_alt(spec["u"], spec["i"])
    if not recs:
        return None
    t_lo = min(r["window_start"] for r in recs) - 120
    t_hi = max(r["window_end"] for r in recs) + 120
    idx = fetch_klines(t_lo, t_hi, SYMBOL[spec["u"]])
    rows = simulate_lead(recs, idx, SEC[spec["i"]], FRAC, spec["lo"], spec["hi"], HC,
                         vol_cap=spec["vol_cap"], min_lead=spec["min_lead"],
                         mode=spec["mode"])
    if not rows:
        return None
    rows.sort(key=lambda x: x[3]["window_start"])    # chronological (defensive)

    # COMPOUND from $100 with the weighted (10 %-of-capital, book-depth-capped) model —
    # the SAME staking.weighted_clip the paper engine + pilot use, per coin. The curve is
    # cumulative DOLLAR P&L (from a $100 base); EV/$ below stays size-invariant.
    cap = COIN_BET_MAX.get(spec["u"], 50.0)
    min_clip = min(5.0, cap)
    bankroll, full, split_x = START_CAPITAL, [], None
    for k, (pnl, _px, _win, r) in enumerate(rows):
        if split_x is None and day_of(r) >= IS_OOS_SPLIT:
            split_x = k
        stake = weighted_clip(bankroll, WEIGHT_PCT, cap, min_clip)
        bankroll += pnl * stake
        full.append([k, round(bankroll - START_CAPITAL, 2)])
    if split_x is None:        # all in-sample (alt data may post-date the split)
        split_x = len(full)

    is_pts = downsample(full[:split_x + 1] or full[:1])
    oos_pts = downsample(full[split_x:]) if split_x < len(full) else []
    oos_stats = split_stats([x for x in rows if day_of(x[3]) >= IS_OOS_SPLIT])
    allst = split_stats(rows)

    rec = {"is": is_pts, "oos": oos_pts, "split_x": split_x,
           "n": allst[1], "ev": round(allst[0], 4),
           "start": START_CAPITAL, "weight_pct": WEIGHT_PCT, "bet_max": cap,
           "final": full[-1][1]}
    if oos_stats:
        rec.update({"n_oos": oos_stats[1], "ev_oos": round(oos_stats[0], 4),
                    "win_oos": round(oos_stats[3], 3), "px_oos": round(oos_stats[4], 3),
                    "real": oos_stats[3] > oos_stats[4]})
    return rec


def main() -> None:
    out = {}
    for name, spec in SPECS.items():
        try:
            rec = curve_for(spec)
        except Exception as e:               # a missing klines range etc. — skip, don't crash
            print(f"  {name}: SKIP ({e})")
            continue
        if rec is None:
            print(f"  {name}: no qualifying windows / no cache")
            continue
        out[name] = rec
        oo = f"OOS n={rec.get('n_oos','—')} ev={rec.get('ev_oos','—')} " \
             f"[{'REAL' if rec.get('real') else 'dead'}]"
        print(f"  {name:18s}: n={rec['n']:<4d} final ${rec['final']:+.0f} "
              f"(${START_CAPITAL:.0f} start, {WEIGHT_PCT:.0%}/cap ${rec['bet_max']:.0f}) "
              f"· split@{rec['split_x']} · {oo}")
    dest = os.path.join(os.path.dirname(__file__), "backtest_curves.json")
    json.dump(out, open(dest, "w"))
    print(f"\n{len(out)} courbe(s) → {dest}")


if __name__ == "__main__":
    main()
