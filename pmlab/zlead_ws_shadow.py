"""zlead WS-vs-REST z-lead shadow — does a real-time WebSocket spot make zlead's entry signal better
(less adverse selection) than the REST-polled spot? ZERO money: it only LOGS, never trades.

The zlead entry signal is the vol-normalized lead z = lead_z(spot, open, sigma, tau, dir). The ONLY
thing that differs WS vs REST is `spot` (a live Binance bookTicker WS vs a REST snapshot that can be
~1s stale). So at each window's entry slot we log z_REST and z_WS side by side (everything else
identical) + the favorite + the books. The outcome (did the favorite win) is resolved OFFLINE at
check-time via the oracle, so this long-running process is pure logging = robust for days.

Run on a box with websocket-client (AWS .venv-live):  python3 -m pmlab.zlead_ws_shadow
"""
from __future__ import annotations

import time
from pathlib import Path

from . import feeds
from .entry import favorite_of, lead_z
from .feeds import INTERVALS
from .hftmm import SpotFeed

MARKETS = [("btc", "5m"), ("eth", "5m"), ("btc", "15m"), ("eth", "15m")]
ENTER_LO, ENTER_HI = 0.27, 0.40            # zlead's entry slot (fraction of window remaining)
OUT = Path("zlead_ws_shadow")


def main():
    OUT.mkdir(exist_ok=True)
    jf = OUT / "shadow.csv"
    if not jf.exists():
        jf.write_text("ts,coin,frame,slug,window_start,frac_rem,mid_fav,fav_dir,open,sigma,"
                      "tau_min,spot_rest,spot_ws,z_rest,z_ws\n")

    # one persistent Binance spot WS per coin (the only WS we need; no per-window re-subscription)
    spot = {}
    for coin in {c for c, _ in MARKETS}:
        sym = feeds.SYMBOL.get(coin, "BTCUSDT")
        sf = SpotFeed(sym, log=lambda *a: None)
        sf.start()
        spot[coin] = sf
    print(f"[{time.strftime('%H:%M:%S')}] zlead WS shadow up — {len(spot)} spot WS, markets={MARKETS}", flush=True)

    logged = set()            # (coin,frame,window_start) already snapshotted this window
    open_cache = {}           # window_start -> (open_px, sigma) per coin (1 fetch/window)
    n = 0
    while True:
        for coin, frame in MARKETS:
            try:
                sym = feeds.SYMBOL.get(coin, "BTCUSDT")
                sec = INTERVALS[frame]
                now = time.time()
                ws = int(now // sec) * sec
                m = feeds.fetch_updown_market(frame, ws, coin)
                if not m:
                    continue
                frac_rem = (m.window_end - now) / sec
                key = (coin, frame, m.window_start)
                if key in logged or not (ENTER_LO <= frac_rem <= ENTER_HI):
                    continue
                bu = feeds.fetch_book(m.token_up) or {}
                bids = bu.get("bids") or []; asks = bu.get("asks") or []
                if not bids or not asks:
                    continue
                mid_up = round((max(b[0] for b in bids) + min(a[0] for a in asks)) / 2, 4)
                fav_dir, mid_fav = favorite_of(mid_up)
                if mid_fav < 0.85:                       # zlead only cares about extreme favorites
                    continue
                ck = (coin, m.window_start)
                if ck not in open_cache:
                    try:
                        op = feeds.btc_price_at(m.window_start, sym)
                        sg = feeds.realized_vol_per_min(feeds.btc_klines_1m(symbol=sym))
                    except Exception:
                        op, sg = 0.0, 0.0
                    open_cache[ck] = (op, sg)
                op, sg = open_cache[ck]
                tau = max(1.0 / 60, (m.window_end - now) / 60.0)
                spot_rest = feeds.btc_spot(sym)
                spot_ws = spot[coin].spot
                if not spot_rest or not spot_ws:
                    continue
                z_rest = lead_z(spot_rest, op, sg, tau, fav_dir)
                z_ws = lead_z(spot_ws, op, sg, tau, fav_dir)
                with jf.open("a") as fh:
                    fh.write(f"{int(now)},{coin},{frame},{m.slug},{m.window_start},{frac_rem:.3f},"
                             f"{mid_fav:.4f},{fav_dir},{op:.2f},{sg:.6f},{tau:.2f},"
                             f"{spot_rest:.2f},{spot_ws:.2f},"
                             f"{'' if z_rest is None else round(z_rest,3)},"
                             f"{'' if z_ws is None else round(z_ws,3)}\n")
                logged.add(key)
                print(f"[{time.strftime('%H:%M:%S')}] logged {coin}-{frame} fav={fav_dir} "
                      f"mid={mid_fav:.3f} z_rest={z_rest} z_ws={z_ws}", flush=True)
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] {coin}-{frame} err: {type(e).__name__}: {e}", flush=True)
        # prune caches so they don't grow unbounded over days
        if len(logged) > 4000: logged = set(list(logged)[-1000:])
        if len(open_cache) > 4000: open_cache = dict(list(open_cache.items())[-1000:])
        n += 1
        time.sleep(5)


if __name__ == "__main__":
    main()
