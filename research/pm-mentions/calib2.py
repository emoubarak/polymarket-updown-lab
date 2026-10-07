"""Lookahead-free calibration of Polymarket Mentions markets.
Event start = earliest YES-sibling jump (<0.6 -> >=0.95); entry = last REAL trade before (start - off)."""
import json,urllib.request,time,statistics as st,random
from concurrent.futures import ThreadPoolExecutor
def get(u):
    for i in range(3):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"}),timeout=60))
        except Exception: time.sleep(1)
ev=[]
for off in range(0,2000,100):
    r=get(f"https://gamma-api.polymarket.com/events?tag_slug=mention-markets&closed=true&limit=100&offset={off}&order=endDate&ascending=false")
    if not r: break
    ev+=r
    if len(r)<100: break
ev=[e for e in ev if (e.get("closedTime") or "")>="2026-04-01"]
random.seed(1); random.shuffle(ev); ev=ev[:260]
print("events:",len(ev),flush=True)
def trades(m):
    tok=json.loads(m["clobTokenIds"])[0]
    tr=get(f"https://data-api.polymarket.com/trades?market={m['conditionId']}&limit=1000") or []
    return sorted((int(t["timestamp"]), float(t["price"]) if t.get("asset")==tok else 1-float(t["price"]), float(t["size"])) for t in tr)
def do_event(e):
    ms=[]
    for m in e.get("markets",[]):
        try: y=float(json.loads(m["outcomePrices"])[0])
        except: continue
        if 0.02<y<0.98: continue
        ms.append((m,1 if y>0.5 else 0))
    if len(ms)<3: return []
    T={m["id"]:trades(m) for m,_ in ms}
    jumps=[]
    for m,y in ms:
        s=T[m["id"]]
        if y!=1 or not s: continue
        lows=[t for t,p,_ in s if p<0.6]
        if not lows: continue
        j=next((t for t,p,_ in s if t>lows[-1] and p>=0.95),None)
        if j: jumps.append(j)
    if not jumps: return []
    t_ev=min(jumps); rows=[]
    for m,y in ms:
        s=T[m["id"]]; r={"y":y,"ev":e["slug"][:60],"q":m.get("groupItemTitle")}
        for off in (0.5,2,6):
            a=t_ev-off*3600
            pre=[x for x in s if a-6*3600<=x[0]<=a]
            r[str(off)]=pre[-1][1] if pre else None
        rows.append(r)
    return rows
out=[]
with ThreadPoolExecutor(12) as ex:
    for i,rs in enumerate(ex.map(do_event,ev)):
        out+=rs
        if i%40==0: print("done",i,flush=True)
json.dump(out,open("calib2.json","w"))
print("market rows:",len(out))
for off in ("0.5","2","6"):
    print(f"\n== last real trade within 6h, ending {off}h before event start ==")
    print(" bucket     n  events mean_px yes_rate ROI_YES ROI_NO(+1c)")
    for lo,hi in [(0,.1),(.1,.2),(.2,.3),(.3,.4),(.4,.5),(.5,.6),(.6,.7),(.7,.85),(.85,1)]:
        r=[o for o in out if o[off] is not None and lo<=o[off]<hi]
        if len(r)<15: continue
        px=st.mean(o[off] for o in r); yr=st.mean(o["y"] for o in r)
        ry=st.mean(o["y"]/min(o[off]+.01,1)-1 for o in r); rn=st.mean((1-o["y"])/min(1-o[off]+.01,1)-1 for o in r)
        print(f" {lo:.2f}-{hi:.2f} {len(r):5} {len(set(o['ev'] for o in r)):5} {px:7.3f} {yr:7.3f} {ry:+7.3f} {rn:+7.3f}")
