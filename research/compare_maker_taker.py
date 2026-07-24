"""A/B read-out: the co-located MAKER pilot vs its TAKER twin on the SAME cell.

The colo experiment (2026-06-28): a real zleadmk-btc-15m pilot with fast poll + maker
recenter runs head-to-head with the taker zlead-btc-15m pilot on Ireland. The Morocco
paper falsification of maker entry used an 8s poll + simulated fills and NEVER ran
co-located; this is the first real low-latency test. The question the LEVELS can't fake:

  1. FILL MIX        — of the windows the maker acted on, how many filled as a real
                       maker (FILL_BUY, free + cheap) vs crossed as taker-fallback (BUY)
                       vs were abandoned (recenter saw the favorite flip).
  2. ADVERSE SEL.    — maker filled-win-rate vs taker win-rate. If the recenter dodges
                       the weakening-favorite pickoffs, the gap should be SMALL.
  3. PRICE CAPTURED  — avg price paid maker vs taker (maker should pay ~2-3c less:
                       no +2c haircut, no taker fee). That saving is the whole point.
  4. NET            — realized P&L per $ staked, maker vs taker, on COMMON windows.

Run on the engine host (AWS), where the live_state dirs are:
    python3 research/compare_maker_taker.py \
        live_state_zleadmk-btc-15m live_state_zlead-btc-15m
"""
from __future__ import annotations
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pmlab.journal import is_entry, is_close, won, window_ts  # noqa: E402


def load(state_dir):
    """Per-window dict: {ts: {entry_kind, price, shares, settled, won, pnl, posted, cancels}}."""
    path = os.path.join(state_dir, "journal.csv")
    out: dict[int, dict] = {}
    if not os.path.exists(path):
        return out
    with open(path) as fh:
        for r in csv.DictReader(fh):
            ts = window_ts(r.get("slug", ""))
            if ts is None:
                continue
            w = out.setdefault(ts, dict(entry_kind=None, price=None, shares=0.0,
                                        settled=False, won=False, pnl=0.0,
                                        posted=False, cancels=0))
            kind = r.get("kind", "")
            try:
                price = float(r["price"]) if r.get("price") else None
                shares = float(r["shares"]) if r.get("shares") else 0.0
                pnl = float(r["pnl"]) if r.get("pnl") else 0.0
            except (TypeError, ValueError):
                price = pnl = None; shares = 0.0
            if kind.startswith("REST_BUY"):
                w["posted"] = True
            elif kind == "CANCEL":
                w["cancels"] += 1
            elif is_entry(kind):
                w["entry_kind"] = "maker" if kind.startswith("FILL_BUY") else "taker"
                w["price"] = price
                w["shares"] += shares
            if is_close(kind):
                w["settled"] = True
                w["won"] = won(kind, pnl or 0.0)
                w["pnl"] = (w["pnl"] or 0.0) + (pnl or 0.0)
    return out


def summ(label, wins):
    entered = [w for w in wins.values() if w["entry_kind"]]
    settled = [w for w in entered if w["settled"]]
    n = len(settled)
    print(f"\n  [{label}]  acted-on={len(entered)}  settled={n}")
    if not n:
        print("     (no settled entries yet — waiting for windows to resolve)")
        return
    mk = [w for w in settled if w["entry_kind"] == "maker"]
    tk = [w for w in settled if w["entry_kind"] == "taker"]
    wr = sum(w["won"] for w in settled) / n
    px = sum(w["price"] for w in settled if w["price"]) / max(1, sum(1 for w in settled if w["price"]))
    pnl = sum(w["pnl"] for w in settled)
    stake = sum((w["price"] or 0) * w["shares"] for w in settled)
    print(f"     fill mix: maker {len(mk)} / taker-fallback {len(tk)}  "
          f"(maker-fill {len(mk)/n:.0%})")
    print(f"     win {wr:.3f}  avg-px {px:.4f}  net ${pnl:+.2f}  "
          f"EV/$ {(pnl/stake if stake else 0):+.4f}")
    if mk:
        mwr = sum(w["won"] for w in mk) / len(mk)
        mpx = sum(w["price"] for w in mk if w["price"]) / max(1, len(mk))
        print(f"     maker-filled only: n={len(mk)} win {mwr:.3f} avg-px {mpx:.4f}")


def main():
    if len(sys.argv) < 3:
        print("usage: compare_maker_taker.py <maker_state_dir> <taker_state_dir>")
        return
    mk_dir, tk_dir = sys.argv[1], sys.argv[2]
    mk, tk = load(mk_dir), load(tk_dir)
    print("=== MAKER (colo) vs TAKER head-to-head ===")
    summ(f"MAKER {mk_dir}", mk)
    summ(f"TAKER {tk_dir}", tk)
    # apples-to-apples on COMMON settled windows (both entered + resolved)
    common = [ts for ts in mk if ts in tk
              and mk[ts]["entry_kind"] and tk[ts]["entry_kind"]
              and mk[ts]["settled"] and tk[ts]["settled"]]
    print(f"\n  === COMMON settled windows: n={len(common)} ===")
    if common:
        dpx = sum((tk[ts]["price"] or 0) - (mk[ts]["price"] or 0) for ts in common) / len(common)
        mwr = sum(mk[ts]["won"] for ts in common) / len(common)
        twr = sum(tk[ts]["won"] for ts in common) / len(common)
        mpnl = sum(mk[ts]["pnl"] for ts in common)
        tpnl = sum(tk[ts]["pnl"] for ts in common)
        print(f"     avg price saved by maker: {dpx:+.4f}  (taker px − maker px)")
        print(f"     win-rate  maker {mwr:.3f}  vs taker {twr:.3f}   (Δ {mwr-twr:+.3f} = adverse selection)")
        print(f"     net P&L   maker ${mpnl:+.2f}  vs taker ${tpnl:+.2f}")
        print(f"     >>> VERDICT: maker {'BEATS' if mpnl > tpnl else 'TRAILS'} taker on common windows "
              f"by ${mpnl-tpnl:+.2f}")


if __name__ == "__main__":
    main()
