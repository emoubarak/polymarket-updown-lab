"""Cross-coin lead-lag shadow — REAL-TIME book logger to settle the staleness question.

The cached tape (research/data) is ~55s stale + 1-min granularity, so it CANNOT distinguish a
real sub-minute lead-lag (laggard alt follows BTC within seconds) from a pure staleness artifact.
The same-window lead-lag looked huge (+14-60%/$ on doge/bnb) but the staleness-free cross-window
test was DEAD → the same-window effect is artifact-or-sub-minute, unresolvable on the tape.

This polls EVERY coin's live 5m up/down book every ~2s and logs the favorite mid with a precise
timestamp. ZERO money: pure logging. Offline we then measure (a) cross-correlation of mid moves at
lags 0..60s (does laggard(t) track leader(t−Δ)?), and (b) whether a real-time leader/laggard
DISAGREEMENT is followed by the laggard catching up within seconds (= a capturable lag) or not
(= tape artifact). Resolution is via the oracle at check-time, so this runs for days unattended.

Run on the co-located box (AWS Dublin, ~22ms /book):  python3 -m pmlab.leadlag_shadow
"""
from __future__ import annotations

import time
from pathlib import Path

from . import feeds
from .entry import favorite_of
from .feeds import INTERVALS

COINS = ["btc", "eth", "sol", "xrp", "doge", "bnb"]
FRAME = "5m"                     # 5m: fastest repricing = where a sub-minute lag would live
POLL = 2.0                       # seconds between full sweeps (sub-minute resolution)
OUT = Path("leadlag_shadow")


def main():
    OUT.mkdir(exist_ok=True)
    jf = OUT / "books.csv"
    if not jf.exists():
        jf.write_text("ts,coin,slug,window_start,frac_rem,mid_up,mid_fav,fav_dir,best_bid,best_ask,"
                      "bid_depth5,ask_depth5\n")
    print(f"[{time.strftime('%H:%M:%S')}] leadlag shadow up — {COINS} {FRAME} poll={POLL}s", flush=True)

    sec = INTERVALS[FRAME]
    mkt_cache: dict[tuple, object] = {}      # (coin, window_start) -> UpDownMarket (1 fetch/window/coin)
    n = 0
    while True:
        t0 = time.time()
        now = time.time()
        ws = int(now // sec) * sec
        rows = []
        for coin in COINS:
            try:
                key = (coin, ws)
                m = mkt_cache.get(key)
                if m is None:
                    m = feeds.fetch_updown_market(FRAME, ws, coin)
                    if not m:
                        continue
                    mkt_cache[key] = m
                bu = feeds.fetch_book(m.token_up) or {}
                bids = bu.get("bids") or []
                asks = bu.get("asks") or []
                if not bids or not asks:
                    continue
                bb = max(b[0] for b in bids)
                ba = min(a[0] for a in asks)
                mid_up = round((bb + ba) / 2, 4)
                fav_dir, mid_fav = favorite_of(mid_up)
                frac_rem = (m.window_end - now) / sec
                # top-5-level depth on each side → order-book imbalance (a microstructure predictor)
                bid_d = sum(b[1] for b in sorted(bids, key=lambda x: -x[0])[:5])
                ask_d = sum(a[1] for a in sorted(asks, key=lambda x: x[0])[:5])
                rows.append(f"{int(now*1000)/1000:.3f},{coin},{m.slug},{ws},{frac_rem:.3f},"
                            f"{mid_up:.4f},{mid_fav:.4f},{fav_dir},{bb:.4f},{ba:.4f},"
                            f"{bid_d:.1f},{ask_d:.1f}")
            except Exception as e:
                print(f"[{time.strftime('%H:%M:%S')}] {coin} err: {type(e).__name__}: {e}", flush=True)
        if rows:
            with jf.open("a") as fh:
                fh.write("\n".join(rows) + "\n")
        # prune old market cache (keep last ~3 windows)
        if len(mkt_cache) > 6 * 4:
            keep = {(c, w) for (c, w) in mkt_cache if w >= ws - 2 * sec}
            mkt_cache = {k: v for k, v in mkt_cache.items() if k in keep}
        n += 1
        if n % 60 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] {n} sweeps, last {len(rows)} books", flush=True)
        dt = time.time() - t0
        time.sleep(max(0.2, POLL - dt))


if __name__ == "__main__":
    main()
