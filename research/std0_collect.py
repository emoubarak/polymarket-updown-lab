#!/usr/bin/env python3
"""Collect the full recent activity stream of the std0 market-maker proxy wallet.

std0 proxy   : 0xdf7930e89a2c47560165331863c31deca0733dcd
signer EOA   : 0x3Ec3577A6a22F9B4716C5AeFe0963a052BF703a6

Paginates data-api /activity and caches every row to research/data/std0_activity.json
so the downstream analysers (cadence / sizes / batching / latency) read one snapshot.
"""
import json, os, time, urllib.request, urllib.error

WALLET = "0xdf7930e89a2c47560165331863c31deca0733dcd"
OUT = os.path.join(os.path.dirname(__file__), "data", "std0_activity.json")
UA = {"User-Agent": "Mozilla/5.0 research"}


class CapReached(Exception):
    pass


def get(url, tries=5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            return json.load(urllib.request.urlopen(req, timeout=30))
        except urllib.error.HTTPError as e:
            if e.code == 400:  # data-api caps pagination ~offset 3000
                raise CapReached()
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))
        except (urllib.error.URLError, TimeoutError):
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def collect(max_offset=8000, limit=500):
    rows = []
    seen = set()  # (txhash, asset, side, type, timestamp) dedup
    offset = 0
    while offset <= max_offset:
        url = (f"https://data-api.polymarket.com/activity?user={WALLET}"
               f"&limit={limit}&offset={offset}")
        try:
            batch = get(url)
        except CapReached:
            print(f"offset {offset}: data-api pagination cap reached, stopping")
            break
        if not batch:
            break
        new = 0
        for r in batch:
            k = (r.get("transactionHash"), r.get("asset"), r.get("side"),
                 r.get("type"), r.get("timestamp"), r.get("size"))
            if k in seen:
                continue
            seen.add(k)
            rows.append(r)
            new += 1
        ts0 = batch[0]["timestamp"]
        ts1 = batch[-1]["timestamp"]
        print(f"offset {offset:5d}: got {len(batch)} (+{new} new) "
              f"ts {ts1}..{ts0}  total={len(rows)}")
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(0.25)
    rows.sort(key=lambda r: r["timestamp"])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(rows, f)
    span_h = (rows[-1]["timestamp"] - rows[0]["timestamp"]) / 3600 if rows else 0
    print(f"\nsaved {len(rows)} rows to {OUT}")
    print(f"span: {span_h:.2f} h  ({rows[0]['timestamp']}..{rows[-1]['timestamp']})")
    return rows


if __name__ == "__main__":
    collect()
