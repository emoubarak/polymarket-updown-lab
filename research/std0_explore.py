"""Quick exploration of std0's recent activity (offset pages, no time window)
to understand the BUY-vs-SELL / SPLIT / REDEEM structure before the full
history analysis. Pulls up to ~3500 recent rows."""
from __future__ import annotations
import requests, statistics
from collections import Counter, defaultdict

WALLET = "0xdf7930e89a2c47560165331863c31deca0733dcd"
DATA_API = "https://data-api.polymarket.com"
S = requests.Session(); S.headers["User-Agent"] = "pmlab-research/std0"

rows = []
for off in range(0, 3500, 500):
    r = S.get(f"{DATA_API}/activity",
              params={"user": WALLET, "limit": 500, "offset": off}, timeout=20).json()
    rows.extend(r)
    if len(r) < 500:
        break
print(f"pulled {len(rows)} recent rows")

def market(slug): return slug.rsplit("-", 1)[0]

print("\n=== type counts ===", Counter(r["type"] for r in rows))
trades = [r for r in rows if r["type"] == "TRADE"]
buys = [r for r in trades if r["side"] == "BUY"]
sells = [r for r in trades if r["side"] == "SELL"]
print(f"TRADE: {len(trades)}  BUY {len(buys)}  SELL {len(sells)}")

def pstats(rs, label):
    if not rs: return
    pr = [r["price"] for r in rs]
    usd = [r["usdcSize"] for r in rs]
    print(f"\n{label}: n={len(rs)}")
    print(f"  price   p10={_q(pr,.1):.3f} med={statistics.median(pr):.3f} p90={_q(pr,.9):.3f}")
    print(f"  usdcSize med={statistics.median(usd):.1f} mean={statistics.mean(usd):.1f}")
    print(f"  outcome {Counter(r['outcome'] for r in rs)}")

def _q(xs, q):
    xs = sorted(xs); i = min(len(xs)-1, int(q*len(xs)))
    return xs[i]

pstats(buys, "BUY")
pstats(sells, "SELL")

# price buckets for buys vs sells
print("\n=== BUY price buckets ===")
bb = Counter()
for r in buys: bb[round(r["price"]*10)/10] += 1
for k in sorted(bb): print(f"  {k:.1f}: {bb[k]}")
print("=== SELL price buckets ===")
sb = Counter()
for r in sells: sb[round(r["price"]*10)/10] += 1
for k in sorted(sb): print(f"  {k:.1f}: {sb[k]}")

# per-market split
print("\n=== markets ===", Counter(market(r["slug"]) for r in trades).most_common(12))

# SPLIT / REDEEM / MERGE
for t in ("SPLIT", "MERGE", "REDEEM"):
    ex = [r for r in rows if r["type"] == t]
    if ex:
        sz = [r["size"] for r in ex]
        print(f"\n{t}: n={len(ex)} size med={statistics.median(sz):.0f} "
              f"total={sum(sz):.0f}")
