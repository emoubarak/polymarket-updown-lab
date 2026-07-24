"""Backtest favorite's entry as a MAKER (resting bid) under the REALISTIC broker fill
model, vs the current TAKER (cross-the-ask) entry.

The live MultiBroker resting-order model (paper.py:288): a resting BUY at L fills only
when the market trades THROUGH it — best ask <= L - trade_through (0.01) — and only
passive_fill_ratio (0.7) of the size fills (we are never first in queue; the flow that
hits a passive bid is informed). Makers pay no fee.

Tape analog of the fill test: the favorite must PRINT at <= L - 0.01 within
(t_entry, window_end]. That requires an actual trade-through, so it is if anything
stricter than the quote test the broker uses — the conservative, honest direction.

Data limit (already paid, FINDINGS): only TAPE windows carry the trade stream a fill
model needs. 15m tape = a 3-day no-loser regime (EV LEVELS meaningless); 5m tape =
100% in-sample. So this measures the MECHANISM faithfully — fill rate, anti-selection
Δwin, per-$ improvement, and the win-rate of MISSED windows (the cost of resting) —
NOT a clean OOS EV. Question: does the realistic trade-through + 0.7 fill model
preserve the maker advantage the generous first-cut showed, or does adverse selection
eat it?

Three entry policies, each over the SAME gated candidates:
  taker        cross the ask: pay fav+0.02 + taker fee, full stake, always fills.
  maker@L      rest a bid at L=fav+dL; fill iff favorite prints <= L-0.01 before
               window_end; 0.7 of size; no fee; else MISS (no trade).
  maker+fb     rest at L; if unfilled by the band's end, cross as taker (certainty).

Run: python3 research/backtest_maker_entry.py [5m|15m]
"""
from __future__ import annotations
import sys

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_lead import fetch_klines, spot_at
from backtest_favorite import IS_OOS_SPLIT, SEC

FR, LO, HI, HC = 0.60, 0.85, 0.95, 0.02
TRADE_THROUGH = 0.01
FILL_RATIO = 0.7
FB_FRACS = [(0.65, "fb65"), (0.75, "fb75"), (0.85, "fb85"), (0.95, "fb95")]
CONFIGS = {
    "favorite (price)":        dict(vol_cap=None,    lead=0.0),
    "favorite_vollead (vol+6bps)": dict(vol_cap=0.00056, lead=6.0),
    # favorite_leadzmk's gate: the vol-normalized lead floor (z >= 1.0), the cross-asset
    # generalizer now deployed as a maker forward-test. Same maker MECHANISM as the
    # others, just gated by z instead of bps — confirms the maker advantage survives
    # on z-gated candidates (BTC tape only; magnitude still IS-degenerate per header).
    "favorite_leadz (vol-norm z>=1)": dict(vol_cap=None, lead=0.0, lead_z=1.0),
}


def fav_print_through(r, t_lo, t_hi, fav_up, L):
    """True iff the favorite trades through L (prints <= L - TRADE_THROUGH) in
    (t_lo, t_hi]. The realistic resting-buy fill test, on the tape."""
    thr = L - TRADE_THROUGH
    for tr in r.get("tape", []):
        if t_lo < tr["t"] <= t_hi:
            p = tr["price"] if tr["outcome"] == "Up" else 1.0 - tr["price"]
            fp = p if fav_up else 1.0 - p
            if fp <= thr + 1e-9:
                return True
    return False


def candidates(recs, idx, sec, cfg):
    """The gated windows favorite would act on at frac FR. Yields a dict per window."""
    out = []
    for r in recs:
        t = r["window_start"] + int(FR * sec)
        p = up_price(r, t)
        if p is None:
            continue
        up = p >= 0.5
        fav = p if up else 1.0 - p
        if not (LO <= fav <= HI):
            continue
        if cfg["vol_cap"] is not None and (r.get("vol_per_min") or 0.0) > cfg["vol_cap"]:
            continue
        if cfg["lead"]:
            op = r.get("binance_open")
            sp = spot_at(idx, t)
            if op is None or sp is None:
                continue
            conv = (sp - op) if up else (op - sp)
            if conv / op * 1e4 < cfg["lead"]:
                continue
        if cfg.get("lead_z"):           # vol-normalized lead floor (favorite_leadz gate)
            op = r.get("binance_open")
            sp = spot_at(idx, t)
            sigma = r.get("vol_per_min") or 0.0
            tau_min = max(0.0, (r["window_end"] - t) / 60.0)
            if op is None or sp is None or sigma <= 0 or tau_min <= 0:
                continue
            conv = (sp - op) if up else (op - sp)
            if (conv / op) / (sigma * tau_min ** 0.5) < cfg["lead_z"]:
                continue
        win = r["up_won"] if up else (not r["up_won"])
        out.append(dict(r=r, t=t, up=up, fav=fav, win=win))
    return out


def per_dollar(price, win, fee=0.0):
    sh = 1.0 / price
    return (sh - sh * price - fee) if win else (-sh * price - fee)


def stats(rows, key, oos=False):
    rr = [x for x in rows if (not oos) or day_of(x["r"]) >= IS_OOS_SPLIT]
    rr = [x for x in rr if x.get(key) is not None]
    if not rr:
        return None
    ev = sum(x[key] for x in rr) / len(rr)
    wr = sum(x["win"] for x in rr) / len(rr)
    return ev, len(rr), wr


def run(interval):
    sec = SEC[interval]
    allr = load_all(interval)
    recs = [r for r in allr if r.get("tape")]
    lo = min(r["window_start"] for r in allr)
    hi = max(r["window_end"] for r in allr)
    idx = fetch_klines(lo - 120, hi + 120)
    oos_n = sum(1 for r in recs if day_of(r) >= IS_OOS_SPLIT)
    print(f"=== MAKER-ENTRY (realistic fill: trade-through {TRADE_THROUGH}, "
          f"{FILL_RATIO:.0%} fill, no fee)  {interval} ===")
    print(f"    {len(recs)}/{len(allr)} tape windows, {oos_n} OOS | frac {FR}, "
          f"fav {LO}-{HI}, hold to settle | taker pays fav+{HC}\n")

    for name, cfg in CONFIGS.items():
        cands = candidates(recs, idx, sec, cfg)
        if not cands:
            print(f"  [{name}] no candidates\n")
            continue
        for c in cands:
            r, fav, win = c["r"], c["fav"], c["win"]
            we = r["window_end"]
            # taker baseline
            px = min(fav + HC, 0.99)
            c["taker"] = per_dollar(px, win, taker_fee(1.0 / px, px))
            # maker at three rest prices
            for dL, key in ((0.01, "mk_p1"), (0.0, "mk_0"), (-0.01, "mk_m1")):
                L = round(fav + dL, 4)
                filled = fav_print_through(r, c["t"], we, c["up"], L)
                c[key] = per_dollar(L, win) if filled else None
            # maker(@fav) + taker fallback at a CONCRETE time t_fb: the maker rests
            # only until t_fb (fills iff trade-through by then); if still unfilled we
            # cross as taker at the THEN price (favorite has drifted -> pricier). This
            # is the honest single-decision model; sweep t_fb to trade fill-rate vs
            # fallback cost. (No having-it-both-ways: rest-window and fallback-price
            # share the same t_fb.)
            L = fav
            for ff, key in FB_FRACS:
                tfb = r["window_start"] + int(ff * sec)
                if fav_print_through(r, c["t"], tfb, c["up"], L):
                    c[key] = per_dollar(L, win)
                    c[key + "_mk"] = True
                else:
                    pfb = up_price(r, tfb)
                    favfb = (pfb if c["up"] else 1.0 - pfb) if pfb is not None else fav
                    pxfb = min(favfb + HC, 0.99)
                    c[key] = per_dollar(pxfb, win, taker_fee(1.0 / pxfb, pxfb))
                    c[key + "_mk"] = False

        tk = stats(cands, "taker")
        tko = stats(cands, "taker", oos=True)
        n_all = tk[1]
        print(f"  [{name}]  candidates n={n_all}")
        print(f"     TAKER     : EV/$ {tk[0]:+.4f} win {tk[2]:.3f}"
              + (f" || OOS {tko[0]:+.4f} n={tko[1]} win {tko[2]:.3f}" if tko else " || OOS none")
              + f"  | $@25 {tk[0]*25*n_all:+.0f}")
        for dL, key in ((0.01, "mk_p1"), (0.0, "mk_0"), (-0.01, "mk_m1")):
            s = stats(cands, key)
            if not s:
                print(f"     maker@fav{dL:+.2f}: no fills")
                continue
            ev, nfill, wr = s
            missed = [c for c in cands if c[key] is None]
            miss_wr = sum(x["win"] for x in missed) / len(missed) if missed else float("nan")
            # total $ at intended $25: only FILL_RATIO of stake deploys on fills
            tot = ev * (FILL_RATIO * 25) * nfill
            print(f"     maker@fav{dL:+.2f}: EV/$ {ev:+.4f} win {wr:.3f} "
                  f"fill {nfill/n_all:.0%} Δwin {wr-tk[2]:+.3f} | "
                  f"missed n={len(missed)} win {miss_wr:.3f} | $@25 {tot:+.0f}")
        # maker@fav + taker fallback at each candidate t_fb (the implementable design)
        for ff, key in FB_FRACS:
            nmk = sum(1 for c in cands if c[key + "_mk"])
            evfb = sum(c[key] for c in cands) / len(cands)
            # $ at intended 25: maker legs deploy FILL_RATIO*25 (sized to target in
            # the real engine), taker-fallback legs deploy 25
            totfb = sum(c[key] * (FILL_RATIO * 25 if c[key + "_mk"] else 25) for c in cands)
            o = stats(cands, key, oos=True)
            print(f"     maker+fb@{ff:.2f}: EV/$ {evfb:+.4f} (maker {nmk}/{n_all}="
                  f"{nmk/n_all:.0%})"
                  + (f" || OOS {o[0]:+.4f} n={o[1]}" if o else "")
                  + f"  | $@25 {totfb:+.0f}")
        print()


def main():
    run(sys.argv[1] if len(sys.argv) > 1 else "15m")


if __name__ == "__main__":
    main()
