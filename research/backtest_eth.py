"""Cross-validate the favorite favorite-longshot edge on an ALT underlying.

ETH/SOL/XRP up/down markets exist with the same structure as BTC (2026-06-20).
If the SAME edge — buy the extreme favorite mid-window, hold to settle, optionally
behind the lead floor — holds on an INDEPENDENT underlying, it's both confirmation
(not a BTC artifact) and a path to more tradable volume + independent samples.

Established already: on ETH the BARE favorite is DEAD and the vol gate is DEAD
(BTC-specific), but the LEAD FLOOR (favorite_lead) is REAL OOS (+0.042). This runs the
same panel on any cached alt underlying.

Discipline unchanged: replicate the live decision (favorite executable price + 2c
ask haircut, hold to settlement, real taker fee, one trade/window), split IS/OOS,
read the tripwire (realized win-rate must beat the average price paid) on OOS.
simulate_lead is symbol-agnostic — only the records and the spot klines change.

Run: python3 research/backtest_eth.py [eth|sol|xrp] [15m|5m] [SPLIT_MMDD]
"""
from __future__ import annotations
import glob
import json
import os
import sys
import urllib.request

from explore2 import day_of
from backtest_favorite import IS_OOS_SPLIT, vol_threshold, split_stats, SEC
import backtest_lead as BL

CACHE = os.path.join(os.path.dirname(__file__), "data")
SYMBOL = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}


def load_alt(underlying: str, interval: str) -> list[dict]:
    prefix = "" if underlying == "btc" else f"{underlying}_"
    recs = []
    for f in glob.glob(os.path.join(CACHE, f"{prefix}{interval}_*.json")):
        try:
            recs.append(json.load(open(f)))
        except (json.JSONDecodeError, OSError):
            continue
    recs = [r for r in recs if r.get("up_won") is not None and r.get("window_start")]
    recs.sort(key=lambda r: r["window_start"])
    return recs


def fetch_klines(lo: int, hi: int, symbol: str) -> dict[int, float]:
    kc = os.path.join(CACHE, f"_klines_{symbol.lower()}.json")
    if os.path.exists(kc):
        idx = {int(k): v for k, v in json.load(open(kc)).items()}
        if idx and min(idx) <= lo and max(idx) >= hi - 60:
            return idx
    idx: dict[int, float] = {}
    cur, end = lo * 1000, hi * 1000
    while cur < end:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m"
               f"&startTime={cur}&endTime={end}&limit=1000")
        ks = json.load(urllib.request.urlopen(url, timeout=20))
        if not ks:
            break
        for k in ks:
            idx[k[0] // 1000] = float(k[4])
        cur = ks[-1][0] + 60000
    json.dump(idx, open(kc, "w"))
    return idx


def report(name, rows, split):
    a = split_stats(rows)
    if not a:
        print(f"  {name:22s}: no qualifying windows")
        return
    oos = split_stats([x for x in rows if day_of(x[3]) >= split])
    ev, n, pos, wr, ap, _ = a
    o = oos and (f"OOS EV/$ {oos[0]:+.4f} n={oos[1]:<3d} days+ {oos[2]:.0%} "
                 f"win {oos[3]:.3f}/px {oos[4]:.3f} [{'REAL' if oos[3] > oos[4] else 'DEAD'}]")
    print(f"  {name:22s}: ALL EV/$ {ev:+.4f} n={n:<4d} | {o or 'no OOS'}")


def main():
    underlying = sys.argv[1] if len(sys.argv) > 1 else "eth"
    interval = sys.argv[2] if len(sys.argv) > 2 else "15m"
    split = sys.argv[3] if len(sys.argv) > 3 else IS_OOS_SPLIT
    sec = SEC[interval]
    recs = load_alt(underlying, interval)
    if not recs:
        print(f"No {underlying} {interval} data cached. Run:")
        print(f"  python3 research/dataset.py --interval {interval} "
              f"--underlying {underlying} --count 1000 --no-tape")
        return
    lo = min(r["window_start"] for r in recs) - 120
    hi = max(r["window_end"] for r in recs) + 120
    idx = fetch_klines(lo, hi, SYMBOL[underlying])
    days = sorted({day_of(r) for r in recs})
    vhi = vol_threshold(recs, 0.66)
    print(f"=== backtest {underlying.upper()} up/down {interval}  ({len(recs)} windows, "
          f"{days[0]}..{days[-1]}, {len(idx)} klines) · OOS @ {split} ===")
    print("    +2c haircut, hold to settle, taker fee, fav 0.85-0.95, enter 60%\n")
    report("favorite (lead=0)",     BL.simulate_lead(recs, idx, sec, 0.60, 0.85, 0.95, 0.02), split)
    report("favorite_vol (volgate)", BL.simulate_lead(recs, idx, sec, 0.60, 0.85, 0.95, 0.02, vol_cap=vhi), split)
    for thr in (4, 6, 8, 12):
        report(f"favorite_lead lead_bps>={thr}",
               BL.simulate_lead(recs, idx, sec, 0.60, 0.85, 0.95, 0.02, min_lead=thr, mode="bps"), split)
    for thr in (0.5, 1.0, 1.5):
        report(f"lead_z>={thr}",
               BL.simulate_lead(recs, idx, sec, 0.60, 0.85, 0.95, 0.02, min_lead=thr, mode="z"), split)


if __name__ == "__main__":
    main()
