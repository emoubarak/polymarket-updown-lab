#!/usr/bin/env python3
"""early_pnl — fetch std0's FULL activity (trades+splits+redeems) for the
26 May 2026 session and compute per-window P&L, to test: profitable at
small capital in sell-only mode? Read-only."""
import json, subprocess, time, re, collections, statistics, datetime

STD0 = "0xdf7930e89a2c47560165331863c31deca0733dcd"
# 26 May 2026 20:51 UTC .. 27 May 03:59 UTC (from the user's CSV span)
LO, HI = 1779828694, 1779854354

def fetch(end):
    u = (f"https://data-api.polymarket.com/activity?user={STD0}&limit=500&end={end}")
    out = subprocess.run(["curl","-s","-A","Mozilla/5.0",u], capture_output=True, text=True, timeout=25).stdout
    return json.loads(out)

seen, rows = set(), []
end = HI
for _ in range(80):
    batch = fetch(end)
    if not batch: break
    new = 0
    for a in batch:
        k = (a.get("transactionHash",""),a.get("asset",""),a.get("timestamp",0),str(a.get("size","")),a.get("side",""),a.get("type",""))
        if k not in seen:
            seen.add(k); rows.append(a); new += 1
    end = min(a["timestamp"] for a in batch) - 1
    if end < LO or new == 0: break
    time.sleep(0.15)

rows = [r for r in rows if LO-300 <= r["timestamp"] <= HI+400]
types = collections.Counter(r["type"] for r in rows)
print(f"{len(rows)} events in session: {dict(types)}")

def ws(slug):
    m = re.search(r"-(\d+)$", slug or ""); return int(m.group(1)) if m else None
def fam(slug):
    m = re.match(r"([a-z]+-updown-\d+m)-\d+$", slug or ""); return m.group(1) if m else "?"

wins = collections.defaultdict(list)
for r in rows:
    w = ws(r.get("slug","")); f = fam(r.get("slug",""))
    if w: wins[(f,w)].append(r)

byf = collections.defaultdict(list)
for (f,w),evs in wins.items():
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
    pnl = cash if hr else cash+max(up,dn)
    byf[f].append(pnl)

print(f"\n{'series':16} {'n':>4} {'sum$':>8} {'mean$':>7} {'median$':>8} {'split~':>7}")
totsum=0; totn=0
for f in sorted(byf):
    v=sorted(byf[f]); n=len(v); s=sum(v); totsum+=s; totn+=n
    print(f"{f:16} {n:4d} {s:8.2f} {statistics.mean(v):7.3f} {statistics.median(v):8.3f}")
print(f"\nSESSION TOTAL trading P&L: ${totsum:+.2f} over {totn} windows "
      f"(mean ${totsum/totn:+.3f}/window)" if totn else "no windows")
print("NOTE: excludes rebate credits (paid daily, separate). This is pure trading.")

# capital proxy: max collateral tied in unredeemed splits at once
sizes=[r.get("size",0) for r in rows if r["type"]=="SPLIT"]
if sizes:
    print(f"\nSPLIT size/event: median {statistics.median(sizes):.1f}, max {max(sizes):.0f} "
          f"(≈ collateral per window)")
