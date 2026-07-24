"""High-frequency 2-coin lead-lag probe — the sub-second frontier.

The 2s logger (leadlag_shadow) showed cross-corr peaking at lag=0 → no lag >2s. But a real
sub-second lag (doge follows btc in 200-800ms) would also appear at lag=0 at 2s resolution, and
WOULD be capturable co-located (22ms RTT). This polls btc + doge favorite books as fast as the API
allows (~6/s, ~167ms resolution), with millisecond timestamps, to resolve sub-second lead-lag.
ZERO money: pure logging. Focused on the strongest tape pair (btc leader → doge laggard).

Run co-located (AWS Dublin):  python3 -m pmlab.leadlag_fast
"""
from __future__ import annotations

import time
from pathlib import Path

from . import feeds
from .entry import favorite_of
from .feeds import INTERVALS

LEADER, LAGGARD = "btc", "doge"
FRAME = "5m"
TARGET_HZ = 6.0                  # polls/sec per coin (12 req/s total — well under the 60/s cap)
OUT = Path("leadlag_fast")


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
    jf = OUT / "fast.csv"
    if not jf.exists():
        jf.write_text("ts,window_start,lead_mid_up,lead_bid,lead_ask,lag_mid_up,lag_bid,lag_ask\n")
    print(f"[{time.strftime('%H:%M:%S')}] leadlag FAST up — {LEADER}->{LAGGARD} {FRAME} ~{TARGET_HZ}/s", flush=True)

    sec = INTERVALS[FRAME]
    mkt: dict[tuple, object] = {}
    period = 1.0 / TARGET_HZ
    n = 0
    while True:
        t0 = time.time()
        ws = int(t0 // sec) * sec
        try:
            ml = mkt.get((LEADER, ws)) or feeds.fetch_updown_market(FRAME, ws, LEADER)
            mg = mkt.get((LAGGARD, ws)) or feeds.fetch_updown_market(FRAME, ws, LAGGARD)
            if ml and mg:
                mkt[(LEADER, ws)] = ml
                mkt[(LAGGARD, ws)] = mg
                lm, lb, la = book_mid(ml.token_up)
                gm, gb, ga = book_mid(mg.token_up)
                if lm is not None and gm is not None:
                    ts = time.time()
                    with jf.open("a") as fh:
                        fh.write(f"{ts:.3f},{ws},{lm:.4f},{lb:.4f},{la:.4f},{gm:.4f},{gb:.4f},{ga:.4f}\n")
        except Exception as e:
            if n % 50 == 0:
                print(f"[{time.strftime('%H:%M:%S')}] err: {type(e).__name__}: {e}", flush=True)
        if len(mkt) > 8:
            mkt = {k: v for k, v in mkt.items() if k[1] >= ws - sec}
        n += 1
        if n % 600 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] {n} polls", flush=True)
        dt = time.time() - t0
        time.sleep(max(0.02, period - dt))


if __name__ == "__main__":
    main()
