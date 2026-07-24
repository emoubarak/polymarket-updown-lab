#!/usr/bin/env python3
"""std0watch — continuous recorder of std0's public activity.

Polls data-api every 15s, dedupes, appends JSONL. Feeds two consumers:
  1. offline behavior fitting (per-window quoting/momentum patterns)
  2. live per-window P&L of std0 for paper-vs-live comparison
Read-only: touches public endpoints only, never trades.
"""
import json
import time
import urllib.request
import pathlib

STD0 = "0xdf7930e89a2c47560165331863c31deca0733dcd"
OUT = pathlib.Path.home() / "rebate" / "std0_activity.jsonl"
SEEN_MAX = 20000

def fetch(limit=100):
    url = (f"https://data-api.polymarket.com/activity?user={STD0}"
           f"&limit={limit}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def key(a):
    return (a.get("transactionHash", ""), a.get("asset", ""),
            a.get("timestamp", 0), str(a.get("size", "")),
            a.get("side", ""), a.get("type", ""))

def main():
    seen = {}
    # Warm the dedupe set from the tail of an existing file.
    if OUT.exists():
        for line in OUT.read_text().splitlines()[-SEEN_MAX:]:
            try:
                seen[key(json.loads(line))] = True
            except Exception:
                pass
    print(f"std0watch: recording to {OUT} ({len(seen)} seen)", flush=True)
    while True:
        try:
            rows = fetch()
            fresh = [a for a in rows if key(a) not in seen]
            if fresh:
                with OUT.open("a") as f:
                    for a in sorted(fresh, key=lambda x: x.get("timestamp", 0)):
                        f.write(json.dumps(a, separators=(",", ":")) + "\n")
                        seen[key(a)] = True
                print(f"{time.strftime('%H:%M:%S')} +{len(fresh)} events "
                      f"(total seen {len(seen)})", flush=True)
            if len(seen) > SEEN_MAX * 2:
                # Keep the dedupe map bounded; old events can't reappear in
                # a limit-100 head query anyway.
                seen = dict(list(seen.items())[-SEEN_MAX:])
        except Exception as e:
            print(f"{time.strftime('%H:%M:%S')} fetch error: {e}", flush=True)
        time.sleep(15)

if __name__ == "__main__":
    main()
