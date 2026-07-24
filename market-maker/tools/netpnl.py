#!/usr/bin/env python3
"""netpnl — net P&L = trading (per-window CSV) + maker rebates (on-chain),
over the last N hours. The true bottom line for the sell-only deploy."""
import json, subprocess, time, collections, sys

US = "0xBbe3..."  # adresse tronquée pour publication — renseigner l'adresse complète du Safe
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 24

def fetch(end=None):
    u = f"https://data-api.polymarket.com/activity?user={US}&limit=500"
    if end: u += f"&end={end}"
    return json.loads(subprocess.run(["curl","-s","-A","Mozilla/5.0",u],
                      capture_output=True, text=True, timeout=25).stdout)

now = int(time.time()); lo = now - int(HOURS*3600)
seen, rows, end = set(), [], None
for _ in range(40):
    b = fetch(end)
    if not b: break
    nw = 0
    for a in b:
        k=(a.get("transactionHash",""),a.get("asset",""),a.get("timestamp",0),str(a.get("size","")),a.get("side",""),a.get("type",""))
        if k not in seen: seen.add(k); rows.append(a); nw+=1
    end = min(a["timestamp"] for a in b) - 1
    if end < lo or nw == 0: break
    time.sleep(0.1)
rows = [r for r in rows if r["timestamp"] > lo]

reb = sum((r.get("usdcSize",0) or r.get("size",0)) for r in rows if "REBATE" in r["type"].upper())
vol = sum(r.get("usdcSize",0) for r in rows if r["type"]=="TRADE")
ntr = sum(1 for r in rows if r["type"]=="TRADE")
print(f"=== {HOURS:.0f}h ===")
print(f"maker rebates: ${reb:.2f}")
print(f"trade volume:  ${vol:.0f} ({ntr} trades) -> rebate rate {100*reb/vol if vol else 0:.3f}%")
print(f"types: {dict(collections.Counter(r['type'] for r in rows))}")
