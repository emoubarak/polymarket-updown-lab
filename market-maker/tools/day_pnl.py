#!/usr/bin/env python3
"""day_pnl — std0's full 26-May-2026 P&L: trading (split/redeem/trade) +
rebate credits, to answer what it earned per day at its early small-capital
days. Read-only."""
import json, subprocess, time, re, collections, statistics, datetime

STD0 = "0xdf7930e89a2c47560165331863c31deca0733dcd"
# 26 May 2026 00:00 UTC .. 27 May 08:00 UTC (full day + next-morning rebate credit)
LO = int(datetime.datetime(2026,5,26,0,0,tzinfo=datetime.timezone.utc).timestamp())
HI = int(datetime.datetime(2026,5,27,8,0,tzinfo=datetime.timezone.utc).timestamp())

def fetch(end):
    u = f"https://data-api.polymarket.com/activity?user={STD0}&limit=500&end={end}"
    out = subprocess.run(["curl","-s","-A","Mozilla/5.0",u], capture_output=True, text=True, timeout=25).stdout
    return json.loads(out)

seen, rows = set(), []
end = HI
for _ in range(120):
    batch = fetch(end)
    if not batch: break
    new = 0
    for a in batch:
        k=(a.get("transactionHash",""),a.get("asset",""),a.get("timestamp",0),str(a.get("size","")),a.get("side",""),a.get("type",""))
        if k not in seen: seen.add(k); rows.append(a); new+=1
    end = min(a["timestamp"] for a in batch)-1
    if end < LO or new==0: break
    time.sleep(0.12)

rows=[r for r in rows if LO-400<=r["timestamp"]<=HI]
print(f"{len(rows)} events, {datetime.datetime.utcfromtimestamp(min(r['timestamp'] for r in rows))} .. "
      f"{datetime.datetime.utcfromtimestamp(max(r['timestamp'] for r in rows))} UTC")
print("types:", dict(collections.Counter(r["type"] for r in rows)))

# rebate credits
reb = collections.defaultdict(float)
for r in rows:
    if "REBATE" in r["type"].upper():
        reb[r["type"]] += r.get("usdcSize",0) or r.get("size",0)
print("\nREBATE credits (26 May):")
tot_reb = 0
for t,s in sorted(reb.items()):
    print(f"  {t}: ${s:.2f}"); tot_reb += s
print(f"  TOTAL rebates: ${tot_reb:.2f}")

# trading P&L per window (split/redeem/trade)
def ws(s): m=re.search(r"-(\d+)$",s or ""); return int(m.group(1)) if m else None
wins=collections.defaultdict(list)
for r in rows:
    w=ws(r.get("slug",""));
    if w: wins[(r.get("slug",""),w)].append(r)
tot_trade=0; nwin=0
for (slug,w),evs in wins.items():
    up=dn=cash=split=0.0; hr=False
    for r in evs:
        t,side=r["type"],r.get("side",""); sz=r.get("size",0); usd=r.get("usdcSize",0); oc=r.get("outcome","")
        if t=="SPLIT": up+=sz;dn+=sz;cash-=sz;split+=sz
        elif t=="TRADE":
            g=-1 if side=="SELL" else 1; cash-=g*usd
            if oc=="Up": up+=g*sz
            else: dn+=g*sz
        elif t=="REDEEM": cash+=(usd if usd else sz); hr=True
    if split<0.5: continue
    tot_trade += cash if hr else cash+max(up,dn); nwin+=1

print(f"\nTRADING P&L (26 May): ${tot_trade:+.2f} over {nwin} windows")
print(f"\n=== TOTAL JOUR 26 MAI: trading ${tot_trade:+.2f} + rebates ${tot_reb:.2f} = "
      f"${tot_trade+tot_reb:+.2f} ===")
sp=[r.get("size",0) for r in rows if r["type"]=="SPLIT"]
if sp: print(f"(split médian {statistics.median(sp):.0f} paires/fenêtre ≈ capital immobilisé)")
