"""Fetch the FULL on-chain activity history of wallet std0
(0xdf7930e89a2c47560165331863c31deca0733dcd) from the Polymarket data-api and
cache it to research/data/std0_activity.json.

KEY TRAP (truncation bias): the /activity endpoint caps offset pagination at
~3500 rows EVEN INSIDE a &start=&end= time window (offset>=3500 → HTTP 400). A
naive 6h window on a busy day holds ~10k rows so you'd silently keep only the
newest 3500 (= 65% data loss, skewed to busy periods). Fix: ADAPTIVE bisection —
if a window saturates (we cannot read past the cap AND the window still has older
rows we haven't reached), split it in half and recurse until each sub-window fits
under the cap. We page newest→oldest by offset within each leaf window.

Checkpoints to disk after every leaf so a kill never loses progress.
"""
from __future__ import annotations
import json, os, time, sys
import requests

WALLET = "0xdf7930e89a2c47560165331863c31deca0733dcd"
DATA_API = "https://data-api.polymarket.com"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "std0_activity.json")
CAP = 3000          # safe offset ceiling (real cap ~3500; stay under it)

S = requests.Session()
S.headers["User-Agent"] = "pmlab-research/std0"

ALL = {}            # key -> row (dedup)


def _key(r):
    return (r.get("transactionHash"), r.get("asset"), r.get("side"),
            r.get("size"), r.get("timestamp"), r.get("type"))


def page(start, end):
    """Return (rows, saturated). rows = all rows readable in [start,end) up to the
    offset cap; saturated=True means we hit the cap and there are MORE older rows
    in the window that we could NOT reach (caller must bisect)."""
    rows = []
    offset = 0
    saturated = False
    while True:
        ok = None
        for attempt in range(3):
            try:
                r = S.get(f"{DATA_API}/activity",
                          params={"user": WALLET, "limit": 500, "offset": offset,
                                  "start": start, "end": end}, timeout=20)
                if r.status_code == 400:        # offset cap hit
                    ok = "CAP"
                    break
                r.raise_for_status()
                ok = r.json()
                break
            except requests.HTTPError:
                ok = "CAP" if r.status_code == 400 else None
                if ok == "CAP":
                    break
                time.sleep(1.0 * (attempt + 1))
            except Exception:
                time.sleep(1.0 * (attempt + 1))
        if ok == "CAP" or ok is None:
            # cap (or hard fail): if we already pulled a full cap's worth, the
            # window has more rows below us → saturated.
            if offset >= CAP:
                saturated = True
            break
        if not ok:
            break
        rows.extend(ok)
        if len(ok) < 500:
            break
        offset += 500
        if offset >= CAP:
            saturated = True       # next page would exceed the cap
            break
        time.sleep(0.1)
    return rows, saturated


def harvest(start, end, depth=0):
    rows, sat = page(start, end)
    new = 0
    for r in rows:
        k = _key(r)
        if k not in ALL:
            ALL[k] = r
            new += 1
    span_h = (end - start) / 3600
    tag = "  " * depth
    print(f"{tag}[{time.strftime('%m-%d %H:%M', time.gmtime(start))} +{span_h:.2f}h] "
          f"{len(rows)} rows (+{new}) sat={sat} total={len(ALL)}", flush=True)
    if sat and (end - start) > 120:
        mid = (start + end) // 2
        harvest(mid, end, depth + 1)       # newer half first (keeps order intuitive)
        harvest(start, mid, depth + 1)
    else:
        _checkpoint()


def _checkpoint():
    rows = sorted(ALL.values(), key=lambda r: r["timestamp"])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rows, f)
    os.replace(tmp, OUT)


def main():
    now = int(time.time()) + 600
    first = 1779000000        # ~2026-05-21, before the wallet's first trade
    step = 3 * 3600           # start with 3h windows; bisection shrinks busy ones
    t1 = now
    while t1 > first:
        t0 = t1 - step
        harvest(t0, t1)
        t1 = t0
    _checkpoint()
    rows = sorted(ALL.values(), key=lambda r: r["timestamp"])
    if rows:
        lo = time.strftime('%Y-%m-%d', time.gmtime(rows[0]["timestamp"]))
        hi = time.strftime('%Y-%m-%d', time.gmtime(rows[-1]["timestamp"]))
        print(f"\nSAVED {len(rows)} rows  {lo} → {hi}  → {OUT}", flush=True)


if __name__ == "__main__":
    main()
