#!/usr/bin/env python3
"""Analyze the real-time lead-lag book shadow (leadlag_shadow/books.csv on AWS).

Settles the question the cached tape cannot: is the cross-coin lead-lag a REAL sub-minute lag
(capturable) or a tape-staleness artifact? Run on AWS where books.csv lives:
    python3 research/leadlag_analyze.py
"""
import csv, sys, os, glob, json
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pmlab import feeds

CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "leadlag_shadow", "books.csv")
COLS = ["ts","coin","slug","window_start","frac_rem","mid_up","mid_fav","fav_dir","best_bid","best_ask","bid_depth5","ask_depth5"]

def load():
    rows=[]
    with open(CSV) as f:
        for line in f:
            p=line.rstrip("\n").split(",")
            if p[0]=="ts" or len(p)<10: continue
            r=dict(zip(COLS,p))
            try:
                r["ts"]=float(r["ts"]); r["mid_up"]=float(r["mid_up"]); r["mid_fav"]=float(r["mid_fav"])
                r["window_start"]=int(r["window_start"]); r["frac_rem"]=float(r["frac_rem"])
                r["bid_depth5"]=float(r.get("bid_depth5",0) or 0); r["ask_depth5"]=float(r.get("ask_depth5",0) or 0)
            except: continue
            rows.append(r)
    return rows

def main():
    rows=load()
    print(f"{len(rows)} snapshots; coins={sorted(set(r['coin'] for r in rows))}")
    span=(max(r['ts'] for r in rows)-min(r['ts'] for r in rows))/3600 if rows else 0
    print(f"span {span:.1f}h; windows={len(set(r['window_start'] for r in rows))}\n")

    # series[coin][ws] = sorted [(ts, mid_up)]
    series=defaultdict(lambda: defaultdict(list))
    for r in rows: series[r["coin"]][r["window_start"]].append((r["ts"], r["mid_up"]))
    for c in series:
        for ws in series[c]: series[c][ws].sort()

    # A. CROSS-CORRELATION of mid moves at lags (laggard follows leader?)
    print("=== A. Cross-corr Δmid : corr( Δlaggard(t), Δleader(t-lag) ) — pic à lag>0 = vrai retard ===")
    def deltas(coin, ws, step=2):
        s=series[coin].get(ws,[])
        d={}
        for i in range(1,len(s)):
            d[round(s[i][0])]=s[i][1]-s[i-1][1]
        return d
    import statistics as st
    pairs=[("btc","doge"),("btc","bnb"),("sol","doge"),("xrp","doge"),("btc","sol"),("eth","doge")]
    for lead,lag in pairs:
        bylag=defaultdict(lambda:[[],[]])
        common_ws=set(series[lead])&set(series[lag])
        for ws in common_ws:
            dl=deltas(lead,ws); dg=deltas(lag,ws)
            for t,gv in dg.items():
                for L in (0,2,4,6,10,20,30):
                    lv=dl.get(t-L)
                    if lv is not None and (lv!=0 or gv!=0):
                        bylag[L][0].append(lv); bylag[L][1].append(gv)
        out=[]
        for L in (0,2,4,6,10,20,30):
            xs,ys=bylag[L]
            if len(xs)<50: out.append(f"L{L}:n{len(xs)}"); continue
            mx,my=st.mean(xs),st.mean(ys)
            num=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
            den=(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))**0.5
            out.append(f"L{L}:{num/den:+.3f}" if den else f"L{L}:0")
        print(f"  {lead}->{lag}: " + "  ".join(out))

    # B. ORDER-BOOK IMBALANCE at entry -> favorite outcome (needs oracle)
    print("\n=== B. IMBALANCE du carnet (bid_depth/(bid+ask)) à l'entrée → issue du favori ===")
    cache={}
    slugs={r["slug"] for r in rows if 0.27<=r["frac_rem"]<=0.40}
    from concurrent.futures import ThreadPoolExecutor
    def res(s):
        try: return s, feeds.resolve_market(s)
        except: return s, None
    with ThreadPoolExecutor(max_workers=12) as ex:
        for s,v in ex.map(res, list(slugs)): cache[s]=v
    buckets=defaultdict(lambda:[0,0])
    seen=set()
    for r in rows:
        if not (0.27<=r["frac_rem"]<=0.40): continue
        key=(r["slug"],)
        if key in seen: continue
        seen.add(key)
        out=cache.get(r["slug"])
        if out is None or r["mid_fav"]<0.85: continue
        tot=r["bid_depth5"]+r["ask_depth5"]
        if tot<=0: continue
        imb=r["bid_depth5"]/tot          # >0.5 = more bid (buy) pressure on UP token
        favwon = out if r["mid_up"]>=0.5 else (not out)
        b = "bid-lourd(>0.6)" if imb>0.6 else ("ask-lourd(<0.4)" if imb<0.4 else "équilibré")
        buckets[b][0]+=1; buckets[b][1]+=favwon
    for b in ("bid-lourd(>0.6)","équilibré","ask-lourd(<0.4)"):
        n,w=buckets[b]
        if n: print(f"  {b:16} n={n:5} favWin={w/n*100:.1f}%")

if __name__=="__main__":
    main()
