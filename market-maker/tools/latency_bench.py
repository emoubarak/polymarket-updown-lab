#!/usr/bin/env python3
"""latency_bench — compare la vitesse de captation du prix BTC (Binance vs
Coinbase) et mesure le LEAD TIME sur la réaction du book Polymarket Up/Down.
Objectif: voir de combien on précède le marché Polymarket avec chaque feed.

Usage: python latency_bench.py <UP_TOKEN> <DOWN_TOKEN> [duree_s]
Horodatage sur horloge locale (monotonic) → délais relatifs fiables sans sync.
Latence feed absolue via timestamp exchange (suppose NTP sync du VPS).
"""
import asyncio, websockets, json, time, statistics, sys, datetime

UP, DOWN = sys.argv[1], sys.argv[2]
DURATION = float(sys.argv[3]) if len(sys.argv) > 3 else 120

ev = []  # (t_mono, source, price, feed_latency_ms|None)
t0 = time.monotonic()

def mono(): return time.monotonic() - t0
def wall_ms(): return time.time() * 1000

async def binance():
    u = "wss://stream.binance.com:9443/ws/btcusdt@trade"
    try:
        async with websockets.connect(u, ping_interval=15) as ws:
            async for m in ws:
                tm, tw = mono(), wall_ms()
                d = json.loads(m)
                ev.append((tm, "binance", float(d["p"]), tw - d["T"]))
    except Exception as e: print("binance err:", e)

async def coinbase():
    u = "wss://ws-feed.exchange.coinbase.com"
    try:
        async with websockets.connect(u, ping_interval=15) as ws:
            await ws.send(json.dumps({"type": "subscribe",
                "channels": [{"name": "ticker", "product_ids": ["BTC-USD"]}]}))
            async for m in ws:
                tm, tw = mono(), wall_ms()
                d = json.loads(m)
                if d.get("type") == "ticker" and "price" in d and d.get("time"):
                    et = datetime.datetime.fromisoformat(
                        d["time"].replace("Z", "+00:00")).timestamp() * 1000
                    ev.append((tm, "coinbase", float(d["price"]), tw - et))
    except Exception as e: print("coinbase err:", e)

async def polymarket():
    u = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    try:
        async with websockets.connect(u, ping_interval=15) as ws:
            await ws.send(json.dumps({"assets_ids": [UP, DOWN], "type": "market"}))
            async for m in ws:
                tm = mono()
                d = json.loads(m)
                for e in (d if isinstance(d, list) else [d]):
                    if e.get("asset_id") == UP and e.get("event_type") in ("book", "price_change"):
                        ev.append((tm, "poly_up", None, None))
    except Exception as e: print("poly err:", e)

async def main():
    try:
        await asyncio.wait_for(
            asyncio.gather(binance(), coinbase(), polymarket()), timeout=DURATION)
    except asyncio.TimeoutError:
        pass

asyncio.run(main())

# ---------- Analyse ----------
def feed_stats(src):
    lat = [e[3] for e in ev if e[1] == src and e[3] is not None]
    n = sum(1 for e in ev if e[1] == src)
    if not lat: return n, None, None
    return n, statistics.median(lat), min(lat)

print(f"\n=== {DURATION:.0f}s, {len(ev)} events ===\n")
print(f"{'feed':>10} {'updates':>8} {'rate/s':>7} {'lat_med_ms':>11} {'lat_min_ms':>11}")
for s in ("binance", "coinbase", "poly_up"):
    n, med, mn = feed_stats(s)
    r = n / DURATION
    ms = f"{med:.0f}" if med is not None else "-"
    mn_s = f"{mn:.0f}" if mn is not None else "-"
    print(f"{s:>10} {n:>8} {r:>7.1f} {ms:>11} {mn_s:>11}")

# LEAD TIME: pour chaque mouvement BTC (via le feed le plus rapide=coinbase),
# délai jusqu'à la prochaine réaction du book Polymarket Up.
cb = [(t, p) for t, s, p, _ in ev if s == "coinbase" and p]
poly = sorted(t for t, s, _, _ in ev if s == "poly_up")
bn = [(t, p) for t, s, p, _ in ev if s == "binance" and p]

def lead(feed):
    """délai entre un mouvement de prix >= 3$ vu sur `feed` et la prochaine
    réaction du book Polymarket."""
    leads = []
    last = None
    import bisect
    for t, p in feed:
        if last is not None and abs(p - last) >= 3.0:
            i = bisect.bisect_right(poly, t)
            if i < len(poly):
                leads.append((poly[i] - t) * 1000)  # ms
        last = p
    return leads

for name, feed in (("coinbase", cb), ("binance", bn)):
    L = lead(feed)
    if L:
        print(f"\nLEAD TIME {name}→poly (mvt BTC>=3$, n={len(L)}): "
              f"médiane {statistics.median(L):.0f}ms, "
              f"p25 {statistics.quantiles(L, n=4)[0]:.0f}ms" if len(L) >= 4
              else f"\nLEAD TIME {name}→poly (n={len(L)}): médiane {statistics.median(L):.0f}ms")
    else:
        print(f"\nLEAD TIME {name}→poly: pas de mouvement >=3$ capté")

# Coinbase vs Binance: qui voit le même prix en premier?
print(f"\n(coinbase lat - binance lat) médiane = "
      f"{statistics.median([e[3] for e in ev if e[1]=='coinbase' and e[3] is not None] or [0]) - statistics.median([e[3] for e in ev if e[1]=='binance' and e[3] is not None] or [0]):.0f}ms "
      f"(négatif = coinbase plus rapide)")
