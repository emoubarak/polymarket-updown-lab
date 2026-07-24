"""Supplementary detail stats for the std0 strategy spec:
  - SPLIT (mint) timing relative to window open  (does it pre-mint?)
  - REDEEM timing relative to window close       (when does it harvest?)
  - quote/trade cadence (inter-fill seconds) and fills-per-window
  - settlement cutoff: activity in the last N seconds of the window
  - per-window mint size vs shares actually sold (ammunition vs usage)
  - bid/ask offset from mid per token (the quoting spread it posts)
"""
from __future__ import annotations
import json, os, statistics
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "data", "std0_activity.json")
FRAME_SEC = {"5m": 300, "15m": 900}


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs)-1, max(0, int(round(p*(len(xs)-1)))))] if xs else float("nan")


def frame_of(s): return "5m" if "-5m-" in s else ("15m" if "-15m-" in s else "?")
def ws_of(s):
    try: return int(s.rsplit("-",1)[1])
    except Exception: return None
def mkt_of(s): return s.rsplit("-",1)[0]


def main():
    rows = json.load(open(SRC))
    for r in rows:
        r["ws"] = ws_of(r["slug"]); r["frame"] = frame_of(r["slug"])
        r["sec"] = FRAME_SEC.get(r["frame"])
        r["tiw"] = (r["timestamp"]-r["ws"]) if r["ws"] else None
    rows.sort(key=lambda r: r["timestamp"])

    splits = [r for r in rows if r["type"]=="SPLIT" and r["tiw"] is not None]
    redeems = [r for r in rows if r["type"]=="REDEEM" and r["tiw"] is not None]
    trades = [r for r in rows if r["type"]=="TRADE" and r["tiw"] is not None]

    print("=== SPLIT (mint) timing: seconds relative to window OPEN (neg = pre-mint) ===")
    for fr in ("5m","15m"):
        ts = [r["tiw"] for r in splits if r["frame"]==fr]
        if ts:
            print(f"  {fr}: n={len(ts)} med={statistics.median(ts):+.0f}s "
                  f"p10={q(ts,.1):+.0f} p90={q(ts,.9):+.0f}  "
                  f"frac pre-open(tiw<0)={100*sum(1 for x in ts if x<0)/len(ts):.0f}%")

    print("\n=== REDEEM timing: seconds relative to window OPEN (frame_sec=close) ===")
    for fr in ("5m","15m"):
        sec = FRAME_SEC[fr]
        ts = [r["tiw"] for r in redeems if r["frame"]==fr]
        if ts:
            after = [t-sec for t in ts]   # seconds after CLOSE
            print(f"  {fr}: n={len(ts)} med after close={statistics.median(after):+.0f}s "
                  f"p10={q(after,.1):+.0f} p90={q(after,.9):+.0f}")

    print("\n=== TRADE settlement cutoff: % of window $ in last 60s / last 30s ===")
    for fr in ("5m","15m"):
        sec = FRAME_SEC[fr]
        tr = [r for r in trades if r["frame"]==fr and 0<=r["tiw"]<=sec]
        tot = sum(r["usdcSize"] for r in tr) or 1
        last60 = sum(r["usdcSize"] for r in tr if r["tiw"]>sec-60)
        last30 = sum(r["usdcSize"] for r in tr if r["tiw"]>sec-30)
        first_half = sum(r["usdcSize"] for r in tr if r["tiw"]<=sec/2)
        print(f"  {fr}: first-half={100*first_half/tot:.0f}%  last60s={100*last60/tot:.1f}%  "
              f"last30s={100*last30/tot:.1f}%")

    # cadence + fills-per-window (btc-5m)
    print("\n=== CADENCE & fills/window (btc-5m) ===")
    byw = defaultdict(list)
    for r in trades:
        if mkt_of(r["slug"])=="btc-updown-5m":
            byw[r["ws"]].append(r)
    nfills = [len(v) for v in byw.values()]
    inter = []
    for v in byw.values():
        ts = sorted(x["timestamp"] for x in v)
        inter += [b-a for a,b in zip(ts,ts[1:]) if b>=a]
    if nfills:
        print(f"  fills/window: med={statistics.median(nfills):.0f} "
              f"p10={q(nfills,.1):.0f} p90={q(nfills,.9):.0f}")
        print(f"  inter-fill gap: med={statistics.median(inter):.1f}s "
              f"p90={q(inter,.9):.0f}s (0=same-second burst)")

    # mint ammo vs usage per window (btc-5m): split shares vs sold shares
    print("\n=== MINT AMMO vs SOLD (per btc-5m window) ===")
    wsplit = defaultdict(float); wsold = defaultdict(float); wbought = defaultdict(float)
    for r in rows:
        if r["ws"] is None or mkt_of(r["slug"])!="btc-updown-5m": continue
        if r["type"]=="SPLIT": wsplit[r["ws"]] += r["size"]
        elif r["type"]=="TRADE" and r["side"]=="SELL": wsold[r["ws"]] += r["size"]
        elif r["type"]=="TRADE" and r["side"]=="BUY": wbought[r["ws"]] += r["size"]
    minted = [v for v in wsplit.values() if v>0]
    used = [wsold[w]/wsplit[w] for w in wsplit if wsplit[w]>0]
    if minted:
        print(f"  minted sets/window: med={statistics.median(minted):.0f} "
              f"p90={q(minted,.9):.0f}")
        print(f"  sold/minted ratio: med={statistics.median(used):.2f} "
              f"p90={q(used,.9):.2f}  (most minted inventory is redeemed, not sold)")

    # quoting spread per token per window: best buy vs best sell price gap
    print("\n=== POSTED SPREAD per token/window (max BUY px vs min SELL px is wrong; use VWAP) ===")
    # already in std0_mm; here: distribution of (sell_vwap - buy_vwap) per token-window
    W = defaultdict(lambda: {"b": defaultdict(list), "s": defaultdict(list)})
    for r in trades:
        key = (mkt_of(r["slug"]), r["ws"])
        side = "b" if r["side"]=="BUY" else "s"
        W[key][side][r["outcome"]].append((r["size"], r["price"]))
    spreads = defaultdict(list)
    for (m,ws),w in W.items():
        for oc in ("Up","Down"):
            b=w["b"].get(oc); s=w["s"].get(oc)
            if b and s:
                bsh=sum(x for x,_ in b); ssh=sum(x for x,_ in s)
                bvw=sum(x*p for x,p in b)/bsh; svw=sum(x*p for x,p in s)/ssh
                if svw>bvw: spreads[m].append(svw-bvw)
    for m in sorted(spreads):
        sp=spreads[m]
        print(f"  {m:16} full posted spread (askVWAP−bidVWAP): "
              f"med={statistics.median(sp):.3f} p25={q(sp,.25):.3f} p75={q(sp,.75):.3f} n={len(sp)}")


if __name__ == "__main__":
    main()
