#!/usr/bin/env python3
"""Probe Polymarket liquidity-rewards mechanics: std0 rebate feed, current
crypto Up/Down reward config (gamma), and CLOB rewards endpoints.

Read-only, public endpoints only (no creds). Run: python3 research/rewards_probe.py
"""
import json
import time
import sys
from collections import defaultdict
from datetime import datetime, timezone
import requests

DATA = "https://data-api.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
STD0 = "0xdf7930e89a2c47560165331863c31deca0733dcd"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 research"


def get(url, **params):
    r = S.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def jdump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, default=str)


# ---------------------------------------------------------------- std0 rebates
def std0_rebates():
    print("=" * 70)
    print("STD0 REBATE FEED (data-api/activity)")
    print("=" * 70)
    out = {}
    for typ in ("MAKER_REBATE", "TAKER_REBATE"):
        rows = []
        # paginate by offset
        off = 0
        while True:
            batch = get(f"{DATA}/activity", user=STD0, type=typ, limit=500, offset=off)
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < 500:
                break
            off += 500
            if off > 5000:
                break
        out[typ] = rows
        print(f"\n--- {typ}: {len(rows)} rows ---")
        if not rows:
            print("  (none)")
            continue
        # sample row to see schema
        print("  sample row keys:", sorted(rows[0].keys()))
        print("  sample row:", json.dumps(rows[0], default=str)[:500])
        # aggregate
        total = 0.0
        by_day = defaultdict(float)
        by_day_n = defaultdict(int)
        by_market = defaultdict(float)
        amts = []
        for r in rows:
            amt = float(r.get("usdcSize") or r.get("size") or r.get("amount") or 0)
            total += amt
            amts.append(amt)
            ts = r.get("timestamp") or r.get("time") or 0
            try:
                day = datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                day = "?"
            by_day[day] += amt
            by_day_n[day] += 1
            mk = r.get("title") or r.get("slug") or r.get("conditionId") or "?"
            by_market[mk] += amt
        amts.sort()
        n = len(amts)
        print(f"  TOTAL ${total:,.2f}  n={n}  median=${amts[n//2]:,.2f}  "
              f"min=${amts[0]:,.2f}  max=${amts[-1]:,.2f}")
        print("  last 14 days:")
        for day in sorted(by_day)[-14:]:
            print(f"    {day}  ${by_day[day]:8,.2f}  ({by_day_n[day]} payouts)")
        print("  top markets by $:")
        for mk, v in sorted(by_market.items(), key=lambda x: -x[1])[:10]:
            print(f"    ${v:9,.2f}  {str(mk)[:60]}")
    jdump(out, "research/data/std0_rebates.json")
    return out


# ------------------------------------------------------ current updown markets
def current_updown(coin="btc"):
    print("\n" + "=" * 70)
    print("CURRENT CRYPTO UP/DOWN REWARD CONFIG (gamma)")
    print("=" * 70)
    result = {}
    now = time.time()
    for interval, sec in (("5m", 300), ("15m", 900)):
        start = int(now // sec) * sec
        markets = {}
        # try current and a couple future/past windows to find a live one with reward cfg
        for k in (0, 1, -1, 2, -2):
            ws = start + k * sec
            slug = f"{coin}-updown-{interval}-{ws}"
            try:
                ev = get(f"{GAMMA}/events", slug=slug)
            except Exception as e:
                ev = None
            if ev and ev[0].get("markets"):
                m = ev[0]["markets"][0]
                markets[slug] = m
                # also fetch via /markets?slug to compare fields
                try:
                    mk2 = get(f"{GAMMA}/markets", slug=slug)
                except Exception:
                    mk2 = None
                print(f"\n--- {slug}  (k={k}) ---")
                reward_keys = [key for key in m
                               if "reward" in key.lower() or "fee" in key.lower()
                               or "clob" in key.lower() or "spread" in key.lower()
                               or "incent" in key.lower() or "minSize" in key]
                for key in reward_keys:
                    print(f"    {key}: {m[key]}")
                # dump useful identifiers
                for key in ("id", "conditionId", "clobTokenIds", "questionID",
                            "startDate", "endDate", "closed", "active"):
                    if key in m:
                        print(f"    {key}: {m[key]}")
                if mk2:
                    extra = {key: mk2[0][key] for key in mk2[0]
                             if ("reward" in key.lower() or "spread" in key.lower()
                                 or "clob" in key.lower()) and key not in m}
                    if extra:
                        print("    [/markets extra reward fields]:", extra)
                break
        result[interval] = markets
    jdump(result, f"research/data/updown_reward_cfg_{coin}.json")
    return result


# ------------------------------------------------------------- CLOB endpoints
def clob_rewards(markets):
    print("\n" + "=" * 70)
    print("CLOB REWARDS ENDPOINTS")
    print("=" * 70)
    # collect a condition id + market id from gamma result
    cond_ids, mkt_ids = [], []
    for interval, ms in markets.items():
        for slug, m in ms.items():
            if m.get("conditionId"):
                cond_ids.append((slug, m["conditionId"]))
            if m.get("id"):
                mkt_ids.append((slug, m["id"]))

    endpoints = [
        ("GET /rewards/markets/current", f"{CLOB}/rewards/markets/current", {}),
        ("GET /rewards/markets", f"{CLOB}/rewards/markets", {}),
    ]
    for label, url, params in endpoints:
        try:
            r = S.get(url, params=params, timeout=30)
            print(f"\n{label} -> HTTP {r.status_code}")
            txt = r.text[:800]
            print("  ", txt)
        except Exception as e:
            print(f"\n{label} -> ERROR {e}")

    for slug, cid in cond_ids[:2]:
        for tmpl in (f"{CLOB}/rewards/markets/{cid}",
                     f"{CLOB}/rewards/user/markets?condition_id={cid}"):
            try:
                r = S.get(tmpl, timeout=30)
                print(f"\nGET {tmpl}\n  -> HTTP {r.status_code}: {r.text[:600]}")
            except Exception as e:
                print(f"\nGET {tmpl} -> ERROR {e}")

    # the documented py-clob-client path: GET /markets/{condition_id} on CLOB
    for slug, cid in cond_ids[:2]:
        try:
            r = S.get(f"{CLOB}/markets/{cid}", timeout=30)
            if r.status_code == 200:
                j = r.json()
                rk = {k: v for k, v in j.items()
                      if "reward" in k.lower() or "spread" in k.lower()
                      or "min" in k.lower() or "fee" in k.lower()}
                print(f"\nCLOB GET /markets/{cid} ({slug}) reward fields:")
                print("  ", json.dumps(rk, indent=2, default=str)[:1200])
        except Exception as e:
            print(f"\nCLOB /markets/{cid} -> ERROR {e}")


if __name__ == "__main__":
    import os
    os.makedirs("research/data", exist_ok=True)
    try:
        std0_rebates()
    except Exception as e:
        print("std0_rebates FAILED:", e)
    mk = {}
    for coin in ("btc",):
        try:
            mk = current_updown(coin)
        except Exception as e:
            print(f"current_updown({coin}) FAILED:", e)
    try:
        clob_rewards(mk)
    except Exception as e:
        print("clob_rewards FAILED:", e)
