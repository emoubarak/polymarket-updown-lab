"""Backtest favorite and its improved variants on the cached tape.

The discipline demanded by the autopsy of the first falsified favorite strategy (coagula) (FINDINGS.md, 2026-06-17): replicate
the LIVE decision EXACTLY — enter at the favorite's executable price + an ask
haircut, hold to settlement, pay the real taker fee, one trade per window — then
split IN-SAMPLE vs OUT-OF-SAMPLE and read day-by-day sign stability. "days+ N/N
in-sample" is worth NOTHING; only OOS / live tranche.

The variants are not parameter cherry-picks. Each is one named hypothesis about
the SHADOW that eats the favorite-harvester:
  - favorite      the validated control (extreme favorite, mid-window, hold).
  - favorite_vol  the yellowing: gate out high realized vol — the storm-favorite
                that is about to flip is the shadow favorite cannot see by price.
  - albedo      the whitening: enter earlier (more time premium left) and demand
                a purer extremity — fewer, cleaner harvests.
  - nigredo     the blackening: same entries, but SIZE the calibration gap
                (Kelly-lite) instead of flat stake — integrate the asymmetry
                rather than ignore it. EV/$ is unchanged; the $-curve is not.

Run: python3 research/backtest_engine.py [5m|15m|4h]
"""
from __future__ import annotations
import sys

from explore2 import load_all, taker_fee, up_price, day_of

SEC = {"5m": 300, "15m": 900, "4h": 14400}
IS_OOS_SPLIT = "06-15"          # OOS = the choppy days that killed coagula, onward


def vol_threshold(recs, pct: float) -> float:
    """The vol_per_min value at the given percentile of the sample."""
    vols = sorted(r["vol_per_min"] for r in recs if r.get("vol_per_min"))
    if not vols:
        return float("inf")
    return vols[min(len(vols) - 1, int(pct * len(vols)))]


def simulate(recs, sec, frac_in, min_fav, max_fav, haircut, vol_cap=None):
    """One trade per qualifying window. Returns rows of (pnl_per_$, px, win, r)."""
    rows = []
    for r in recs:
        if not (r.get("tape") or r.get("price_track")):
            continue
        if vol_cap is not None and (r.get("vol_per_min") or 0.0) > vol_cap:
            continue
        t = r["window_start"] + int(frac_in * sec)
        p = up_price(r, t)
        if p is None:
            continue
        up_lead = p >= 0.5
        fav = p if up_lead else 1.0 - p          # favorite's own price (>=0.5)
        if not (min_fav <= fav <= max_fav):       # favorite's band, by PRICE
            continue
        px = min(fav + haircut, 0.99)             # cross the ask: the 2c haircut
        win = r["up_won"] if up_lead else (not r["up_won"])
        sh = 1.0 / px
        fee = taker_fee(sh, px)
        pnl = (sh - sh * px - fee) if win else (-sh * px - fee)   # per $1 staked
        rows.append((pnl, px, win, r))
    return rows


def split_stats(rows):
    """(ev, n, days+, win_rate, avg_px) over a row set."""
    if not rows:
        return None
    ev = sum(x[0] for x in rows) / len(rows)
    byday = {}
    for pnl, px, win, r in rows:
        byday.setdefault(day_of(r), []).append(pnl)
    daymeans = {d: sum(v) / len(v) for d, v in byday.items()}
    pos = sum(1 for m in daymeans.values() if m > 0) / len(daymeans)
    wr = sum(1 for _, _, w, _ in rows if w) / len(rows)
    avg_px = sum(px for _, px, _, _ in rows) / len(rows)
    return ev, len(rows), pos, wr, avg_px, daymeans


def report(name, rows, stake=25.0):
    alls = split_stats(rows)
    if alls is None:
        print(f"  {name:11s}: no qualifying windows")
        return
    ins = split_stats([x for x in rows if day_of(x[3]) < IS_OOS_SPLIT])
    oos = split_stats([x for x in rows if day_of(x[3]) >= IS_OOS_SPLIT])
    ev, n, pos, wr, avg_px, _ = alls
    total = ev * stake * n
    # tripwire: realized win-rate must beat the average price paid
    edge = "REAL" if wr > avg_px else "DEAD"
    print(f"  {name:11s}: EV/$ {ev:+.4f}  n={n:<4d} days+ {pos:.0%}  "
          f"win {wr:.3f} vs px {avg_px:.3f} [{edge}]  ${total:+.0f}@{stake:.0f}")
    for tag, s in (("  in-sample", ins), ("  oos     ", oos)):
        if s:
            e, nn, pp, w, ap, _ = s
            print(f"      {tag}: EV/$ {e:+.4f}  n={nn:<4d} days+ {pp:.0%}  "
                  f"win {w:.3f} vs px {ap:.3f}")


def nigredo_pnl(rows, base_stake=25.0):
    """Same entries as favorite, but stake scales with the calibration gap the
    favorite carries: more $ on the 0.86 favorite (fat upside, gap real), less
    on the 0.94 (thin upside). gap_proxy = (1 - fav): the cheaper the favorite,
    the more longshot premium the crowd is overpaying on the other side."""
    total = 0.0
    staked = 0.0
    for pnl, px, win, r in rows:
        fav = px            # ~= entry price
        size = base_stake * (1.0 + 2.0 * (0.95 - fav))   # 0.86->1.18x, 0.94->1.02x
        total += pnl * size
        staked += size
    return total, staked, len(rows)


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "15m"
    sec = SEC[interval]
    recs = load_all(interval)
    days = sorted({day_of(r) for r in recs})
    print(f"=== backtest favorite+variants  {interval}  "
          f"({len(recs)} windows, days {days[0]}..{days[-1]}) ===")
    print(f"    IS/OOS split at {IS_OOS_SPLIT}  |  realistic +2c ask haircut, "
          f"hold to settle, real taker fee\n")

    vhi = vol_threshold(recs, 0.66)      # drop top vol tercile
    HC = 0.02

    # frac_in is frac ELAPSED; favorite lives at ~0.60 (== ~6 min left in 15m).
    variants = [
        # name,        frac, min_fav, max_fav, vol_cap
        ("favorite",     0.60, 0.85,   0.95,   None),
        ("favorite_vol", 0.60, 0.85,   0.95,   vhi),    # vol-gated
        ("albedo",     0.55, 0.87,   0.97,   None),   # earlier, purer
        ("albedo+vol", 0.55, 0.87,   0.97,   vhi),
        ("wide",       0.60, 0.80,   0.95,   None),   # looser threshold
        ("wide+vol",   0.60, 0.80,   0.95,   vhi),
    ]
    for name, frac, lo, hi, vc in variants:
        rows = simulate(recs, sec, frac, lo, hi, HC, vol_cap=vc)
        report(name, rows)

    print("\n  -- nigredo (extremity-scaled stake on favorite's entries) --")
    base_rows = simulate(recs, sec, 0.60, 0.85, 0.95, HC)
    tot, staked, n = nigredo_pnl(base_rows)
    flat = sum(x[0] for x in base_rows) * 25.0
    print(f"     flat $25:  ${flat:+.0f}   |   nigredo-sized: ${tot:+.0f}  "
          f"(avg stake ${staked/n:.1f}, n={n})")

    print("\n  -- haircut robustness (favorite band) --")
    for hc in (0.0, 0.01, 0.02, 0.03):
        rows = simulate(recs, sec, 0.60, 0.85, 0.95, hc)
        s = split_stats(rows)
        if s:
            print(f"     +{hc*100:.0f}c: EV/$ {s[0]:+.4f}  n={s[1]}  days+ {s[2]:.0%}")


if __name__ == "__main__":
    main()
