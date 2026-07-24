#!/usr/bin/env python3
"""eps verdict reader for the HFT-MM measurement run. Parses the `FILL ...` lines that
hftmm.on_fill emits and computes our per-fill execution edge:
    edge = (mid - px) if BUY  else (px - mid)     [+ = capture spread, - = adverse selection]
This is THE number that decides the strategy: std0 runs +0.22c; breakeven (vs ~$1/window btc-5m
rebate at our fill rate) is ~ -0.33c. >= -0.3c => the WS speed beat the pickoff => scale. << 0 => stop.

Usage: python3 eps_reader.py <hft_measure_btc_5m.log>
"""
import sys, re, statistics as st
from collections import defaultdict

LOG = sys.argv[1] if len(sys.argv) > 1 else "hft_measure_btc_5m.log"
pat = re.compile(r"FILL (Up|Down) (BUY|SELL) px=([\d.]+) mid=([\d.None]+) sz=([\d.]+)")
fills = []
for line in open(LOG):
    m = pat.search(line)
    if not m:
        continue
    oc, side, px, mid, sz = m.group(1), m.group(2), float(m.group(3)), m.group(4), float(m.group(5))
    fills.append((oc, side, px, (None if mid == "None" else float(mid)), sz))

n = len(fills)
print(f"== eps reader: {n} fills in {LOG} ==")
if n == 0:
    print("no fills yet."); sys.exit()

def edge(side, px, mid):
    if mid is None: return None
    return (mid - px) if side == "BUY" else (px - mid)

ed = [(e, sz) for (oc, side, px, mid, sz) in fills for e in [edge(side, px, mid)] if e is not None]
if ed:
    vals = [e for e, _ in ed]; cents = [e * 100 for e in vals]
    swsum = sum(e * sz for e, sz in ed); shsum = sum(sz for _, sz in ed)
    sw = swsum / shsum * 100
    fav = 100 * sum(1 for v in vals if v > 0) / len(vals)
    print(f"per-fill edge vs mid:  mean={st.mean(cents):+.2f}c  median={st.median(cents):+.2f}c  "
          f"size-weighted={sw:+.2f}c  %favorable={fav:.0f}%  (n_with_mid={len(vals)})")
else:
    sw = None
    print("(no fills had a mid benchmark)")

# buy/sell VWAP per outcome (cross-check, drift-sensitive)
buy = defaultdict(lambda: [0., 0.]); sell = defaultdict(lambda: [0., 0.])
reb = 0.
for oc, side, px, mid, sz in fills:
    reb += 0.014 * px * (1 - px) * sz
    (buy if side == "BUY" else sell)[oc][0] += sz
    (buy if side == "BUY" else sell)[oc][1] += sz * px
print("\nbuy/sell VWAP per outcome (cross-check):")
for oc in ("Up", "Down"):
    bs, bd = buy[oc]; ss, sd = sell[oc]
    bv = bd / bs if bs else float("nan"); sv = sd / ss if ss else float("nan")
    print(f"  {oc:<5} buy={bv:.3f}({bs:.0f}sh)  sell={sv:.3f}({ss:.0f}sh)  sell-buy={ (sv-bv)*100:+.2f}c")
print(f"\nrebate so far (0.014·p(1−p)·sz) = ${reb:.3f}")

# VERDICT
if sw is not None:
    bar_bk, bar_std0 = -0.33, 0.22
    if sw >= bar_std0:   v = "✅ STRONG — matches/beats std0. Scale up."
    elif sw >= bar_bk:   v = "🟡 VIABLE — above breakeven; net-positive at our btc-5m rebate. Scale carefully."
    elif sw >= -1.0:     v = "🟠 MARGINAL/NEG — below breakeven; WS helped but not enough. Don't scale yet."
    else:                v = "❌ BLEEDS — heavy pickoff (like the old poll bot). WS didn't save it. Stop."
    print(f"\nVERDICT (size-weighted eps {sw:+.2f}c vs breakeven -0.33c / std0 +0.22c):\n  {v}")
