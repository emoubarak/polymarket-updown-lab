"""Validate the conditioned-favorite edge found in explore2.

Hypothesis: with little time left, an already-extreme favorite is UNDERpriced
(realized win rate > price), except in high volatility. Equivalent statement:
the cheap longshot side is overpriced. We try to break it:

  - realistic execution: pay a haircut over the last trade (you cross the ask
    and eat slippage) — sweep 0 / 1c / 2c;
  - entry-time sweep (does it need a specific moment?);
  - the vol filter from exp4 combined with the extremity filter;
  - day-by-day table (regime-noise filter);
  - 15m sanity check;
  - a realistic fixed-$ P&L with one trade per qualifying window.

Run: python3 research/validate_fav.py [5m|15m]
"""
from __future__ import annotations
import datetime as dt
import sys

from explore2 import load_all, taker_fee, up_price, day_of


def follow_pnl(r, p_up, haircut):
    """Buy the FAVORITE side (the one trading >0.5) at its price+haircut,
    hold to settle, pay one taker fee. p_up is the raw Up price at decision
    time. Returns net pnl per $1 staked."""
    up_lead = (p_up >= 0.5)
    price = p_up if up_lead else 1.0 - p_up   # favorite's own price (>=0.5)
    px = min(price + haircut, 0.99)
    wins = r["up_won"] if up_lead else (not r["up_won"])
    sh = 1.0 / px
    fee = taker_fee(sh, px)
    pnl = (sh - sh * px - fee) if wins else (-sh * px - fee)
    return pnl  # per $1 staked


def run(recs, sec, frac_in, thr, haircut, vol_hi=None, label=""):
    rows = []
    for r in recs:
        if not (r.get("tape") or r.get("price_track")):
            continue
        if vol_hi is not None and (r.get("vol_per_min") or 1) > vol_hi:
            continue
        t = r["window_start"] + int(frac_in * sec)
        p = up_price(r, t)
        if p is None:
            continue
        rich = max(p, 1 - p)
        if rich < thr:
            continue
        rows.append((follow_pnl(r, p, haircut), r))
    if not rows:
        return None
    ev = sum(x[0] for x in rows) / len(rows)
    byday = {}
    for pnl, r in rows:
        byday.setdefault(day_of(r), []).append(pnl)
    daymeans = {d: sum(v) / len(v) for d, v in byday.items()}
    pos = sum(1 for m in daymeans.values() if m > 0) / len(daymeans)
    return ev, len(rows), pos, daymeans


def header(s):
    print(f"\n{'='*4} {s} {'='*4}")


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "5m"
    sec = 300 if interval == "5m" else 900 if interval == "15m" else 3600
    recs = load_all(interval)
    print(f"favorite-edge validation — {interval}, {len(recs)} windows")

    header("A. execution haircut sweep (frac_in=0.70, thr=0.75, no vol filter)")
    for hc in (0.0, 0.01, 0.02, 0.03):
        out = run(recs, sec, 0.70, 0.75, hc)
        if out:
            ev, n, pos, _ = out
            print(f"  haircut {hc:.2f}: EV/$ {ev:+.4f}  n={n}  days+ {pos:.0%}")

    header("B. entry-time sweep (thr=0.75, haircut=0.01)")
    for fr in (0.40, 0.55, 0.70, 0.80, 0.90):
        out = run(recs, sec, fr, 0.75, 0.01)
        if out:
            ev, n, pos, _ = out
            mins = (1 - fr) * sec / 60
            print(f"  enter {fr:.0%} ({mins:.1f}min left): EV/$ {ev:+.4f}  "
                  f"n={n}  days+ {pos:.0%}")

    header("C. extremity threshold sweep (frac_in=0.70, haircut=0.01)")
    for thr in (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90):
        out = run(recs, sec, 0.70, thr, 0.01)
        if out:
            ev, n, pos, _ = out
            print(f"  thr {thr:.2f}: EV/$ {ev:+.4f}  n={n}  days+ {pos:.0%}")

    # vol terciles from the data
    vols = sorted(r["vol_per_min"] for r in recs if r.get("vol_per_min"))
    v33 = vols[len(vols) // 3]
    v66 = vols[2 * len(vols) // 3]
    header(f"D. + vol filter (drop vol>{v66:.5f} = high tercile; "
           f"frac=0.70 thr=0.75 hc=0.01)")
    out = run(recs, sec, 0.70, 0.75, 0.01, vol_hi=v66)
    if out:
        ev, n, pos, dm = out
        print(f"  EV/$ {ev:+.4f}  n={n}  days+ {pos:.0%}")
        print("  day-by-day EV/$:")
        for d in sorted(dm):
            print(f"    {d}: {dm[d]:+.4f}")

    header(f"E. tighter: vol<{v33:.5f} (low only), thr=0.80, frac=0.70 hc=0.02")
    out = run(recs, sec, 0.70, 0.80, 0.02, vol_hi=v33)
    if out:
        ev, n, pos, dm = out
        print(f"  EV/$ {ev:+.4f}  n={n}  days+ {pos:.0%}")

    header("F. realistic fixed-$ P&L (stake $20/qualifying window, "
           "frac=0.70 thr=0.75 hc=0.02, drop high-vol)")
    out = run(recs, sec, 0.70, 0.75, 0.02, vol_hi=v66)
    if out:
        ev, n, pos, dm = out
        days = len(dm)
        total = ev * 20 * n
        print(f"  {n} trades over {days} days  ->  net ${total:+.2f}  "
              f"(${total/days:+.2f}/day)  EV/$ {ev:+.4f}  days+ {pos:.0%}")


if __name__ == "__main__":
    main()
