#!/usr/bin/env python3
"""aligned — compare paper regimes on the SAME windows (controls for market
volume, which drives fill count regardless of our ladder).

Aligns neutral vs momentum vs std0 by window-start timestamp and reports
paired differences only over windows all sources cover.
"""
import pathlib, statistics as st, re, json, collections, datetime

HOME = pathlib.Path.home() / "rebate"

def slug_ws(slug):
    m = re.search(r"-(\d+)$", slug or "")
    return int(m.group(1)) if m else None

def load_csv(fn):
    # returns {window_start: (pnl, fills)}
    out = {}
    p = HOME / fn
    if not p.exists(): return out
    for line in p.read_text().splitlines()[1:]:
        f = line.split(",")
        if len(f) < 12: continue
        ws = slug_ws(f[1])
        if ws is None: continue
        try: out[ws] = (float(f[11]), int(f[5]))
        except ValueError: pass
    return out

def load_std0():
    # per-window btc-5m P&L + trade count from recorded activity
    rows, seen = [], set()
    for fn in ["std0_hist.jsonl", "std0_activity.jsonl"]:
        p = HOME / fn
        if not p.exists(): continue
        for line in p.read_text().splitlines():
            if not line.strip(): continue
            try: a = json.loads(line)
            except Exception: continue
            k=(a.get("transactionHash",""),a.get("asset",""),a.get("timestamp",0),str(a.get("size","")),a.get("side",""),a.get("type",""))
            if k in seen: continue
            seen.add(k); rows.append(a)
    wins = collections.defaultdict(list)
    for r in rows:
        s = r.get("slug","")
        if not s.startswith("btc-updown-5m-"): continue
        ws = slug_ws(s)
        if ws: wins[ws].append(r)
    out = {}
    for ws, evs in wins.items():
        up=dn=cash=split=0.0; hr=False; trades=0
        for r in evs:
            t,side=r["type"],r.get("side",""); sz=r.get("size",0); usd=r.get("usdcSize",0); oc=r.get("outcome","")
            if t=="SPLIT": up+=sz;dn+=sz;cash-=sz;split+=sz
            elif t=="TRADE":
                trades+=1; g=-1 if side=="SELL" else 1; cash-=g*usd
                if oc=="Up": up+=g*sz
                else: dn+=g*sz
            elif t=="REDEEM": cash+=(usd if usd else sz); hr=True
        if split<50: continue
        pnl = cash if hr else cash+max(up,dn)
        out[ws]=(pnl,trades)
    return out

neutral = load_csv("paper.csv")
mom     = load_csv("paper_mom.csv")
std0    = load_std0()

# windows momentum covers (the newest regime), intersect with others
common = sorted(set(mom) & set(std0))
common_n = [w for w in common if w in neutral]
print(f"momentum windows: {len(mom)}, overlapping std0: {len(common)}, "
      f"all-three: {len(common_n)}\n")

def report(label, get, wins):
    fills=[get(w)[1] for w in wins]; pnl=[get(w)[0] for w in wins]
    fills=[x for x in fills if x>0]
    if not wins: return
    print(f"{label:14} n={len(wins):3d}  fills={st.mean(fills):4.0f}  "
          f"pnl mean={st.mean(pnl):+6.2f}  median={st.median(pnl):+6.2f}  sd={st.pstdev(pnl):5.1f}")

print("=== SAME windows (momentum ∩ std0) — market volume controlled ===")
report("MOMENTUM", lambda w: mom[w], common)
report("STD0",     lambda w: std0[w], common)
if common:
    mf=st.mean([mom[w][1] for w in common if mom[w][1]>0])
    sf=st.mean([std0[w][1] for w in common if std0[w][1]>0])
    print(f"\nfill capture: {100*mf/sf:.0f}% of std0's fills on the same windows")
    # correlation of our fills with std0's (both driven by market volume)
    xs=[mom[w][1] for w in common]; ys=[std0[w][1] for w in common]
    if len(xs)>3:
        mx,my=st.mean(xs),st.mean(ys)
        cov=sum((a-mx)*(b-my) for a,b in zip(xs,ys))/len(xs)
        sx,sy=st.pstdev(xs),st.pstdev(ys)
        print(f"corr(our fills, std0 fills) = {cov/(sx*sy) if sx*sy else 0:.2f} "
              f"(high => same market drives both; ratio is the real metric)")
