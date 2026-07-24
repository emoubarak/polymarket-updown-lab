"""Trade-flow shadow — the last untested microstructure signal: does INFORMED aggressive flow
(a burst of taker BUYs on one side) predict the outcome, beyond the price?

The cached tape is too sparse (531 usable windows) + looks like MM/lottery noise. This captures the
LIVE executed-trade tape per coin/window and logs the running net aggressive flow on each side, so
offline we can test whether a flow imbalance at the entry slot predicts the favorite outcome (real
informed flow) or is just reverse-causal (the buy IS what makes it the favorite). ZERO money.

Run co-located (AWS Dublin):  python3 -m pmlab.flow_shadow
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import feeds
from .entry import favorite_of
from .feeds import INTERVALS

COINS = ["btc", "eth", "sol", "xrp", "doge", "bnb"]
FRAME = "5m"
POLL = 12.0
DATA = "https://data-api.polymarket.com"
OUT = Path("flow_shadow")


def get(url, **params):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "pmlab-flow/0.1"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    OUT.mkdir(exist_ok=True)
    jf = OUT / "flow.csv"
    if not jf.exists():
        jf.write_text("ts,coin,slug,window_start,frac_rem,mid_up,fav_dir,"
                      "buy_up,buy_down,sell_up,sell_down,ntrades\n")
    print(f"[{time.strftime('%H:%M:%S')}] flow shadow up — {COINS} {FRAME} poll={POLL}s", flush=True)

    sec = INTERVALS[FRAME]
    mkt: dict[tuple, object] = {}
    n = 0
    while True:
        t0 = time.time()
        ws = int(t0 // sec) * sec
        rows = []
        for coin in COINS:
            try:
                key = (coin, ws)
                m = mkt.get(key) or feeds.fetch_updown_market(FRAME, ws, coin)
                if not m:
                    continue
                mkt[key] = m
                bu = feeds.fetch_book(m.token_up) or {}
                bids = bu.get("bids") or []
                asks = bu.get("asks") or []
                mid_up = round((max(b[0] for b in bids) + min(a[0] for a in asks)) / 2, 4) if bids and asks else None
                if mid_up is None:
                    continue
                fav_dir, _ = favorite_of(mid_up)
                trades = get(f"{DATA}/trades", market=m.condition_id, limit=500) or []
                bu_up = bu_dn = se_up = se_dn = 0.0
                for tr in trades:
                    try:
                        sz = float(tr.get("size") or 0)
                        outc = str(tr.get("outcome") or "").lower()
                        side = str(tr.get("side") or "").upper()
                    except Exception:
                        continue
                    up = "up" in outc
                    if side == "BUY":
                        if up: bu_up += sz
                        else: bu_dn += sz
                    elif side == "SELL":
                        if up: se_up += sz
                        else: se_dn += sz
                frac_rem = (m.window_end - t0) / sec
                rows.append(f"{int(t0)},{coin},{m.slug},{ws},{frac_rem:.3f},{mid_up:.4f},{fav_dir},"
                            f"{bu_up:.1f},{bu_dn:.1f},{se_up:.1f},{se_dn:.1f},{len(trades)}")
            except Exception as e:
                if n % 30 == 0:
                    print(f"[{time.strftime('%H:%M:%S')}] {coin} err: {type(e).__name__}: {e}", flush=True)
        if rows:
            with jf.open("a") as fh:
                fh.write("\n".join(rows) + "\n")
        if len(mkt) > 24:
            mkt = {k: v for k, v in mkt.items() if k[1] >= ws - sec}
        n += 1
        if n % 30 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] {n} sweeps", flush=True)
        time.sleep(max(0.5, POLL - (time.time() - t0)))


if __name__ == "__main__":
    main()
