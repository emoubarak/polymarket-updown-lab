"""Spot -> BTC-Polymarket latency probe — the capturable-edge hypothesis on the DEEP market.

doge lead-lag was real but UNcapturable (3c spread ate the 1c lag). BTC-Poly has a TIGHT ~1c spread
and is the only scalable book. The BTC-native outlier candidate: does the BTC-Poly book LAG the live
Binance spot by tens/hundreds of ms, leaving a stale quote a 22ms-co-located taker can grab BEFORE
it reprices — a classic latency arb the tight spread would NOT eat?

Leader = Binance BTC spot (WS bookTicker, real-time). Laggard = BTC up-token book (5m AND 15m),
polled fast with ms timestamps. ZERO money: pure logging. Offline event-study: when the spot jumps,
does the poly mid follow over the next N ms, and does buying the stale poly ask capture it net of the
(tight) spread?

Run co-located with .venv-live (has websocket):  .venv-live/bin/python -m pmlab.btc_spot_shadow
"""
from __future__ import annotations

import time
from pathlib import Path

from . import feeds
from .feeds import INTERVALS
from .hftmm import SpotFeed

FRAMES = ["5m", "15m"]
TARGET_HZ = 6.0
OUT = Path("btc_spot_shadow")


def book_mid(token):
    b = feeds.fetch_book(token) or {}
    bids = b.get("bids") or []
    asks = b.get("asks") or []
    if not bids or not asks:
        return None, None, None
    bb = max(x[0] for x in bids)
    ba = min(x[0] for x in asks)
    return round((bb + ba) / 2, 4), bb, ba


def main():
    OUT.mkdir(exist_ok=True)
    jf = OUT / "spot_book.csv"
    if not jf.exists():
        jf.write_text("ts,frame,slug,window_start,frac_rem,spot,poly_mid_up,poly_bid,poly_ask\n")

    sf = SpotFeed("btcusdt", log=lambda *a: None)
    sf.start()
    t_wait = time.time()
    while sf.spot is None and time.time() - t_wait < 10:
        time.sleep(0.2)
    print(f"[{time.strftime('%H:%M:%S')}] btc spot->poly probe up — spot={sf.spot} frames={FRAMES} ~{TARGET_HZ}/s", flush=True)

    mkt: dict[tuple, object] = {}
    period = 1.0 / TARGET_HZ
    n = 0
    while True:
        t0 = time.time()
        rows = []
        for frame in FRAMES:
            try:
                sec = INTERVALS[frame]
                ws = int(t0 // sec) * sec
                m = mkt.get((frame, ws)) or feeds.fetch_updown_market(frame, ws, "btc")
                if not m:
                    continue
                mkt[(frame, ws)] = m
                mid, bb, ba = book_mid(m.token_up)
                if mid is None:
                    continue
                ts = time.time()
                spot = sf.spot
                frac_rem = (m.window_end - ts) / sec
                rows.append(f"{ts:.3f},{frame},{m.slug},{ws},{frac_rem:.3f},{spot},{mid:.4f},{bb:.4f},{ba:.4f}")
            except Exception as e:
                if n % 50 == 0:
                    print(f"[{time.strftime('%H:%M:%S')}] {frame} err: {type(e).__name__}: {e}", flush=True)
        if rows:
            with jf.open("a") as fh:
                fh.write("\n".join(rows) + "\n")
        if len(mkt) > 8:
            mkt = {k: v for k, v in mkt.items() if k[1] >= int(t0 // 900) * 900 - 900}
        n += 1
        if n % 600 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] {n} polls, spot_msgs={sf.msgs}", flush=True)
        time.sleep(max(0.02, period - (time.time() - t0)))


if __name__ == "__main__":
    main()
