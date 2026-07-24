#!/usr/bin/env python3
"""channel_test — compare les CANAUX de données pour minimiser la latence:
Coinbase ticker vs matches vs advanced-trade, + Bitstamp trades vs order book.
But: trouver le canal le moins agrégé / le plus proche de l'événement brut.
Usage: python channel_test.py [duree_s]
"""
import asyncio, websockets, json, time, statistics, sys, datetime

DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 60
ev = []
def wms(): return time.time()*1000
def rec(feed, exch_ms):
    if exch_ms: ev.append((feed, wms()-exch_ms))

def iso(s): return datetime.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()*1000

async def run(feed, url, sub, parse):
    try:
        async with websockets.connect(url, ping_interval=15) as ws:
            if sub: await ws.send(json.dumps(sub))
            async for m in ws:
                try: parse(json.loads(m))
                except Exception: pass
    except Exception as e: print(f"{feed}: {str(e)[:70]}")

async def main():
    T = [
      run("cb_ticker","wss://ws-feed.exchange.coinbase.com",
          {"type":"subscribe","channels":[{"name":"ticker","product_ids":["BTC-USD"]}]},
          lambda d: d.get("type")=="ticker" and d.get("time") and rec("cb_ticker", iso(d["time"]))),
      run("cb_matches","wss://ws-feed.exchange.coinbase.com",
          {"type":"subscribe","channels":[{"name":"matches","product_ids":["BTC-USD"]}]},
          lambda d: d.get("type")=="match" and d.get("time") and rec("cb_matches", iso(d["time"]))),
      run("cb_adv","wss://advanced-trade-ws.coinbase.com",
          {"type":"subscribe","channel":"market_trades","product_ids":["BTC-USD"]},
          lambda d: [rec("cb_adv", iso(t["time"])) for e in d.get("events",[]) for t in e.get("trades",[]) if t.get("time")]),
      run("bitstamp_tr","wss://ws.bitstamp.net",
          {"event":"bts:subscribe","data":{"channel":"live_trades_btcusd"}},
          lambda d: d.get("event")=="trade" and rec("bitstamp_tr", int(d["data"]["microtimestamp"])/1000)),
      run("bitstamp_ob","wss://ws.bitstamp.net",
          {"event":"bts:subscribe","data":{"channel":"order_book_btcusd"}},
          lambda d: d.get("event")=="data" and rec("bitstamp_ob", int(d["data"]["microtimestamp"])/1000)),
    ]
    try: await asyncio.wait_for(asyncio.gather(*T), timeout=DUR)
    except asyncio.TimeoutError: pass

asyncio.run(main())
print(f"\n=== canaux, {DUR:.0f}s ===")
print(f"{'canal':>14} {'updates':>8} {'rate/s':>7} {'lat_med':>8} {'lat_min':>8} {'lat_p10':>8}")
for f in ("cb_ticker","cb_matches","cb_adv","bitstamp_tr","bitstamp_ob"):
    L=[l for ff,l in ev if ff==f]
    if L:
        p10=statistics.quantiles(L,n=10)[0] if len(L)>=10 else min(L)
        print(f"{f:>14} {len(L):>8} {len(L)/DUR:>7.1f} {statistics.median(L):>8.0f} {min(L):>8.0f} {p10:>8.0f}")
    else:
        print(f"{f:>14} {0:>8} {'-':>7} {'-':>8} {'-':>8} {'-':>8}")
