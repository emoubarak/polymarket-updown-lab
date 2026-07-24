#!/usr/bin/env python3
"""Sub-second cross-correlation of the btc->doge fast probe (leadlag_fast/fast.csv on AWS).
Peak at lag>0 (100-800ms) = real capturable sub-second lag = the outlier. Peak at 0 = contemporaneous
(artifact). Run on AWS:  python3 research/leadlag_fast_analyze.py
"""
import os, statistics as st
from collections import defaultdict
CSV=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"leadlag_fast","fast.csv")

def main():
    rows=[]
    with open(CSV) as f:
        for line in f:
            p=line.rstrip().split(",")
            if p[0]=="ts" or len(p)<8: continue
            try: rows.append((float(p[0]), int(p[1]), float(p[2]), float(p[5])))  # ts, ws, lead_mid, lag_mid
            except: continue
    if not rows: print("pas de données"); return
    span=(rows[-1][0]-rows[0][0])/3600
    print(f"{len(rows)} polls, span {span:.2f}h, {len(set(r[1] for r in rows))} fenêtres, "
          f"cadence {len(rows)/max(1,(rows[-1][0]-rows[0][0])):.1f}/s\n")
    # per window, build (ts, lead, lag); compute deltas; correlate lag-delta(t) vs lead-delta(t-Δ)
    byws=defaultdict(list)
    for ts,ws,lm,gm in rows: byws[ws].append((ts,lm,gm))
    # bucket ts to a fine grid to align lead/lag deltas
    LAGS=[0.0,0.15,0.3,0.5,0.8,1.2,2.0]
    acc=defaultdict(lambda:[[],[]])
    for ws,s in byws.items():
        s.sort()
        # lead delta series & lag delta series keyed by rounded ts
        ld={}; gd={}
        for i in range(1,len(s)):
            ld[s[i][0]]=s[i][1]-s[i-1][1]
            gd[s[i][0]]=s[i][2]-s[i-1][2]
        gts=sorted(gd)
        lts=sorted(ld)
        import bisect
        for t in gts:
            gv=gd[t]
            for L in LAGS:
                # find lead delta nearest to t-L (within 100ms)
                target=t-L
                j=bisect.bisect_left(lts,target)
                best=None
                for k in (j-1,j):
                    if 0<=k<len(lts) and abs(lts[k]-target)<=0.1:
                        if best is None or abs(lts[k]-target)<abs(lts[best]-target): best=k
                if best is not None:
                    lv=ld[lts[best]]
                    if lv!=0 or gv!=0:
                        acc[L][0].append(lv); acc[L][1].append(gv)
    print("Cross-corr  corr( Δdoge(t), Δbtc(t-lag) )  — pic à lag>0 = vrai retard capturable :")
    for L in LAGS:
        xs,ys=acc[L]
        if len(xs)<50: print(f"  lag {int(L*1000):4}ms : n={len(xs)} (peu)"); continue
        mx,my=st.mean(xs),st.mean(ys)
        num=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
        den=(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))**0.5
        print(f"  lag {int(L*1000):4}ms : corr={num/den:+.3f}  (n={len(xs)})" if den else f"  lag {int(L*1000)}ms: 0")

if __name__=="__main__": main()
