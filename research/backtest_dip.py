"""Backtest the WHIPSAW / buy-the-dip pattern (user's idea, 2026-06-21).

Observation: the favorite token sometimes rises, drops hard mid-window, then
recovers to win. The actionable read isn't "buy the longshot" (already falsified
in backtest_fade.py) — it's "buy the FAVORITE during its dip", a cheaper entry on
the same +EV side, betting the drop over-shot.

Decisive question, binned: conditional on the favorite's price dropping to X
mid-window, what's its realized win-rate?
  win-rate(X) > X  → the dip over-shot → buying it is a real edge (user is right)
  win-rate(X) ≈ X  → price tracks probability → the dip is just lower odds, no edge

Two views:
  PERFECT  — buy at the favorite's lowest point in [30%,95%] (hindsight upper bound;
             if even perfect dip-timing can't beat the price, the idea is dead).
  TRADEABLE— buy the first time the favorite falls >=drop below its running peak
             (a rule you could actually fire live), hold to settle, +2c + taker fee.

Run: python3 research/backtest_dip.py [5m|15m]
"""
from __future__ import annotations
import sys

from explore2 import load_all, taker_fee, up_price, day_of
from backtest_favorite import IS_OOS_SPLIT, SEC


def collect(recs, interval, lo_peak=0.85, drop=0.08):
    """Lookahead-clean: the favourite side is fixed at the 30% mark (no future
    price used to pick the side), then the running peak and the pullback are
    detected strictly FORWARD. The buy uses only info up to the buy time; only the
    settlement is the real outcome."""
    sec = SEC[interval]
    perfect, trade = [], []          # (dip_price, won, r) ; (entry_px, won, pnl, r)
    for r in recs:
        track = r.get("price_track") or []
        if len(track) < 6:
            continue
        ws, we = r["window_start"], r["window_end"]
        series = sorted((ts, p) for ts, p in track if ws <= ts <= we)
        ref = [(ts, p) for ts, p in series if ts >= ws + 0.30 * sec]
        if len(ref) < 3:
            continue
        up_fav = ref[0][1] >= 0.5                     # side fixed at 30% — no lookahead
        won = r["up_won"] if up_fav else (not r["up_won"])
        favser = [(ts, (p if up_fav else 1.0 - p)) for ts, p in ref]
        # PERFECT (hindsight upper bound): lowest favourite price post-30%
        perfect.append((min(fp for _, fp in favser), won, r))
        # TRADEABLE: rose to >=lo_peak then fell >=drop below that running peak
        peak = favser[0][1]
        for ts, fp in favser:
            peak = max(peak, fp)
            if peak >= lo_peak and peak - fp >= drop:
                px = min(fp + 0.02, 0.99)             # pay +2c to take it
                sh = 1.0 / px
                fee = taker_fee(sh, px)
                pnl = (sh - sh * px - fee) if won else (-sh * px - fee)
                trade.append((px, won, pnl, r))
                break
    return perfect, trade


def bin_table(rows, edges):
    print(f"      {'creux':>12}{'n':>6}{'win':>8}{'prix':>8}{'edge':>9}")
    for lo, hi in zip(edges, edges[1:]):
        sub = [(px, w) for px, w, *_ in rows if lo <= px < hi]
        if len(sub) < 10:
            print(f"      [{lo:.2f},{hi:.2f})  n={len(sub):<4d}  (trop peu)")
            continue
        wr = sum(w for _, w in sub) / len(sub)
        pxm = sum(px for px, _ in sub) / len(sub)
        print(f"      [{lo:.2f},{hi:.2f})  {len(sub):>4d}  {wr:>7.3f} {pxm:>7.3f} {wr-pxm:>+8.3f}")


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "15m"
    recs = load_all(interval)
    perfect, trade = collect(recs, interval)
    print(f"=== backtest WHIPSAW / buy-the-dip {interval} "
          f"({len(recs)} windows, side fixed @30%, lookahead-clean) ===")
    print(f"    edge = win − prix ; >0 ⇒ le creux a sur-réagi (achat rentable)\n")

    print(f"  PERFECT — acheter au plus bas du favori en [30%,95%] (borne haute, hindsight):")
    print(f"    n={len(perfect)} fenêtres avec creux mesurable")
    bin_table(perfect, [0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01])
    if perfect:
        wr = sum(w for _, w, *_ in perfect) / len(perfect)
        pxm = sum(px for px, _, *_ in perfect) / len(perfect)
        print(f"    GLOBAL   win {wr:.3f} vs creux moyen {pxm:.3f}  edge {wr-pxm:+.3f}")

    print(f"\n  TRADEABLE — acheter le 1er repli ≥8c sous le pic (+2c, frais), tenu au règlement:")
    if trade:
        n = len(trade)
        wr = sum(w for _, w, _, _ in trade) / n
        pxm = sum(px for px, _, _, _ in trade) / n
        ev = sum(p for _, _, p, _ in trade) / n
        oos = [(px, w, p) for px, w, p, r in trade if day_of(r) >= IS_OOS_SPLIT]
        ow = sum(w for _, w, _ in oos) / len(oos) if oos else 0
        opx = sum(px for px, _, _ in oos) / len(oos) if oos else 0
        print(f"    ALL n={n:<4d} win {wr:.3f} vs prix {pxm:.3f}  edge {wr-pxm:+.3f}  EV/$ {ev:+.4f}")
        print(f"    OOS n={len(oos):<4d} win {ow:.3f} vs prix {opx:.3f}  "
              f"[{'REAL' if ow > opx else 'DEAD'}]")
    else:
        print("    aucun repli qualifiant")


if __name__ == "__main__":
    main()
