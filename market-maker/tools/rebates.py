#!/usr/bin/env python3
"""rebates — aggregate std0's actual rebate credits by type, and estimate
taker fees paid, to answer: why is std0 a taker, is it profitable, should we
only do MM. Read-only over recorded activity."""
import json, pathlib, collections

rows, seen = [], set()
for fn in ["std0_hist.jsonl", "std0_activity.jsonl"]:
    p = pathlib.Path.home()/"rebate"/fn
    if not p.exists(): continue
    for line in p.read_text().splitlines():
        if not line.strip(): continue
        try: a = json.loads(line)
        except Exception: continue
        k=(a.get("transactionHash",""),a.get("asset",""),a.get("timestamp",0),str(a.get("size","")),a.get("side",""),a.get("type",""))
        if k not in seen: seen.add(k); rows.append(a)

types = collections.Counter(r["type"] for r in rows)
print("event types recorded:", dict(types))

# rebate credits by type
reb = collections.defaultdict(lambda: [0.0,0])
for r in rows:
    t = r["type"]
    if "REBATE" in t.upper():
        usd = r.get("usdcSize", 0) or r.get("size", 0)
        reb[t][0]+=usd; reb[t][1]+=1
print("\nrebate credits recorded:")
for t,(s,n) in sorted(reb.items()):
    print(f"  {t}: ${s:.2f} over {n} credits")

# taker fee PAID estimate: crypto taker fee = 0.07*p*(1-p)*shares on TRADE
# rows where std0 was the taker. We can't tell maker/taker from activity
# rows alone, so estimate the ceiling: fee if ALL buys were taker (upper
# bound) vs the ~33% taker share measured on-chain.
buy_fee_full = 0.0; buys = 0
for r in rows:
    if r["type"]!="TRADE": continue
    p = r.get("price",0); sz = r.get("size",0)
    if 0<p<1:
        f = 0.07*p*(1-p)*sz
        if r.get("side")=="BUY":
            buy_fee_full += f; buys += 1
# on-chain measured: ~33% of fills are taker
print(f"\ntaker fee estimate (crypto 0.07·p(1-p)):")
print(f"  if ALL {buys} buys were taker: ${buy_fee_full:.2f} (upper bound)")
print(f"  at measured 33% taker share: ~${buy_fee_full*0.33:.2f}")
