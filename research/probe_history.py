"""Probe what Polymarket actually serves for *closed* BTC up/down windows.

Decides feasibility of a retroactive backtest:
  - Gamma /events by deterministic slug for resolved windows?
  - CLOB /prices-history price track for a token?
  - data-api /trades tape (needed for a faithful tao maker sim)?
  - how far back are markets still discoverable?
"""
from __future__ import annotations
import json
import time
import urllib.parse
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"


def get(url, **params):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "pmlab-probe/0.1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def probe(window_start: int, interval: str = "5m"):
    slug = f"btc-updown-{interval}-{window_start}"
    age_h = (time.time() - window_start) / 3600
    print(f"\n=== {slug}  (clos il y a ~{age_h:.1f} h) ===")
    try:
        events = get(f"{GAMMA}/events", slug=slug)
    except Exception as e:
        print(f"  Gamma ERROR: {e}")
        return
    if not events:
        print("  Gamma: AUCUN event (slug introuvable)")
        return
    m = events[0]["markets"][0]
    tokens = json.loads(m["clobTokenIds"])
    outcomes = json.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
    by = dict(zip(outcomes, tokens))
    print(f"  Gamma OK: closed={m.get('closed')} umaResolution={m.get('umaResolutionStatus')} "
          f"outcomePrices={m.get('outcomePrices')}")
    up = by.get("Up")
    # prices-history for Up token
    try:
        ph = get(f"{CLOB}/prices-history", market=up,
                 startTs=window_start - 120, endTs=window_start + 360, fidelity=1)
        h = ph.get("history", [])
        pts = [(p["t"], round(p["p"], 3)) for p in h]
        print(f"  prices-history Up: {len(h)} points  {pts[:8]}")
    except Exception as e:
        print(f"  prices-history ERROR: {e}")
    # data-api trades tape
    for key in ("market", "condition_id"):
        try:
            tr = get(f"{DATA}/trades", **{key: m["conditionId"], "limit": 5})
            n = len(tr) if isinstance(tr, list) else "?"
            sample = tr[0] if isinstance(tr, list) and tr else None
            print(f"  data-api/trades?{key}: n={n} sample_keys={list(sample) if sample else None}")
            break
        except Exception as e:
            print(f"  data-api/trades?{key} ERROR: {e}")


if __name__ == "__main__":
    now = int(time.time())
    base = (now // 300) * 300
    # recent (minutes/hours old) -> older (a day) -> much older (days)
    for back_min in (15, 60, 240, 720, 1440, 1440 * 3, 1440 * 7):
        probe(base - back_min * 60)
