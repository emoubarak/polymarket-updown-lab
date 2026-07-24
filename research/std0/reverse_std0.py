#!/usr/bin/env python3
"""Scalpel reverse-engineering of std0 (proxy 0xdf79…3dcd) from the Polymarket data-api activity feed.
v2: time-paginated (end=<ts>) for a large sample; per-WINDOW decomposition to separate a real overround
edge from outcome luck. Maps every market, entry prices, delta-neutrality, per-window P&L, cadence."""
import json, urllib.request, time
from collections import defaultdict

STD0 = "0xdf7930e89a2c47560165331863c31deca0733dcd"
BASE = "https://data-api.polymarket.com/activity"
TARGET_HOURS = 10          # how far back to paginate
MAX_ROWS = 40000

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    return json.load(urllib.request.urlopen(req, timeout=30))

def collect():
    rows, end = [], int(time.time())
    t0 = end
    while len(rows) < MAX_ROWS:
        try:
            batch = get(f"{BASE}?user={STD0}&limit=500&end={end}")
        except Exception as e:
            print("# page err", e); break
        if not batch: break
        rows.extend(batch)
        oldest = batch[-1]["timestamp"]
        if t0 - oldest > TARGET_HOURS * 3600: break
        if oldest >= end: break          # no progress guard
        end = oldest - 1
        time.sleep(0.12)
    # dedupe by txhash+asset+type+ts
    seen, uniq = set(), []
    for r in rows:
        k = (r.get("transactionHash"), r.get("asset"), r.get("type"), r.get("timestamp"), r.get("side"), r.get("size"))
        if k in seen: continue
        seen.add(k); uniq.append(r)
    return uniq

def frame_of(slug):
    p = (slug or "").split("-")
    if len(p) >= 3 and p[1] == "updown": return p[0], p[2]
    return (slug or "?")[:18], "other"

def main():
    rows = collect()
    rows.sort(key=lambda r: r["timestamp"])
    span = (rows[-1]["timestamp"] - rows[0]["timestamp"]) / 3600 if len(rows) > 1 else 0
    print(f"# {len(rows)} unique rows, ~{span:.1f}h "
          f"({time.strftime('%m-%d %H:%M', time.gmtime(rows[0]['timestamp']))}→"
          f"{time.strftime('%m-%d %H:%M', time.gmtime(rows[-1]['timestamp']))} UTC)")
    types = defaultdict(int)
    for r in rows: types[r["type"]] += 1
    print("# types:", dict(types), "\n")

    # ---------- (A) MARKET MAP ----------
    mk = defaultdict(lambda: {"w": set(), "tr": 0, "vol": 0.0})
    for r in rows:
        if r["type"] != "TRADE": continue
        a, f = frame_of(r.get("slug")); k = f"{a}-{f}"
        mk[k]["w"].add(r["conditionId"]); mk[k]["tr"] += 1; mk[k]["vol"] += float(r.get("usdcSize") or 0)
    tv = sum(v["vol"] for v in mk.values()) or 1
    print("## (A) MARKETS TARGETED")
    print(f"{'market':<13}{'windows':>8}{'trades':>8}{'vol$':>11}{'share':>7}")
    for k, v in sorted(mk.items(), key=lambda x: -x[1]["vol"]):
        print(f"{k:<13}{len(v['w']):>8}{v['tr']:>8}{v['vol']:>11.0f}{100*v['vol']/tv:>6.1f}%")

    # ---------- per-window accumulation ----------
    W = defaultdict(lambda: {"mint":0.0,"merge":0.0,"redeem":0.0,"slug":"",
                             "bUp_s":0.0,"bUp_u":0.0,"sUp_s":0.0,"sUp_u":0.0,
                             "bDn_s":0.0,"bDn_u":0.0,"sDn_s":0.0,"sDn_u":0.0,"red":False})
    g = defaultdict(lambda: {"s":0.0,"psz":0.0})  # global side×outcome
    for r in rows:
        w = W[r["conditionId"]]; w["slug"] = r.get("slug") or w["slug"]
        t = r["type"]; usd = float(r.get("usdcSize") or 0); sz = float(r.get("size") or 0); px = float(r.get("price") or 0)
        if t == "SPLIT": w["mint"] += sz
        elif t == "MERGE": w["merge"] += usd
        elif t == "REDEEM": w["redeem"] += usd; w["red"] = True
        elif t == "TRADE":
            up = r.get("outcome") == "Up"; buy = r.get("side") == "BUY"
            g[(r.get("side"), r.get("outcome"))]["s"] += sz; g[(r.get("side"), r.get("outcome"))]["psz"] += px*sz
            if up and buy:  w["bUp_s"]+=sz; w["bUp_u"]+=usd
            elif up:        w["sUp_s"]+=sz; w["sUp_u"]+=usd
            elif buy:       w["bDn_s"]+=sz; w["bDn_u"]+=usd
            else:           w["sDn_s"]+=sz; w["sDn_u"]+=usd

    # ---------- (B) ENTRY PRICES ----------
    print("\n## (B) ENTRY PRICES — size-weighted avg px")
    print(f"{'side':<6}{'outcome':<7}{'avg_px':>9}{'shares':>11}")
    for (sd, out), v in sorted(g.items()):
        if v["s"]>0: print(f"{sd:<6}{str(out):<7}{v['psz']/v['s']:>9.4f}{v['s']:>11.0f}")

    # ---------- (C) PER-WINDOW P&L + NEUTRALITY ----------
    comp = [w for w in W.values() if w["red"] and w["mint"] > 0]
    perf = defaultdict(list); imb_ratio = []; over_list = []
    for w in comp:
        endUp = w["mint"] + w["bUp_s"] - w["sUp_s"]
        endDn = w["mint"] + w["bDn_s"] - w["sDn_s"]
        imb = endUp - endDn
        imb_ratio.append(abs(imb) / w["mint"])
        pnl = -w["mint"] - w["bUp_u"] - w["bDn_u"] + w["sUp_u"] + w["sDn_u"] + w["merge"] + w["redeem"]
        a, f = frame_of(w["slug"]); perf[f"{a}-{f}"].append((pnl, imb, w["mint"]))
        # overround on matched sold pairs (risk-free skill component)
        msell = min(w["sUp_s"], w["sDn_s"])
        if msell > 0 and w["sUp_s"]>0 and w["sDn_s"]>0:
            over_list.append((w["sUp_u"]/w["sUp_s"] + w["sDn_u"]/w["sDn_s"]) - 1.0)
    # outcome-split rigor check: is the P&L positive regardless of which side won? (neutral edge vs luck)
    out_split = defaultdict(lambda: defaultdict(list))   # frame -> winner -> [pnl]
    for w in comp:
        endUp = w["mint"]+w["bUp_s"]-w["sUp_s"]; endDn = w["mint"]+w["bDn_s"]-w["sDn_s"]
        pnl = -w["mint"]-w["bUp_u"]-w["bDn_u"]+w["sUp_u"]+w["sDn_u"]+w["merge"]+w["redeem"]
        winner = "Up" if abs(w["redeem"]-endUp) < abs(w["redeem"]-endDn) else "Dn"
        a,f = frame_of(w["slug"]); out_split[f"{a}-{f}"][winner].append(pnl)

    print(f"\n## (C) PER-WINDOW TRADING P&L ex-rebate — {len(comp)} settled windows")
    print(f"{'market':<13}{'win':>5}{'medPnL':>9}{'meanPnL':>9}{'%neg':>6}{'totPnL':>9}{'med|imb|/mint':>14}")
    allp = []
    for k, lst in sorted(perf.items(), key=lambda x: -len(x[1])):
        ps = sorted(p for p,_,_ in lst); allp += [p for p,_,_ in lst]
        ir = sorted(abs(i)/m for _,i,m in lst); med_ir = ir[len(ir)//2]
        med = ps[len(ps)//2]; mean = sum(ps)/len(ps); neg = 100*sum(1 for x in ps if x<0)/len(ps)
        print(f"{k:<13}{len(ps):>5}{med:>9.2f}{mean:>9.2f}{neg:>5.0f}%{sum(ps):>9.0f}{med_ir:>13.1%}")
    if allp:
        allp.sort(); n=len(allp)
        print(f"{'ALL':<13}{n:>5}{allp[n//2]:>9.2f}{sum(allp)/n:>9.2f}"
              f"{100*sum(1 for x in allp if x<0)/n:>5.0f}%{sum(allp):>9.0f}"
              f"{(sorted(imb_ratio)[len(imb_ratio)//2]):>13.1%}")
    if over_list:
        over_list.sort()
        print(f"\n   matched-sold OVERROUND (sell_Up+sell_Dn−1), per window: "
              f"median={over_list[len(over_list)//2]:+.4f} mean={sum(over_list)/len(over_list):+.4f} "
              f"(>0 ⇒ sells a synthetic set for >$1 = risk-free skim)")
    print("\n   OUTCOME-SPLIT (median P&L by which side won — both positive ⇒ neutral edge, not luck):")
    for k in ("btc-5m","btc-15m","eth-5m","xrp-5m","sol-5m"):
        d = out_split.get(k, {})
        def med(l): l=sorted(l); return l[len(l)//2] if l else float('nan')
        up,dn = d.get("Up",[]), d.get("Dn",[])
        print(f"     {k:<8} Up-won: med={med(up):+7.2f} (n={len(up):>3})   Dn-won: med={med(dn):+7.2f} (n={len(dn):>3})")

    # ---------- (E) WITHIN-WINDOW TIMING + CHURN (btc-5m) ----------
    def wstart(slug):
        try: return int((slug or "").split("-")[-1])
        except: return None
    bins = defaultdict(lambda: {"mint":0,"buy":0,"sell":0})  # 30s bin -> counts (btc-5m)
    mint_rel = []; sold=0.0; bought=0.0; minted_tot=0.0
    for r in rows:
        a,f = frame_of(r.get("slug"))
        ws = wstart(r.get("slug"))
        if ws is None: continue
        rel = r["timestamp"] - ws
        if r["type"]=="SPLIT":
            if f=="5m" and a=="btc": mint_rel.append(rel)
            minted_tot += float(r.get("size") or 0)
        if r["type"]=="TRADE":
            if r.get("side")=="BUY": bought += float(r.get("size") or 0)
            else: sold += float(r.get("size") or 0)
            if a=="btc" and f=="5m":
                b = (rel//30)*30
                bins[b]["sell" if r.get("side")=="SELL" else "buy"] += 1
    print("\n## (E) MECHANICS")
    if mint_rel:
        mint_rel.sort(); print(f"   btc-5m MINT timing vs window-open: median {mint_rel[len(mint_rel)//2]}s "
                               f"(min {mint_rel[0]}s, neg=before open) — n={len(mint_rel)}")
    print(f"   churn: total SOLD {sold:.0f} sh vs BOUGHT {bought:.0f} sh vs MINTED {minted_tot:.0f} sets "
          f"→ sold/minted={sold/max(minted_tot,1):.0%}, bought/sold={bought/max(sold,1):.0%}")
    print("   btc-5m fills by 30s bin within the 300s window (buy/sell counts):")
    for b in sorted(bins):
        if b<0 or b>300: continue
        d=bins[b]; bar="#"*int((d['buy']+d['sell'])/max(1,sum(x['buy']+x['sell'] for x in bins.values()))*200)
        print(f"     +{int(b):>3}s  buy{d['buy']:>5} sell{d['sell']:>5}  {bar}")

    # ---------- (D) CADENCE + rebate ----------
    sp = sorted(float(r.get("size") or 0) for r in rows if r["type"]=="SPLIT")
    if sp: print(f"\n## (D) MINT sizes(sets): n={len(sp)} median={sp[len(sp)//2]:.0f} min={sp[0]:.0f} max={sp[-1]:.0f}")
    for t in ("MAKER_REBATE","TAKER_REBATE"):
        try:
            a=get(f"{BASE}?user={STD0}&type={t}&limit=4")
            rec=", ".join(f"{time.strftime('%m-%d',time.gmtime(x['timestamp']))}:${float(x.get('usdcSize') or x.get('size') or 0):.0f}" for x in a)
            print(f"   {t} recent: {rec}")
        except Exception as e: print("  rebate err",e)

if __name__ == "__main__":
    main()
