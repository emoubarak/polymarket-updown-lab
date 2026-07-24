"""Enrich the REAL-money settled windows with entry-context features → loss vs win.

Ground-truth confirmation of the guardrail hunt: for each real settled window
(scratchpad/real_settled.csv, harvested from AWS live_state_*/journal.csv), recompute
the features the gate sees AT ENTRY (no lookahead) from Binance klines, then compare
the LOSS windows vs the WIN windows. Looks for: z near the floor, storm vol, BTC
opposing the alt favorite, time-of-day, AND time-clustering (many coins losing the
SAME window = a systemic move, the correlated-ruin risk).

Run: python3 research/loss_enrich.py <path-to-real_settled.csv>
"""
from __future__ import annotations
import csv
import json
import math
import os
import sys
import time
import urllib.request
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pmlab.coins import SYMBOL  # noqa: E402

SEC = {"5m": 300, "15m": 900}
CACHE = os.environ.get("KLINE_CACHE", "/tmp/loss_klines")


def fetch_klines(symbol, start_s, end_s):
    os.makedirs(CACHE, exist_ok=True)
    cp = os.path.join(CACHE, f"{symbol}.json")
    idx = {}
    if os.path.exists(cp):
        idx = {int(k): v for k, v in json.load(open(cp)).items()}
        if min(idx) <= start_s and max(idx) >= end_s - 60:
            return idx
    cur = start_s * 1000
    end_ms = end_s * 1000
    while cur < end_ms:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1m"
               f"&startTime={cur}&endTime={end_ms}&limit=1000")
        ks = json.load(urllib.request.urlopen(url, timeout=20))
        if not ks:
            break
        for k in ks:
            idx[k[0] // 1000] = {"o": float(k[1]), "c": float(k[4])}
        cur = ks[-1][0] + 60000
        time.sleep(0.1)
    json.dump({str(k): v for k, v in idx.items()}, open(cp, "w"))
    return idx


def spot_at(idx, t):
    return (idx.get((t // 60) * 60 - 60) or {}).get("c")


def ewma_vol(idx, end_ts, lookback=90, lam=0.94):
    e = (end_ts // 60) * 60                 # floor to the minute grid (kline keys are minute-aligned;
    closes = [idx[t]["c"] for t in range(e - lookback * 60, e, 60) if t in idx]  # an arbitrary entry-sec missed every key)
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0]
    if len(rets) < 10:
        return None
    var = sum(r * r for r in rets) / len(rets)
    for r in rets:
        var = lam * var + (1 - lam) * r * r
    return math.sqrt(var)


def main():
    path = sys.argv[1]
    rows = list(csv.DictReader(open(path)))
    coins = sorted({r["coin"] for r in rows})
    lo = min(int(r["entry_ts"]) for r in rows) - 95 * 60
    hi = max(int(r["wstart"]) for r in rows) + 900 + 60
    kl = {}
    for c in set(coins) | {"btc"}:
        sym = SYMBOL.get(c)
        if sym:
            print(f"klines {sym} ...", file=sys.stderr)
            kl[c] = fetch_klines(sym, lo, hi)
    btc = kl.get("btc", {})

    enr = []
    for r in rows:
        c, frame = r["coin"], r["frame"]
        sec = SEC[frame]
        ws = int(r["wstart"]); ets = int(r["entry_ts"]); d = r["dir_fav"]
        idx = kl.get(c, {})
        op = (idx.get(ws) or {}).get("o")
        sp = spot_at(idx, ets)
        sig = ewma_vol(idx, ets)
        if not (op and sp and sig):
            continue
        conv = (sp - op) if d == "Up" else (op - sp)
        tau = max((ws + sec - ets) / 60.0, 1e-9)
        z = (conv / op) / (sig * math.sqrt(tau)) if op and sig else None
        bps = conv / op * 1e4
        # btc alignment for the alt favorite (skip for btc itself)
        bopp = None
        if c != "btc":
            bop = (btc.get(ws) or {}).get("o"); bsp = spot_at(btc, ets)
            if bop and bsp:
                blead = (bsp - bop) / bop
                bopp = (d == "Up" and blead < -0.0002) or (d == "Down" and blead > 0.0002)
        enr.append(dict(coin=c, frame=frame, ws=ws, won=int(r["won"]), px=float(r["entry_px"]),
                        z=z, sig=sig, bps=bps, bopp=bopp,
                        tod=time.gmtime(ets).tm_hour, fracleft=tau / (sec / 60.0)))

    L = [e for e in enr if not e["won"]]
    W = [e for e in enr if e["won"]]
    print(f"\n=== REAL settled enriched: n={len(enr)}  losses={len(L)}  wins={len(W)}  "
          f"(loss-rate {len(L)/len(enr):.1%}) ===")

    def cmp(feat, fmt="{:.3f}"):
        lv = [e[feat] for e in L if e[feat] is not None]
        wv = [e[feat] for e in W if e[feat] is not None]
        if lv and wv:
            print(f"  {feat:8s}: LOSS mean {fmt.format(sum(lv)/len(lv))}  "
                  f"WIN mean {fmt.format(sum(wv)/len(wv))}")
    cmp("z"); cmp("sig", "{:.5f}"); cmp("bps", "{:.1f}"); cmp("px", "{:.3f}")

    # z bucket loss-rate (is the loss just above the z>=1 floor?)
    print("\n  loss-rate by z bucket (entered windows have z>=~1 by the live gate):")
    zb = defaultdict(lambda: [0, 0])
    for e in enr:
        if e["z"] is None:
            continue
        b = ("z<1.0" if e["z"] < 1 else "1.0-1.5" if e["z"] < 1.5 else "1.5-2.5" if e["z"] < 2.5 else "z>2.5")
        zb[b][0] += 1; zb[b][1] += (0 if e["won"] else 1)
    for b in ("z<1.0", "1.0-1.5", "1.5-2.5", "z>2.5"):
        if zb[b][0]:
            print(f"    {b:8s}: n={zb[b][0]:3d}  loss {zb[b][1]/zb[b][0]:.1%}")

    # storm-vol loss-rate, 5m vs 15m
    print("\n  loss-rate by vol, by frame (the Tier-1 5m-storm candidate):")
    for frame in ("5m", "15m"):
        for lab, loq, hiq in (("calm <.0009", 0, 0.0009), ("mid .0009-.0014", 0.0009, 0.0014), ("storm >.0014", 0.0014, 9)):
            g = [e for e in enr if e["frame"] == frame and e["sig"] and loq <= e["sig"] < hiq]
            if g:
                print(f"    {frame} {lab:18s}: n={len(g):3d}  loss {sum(1 for e in g if not e['won'])/len(g):.1%}")

    # BTC-align on alts
    alts = [e for e in enr if e["coin"] != "btc" and e["bopp"] is not None]
    if alts:
        opp = [e for e in alts if e["bopp"]]; al = [e for e in alts if not e["bopp"]]
        print(f"\n  BTC-align (alts n={len(alts)}): OPPOSED n={len(opp)} loss "
              f"{sum(1 for e in opp if not e['won'])/len(opp):.1%}  |  ALIGNED n={len(al)} loss "
              f"{sum(1 for e in al if not e['won'])/len(al):.1%}")

    # time-of-day of losses
    print("\n  losses by UTC hour:", dict(sorted(Counter(e["tod"] for e in L).items())))

    # time-clustering: windows (same wstart) where >=2 coins LOST
    byws = defaultdict(list)
    for e in L:
        byws[e["ws"]].append(e["coin"])
    clusters = {ws: cs for ws, cs in byws.items() if len(cs) >= 2}
    print(f"\n  time-clustered losses (same window_ts, >=2 coins lost): {len(clusters)} clusters")
    for ws, cs in sorted(clusters.items()):
        print(f"    {time.strftime('%m-%d %H:%M', time.gmtime(ws))}: {cs}")


if __name__ == "__main__":
    main()
