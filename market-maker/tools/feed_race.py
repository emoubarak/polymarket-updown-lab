#!/usr/bin/env python3
"""feed_race — compare la latence de captation du prix BTC sur TOUS les feeds
WS majeurs, + le lead time du meilleur feed sur le book Polymarket.
Usage: python feed_race.py <UP_TOKEN> <DOWN_TOKEN> [duree_s]
"""
import asyncio, websockets, json, time, statistics, sys, datetime, bisect

UP, DOWN = sys.argv[1], sys.argv[2]
DURATION = float(sys.argv[3]) if len(sys.argv) > 3 else 90
ev = []           # (t_mono, feed, price, exch_lat_ms|None)
t0 = time.monotonic()
def mono(): return time.monotonic() - t0
def wms(): return time.time() * 1000

def rec(feed, price, exch_ms=None):
    ev.append((mono(), feed, price, (wms() - exch_ms) if exch_ms else None))

async def run(feed, url, sub, parse, **kw):
    try:
        async with websockets.connect(url, ping_interval=15, **kw) as ws:
            if sub: await ws.send(json.dumps(sub))
            async for m in ws:
                try: parse(json.loads(m))
                except Exception: pass
    except Exception as e:
        print(f"{feed} err: {str(e)[:60]}")

def iso_ms(s): return datetime.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()*1000

async def main():
    tasks = [
        run("coinbase", "wss://ws-feed.exchange.coinbase.com",
            {"type":"subscribe","channels":[{"name":"ticker","product_ids":["BTC-USD"]}]},
            lambda d: d.get("type")=="ticker" and "price" in d and d.get("time") and rec("coinbase", float(d["price"]), iso_ms(d["time"]))),
        run("binance", "wss://stream.binance.com:9443/ws/btcusdt@trade", None,
            lambda d: "p" in d and rec("binance", float(d["p"]), d.get("T"))),
        run("binance_fut", "wss://fstream.binance.com/ws/btcusdt@aggTrade", None,
            lambda d: "p" in d and rec("binance_fut", float(d["p"]), d.get("T"))),
        run("kraken", "wss://ws.kraken.com/v2",
            {"method":"subscribe","params":{"channel":"ticker","symbol":["BTC/USD"]}},
            lambda d: d.get("channel")=="ticker" and d.get("data") and rec("kraken", float(d["data"][0]["last"]))),
        run("okx", "wss://ws.okx.com:8443/ws/v5/public",
            {"op":"subscribe","args":[{"channel":"tickers","instId":"BTC-USDT"}]},
            lambda d: d.get("data") and rec("okx", float(d["data"][0]["last"]), int(d["data"][0]["ts"]))),
        run("bybit", "wss://stream.bybit.com/v5/public/spot",
            {"op":"subscribe","args":["tickers.BTCUSDT"]},
            lambda d: d.get("data",{}).get("lastPrice") and rec("bybit", float(d["data"]["lastPrice"]), d.get("ts"))),
        run("bitstamp", "wss://ws.bitstamp.net",
            {"event":"bts:subscribe","data":{"channel":"live_trades_btcusd"}},
            lambda d: d.get("event")=="trade" and rec("bitstamp", float(d["data"]["price"]), int(d["data"]["microtimestamp"])/1000)),
        run("poly", "wss://ws-subscriptions-clob.polymarket.com/ws/market",
            {"assets_ids":[UP,DOWN],"type":"market"},
            lambda d: [rec("poly", None) for e in (d if isinstance(d,list) else [d]) if e.get("asset_id")==UP and e.get("event_type") in ("book","price_change")]),
    ]
    try: await asyncio.wait_for(asyncio.gather(*tasks), timeout=DURATION)
    except asyncio.TimeoutError: pass

asyncio.run(main())

FEEDS = ["coinbase","binance","binance_fut","kraken","okx","bybit","bitstamp"]
print(f"\n=== {DURATION:.0f}s, {len(ev)} events ===\n")
print(f"{'feed':>12} {'updates':>8} {'rate/s':>7} {'lat_med':>8} {'lat_min':>8}")
for f in FEEDS + ["poly"]:
    lat = [e[3] for e in ev if e[1]==f and e[3] is not None]
    n = sum(1 for e in ev if e[1]==f)
    med = f"{statistics.median(lat):.0f}" if lat else "-"
    mn = f"{min(lat):.0f}" if lat else "-"
    print(f"{f:>12} {n:>8} {n/DURATION:>7.1f} {med:>8} {mn:>8}")

# FIRST-MOVER: pour chaque changement de prix >=2$, quel feed l'a vu en premier?
# On construit une timeline par feed et on regarde qui franchit un niveau en 1er.
series = {f: sorted((t,p) for t,ff,p,_ in ev if ff==f and p) for f in FEEDS}
wins = {f:0 for f in FEEDS}
# référence = union des prix; pour chaque mvt sur le feed le + dense, chercher qui l'a atteint avant
ref = max(FEEDS, key=lambda f: len(series[f]))
print(f"\nFIRST-MOVER (référence densité: {ref}) — qui atteint un nouveau niveau de prix en premier:")
last = None
for t, p in series[ref]:
    if last is not None and abs(p-last) >= 2.0:
        # pour ce niveau p, le 1er feed à l'avoir rapporté (prix proche <1$) le + tôt
        best_f, best_t = None, 1e9
        for f in FEEDS:
            for tt, pp in series[f]:
                if abs(pp - p) < 1.0:
                    if tt < best_t: best_t, best_f = tt, f
                    break
        if best_f: wins[best_f] += 1
    last = p
tot = sum(wins.values()) or 1
for f in sorted(wins, key=lambda x:-wins[x]):
    if wins[f]: print(f"  {f:>12}: {wins[f]:>3} fois 1er ({100*wins[f]/tot:.0f}%)")

# LEAD TIME du meilleur feed vs poly
poly_t = sorted(t for t,f,_,_ in ev if f=="poly")
def lead(feed):
    L=[]; last=None
    for t,p in series.get(feed,[]):
        if last is not None and abs(p-last)>=3.0:
            i=bisect.bisect_right(poly_t,t)
            if i<len(poly_t): L.append((poly_t[i]-t)*1000)
        last=p
    return L
print("\nLEAD TIME feed→poly (mvt>=3$):")
for f in FEEDS:
    L=lead(f)
    if L: print(f"  {f:>12}: médiane {statistics.median(L):>4.0f}ms (n={len(L)})")
