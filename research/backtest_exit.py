"""Early-exit sweep: HOLD-to-settle vs SELL-the-favorite at a price threshold.

The deployed brains (favorite/favorite_leadz) enter an extreme favorite mid-window and
HOLD to the oracle settlement. The user's hypothesis: instead of waiting for the
echeance, rest a SELL once the favorite climbs to a threshold (0.96..0.99) and
lock the gain early. Two exit styles, because the OLD churn autopsy (FINDINGS.md
"cout du churn") blamed the DOUBLE TAKER FEE, not the signal:

  maker : rest a limit sell AT the threshold; fills when the favorite mid reaches
          it; pays ZERO fee on the way out (the maker never pays).
  taker : cross the book to exit; fills at threshold - 1c; pays the real taker fee
          on the way out (the double-fee the old churn died on).

Entry is replicated EXACTLY from backtest_favorite.simulate (frac 0.60, favorite
price band, +2c ask haircut, real taker entry fee). Windows whose favorite never
reaches the threshold FALL BACK to hold-to-settle, byte-identical to the control,
so the strategy only ever differs on the windows it actually touches.

Decisive diagnostic printed per threshold: of the windows that DID touch it, how
many still WON at settlement (settle-win). 1 - settle-win = the crater-after-touch
rate. Capping every winner by 1c only pays if that crater rate is materially > 0.

Run: python3 research/backtest_exit.py            # all four: btc/eth x 5m/15m
     python3 research/backtest_exit.py 5m btc      # one cell
"""
from __future__ import annotations
import sys

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_favorite import SEC, IS_OOS_SPLIT      # canonical: don't redefine

FRAC_IN = 0.60
MIN_FAV, MAX_FAV = 0.85, 0.95
HAIRCUT = 0.02            # entry: cross the ask
EXIT_HC = 0.01           # taker exit: hit the bid below mid
THRESHOLDS = (0.96, 0.97, 0.98, 0.99)


def entries(recs, sec):
    """Replicate favorite's entries; return one dict per qualifying window with the
    hold-to-settle pnl AND the favorite's forward price path for the exit walk."""
    out = []
    for r in recs:
        track = r.get("price_track")
        if not (r.get("tape") or track):
            continue
        t = r["window_start"] + int(FRAC_IN * sec)
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
        out.append(dict(px=px, sh=sh, fee_in=fee_in, win=win, hold=hold,
                        path=path, r=r))
    return out


def exit_pnl(e, th, maker):
    """Pnl for one window under the exit rule. Returns (pnl, triggered)."""
    if e["px"] >= th:                          # no room to climb -> just hold
        return e["hold"], False
    for _ts, mid in e["path"]:
        if mid >= th:
            sh = e["sh"]
            if maker:                          # fill at the limit, zero fee
                return sh * th - sh * e["px"] - e["fee_in"], True
            fill = th - EXIT_HC                 # cross to the bid
            fee_out = taker_fee(sh, fill)
            return sh * fill - sh * e["px"] - e["fee_in"] - fee_out, True
    return e["hold"], False                    # never touched -> hold


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


def run_cell(prefix, sec):
    recs = load_all(prefix)
    es = entries(recs, sec)
    if not es:
        print(f"\n### {prefix}: no qualifying windows\n")
        return
    days = sorted({day_of(e["r"]) for e in es})
    base_all, base_is, base_oos, n = ev_split(es, lambda e: e["hold"])
    print(f"\n### {prefix}  (n={n} entries, {days[0]}..{days[-1]}, "
          f"avg entry px {sum(e['px'] for e in es)/n:.3f})")
    print(f"  {'rule':>16}   {'EV/$ all':>9}  {'IS':>8}  {'OOS':>8}   "
          f"touch%  settle-win(touched)")
    print(f"  {'HOLD (control)':>16}   {fmt(base_all)}  {fmt(base_is)}  "
          f"{fmt(base_oos)}      --        --")
    for maker in (True, False):
        style = "maker" if maker else "taker"
        for th in THRESHOLDS:
            a, i, o, _ = ev_split(es, lambda e: exit_pnl(e, th, maker)[0])
            touched = [e for e in es if exit_pnl(e, th, maker)[1]]
            tr = len(touched) / n
            sw = (sum(1 for e in touched if e["win"]) / len(touched)
                  if touched else None)
            dlt = a - base_all
            mark = " <" if (o is not None and base_oos is not None
                            and o > base_oos) else ""
            sw_s = f"{sw:.3f}" if sw is not None else "  -  "
            print(f"  {style+' @'+format(th,'.2f'):>16}   {fmt(a)}  {fmt(i)}  "
                  f"{fmt(o)}   {tr:5.1%}     {sw_s}   (d{dlt:+.4f}){mark}")


def main():
    if len(sys.argv) > 2:
        frame, asset = sys.argv[1], sys.argv[2]
        prefix = frame if asset == "btc" else f"{asset}_{frame}"
        run_cell(prefix, SEC[frame])
        return
    print("=== EARLY-EXIT SWEEP — hold-to-settle vs sell-favorite-at-threshold ===")
    print(f"    entry = favorite control (frac {FRAC_IN}, fav {MIN_FAV}-{MAX_FAV}, "
          f"+{HAIRCUT*100:.0f}c haircut, taker fee) | IS/OOS @ {IS_OOS_SPLIT}")
    print("    '<' marks an OOS EV above the HOLD control. dX = EV/$ delta vs HOLD.")
    for asset in ("btc", "eth"):
        for frame in ("5m", "15m"):
            prefix = frame if asset == "btc" else f"{asset}_{frame}"
            run_cell(prefix, SEC[frame])


if __name__ == "__main__":
    main()
