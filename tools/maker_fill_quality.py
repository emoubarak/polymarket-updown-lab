#!/usr/bin/env python3
"""Maker fill-quality analyzer — THE falsification instrument for the co-located
maker-entry experiment (mm-colocation-probe).

Reads one or more run_live journal.csv files and, per resolved window, classifies
the entry as MAKER-filled (FILL_BUY) vs TAKER (BUY / fallback / direct) and joins
the settlement (SETTLE_WIN/LOSS). It then contrasts the two populations on the
metrics that decide whether a co-located resting bid is ADVERSELY selected:

  fill-rate      #FILL_BUY / #REST_BUY        (did the resting bid actually fill?)
  win-rate       maker-filled vs taker        (adverse selection = maker << taker)
  entry price    maker fill vs taker price     (the half-spread we hoped to save)
  net $/window   maker vs taker

The structural trap this measures: a STATIC bid at the favorite fills preferentially
when the favorite WEAKENS (price falls to the bid) → lower win-rate than the taker
that chases the runaways. Re-centering (only possible co-located) should lift the
maker win-rate toward the taker baseline by converting directional-adverse fills into
spread-capture fills. This tool is how we read that.

Usage:
  python3 tools/maker_fill_quality.py state_mk_eth_15m [state_mk_btc_15m ...]
  python3 tools/maker_fill_quality.py --selftest
Stdlib-only (mirrors the paper side); imports journal.py so classification can't drift.
"""
from __future__ import annotations

import sys
import csv
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pmlab.journal import window_ts, is_settle  # one source of truth


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as fh:
        return list(csv.DictReader(fh))


def analyze(rows: list[dict]) -> dict:
    """Group rows by window, classify entry kind, join settlement → per-window records."""
    by_win: dict[int, dict] = defaultdict(lambda: {"rested": False, "entry": None,
                                                    "entry_px": None, "settled": None, "pnl": 0.0})
    for r in rows:
        kind = (r.get("kind") or "").strip()
        ts = window_ts(r.get("slug", ""))
        if ts is None:
            continue
        w = by_win[ts]
        px = _f(r.get("price"))
        if kind.startswith("REST_BUY"):
            w["rested"] = True
        elif kind.startswith("FILL_BUY"):
            w["entry"], w["entry_px"] = "maker", px        # maker bid filled
        elif kind.startswith("BUY"):                       # BUY / BUY+ = taker (incl. fallback)
            if w["entry"] != "maker":                      # a maker fill in the same window wins
                w["entry"], w["entry_px"] = "taker", px
        elif is_settle(kind):
            w["settled"] = kind.endswith("WIN")
            w["pnl"] += _f(r.get("pnl")) or 0.0
    return by_win


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _stats(records: list[dict], who: str) -> dict:
    settled = [r for r in records if r["entry"] == who and r["settled"] is not None]
    n = len(settled)
    if not n:
        return {"who": who, "n": 0}
    wins = sum(1 for r in settled if r["settled"])
    pxs = [r["entry_px"] for r in settled if r["entry_px"] is not None]
    pnl = sum(r["pnl"] for r in settled)
    return {"who": who, "n": n, "winrate": wins / n, "avg_px": (sum(pxs) / len(pxs)) if pxs else None,
            "net": pnl, "net_per": pnl / n}


def report(by_win: dict) -> None:
    recs = list(by_win.values())
    n_rest = sum(1 for r in recs if r["rested"])
    n_maker = sum(1 for r in recs if r["entry"] == "maker")
    n_taker = sum(1 for r in recs if r["entry"] == "taker")
    print(f"windows seen: {len(recs)}  |  REST_BUY placed: {n_rest}  "
          f"maker-filled: {n_maker}  taker: {n_taker}")
    if n_rest:
        print(f"maker fill-rate: {n_maker}/{n_rest} = {n_maker/n_rest:.0%}  "
              f"(the rest fell back to taker / expired)")
    print(f"\n{'pop':6s}{'n':>5s}{'winrate':>9s}{'avg_px':>8s}{'net$':>9s}{'net$/win':>9s}")
    m = _stats(recs, "maker"); t = _stats(recs, "taker")
    for s in (m, t):
        if s["n"]:
            print(f"{s['who']:6s}{s['n']:5d}{s['winrate']:9.1%}"
                  f"{(s['avg_px'] or 0):8.3f}{s['net']:9.2f}{s['net_per']:9.3f}")
        else:
            print(f"{s['who']:6s}{0:5d}{'—':>9s}{'—':>8s}{'—':>9s}{'—':>9s}")
    if m["n"] and t["n"]:
        dwr = m["winrate"] - t["winrate"]
        print(f"\nADVERSE-SELECTION read: maker winrate − taker winrate = {dwr:+.1%}")
        print("  (strongly negative = maker fills are the weakening favorites = adverse;"
              "\n   near-zero or positive = co-located re-centering is converting fills to spread capture)")
        if m["avg_px"] and t["avg_px"]:
            print(f"  entry-price edge: maker {m['avg_px']:.3f} vs taker {t['avg_px']:.3f} "
                  f"= {(t['avg_px']-m['avg_px'])*100:+.2f}c saved/share (the half-spread thesis)")
    print("\nNOTE: need n≥~50 settled per population before trusting this — favorite_leadzmk was "
          "killed too early at n≈14 (maker-entry-lead). Favorites win ~90%, so losses are rare.")


def _selftest():
    # crafted journal: 4 windows. w1 maker-fill WIN, w2 maker-fill LOSS (adverse),
    # w3 taker WIN, w4 taker LOSS. Validates classification + the maker<taker read.
    rows = [
        {"kind": "REST_BUY", "slug": "eth-updown-15m-1000", "price": "0.88", "pnl": ""},
        {"kind": "FILL_BUY", "slug": "eth-updown-15m-1000", "price": "0.88", "pnl": ""},
        {"kind": "SETTLE_WIN", "slug": "eth-updown-15m-1000", "price": "", "pnl": "1.2"},
        {"kind": "REST_BUY", "slug": "eth-updown-15m-2000", "price": "0.87", "pnl": ""},
        {"kind": "FILL_BUY", "slug": "eth-updown-15m-2000", "price": "0.87", "pnl": ""},
        {"kind": "SETTLE_LOSS", "slug": "eth-updown-15m-2000", "price": "", "pnl": "-8.7"},
        {"kind": "REST_BUY", "slug": "eth-updown-15m-3000", "price": "0.90", "pnl": ""},
        {"kind": "CANCEL", "slug": "eth-updown-15m-3000", "price": "0.90", "pnl": ""},
        {"kind": "BUY", "slug": "eth-updown-15m-3000", "price": "0.92", "pnl": ""},
        {"kind": "SETTLE_WIN", "slug": "eth-updown-15m-3000", "price": "", "pnl": "0.9"},
        {"kind": "BUY", "slug": "eth-updown-15m-4000", "price": "0.91", "pnl": ""},
        {"kind": "SETTLE_LOSS", "slug": "eth-updown-15m-4000", "price": "", "pnl": "-9.1"},
    ]
    by_win = analyze(rows)
    assert len(by_win) == 4, by_win
    m = _stats(list(by_win.values()), "maker"); t = _stats(list(by_win.values()), "taker")
    assert m["n"] == 2 and t["n"] == 2, (m, t)
    assert abs(m["winrate"] - 0.5) < 1e-9 and abs(t["winrate"] - 0.5) < 1e-9
    print("selftest OK — classification & stats sound\n")
    report(by_win)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif len(sys.argv) > 1:
        all_rows = []
        for d in sys.argv[1:]:
            p = Path(d)
            jp = p / "journal.csv" if p.is_dir() else p
            all_rows += _read(jp)
        if not all_rows:
            print("no journal rows found in:", sys.argv[1:])
        else:
            report(analyze(all_rows))
    else:
        print(__doc__)
