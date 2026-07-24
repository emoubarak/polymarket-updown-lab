"""Reversal-exit sweep: HOLD-to-settle vs CUT the favorite when it reverts down.

The deployed brains (zlead family) buy an extreme favorite (0.85-0.95) mid-window
and HOLD to the oracle settlement. The user's question: when the favorite REVERSES
(price falls back toward the tie), is there a floor price below which CUTTING the
position beats holding to settle?

This is the DOWN-side mirror of backtest_exit.py (which tests selling a CLIMBING
favorite at 0.96-0.99). Entry is replicated exactly from the favorite/zlead control
(fixed frac, fav band, +2c ask haircut, real taker entry fee). Then we walk the
favorite's mid path AFTER entry; if it ever falls to/through a STOP threshold, we
CUT — a taker sell into the bid (fill = min(stop, mid) - 1c so a gap-down fills at
the worse price; pays the real taker fee on the way out, the double-fee the churn
autopsy died on). Else hold to settle, byte-identical to the control.

THE ECONOMICS. Cutting at `stop` nets ~stop-1c-fee per share. Holding nets the
realized win-rate of favorites that fell that far. So a reversal stop pays IFF

    recovery_rate(touched stop)  <  stop  -  exit_cost

i.e. the book OVERprices the cratering favorite. If instead cratering favorites
recover MORE often than `stop` implies (the price-reversion-scalp finding: the CLOB
overshoots and mean-reverts), the stop sells the dip and only caps winners.

Decisive diagnostic per stop: of the windows that hit it, the RECOVERY RATE
(= would-have-won at settle). Compare it to the stop price.

Run: python3 research/backtest_revexit.py                 # all cells, frac 0.60
     python3 research/backtest_revexit.py --frac 0.40     # earlier entry (live-like)
     python3 research/backtest_revexit.py 15m btc         # one cell
"""
from __future__ import annotations
import sys

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_favorite import SEC, IS_OOS_SPLIT      # canonical: don't redefine

MIN_FAV, MAX_FAV = 0.85, 0.95
HAIRCUT = 0.02            # entry: cross the ask
EXIT_HC = 0.01           # cut: hit the bid below mid
STOPS = (0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50)

CELLS = [("btc", "15m"), ("eth", "15m"), ("sol", "15m"), ("xrp", "15m"),
         ("doge", "15m"), ("bnb", "15m"),
         ("btc", "5m"), ("eth", "5m"), ("sol", "5m"), ("xrp", "5m")]


def entries(recs, sec, frac):
    """Replicate the favorite/zlead band entry; return one dict per qualifying window
    with the hold-to-settle pnl AND the favorite's forward mid path for the cut walk."""
    out = []
    for r in recs:
        track = r.get("price_track")
        if not (r.get("tape") or track):
            continue
        t = r["window_start"] + int(frac * sec)
        p = up_price(r, t)
        if p is None:
            continue
        up_lead = p >= 0.5
        fav = p if up_lead else 1.0 - p
        if not (MIN_FAV <= fav <= MAX_FAV):
            continue
        px = min(fav + HAIRCUT, 0.99)          # entry executable price
        win = r["up_won"] if up_lead else (not r["up_won"])
        sh = 1.0 / px
        fee_in = taker_fee(sh, px)
        hold = (sh - sh * px - fee_in) if win else (-sh * px - fee_in)
        # favorite mid path strictly AFTER the entry instant
        path = [(ts, (pu if up_lead else 1.0 - pu))
                for ts, pu in (track or []) if ts > t]
        out.append(dict(px=px, sh=sh, fee_in=fee_in, win=win, hold=hold, path=path, r=r))
    return out


def cut_pnl(e, stop):
    """(pnl, triggered, would_have_won, fill). Cut = taker sell into the bid when
    the favorite mid first falls to/through `stop`. fill = min(stop, mid)-1c so a
    gap-down fills at the worse price (honest slippage), never above the stop."""
    if e["px"] <= stop:                        # entered already at/below stop -> hold
        return e["hold"], False, None, None
    sh = e["sh"]
    for _ts, mid in e["path"]:
        if mid <= stop:
            fill = max(0.0, min(stop, mid) - EXIT_HC)
            fee_out = taker_fee(sh, fill)
            return sh * fill - sh * e["px"] - e["fee_in"] - fee_out, True, e["win"], fill
    return e["hold"], False, None, None        # never touched -> hold


def ev_split(es, pnl_fn):
    """(ev_all, ev_is, ev_oos, n) for a pnl(e)->float over the entry set."""
    rows = [(pnl_fn(e), day_of(e["r"])) for e in es]
    def ev(sub): return (sum(p for p, _ in sub) / len(sub)) if sub else None
    return (ev(rows),
            ev([x for x in rows if x[1] < IS_OOS_SPLIT]),
            ev([x for x in rows if x[1] >= IS_OOS_SPLIT]),
            len(rows))


def fmt(v):
    return f"{v:+.4f}" if v is not None else "   -   "


def run_cell(coin, frame, frac):
    prefix = frame if coin == "btc" else f"{coin}_{frame}"
    recs = load_all(prefix)
    sec = SEC[frame]
    es = entries(recs, sec, frac)
    if not es:
        print(f"\n### {coin}-{frame}: no qualifying windows\n")
        return
    days = sorted({day_of(e["r"]) for e in es})
    base_all, base_is, base_oos, n = ev_split(es, lambda e: e["hold"])
    base_wr = sum(1 for e in es if e["win"]) / n
    print(f"\n### {coin}-{frame}  (n={n} entries, {days[0]}..{days[-1]}, "
          f"avg entry px {sum(e['px'] for e in es)/n:.3f}, hold win {base_wr:.3f})")
    print(f"  {'rule':>14}   {'EV/$ all':>9}  {'IS':>8}  {'OOS':>8}   "
          f"trig%  recov  avg-fill  {'dEV':>8}  verdict")
    print(f"  {'HOLD (ctrl)':>14}   {fmt(base_all)}  {fmt(base_is)}  "
          f"{fmt(base_oos)}     --     --     --        --     --")
    for stop in STOPS:
        a, i, o, _ = ev_split(es, lambda e: cut_pnl(e, stop)[0])
        touched = [(cut_pnl(e, stop), e) for e in es if cut_pnl(e, stop)[1]]
        tr = len(touched) / n
        rec = (sum(1 for (_p, _t, w, _f), _e in touched if w) / len(touched)) if touched else None
        fill = (sum(f for (_p, _t, _w, f), _e in touched) / len(touched)) if touched else None
        dlt = a - base_all
        # the gap-through: you mean to exit AT `stop` but the price that triggers the
        # stop is the same move resolving the window -> you actually fill at avg-fill,
        # often FAR below stop. So the cut only wins if dEV>0, NOT if recov<stop.
        verdict = ("CUT>hold" if dlt > 0 else "hold>CUT") if rec is not None else ""
        rec_s = f"{rec:.3f}" if rec is not None else "  -  "
        fill_s = f"{fill:.3f}" if fill is not None else "  -  "
        mark = " <OOS" if (o is not None and base_oos is not None and o > base_oos) else ""
        print(f"  {'cut @'+format(stop,'.2f'):>14}   {fmt(a)}  {fmt(i)}  {fmt(o)}   "
              f"{tr:5.1%}  {rec_s}  {fill_s}   (d{dlt:+.4f}) {verdict}{mark}")


def main():
    frac = 0.60
    args = [a for a in sys.argv[1:]]
    if "--frac" in args:
        k = args.index("--frac")
        frac = float(args[k + 1])
        del args[k:k + 2]
    print("=== REVERSAL-EXIT SWEEP — hold-to-settle vs cut-the-favorite-on-reversal ===")
    print(f"    entry = zlead band control (frac {frac:.2f}, fav {MIN_FAV}-{MAX_FAV}, "
          f"+{HAIRCUT*100:.0f}c haircut, taker fee) | IS/OOS @ {IS_OOS_SPLIT}")
    print("    cut = taker sell into the bid (fill min(stop,mid)-1c, taker fee out).")
    print("    recov = win-rate AT SETTLE among windows that hit the stop. "
          "CUT>hold iff recov < stop-cost.")
    if len(args) >= 2:                          # one cell: accept <frame> <coin> or <coin> <frame>
        a, b = args[0], args[1]
        coin, frame = (a, b) if b in SEC else (b, a)
        run_cell(coin, frame, frac)
        return
    for coin, frame in CELLS:
        run_cell(coin, frame, frac)


if __name__ == "__main__":
    main()
