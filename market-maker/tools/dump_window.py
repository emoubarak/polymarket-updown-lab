#!/usr/bin/env python3
"""Dump every event of one btc-5m window + reconcile cashflow, for
verifying REDEEM accounting against splits/trades."""
import json, re, collections, pathlib

rows = [json.loads(l) for l in (pathlib.Path.home()/"rebate"/"std0_hist.jsonl").read_text().splitlines() if l.strip()]

def ws(s):
    m = re.search(r"-(\d+)$", s or "")
    return int(m.group(1)) if m else None

wins = collections.defaultdict(list)
for r in rows:
    if (r.get("slug") or "").startswith("btc-updown-5m-"):
        w = ws(r["slug"])
        if w:
            wins[w].append(r)

# first window with both a split and a redeem
target = None
for w, evs in sorted(wins.items()):
    if any(e["type"] == "REDEEM" for e in evs) and any(e["type"] == "SPLIT" for e in evs):
        target = w
        break

evs = sorted(wins[target], key=lambda x: x["timestamp"])
print("window", target, "events", len(evs))
up = dn = cash = 0.0
for e in evs:
    off = e["timestamp"] - target
    t, side = e["type"], e.get("side", "")
    sz, px = e.get("size", 0), e.get("price", 0)
    usd = e.get("usdcSize", 0)
    oc = e.get("outcome", "")
    print(f"  {off:+5d}s {t:7} {side:4} sz={sz:8.2f} px={px:5} usd={usd:9.3f} {oc}")
    if t == "SPLIT":
        up += sz; dn += sz; cash -= sz
    elif t == "TRADE":
        sgn = -1 if side == "SELL" else 1
        cash -= sgn * usd
        if oc == "Up": up += sgn*sz
        else: dn += sgn*sz
    elif t == "REDEEM":
        cash += usd if usd else sz
print(f"\nend inventory: up={up:.2f} dn={dn:.2f}  net={up-dn:.2f}  paired={min(up,dn):.2f}")
print(f"cash P&L (incl redeem): {cash:+.2f}")
print(f"cash P&L (excl redeem, mark unredeemed pairs @ settle unknown): needs winner")
