"""Backtest the POLL-RATE optimization on favorite's entry band.

Question (2026-06-22): the live runners poll every 10s. Inside the entry band
[0.35,0.45] frac-REMAINING (== [0.55,0.65] elapsed) the engine samples the market
at that cadence and enters at the FIRST qualifying tick, once per window. Two
nuances were floated:
  (1) adaptive poll  — poll slow OUTSIDE the band (free: favorite no-ops there) and
                       faster INSIDE it;
  (2) 5m specifically — its band is ~30s wide -> only ~3 ticks at 10s -> try 5s.

The "near tau->0" half of (1) is NOT testable and NOT relevant: favorite never acts
near settlement (it trades at 5-7 min left), so there is no entry there to sharpen.
What IS testable is whether a finer in-band poll (a) catches windows a coarse poll
MISSES (gate true only briefly between ticks), or (b) lands a better entry PRICE.

Faithful model (the discipline from the falsified coagula strategy, FINDINGS.md): replicate the live FIRST-
QUALIFYING-TICK entry, sweep the poll step, hold to settle, real taker fee + 2c ask
haircut, ONE trade per window, split IS/OOS at 06-15, read the OOS tranche only.
Resolution of the inputs bounds what a finer poll can even change:
  - favorite PRICE  = the tape (sub-second) -> finer poll CAN move the fill;
  - spot / LEAD     = 1m klines            -> a finer-than-1min poll canNOT move
                                              the lead gate, only when it is first
                                              SEEN after a minute boundary.

Run: python3 research/backtest_poll.py [5m|15m]
"""
from __future__ import annotations
import sys

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_lead import fetch_klines, spot_at
from backtest_favorite import IS_OOS_SPLIT, SEC, split_stats

# enter_lo/enter_hi = 0.35/0.45 frac-REMAINING  ==  0.55/0.65 frac-ELAPSED
BAND_LO, BAND_HI = 0.55, 0.65
HC = 0.02

# prod configs (main.py STRATEGY_FAMILY). favorite_vollead == favorite_conviction's gates (sizing aside).
CONFIGS = {
    "favorite (price only)": dict(min_fav=0.85, max_fav=0.95, vol_cap=None,
                                min_lead_bps=0.0),
    "favorite_vollead (vol+6bps)": dict(min_fav=0.85, max_fav=0.95, vol_cap=0.00056,
                                  min_lead_bps=6.0),
}
# poll steps in seconds; None == continuous (re-evaluate at every tape print).
POLLS = [None, 1, 5, 10, 30, 60, 90]


def decision_times(r, t0, t1, poll_s):
    """Instants the engine would evaluate the gate inside [t0, t1]. Continuous ==
    every instant the OBSERVABLE price could change: every tape print AND every
    price_track point (1m midpoint) in the band, plus the band open. This is the
    true upper bound a faster poll could ever reach."""
    if poll_s is None:
        ts = {t0}
        for tr in r.get("tape", []):
            if t0 <= tr["t"] <= t1:
                ts.add(tr["t"])
        for tsec, _p in (r.get("price_track") or []):
            if t0 <= tsec <= t1:
                ts.add(tsec)
        return sorted(ts)
    return list(range(int(t0), int(t1) + 1, poll_s))


def gate(r, idx, t, cfg):
    """(fav_price, up_lead) if this instant qualifies for entry, else None."""
    p = up_price(r, t)
    if p is None:
        return None
    up_lead = p >= 0.5
    fav = p if up_lead else 1.0 - p
    if not (cfg["min_fav"] <= fav <= cfg["max_fav"]):
        return None
    if cfg["vol_cap"] is not None and (r.get("vol_per_min") or 0.0) > cfg["vol_cap"]:
        return None                                 # per-window: poll-invariant
    if cfg["min_lead_bps"]:
        op = r.get("binance_open")
        spot = spot_at(idx, t)
        if op is None or spot is None:
            return None
        conv = (spot - op) if up_lead else (op - spot)
        if conv / op * 1e4 < cfg["min_lead_bps"]:
            return None
    return fav, up_lead


def simulate(recs, idx, sec, poll_s, cfg):
    """First qualifying tick in the band -> enter, hold to settle. Rows carry the
    entry instant (frac-elapsed) so we can measure how poll granularity moves it."""
    rows = []
    for r in recs:
        if not (r.get("tape") or r.get("price_track")):
            continue
        ws = r["window_start"]
        t0, t1 = ws + BAND_LO * sec, ws + BAND_HI * sec
        for t in decision_times(r, t0, t1, poll_s):
            g = gate(r, idx, t, cfg)
            if g is None:
                continue
            fav, up_lead = g
            px = min(fav + HC, 0.99)
            win = r["up_won"] if up_lead else (not r["up_won"])
            sh = 1.0 / px
            fee = taker_fee(sh, px)
            pnl = (sh - sh * px - fee) if win else (-sh * px - fee)
            rows.append((pnl, px, win, r, (t - ws) / sec))
            break                                   # one trade per window
    return rows


def line(tag, rows, stake=25.0):
    a = split_stats([row[:4] for row in rows])
    if a is None:
        print(f"    {tag:>10}: no qualifying windows")
        return None
    ev, n, pos, wr, ap, _ = a
    oos = split_stats([row[:4] for row in rows if day_of(row[3]) >= IS_OOS_SPLIT])
    avg_t = sum(row[4] for row in rows) / len(rows)
    o = (f"OOS {oos[0]:+.4f} n={oos[1]:<3d} win {oos[3]:.3f}/px {oos[4]:.3f} "
         f"[{'REAL' if oos[3] > oos[4] else 'DEAD'}]") if oos else "OOS n/a"
    print(f"    {tag:>10}: EV/$ {ev:+.4f} n={n:<4d} win {wr:.3f} px {ap:.3f} "
          f"@{avg_t*100:.1f}%win ${ev*stake*n:+.0f} | {o}")
    return rows


def diverge(base, test):
    """How a coarse poll (test) differs from continuous (base), window-by-window:
    windows missed, and the mean entry-price penalty where both entered."""
    bmap = {row[3]["slug"]: row for row in base}
    tmap = {row[3]["slug"]: row for row in test}
    missed = [s for s in bmap if s not in tmap]
    both = [s for s in bmap if s in tmap]
    dpx = [tmap[s][1] - bmap[s][1] for s in both]     # +ve == coarse pays MORE
    worse = sum(1 for d in dpx if d > 1e-9)
    better = sum(1 for d in dpx if d < -1e-9)
    mean_dpx = sum(dpx) / len(dpx) if dpx else 0.0
    print(f"        vs continuous: missed {len(missed)}/{len(bmap)} windows | "
          f"price moved on {worse+better}/{len(both)} (worse {worse}/better {better}), "
          f"mean Δpx {mean_dpx:+.5f}")


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "15m"
    sec = SEC[interval]
    allrecs = load_all(interval)
    lo_w = min(r["window_start"] for r in allrecs)
    hi_w = max(r["window_end"] for r in allrecs)
    idx = fetch_klines(lo_w - 120, hi_w + 120)
    # The poll rate can ONLY matter where the price moves between ticks. 94% of 15m
    # windows have NO tape -> price = 1m price_track -> any sub-1min poll is
    # definitionally identical. Restrict the sweep to TAPE-covered windows: the only
    # subset with sub-second resolution, hence the strongest chance of a poll effect.
    # If it is null even here, "poll rate is irrelevant" holds a fortiori.
    recs = [r for r in allrecs if r.get("tape")]
    days = sorted({day_of(r) for r in recs})
    oos = sum(1 for r in recs if day_of(r) >= IS_OOS_SPLIT)
    print(f"=== POLL-RATE sweep  {interval}  ===")
    print(f"    {len(recs)}/{len(allrecs)} windows have a real tape (sub-second); "
          f"the rest are 1m price_track -> poll-invariant below 1min.")
    print(f"    tape windows span {days[0]}..{days[-1]} ({oos} OOS).")
    print(f"    band [{BAND_LO:.2f},{BAND_HI:.2f}] elapsed = "
          f"{(BAND_HI-BAND_LO)*sec:.0f}s wide | first qualifying tick, +2c haircut, "
          f"hold to settle, real fee | IS/OOS @ {IS_OOS_SPLIT}\n")
    for name, cfg in CONFIGS.items():
        print(f"  [{name}]  (n at 10s vs continuous = the 'missed opportunity' test)")
        base = None
        for poll in POLLS:
            tag = "cont" if poll is None else f"{poll}s"
            rows = simulate(recs, idx, sec, poll, cfg)
            line(tag, rows)
            if poll is None:
                base = rows
            elif poll in (10, 30, 60) and base is not None:
                diverge(base, rows)
        print()


if __name__ == "__main__":
    main()
