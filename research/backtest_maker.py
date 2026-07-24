"""Backtest the MAKER-vs-TAKER hypothesis on favorite's 15m entries.

favorite buys the extreme favorite (price 0.85-0.95) ~6 min before a 15m window
closes, as a TAKER: it crosses the ask (+2c haircut). It also pays the crypto
taker fee C*FEE_RATE*p*(1-p) with FEE_RATE=0.07 — that fee IS charged on-chain
(re-verified 2026-06-27 from raw tx receipts; the 2026-06-26 "phantom fee=0" read
missed the pUSD fee leg). So the taker cost = +2c haircut + fee. The maker idea: post a bid at (fav - eps),
fill cheaper (no haircut) — but watch the adverse selection below.

The trap that killed "hermetic" (the delta-neutral market-maker): ADVERSE
SELECTION. A resting bid is hit mostly when a seller crosses DOWN through it,
i.e. exactly in the windows where the favorite is WEAKENING (the losing ones).
A guaranteed fill would over-state the maker; we must only fill when the tape
actually trades through our level AFTER the entry instant, which is the real
adverse-selection mechanism.

This file models that honestly:
  - TAKER (baseline): enter at fav+0.02, pay taker fee.   (= backtest_engine.py)
  - MAKER: post a bid at fav-eps (sweep eps in {0.005, 0.01, 0.02}). Fill ONLY
    if, AFTER the entry instant and before settle, the tape prints an execution
    at a favorite-side price <= our bid (the market traded through us). Fee = 0.
    Two fill models, from loose to strict:
      px-cross : ANY print (any side) at favorite-price <= bid fills us.
      sell-cross: only an aggressive SELL of the favorite at <= bid fills us
                  (the purest adverse-selection read: a seller hit our bid).
    If the window has no usable tape, it is NOT filled (and we count it as
    volume lost to DATA, distinct from volume lost to PRICE never reaching us).

Discipline (inherited from the falsified coagula strategy, see FINDINGS): replicate favorite's entry exactly, hold to settle,
ONE trade per window, split IS/OOS at 06-15, and read the tripwire (realized
win-rate must beat the average price paid) on the OOS tranche. The decisive
number is the maker fill win-rate vs the taker win-rate: a maker win-rate BELOW
the taker's on the SAME window universe is adverse selection caught red-handed.

DATA CAVEAT: tape is present for only a minority of windows (mostly 06-14..06-16),
so the maker arm is thin, especially in-sample. The report states the fill counts
explicitly; treat a thin OOS n as a weak signal, not a verdict.

Run: python3 research/backtest_maker.py [15m]
"""
from __future__ import annotations
import sys

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_favorite import SEC, IS_OOS_SPLIT

FRAC, LO, HI, HAIRCUT = 0.60, 0.85, 0.95, 0.02


# ------------------------------------------------------------- helpers ---
def fav_side_price(tr: dict, up_is_fav: bool) -> float:
    """Execution price expressed on the FAVORITE side. A Down trade at raw p is
    an Up price of 1-p; we then flip to the favorite's own side."""
    up_px = tr["price"] if tr["outcome"] == "Up" else 1.0 - tr["price"]
    return up_px if up_is_fav else 1.0 - up_px


def maker_fills(r: dict, entry_t: int, up_is_fav: bool, bid: float,
                model: str) -> bool:
    """Would a resting maker bid at `bid` (favorite side) get filled by the tape
    AFTER entry_t and before/at settle? This is the adverse-selection model: we
    are hit only when the favorite trades DOWN through our bid.

    model='px'   : any print at favorite-price <= bid fills us (loose).
    model='sell' : only an aggressive SELL of the favorite at <= bid (strict).
    """
    we = r.get("window_end") or (entry_t + 10 ** 9)
    for tr in r.get("tape", []):                 # descending by t
        t = tr["t"]
        if t <= entry_t or t > we:               # only post-entry, in-window
            continue
        fp = fav_side_price(tr, up_is_fav)
        if fp > bid:
            continue
        if model == "px":
            return True
        # 'sell': the aggressor must be SELLING the favorite into our bid.
        # A favorite SELL is: SELL of the favorite outcome, OR BUY of the
        # OTHER outcome (same economic direction = pushing the favorite down).
        fav_out = "Up" if up_is_fav else "Down"
        other_out = "Down" if up_is_fav else "Up"
        is_fav_sell = ((tr["outcome"] == fav_out and tr["side"] == "SELL") or
                       (tr["outcome"] == other_out and tr["side"] == "BUY"))
        if is_fav_sell:
            return True
    return False


# --------------------------------------------------------- simulation ---
def simulate(recs, sec, arm, eps=0.01, fill_model="px"):
    """One trade per qualifying window (favorite's band). Returns
    (rows, n_qualify, n_no_tape, n_no_fill).
      rows : list of (pnl_per_$, px, win, r) for the trades that HAPPENED
      n_qualify : windows favorite would enter at all
      n_no_tape : qualifying windows the maker could not test for lack of tape
      n_no_fill : qualifying windows with tape where the maker bid never filled
    arm in {'taker','maker'}.
    """
    rows = []
    n_qualify = n_no_tape = n_no_fill = 0
    elapsed = int(FRAC * sec)
    for r in recs:
        if not (r.get("tape") or r.get("price_track")):
            continue
        t = r["window_start"] + elapsed
        p = up_price(r, t)
        if p is None:
            continue
        up_is_fav = p >= 0.5
        fav = p if up_is_fav else 1.0 - p
        if not (LO <= fav <= HI):
            continue
        n_qualify += 1
        win = r["up_won"] if up_is_fav else (not r["up_won"])

        if arm == "taker":
            px = min(fav + HAIRCUT, 0.99)
            sh = 1.0 / px
            fee = taker_fee(sh, px)
            pnl = (sh - sh * px - fee) if win else (-sh * px - fee)
            rows.append((pnl, px, win, r))
            continue

        # maker arm: needs tape to model the fill at all
        if not r.get("tape"):
            n_no_tape += 1
            continue
        bid = round(fav - eps, 4)
        if not maker_fills(r, t, up_is_fav, bid, fill_model):
            n_no_fill += 1
            continue
        px = bid                              # filled at our better price
        sh = 1.0 / px
        # makers pay NO fee
        pnl = (sh - sh * px) if win else (-sh * px)
        rows.append((pnl, px, win, r))
    return rows, n_qualify, n_no_tape, n_no_fill


# ------------------------------------------------------------- report ---
def stats(rows):
    if not rows:
        return None
    ev = sum(x[0] for x in rows) / len(rows)
    wr = sum(1 for _, _, w, _ in rows if w) / len(rows)
    apx = sum(px for _, px, _, _ in rows) / len(rows)
    days = sorted({day_of(x[3]) for x in rows})
    byday = {}
    for pnl, _, _, r in rows:
        byday.setdefault(day_of(r), []).append(pnl)
    pos = sum(1 for v in byday.values() if sum(v) / len(v) > 0) / len(byday)
    return ev, len(rows), pos, wr, apx, len(days)


def split(rows):
    ins = [x for x in rows if day_of(x[3]) < IS_OOS_SPLIT]
    oos = [x for x in rows if day_of(x[3]) >= IS_OOS_SPLIT]
    return ins, oos


def line(tag, rows):
    s = stats(rows)
    if s is None:
        print(f"    {tag:26s}: (no fills)")
        return
    ev, n, pos, wr, apx, nd = s
    edge = "REAL" if wr > apx else "DEAD"
    print(f"    {tag:26s}: EV/$ {ev:+.4f}  n={n:<3d} days+ {pos:.0%}  "
          f"win {wr:.3f} vs px {apx:.3f} [{edge}]")


def report(name, rows, n_qualify, n_no_tape, n_no_fill):
    ins, oos = split(rows)
    filled = len(rows)
    print(f"\n  {name}")
    print(f"    universe: {n_qualify} qualifying windows | filled {filled} | "
          f"no-tape (data) {n_no_tape} | no-fill (price) {n_no_fill}")
    line("ALL", rows)
    line("IN-SAMPLE", ins)
    line("OUT-OF-SAMPLE", oos)


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "15m"
    sec = SEC[interval]
    recs = load_all(interval)
    days = sorted({day_of(r) for r in recs})
    n_tape = sum(1 for r in recs if r.get("tape"))
    print(f"=== backtest MAKER vs TAKER  {interval}  "
          f"({len(recs)} windows, {days[0]}..{days[-1]}, {n_tape} with tape) ===")
    print(f"    favorite band fav {LO}-{HI}, enter {FRAC:.0%}, IS/OOS @ "
          f"{IS_OOS_SPLIT}, hold to settle")
    print("    TAKER pays fav+0.02 + taker fee | MAKER bids fav-eps, no fee, "
          "fills only on a tape cross-through")

    # baseline taker (this is favorite exactly)
    t_rows, t_q, _, _ = simulate(recs, sec, "taker")
    report("TAKER  (baseline = favorite)", t_rows, t_q, 0, 0)

    # maker sweeps over eps and fill model
    for model in ("px", "sell"):
        mname = ("px-cross (any print through bid)" if model == "px"
                 else "sell-cross (aggressive favorite SELL only)")
        print(f"\n  ===== MAKER fill model: {mname} =====")
        for eps in (0.005, 0.01, 0.02):
            rows, q, nt, nf = simulate(recs, sec, "maker", eps=eps,
                                       fill_model=model)
            report(f"MAKER  bid = fav-{eps:.3f}", rows, q, nt, nf)

    # head-to-head on the SAME windows: where the maker actually filled (px,
    # eps=0.01), what would the taker have made on those exact windows? This
    # isolates adverse selection from window-mix differences.
    print("\n  ===== APPLES-TO-APPLES on maker-filled windows "
          "(px model, eps=0.01) =====")
    m_rows, _, _, _ = simulate(recs, sec, "maker", eps=0.01, fill_model="px")
    filled_ids = {id(x[3]) for x in m_rows}
    t_on_filled = [x for x in t_rows if id(x[3]) in filled_ids]
    print("    On the windows where the maker bid filled:")
    line("  TAKER (same windows)", t_on_filled)
    line("  MAKER (same windows)", m_rows)
    ins_m, oos_m = split(m_rows)
    ins_t = [x for x in t_on_filled if day_of(x[3]) < IS_OOS_SPLIT]
    oos_t = [x for x in t_on_filled if day_of(x[3]) >= IS_OOS_SPLIT]
    print("    OOS only (the honest tranche):")
    line("  TAKER (OOS, same windows)", oos_t)
    line("  MAKER (OOS, same windows)", oos_m)

    # ---- the decisive data check: can we even SEE adverse selection? ----
    # Adverse selection lives ENTIRELY in the losing windows (the maker is hit
    # most when the favorite is sliding to a loss). If no losing favorite entry
    # has tape, the experiment is structurally blind to its own central risk,
    # and a 100% maker win-rate is a coverage artifact, not evidence.
    print("\n  ===== ADVERSE-SELECTION VISIBILITY (the verdict gate) =====")
    win_fill = win_no = loss_fill = loss_no = 0
    for r in recs:
        if not r.get("tape"):
            continue
        t = r["window_start"] + int(FRAC * sec)
        p = up_price(r, t)
        if p is None:
            continue
        up_is_fav = p >= 0.5
        fav = p if up_is_fav else 1.0 - p
        if not (LO <= fav <= HI):
            continue
        win = r["up_won"] if up_is_fav else (not r["up_won"])
        filled = maker_fills(r, t, up_is_fav, round(fav - 0.01, 4), "px")
        if win and filled:
            win_fill += 1
        elif win:
            win_no += 1
        elif filled:
            loss_fill += 1
        else:
            loss_no += 1
    n_loss = loss_fill + loss_no
    print(f"    qualifying windows WITH tape, by outcome x maker-fill:")
    print(f"      winning : filled {win_fill:3d}  not-filled {win_no:3d}")
    print(f"      LOSING  : filled {loss_fill:3d}  not-filled {loss_no:3d}")
    if n_loss == 0:
        print("    >>> ZERO losing windows have tape. Adverse selection cannot "
              "be measured.")
        print("    >>> The maker's apparent win-rate is a DATA artifact, not an "
              "edge. INCONCLUSIVE.")
    else:
        las = loss_fill / n_loss
        print(f"    >>> maker fill-rate on LOSING windows = {las:.2f} "
              f"(vs {win_fill/(win_fill+win_no):.2f} on winners) — "
              f"adverse selection is {'PRESENT' if las > win_fill/(win_fill+win_no) else 'not evident'}")


if __name__ == "__main__":
    main()
