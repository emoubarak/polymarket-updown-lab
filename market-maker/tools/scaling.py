#!/usr/bin/env python3
"""scaling — does std0's trading P&L scale linearly with size, or is there
a size-independent edge? Answers: (1) is trading mean significantly != 0?
(2) is P&L-per-1000-pairs stable across series of different sizes? (3) how
does variance scale with size?
"""
import json, re, statistics, collections, pathlib, math

HOME = pathlib.Path.home() / "rebate"
rows, seen = [], set()
for fn in ["std0_hist.jsonl", "std0_activity.jsonl"]:
    p = HOME / fn
    if not p.exists(): continue
    for line in p.read_text().splitlines():
        if not line.strip(): continue
        try: a = json.loads(line)
        except Exception: continue
        k=(a.get("transactionHash",""),a.get("asset",""),a.get("timestamp",0),str(a.get("size","")),a.get("side",""),a.get("type",""))
        if k not in seen: seen.add(k); rows.append(a)

def fam(s):
    m=re.match(r"([a-z]+-updown-\d+m)-\d+$",s or ""); return m.group(1) if m else None
def ws(s):
    m=re.search(r"-(\d+)$",s or ""); return int(m.group(1)) if m else None

wins=collections.defaultdict(list)
for r in rows:
    f=fam(r.get("slug","")); w=ws(r.get("slug",""))
    if f and w: wins[(f,w)].append(r)

import time
now=time.time()
def pnl_and_split(evs):
    up=dn=cash=split=0.0; hr=False
    for r in evs:
        t,side=r["type"],r.get("side",""); sz=r.get("size",0); usd=r.get("usdcSize",0); oc=r.get("outcome","")
        if t=="SPLIT": up+=sz; dn+=sz; cash-=sz; split+=sz
        elif t=="TRADE":
            g=-1 if side=="SELL" else 1; cash-=g*usd
            if oc=="Up": up+=g*sz
            else: dn+=g*sz
        elif t=="REDEEM": cash+= (usd if usd else sz); hr=True
    pnl = cash if hr else cash + max(up,dn)  # mark winner if redeem missing
    return pnl, split, (up-dn), hr

# per-family stats + normalized-per-1000-pairs
byf=collections.defaultdict(list)
for (f,w),evs in wins.items():
    if now-(w+300) < 400: continue
    pnl,split,net,hr = pnl_and_split(evs)
    if split < 50: continue
    byf[f].append((pnl,split,net))

print(f"{'series':16} {'n':>4} {'split':>6} {'mean$':>7} {'t-stat':>6} {'sd$':>6}  "
      f"{'mean/1k':>8} {'sd/1k':>7} {'|net|/split':>11}")
for f in sorted(byf):
    v=byf[f]; n=len(v)
    pnls=[x[0] for x in v]; splits=[x[1] for x in v]
    per1k=[x[0]/(x[1]/1000) for x in v if x[1]>0]
    netratio=[abs(x[2])/x[1] for x in v if x[1]>0]
    m=statistics.mean(pnls); sd=statistics.pstdev(pnls)
    se=sd/math.sqrt(n) if n else 0
    t=m/se if se else 0
    print(f"{f:16} {n:4d} {statistics.median(splits):6.0f} {m:7.2f} {t:6.2f} {sd:6.1f}  "
          f"{statistics.mean(per1k):8.3f} {statistics.pstdev(per1k):7.2f} "
          f"{statistics.mean(netratio):11.3f}")

print("\nt-stat: |t|<2 => trading mean NOT distinguishable from 0 (edge≈0, pure noise)")
print("mean/1k: trading $ per 1000 pairs — if ~equal across series, size-invariant")
print("sd/1k stays large => variance is directional-residual driven, scales with size")

# Variance vs size within btc-5m: does bigger |net| => bigger |pnl|?
btc=[x for x in byf.get("btc-updown-5m",[])]
if len(btc)>20:
    # correlation between |net| and |pnl|
    xs=[abs(x[2]) for x in btc]; ys=[abs(x[0]) for x in btc]
    mx,my=statistics.mean(xs),statistics.mean(ys)
    cov=sum((a-mx)*(b-my) for a,b in zip(xs,ys))/len(xs)
    sx,sy=statistics.pstdev(xs),statistics.pstdev(ys)
    corr=cov/(sx*sy) if sx*sy else 0
    print(f"\nbtc-5m: corr(|net residual|, |pnl|) = {corr:.2f} "
          f"(high => P&L swing IS the residual × binary payoff)")
