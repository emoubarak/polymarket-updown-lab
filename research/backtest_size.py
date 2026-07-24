"""Backtest CONVICTION-SIZING on favorite's (and favorite_lead's) entries.

Engine stakes a FLAT $25 on every qualifying window (extreme favorite 0.85-0.95,
15m, enter at 60%, hold to settle). The payoff is brutally asymmetric: buy the
favorite at 0.90 and you win +0.11 if it holds, lose -1.00 if it flips; the
break-even win-rate IS the price. The entries are validated (favorite OOS EV/$
~+0.03). This file does NOT touch the entry rule. It asks one question:

    Given the SAME set of trades, does sizing the stake by CONVICTION improve the
    dollar return/risk profile vs a flat stake -- WITHOUT manufacturing edge?

This is Kelly-light. Intuition: stake MORE when (a) the favorite is cheap ~0.85
(maximal longshot premium, fat upside) and (b) BTC's lead is well established
(low reversal risk); stake LESS when the favorite is ~0.95 (thin upside) or the
lead is soft. None of these inputs is forward-looking: fav-price and the signed
BTC lead are both known at decision time, exactly as in backtest_lead.py.

Sizing schemes (all multipliers BOUNDED to [MULT_LO, MULT_HI] = [0.5, 2.0] of a
$BASE notional, so total deployed capital is comparable to flat and no single
window can blow up):
  fixed       flat $BASE every window (the control).
  cheapfav    size up the cheap favorite:    m ~ (0.95 - fav)         (nigredo).
  leadz       size up the established lead:   m ~ lead_z (vol-norm).
  combo       both, multiplied.
  kelly       explicit fractional Kelly: f* = edge/odds on the model-free
              per-window edge, capped -- see kelly_mult().

THE SANITY GATE (this is the whole point): sizing must NOT change EV/$ by magic.
Compare capital-weighted EV/$ (= total$ / total_staked) of each scheme against
flat. If a scheme's EV/$ jumps materially above flat's, the stake is correlating
with the *outcome* -> that is a bug or a lookahead, not skill, and we say so.
What sizing legitimately moves is the $-CURVE: total $, $/window, and RISK
(max drawdown, worst loss cluster, std of daily P&L).

Discipline inherited from the falsified coagula strategy (see FINDINGS): replicate the live decision exactly
(executable fav price + 2c ask haircut, hold to settle, real taker fee, one trade
per window), split IS/OOS at 06-15, report the OOS tranche as the only one that
counts. A few days is statistical noise (~12 windows/h on 5m) -- read honestly.

Run: python3 research/backtest_size.py [5m|15m]
"""
from __future__ import annotations
import math
import os
import sys

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_favorite import IS_OOS_SPLIT, SEC
# Mutualised: spot_at + the 1m-kline tape both live in backtest_lead. fetch_klines
# takes its cache path, so we pass a DISTINCT _klines_size.json (never collides with
# backtest_lead.py's _klines_1m.json over a different window span).
from backtest_lead import fetch_klines, spot_at  # noqa: F401  (spot_at re-exported)

# UNIQUE cache name -- must not collide with backtest_lead.py's _klines_1m.json.
KCACHE = os.path.join(os.path.dirname(__file__), "data", "_klines_size.json")

MULT_LO, MULT_HI = 0.5, 2.0     # bounded sizing: no window < 0.5x or > 2.0x base
BASE = 25.0                     # favorite's live flat stake, the sizing pivot
LEAD_FLOOR_BPS = 6.0               # favorite_lead = favorite entries with lead >= 6 bps


# --------------------------------------------------------- entry rows ---
def entry_rows(recs, idx, sec, frac, min_fav, max_fav, haircut, remain_min):
    """favorite's entries, each enriched with the conviction signals known at
    decision time. Returns dicts: pnl (per $1 staked), px, win, day, fav,
    lead_bps, lead_z. No order/sizing here -- pure trade ledger."""
    rows = []
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
        conv = (spot - op) if up_lead else (op - spot)    # signed toward our side
        lead_bps = conv / op * 1e4
        v = r.get("vol_per_min") or 0.0
        denom = op * v * math.sqrt(max(remain_min, 1e-9))
        lead_z = conv / denom if denom > 0 else 0.0
        px = min(fav + haircut, 0.99)
        win = r["up_won"] if up_lead else (not r["up_won"])
        sh = 1.0 / px
        fee = taker_fee(sh, px)
        pnl = (sh - sh * px - fee) if win else (-sh * px - fee)   # per $1 staked
        rows.append({"pnl": pnl, "px": px, "win": win, "day": day_of(r),
                     "fav": fav, "lead_bps": lead_bps, "lead_z": lead_z})
    return rows


# ----------------------------------------------------- sizing schemes ---
def clamp(m: float) -> float:
    return max(MULT_LO, min(MULT_HI, m))


def mult_fixed(row) -> float:
    return 1.0


def mult_cheapfav(row) -> float:
    """nigredo: more $ on the cheap favorite. fav in [0.85,0.95] -> raw gap
    (0.95-fav) in [0, 0.10]. Scaled so 0.85 -> 2.0x and 0.95 -> 0.5x, linearly,
    centered near the 0.90 midpoint at ~1.0x. Then clamped (a no-op inside band)."""
    # map gap 0..0.10 onto multiplier 0.5..2.0 -> m = 0.5 + 15*gap
    return clamp(0.5 + 15.0 * (0.95 - row["fav"]))


def mult_leadz(row) -> float:
    """more $ when the lead is established. lead_z is conviction in remaining-
    window sigmas; a soft/contrary lead (z<=0) gets the floor, a strong lead
    (z>=1.5) gets the cap, linear between. Symmetric pivot ~0.75z -> ~1.0x."""
    z = row["lead_z"]
    return clamp(0.5 + (1.5 / 1.5) * z)     # m = 0.5 + z ; z=0.5->1.0, z>=1.5->2.0


def mult_combo(row) -> float:
    """Both convictions, multiplied then re-clamped so the product still lives
    in [0.5, 2.0]. Geometric-ish: only windows that are BOTH cheap AND
    well-led get the big stake."""
    return clamp(mult_cheapfav(row) * mult_leadz(row))


def mult_kelly(row) -> float:
    """Explicit fractional Kelly on the per-window model-free edge.

    We have no per-window win-probability model (favorite is price-only), so we use
    the SAMPLE-CONSTANT realized edge of the band as p_hat and let the per-window
    PRICE set the odds. Bet = buy 'win' at px: net win = (1-px)/px per $ (odds
    b), lose 1 per $. Kelly f* = (p*b - (1-p))/b = (p - px) / (1 - px) where p is
    the win-prob estimate. Using a single in-band p_hat passed in via row['phat']
    keeps it model-free yet honors that cheap favorites carry MORE Kelly fraction
    (smaller px -> bigger (p-px)). Fractional (KELLY_FRAC) and clamped to band.

    NOTE p_hat is a sample constant, NOT a per-window outcome -> no lookahead in
    the cross-section; it is the same number for every window so it cannot
    correlate stake with which window won."""
    p = row.get("phat", row["px"])          # fallback: break-even -> f*=0 -> floor
    px = row["px"]
    if px >= 1.0:
        return MULT_LO
    fstar = (p - px) / (1.0 - px)           # full-Kelly fraction of bankroll
    KELLY_FRAC = 0.5                         # half-Kelly, the standard safety
    # f* is a *fraction of bankroll*; favorite's flat stake is the 1.0x reference.
    # Normalize so the band-average f* maps to ~1.0x, then clamp.
    m = 1.0 + 8.0 * KELLY_FRAC * (fstar - 0.0)   # scale tuned to keep avg ~1x
    return clamp(m)


SCHEMES = [
    ("fixed", mult_fixed),
    ("cheapfav", mult_cheapfav),
    ("leadz", mult_leadz),
    ("combo", mult_combo),
    ("kelly", mult_kelly),
]


# ---------------------------------------------------------- accounting ---
def daily_pnl(ledger):
    """ledger: list of (day, dollar_pnl) -> {day: summed $ pnl}, day-sorted."""
    by = {}
    for day, d in ledger:
        by[day] = by.get(day, 0.0) + d
    return by


def max_drawdown(ledger):
    """Max peak-to-trough drawdown in $ over the trade-ordered equity curve.
    ledger is (day, dollar_pnl) in chronological order (rows are window-sorted)."""
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for _, d in ledger:
        eq += d
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return mdd                                   # <= 0


def worst_loss_cluster(ledger):
    """Largest contiguous run of negative cumulative drift -- i.e. the most
    negative sum over any contiguous slice of the trade sequence (Kadane on the
    negative side). The single worst losing streak in $."""
    worst = 0.0
    cur = 0.0
    for _, d in ledger:
        cur = min(0.0, cur + d)
        worst = min(worst, cur)
    return worst                                 # <= 0


def std(xs):
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def scheme_stats(rows, mult_fn):
    """Run a sizing scheme over a row set. Returns a metrics dict."""
    if not rows:
        return None
    ledger = []                # (day, dollar_pnl) chronological
    stakes = []
    total = 0.0
    staked = 0.0
    for row in rows:
        stake = BASE * mult_fn(row)
        d = row["pnl"] * stake
        ledger.append((row["day"], d))
        stakes.append(stake)
        total += d
        staked += stake
    dbyday = daily_pnl(ledger)
    return {
        "n": len(rows),
        "total": total,
        "per_win": total / len(rows),            # $ per window
        "avg_stake": staked / len(rows),
        "staked": staked,
        "ev_per_dollar": total / staked if staked else 0.0,   # capital-weighted
        "mdd": max_drawdown(ledger),
        "worst_cluster": worst_loss_cluster(ledger),
        "daily_std": std(list(dbyday.values())),
        "n_days": len(dbyday),
    }


def attach_phat(rows):
    """Give every row the SAME sample win-rate of its tranche as p_hat for Kelly.
    Constant across windows -> cannot encode which window won (no lookahead in the
    cross-section). It DOES use the tranche's own realized rate, which is a mild
    in-tranche peek; we report Kelly separately and read it skeptically."""
    if not rows:
        return rows
    phat = sum(1 for r in rows if r["win"]) / len(rows)
    for r in rows:
        r["phat"] = phat
    return rows


# -------------------------------------------------------------- report ---
def ev_by_favbucket(rows):
    """The cross-section that EXPLAINS why cheapfav moves EV/$: per-window EV is
    NOT flat across the 0.85-0.95 band. If it slopes (cheap favorite richer),
    concentrating capital there raises capital-weighted EV/$ legitimately -- that
    is a real cross-sectional edge, not lookahead. Printed so the <<EV MOVED flag
    is read correctly. Also surfaces the small-sample risk (a 1.000 win-rate
    bucket cannot persist)."""
    buckets = [(0.85, 0.88), (0.88, 0.91), (0.91, 0.93), (0.93, 0.951)]
    print("    EV/$ across the favorite-price band (the cheapfav rationale):")
    for lo, hi in buckets:
        sub = [r for r in rows if lo <= r["fav"] < hi]
        if not sub:
            print(f"      fav[{lo:.2f},{hi:.2f}): empty")
            continue
        ev = sum(r["pnl"] for r in sub) / len(sub)
        wr = sum(1 for r in sub if r["win"]) / len(sub)
        apx = sum(r["px"] for r in sub) / len(sub)
        print(f"      fav[{lo:.2f},{hi:.2f}): n={len(sub):3d}  EV/$ {ev:+.4f}  "
              f"win {wr:.3f} vs px {apx:.3f}  (margin {wr - apx:+.3f})")


def report_entry(title, rows):
    print(f"\n{'='*78}\n{title}  (n={len(rows)})\n{'='*78}")
    ins = [r for r in rows if r["day"] < IS_OOS_SPLIT]
    oos = [r for r in rows if r["day"] >= IS_OOS_SPLIT]
    ev_by_favbucket(rows)
    for tag, sub in (("ALL", rows), ("IS ", ins), ("OOS", oos)):
        if not sub:
            print(f"  [{tag}] no windows")
            continue
        attach_phat(sub)
        flat = scheme_stats(sub, mult_fixed)
        flat_ev = flat["ev_per_dollar"]
        print(f"\n  [{tag}]  n={flat['n']}  flat EV/$ {flat_ev:+.4f}  "
              f"(sanity baseline; sized EV/$ should stay ~here)")
        hdr = (f"    {'scheme':10s} {'total$':>9s} {'$/win':>7s} {'avgStk':>7s} "
               f"{'EV/$':>8s} {'dEV/$':>7s} {'maxDD$':>9s} {'worstClu$':>10s} "
               f"{'dayStd$':>8s}")
        print(hdr)
        for name, fn in SCHEMES:
            s = scheme_stats(sub, fn)
            dev = s["ev_per_dollar"] - flat_ev
            flag = "  <<EV MOVED" if abs(dev) > 0.004 and name != "fixed" else ""
            print(f"    {name:10s} {s['total']:>+9.0f} {s['per_win']:>+7.2f} "
                  f"{s['avg_stake']:>7.1f} {s['ev_per_dollar']:>+8.4f} "
                  f"{dev:>+7.4f} {s['mdd']:>+9.0f} {s['worst_cluster']:>+10.0f} "
                  f"{s['daily_std']:>8.1f}{flag}")


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "15m"
    sec = SEC[interval]
    recs = load_all(interval)
    lo_w = min(r["window_start"] for r in recs)
    hi_w = max(r["window_end"] for r in recs)
    idx = fetch_klines(lo_w - 120, hi_w + 120, cache=KCACHE)
    days = sorted({day_of(r) for r in recs})
    HC, FR, LO, HI = 0.02, 0.60, 0.85, 0.95
    remain_min = (1 - FR) * sec / 60.0

    print(f"=== backtest CONVICTION-SIZING  {interval}  ({len(recs)} windows, "
          f"{days[0]}..{days[-1]}, {len(idx)} klines) ===")
    print(f"    IS/OOS @ {IS_OOS_SPLIT} | +2c haircut, hold to settle, taker fee | "
          f"fav {LO}-{HI}, enter {FR:.0%}")
    print(f"    base stake ${BASE:.0f}, multiplier bounded [{MULT_LO},{MULT_HI}]")
    print("    SANITY: a scheme whose EV/$ drifts > +0.004 above flat is flagged "
          "<<EV MOVED")
    print("            (sizing must not manufacture edge; that = bug/lookahead).")
    print("    Risk metrics are NEGATIVE $ (drawdown / worst losing cluster); "
          "less negative = safer.")

    rub = entry_rows(recs, idx, sec, FR, LO, HI, HC, remain_min)
    report_entry("FAVORITE entries (favorite 0.85-0.95, no lead floor)", rub)

    fix = [r for r in rub if r["lead_bps"] >= LEAD_FLOOR_BPS]
    report_entry(f"FIXATIO entries (favorite + lead >= {LEAD_FLOOR_BPS:.0f} bps)", fix)


if __name__ == "__main__":
    main()
