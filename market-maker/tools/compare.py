#!/usr/bin/env python3
"""compare — paper (us) vs live (std0) per-window trading P&L distribution.

Prints both distributions side by side so the paper strategy can be tuned
until it matches the competitor. Reads our paper.csv (pnl column 12) and
recomputes std0's target via std0_pnl's mark-to-settlement logic.
"""
import subprocess, statistics, pathlib, sys, json, re, collections, datetime

HOME = pathlib.Path.home() / "rebate"

def stats(v, label):
    v = sorted(v); n = len(v)
    if not n:
        print(f"{label:10} (no windows yet)"); return
    win = 100*sum(1 for x in v if x > 0.005)/n
    print(f"{label:10} n={n:4d}  mean={statistics.mean(v):+7.2f}  "
          f"median={statistics.median(v):+7.2f}  sd={statistics.pstdev(v):6.1f}  "
          f"win={win:3.0f}%  p25={v[n//4]:+7.2f}  p75={v[3*n//4]:+7.2f}")

# --- our paper P&L + fills per window (both regimes) ---
def load(fn):
    pnl, fills = [], []
    p = HOME / fn
    if not p.exists():
        return pnl, fills
    for line in p.read_text().splitlines()[1:]:
        f = line.split(",")
        if len(f) > 11:
            try:
                pnl.append(float(f[11])); fills.append(int(f[5]))
            except ValueError:
                pass
    return pnl, fills

neutral, nfills = load("paper.csv")
mom, mfills = load("paper_mom.csv")

import statistics as st
def fillstat(fills, label):
    fills = [x for x in fills if x > 5]
    if fills:
        print(f"{label:24} fills/window: mean {st.mean(fills):.0f} (std0 ~48)")
print()
fillstat(nfills, "PAPER neutral (guard)")
fillstat(mfills, "PAPER momentum (hold)")

# --- std0 target (btc-5m) via the existing tool, parse its summary line ---
out = subprocess.run(["python3", str(HOME/"tools"/"std0_pnl.py")],
                     capture_output=True, text=True).stdout
target = []
for line in out.splitlines():
    if line.strip().startswith("btc-updown-5m "):
        pass
m = re.search(r"btc-5m TARGET: mean ([+-][\d.]+).*median ([+-][\d.]+), sd ([\d.]+), n=(\d+)", out)

print("\n=== per-window TRADING P&L: paper regimes vs std0 (live) ===\n")
stats(neutral, "NEUTRAL")
stats(mom,      "MOMENTUM")
if m:
    print(f"{'STD0 live':10} n={m.group(4):>4}  mean={float(m.group(1)):+7.2f}  "
          f"median={float(m.group(2)):+7.2f}  sd={float(m.group(3)):6.1f}  (btc-5m target)")
else:
    print("STD0 live  (target unavailable)")
print("\nnote: rebates NOT included — trading P&L only. More fills = more")
print("rebate volume; std0's edge is rebates, so fills/window is the KPI.")
