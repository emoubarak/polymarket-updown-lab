#!/usr/bin/env python3
"""Probe 2: distinguish the two reward programs.
 (A) Classic LIQUIDITY REWARDS (Qmin scoring, fixed daily pool per market via CLOB
     /rewards/markets/current -> rate_per_day).
 (B) MAKER REBATES (taker-fee redistribution, gamma feeSchedule.rebateRate).
Confirm which one crypto Up/Down markets are in, dump ALL reward fields, and check
whether std0 shows up under the Qmin user-markets endpoint for a crypto condition.
"""
import json
import time
import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
STD0 = "0xdf7930e89a2c47560165331863c31deca0733dcd"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 research"


def get(url, **p):
    r = S.get(url, params=p, timeout=30)
    r.raise_for_status()
    return r.json()


def cur_slug(coin, interval, sec, k=0):
    start = int(time.time() // sec) * sec
    return f"{coin}-updown-{interval}-{start + k*sec}"


print("=" * 72)
print("(1) FULL gamma market reward/fee fields for a live btc-updown-5m & -15m")
print("=" * 72)
conds = {}
for interval, sec in (("5m", 300), ("15m", 900)):
    found = None
    for k in (0, 1, -1, 2):
        slug = cur_slug("btc", interval, sec, k)
        ev = get(f"{GAMMA}/events", slug=slug)
        if ev and ev[0].get("markets"):
            found = (slug, ev[0]["markets"][0])
            break
    if not found:
        print(f"  no live {interval} market found")
        continue
    slug, m = found
    conds[interval] = m.get("conditionId")
    print(f"\n--- {slug} ---")
    keys = ("rewardsMinSize", "rewardsMaxSpread", "clobRewards", "rewardsDailyRate",
            "makerBaseFee", "takerBaseFee", "makerRebatesFeeShareBps", "feeType",
            "feeSchedule", "feesEnabled", "holdingRewardsEnabled", "spread",
            "enableOrderBook", "competitive", "umaResolutionStatus")
    for key in keys:
        if key in m:
            print(f"    {key}: {m[key]}")
    # show ANY remaining key containing reward
    for key in m:
        if ("reward" in key.lower() or "rebate" in key.lower()) and key not in keys:
            print(f"    [extra] {key}: {m[key]}")

print("\n" + "=" * 72)
print("(2) Is each crypto conditionId in the CLOB Qmin liquidity-rewards pool?")
print("=" * 72)
for interval, cid in conds.items():
    if not cid:
        continue
    r = S.get(f"{CLOB}/rewards/markets/{cid}", timeout=30).json()
    print(f"  {interval} {cid[:14]}..  /rewards/markets -> count={r.get('count')} data={r.get('data')}")

print("\n" + "=" * 72)
print("(3) CLOB /rewards/markets/current : how many markets, what rates, any crypto?")
print("=" * 72)
allmk, cursor = [], ""
while True:
    j = S.get(f"{CLOB}/rewards/markets/current",
              params={"next_cursor": cursor} if cursor else {}, timeout=30).json()
    allmk.extend(j.get("data", []))
    cursor = j.get("next_cursor")
    if not cursor or cursor == "LTE=" or len(allmk) > 4000:
        break
print(f"  total markets in Qmin liquidity-rewards program: {len(allmk)}")
rates = {}
crypto_hits = []
for mk in allmk:
    rd = mk.get("total_daily_rate") or mk.get("native_daily_rate") or 0
    rates[rd] = rates.get(rd, 0) + 1
print("  daily-rate histogram (rate_per_day -> #markets):",
      dict(sorted(rates.items())))
# check none of our crypto updown conditions are present
cset = set(c for c in conds.values() if c)
present = [mk for mk in allmk if mk.get("condition_id") in cset]
print(f"  crypto updown conditions present in Qmin pool: {len(present)}")
# Show a couple sample reward configs (the band/size params used by the Qmin program)
print("  sample Qmin market configs:")
for mk in allmk[:3]:
    print("   ", json.dumps({k: mk.get(k) for k in
          ("condition_id", "rewards_max_spread", "rewards_min_size",
           "native_daily_rate", "total_daily_rate", "rewards_config")},
          default=str)[:400])

print("\n" + "=" * 72)
print("(4) std0 under Qmin /rewards/user/markets (does it earn classic LP rewards?)")
print("=" * 72)
for ep in (f"{CLOB}/rewards/user/markets?user={STD0}",
           f"{CLOB}/rewards/user?user={STD0}"):
    try:
        r = S.get(ep, timeout=30)
        print(f"  GET {ep}\n    HTTP {r.status_code}: {r.text[:500]}")
    except Exception as e:
        print(f"  {ep} -> {e}")

print("\n" + "=" * 72)
print("(5) Decode makerBaseFee/takerBaseFee + the maker-rebate split")
print("=" * 72)
print("  gamma makerBaseFee/takerBaseFee=1000 are LEGACY bps placeholders;")
print("  the LIVE fee is feeSchedule {rate:0.07, takerOnly:true, rebateRate:0.2}")
print("  makerRebatesFeeShareBps=10000 => 100% of the rebate pool goes to MAKERS.")
