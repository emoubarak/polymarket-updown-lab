#!/usr/bin/env python3
"""Run the std0-competitive HFT maker (pmlab.hftmm.HftMM) — WebSocket-driven, co-located.

Market book + Binance spot + our fills all over WS → re-price the instant spot moves (it leads the
lagging CLOB), before the taker can hit our stale quote. The poll-1s rewardmm bot couldn't; this can.
DRY-RUN unless armed. On-chain mint/sync via MINT_JS=~/mint/safeops.js (Safe self-submit).

    MINT_JS=~/mint/safeops.js  + source .poly_env_bbe3 + POLY_LIVE=1 \
    python3 run_hft.py --interval 5m --underlying eth --mint-usd 100 --clip 5
"""
from __future__ import annotations

import argparse
import json
import signal
import time
from pathlib import Path

from pmlab import feeds, hftmm
from pmlab.feeds import INTERVALS
from pmlab.live import LiveBroker, is_armed


def log(m: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main():
    p = argparse.ArgumentParser(description="std0-competitive WS HFT maker")
    p.add_argument("--interval", choices=["5m", "15m"], default="5m")
    p.add_argument("--underlying", default="eth")
    p.add_argument("--clip", type=float, default=5.0)
    p.add_argument("--quote-dist", type=float, default=0.01, dest="quote_dist")
    p.add_argument("--max-inv", type=float, default=10.0, dest="max_inv")
    p.add_argument("--mint-usd", type=float, default=100.0, dest="mint_usd")
    p.add_argument("--kill-loss", type=float, default=8.0, dest="kill_loss")
    p.add_argument("--min-quote", type=float, default=0.40, dest="min_quote")
    p.add_argument("--max-quote", type=float, default=0.60, dest="max_quote")
    p.add_argument("--beta", type=float, default=0.0, help="blend QUOTE PRICE toward spot-fair; 0=price at CLOB mid (rests); spot-lead is defensive in _gates")
    p.add_argument("--skew-k", type=float, default=0.5, dest="skew_k", help="cents of center shift per clip of net inventory")
    p.add_argument("--min-requote-s", type=float, default=0.12, dest="min_requote_s")
    p.add_argument("--flatten-buf", type=float, default=30.0, dest="flatten_buf")
    p.add_argument("--sync-every", type=float, default=4.0, dest="sync_every", help="on-chain inv reconcile cadence (safety net)")
    p.add_argument("--state-dir", default="hft_eth_5m", dest="state_dir")
    p.add_argument("--secs", type=int, default=0, help="run this many seconds then exit (0=forever)")
    a = p.parse_args()

    broker = LiveBroker(log=log)
    if is_armed():
        broker.connect(); broker.ensure_allowances()
    mode = "🔴 LIVE (real money)" if is_armed() else "DRY-RUN (disarmed)"
    # PRE-FLIGHT self-check — refuse to arm real money on a misconfig (clip<5, mint>pUSD, bad band, …)
    from pmlab import mm_guard
    pusd = (broker.usdc_balance() if is_armed() else 1e9)
    ok, _fails = mm_guard.preflight(a, pusd, is_armed(), log=log)
    if is_armed() and not ok:
        log("🛑 PRE-FLIGHT FAILED — refusing to arm real money. Fix the config above."); raise SystemExit(1)
    mm = hftmm.HftMM(a, broker, log=log)
    mm.sym = feeds.SYMBOL.get(a.underlying, "BTCUSDT")
    creds = broker._client.creds if is_armed() else None
    sd = Path(a.state_dir); sd.mkdir(exist_ok=True)

    def shutdown(signum, _f):
        log(f"signal {signum} → cancel resting orders + exit")
        try: mm._cancel_all()
        except Exception as e: log(f"cancel-on-exit err: {e}")
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # persistent Binance spot feed (the re-quote trigger)
    def _spot(s):
        mm.spot = s; mm.requote(time.time())
    spot = hftmm.SpotFeed(mm.sym, on_update=_spot, log=lambda *x: None)
    spot.start()

    book = [None]; user = [None]

    def roll_to(m):
        if book[0]: book[0].stop()
        if user[0]: user[0].stop()
        mm.book = None
        mm._roll(m)
        book[0] = hftmm.LiveBook([mm.tok_up, mm.tok_dn],
                                 on_update=lambda aid: mm.requote(time.time()), log=lambda *x: None)
        mm.book = book[0]; book[0].start()
        if creds:
            user[0] = hftmm.UserFeed(creds, mm.cid, on_fill=mm.on_fill, log=log)
            user[0].start()

    log(f"HFT-MM up — {mode} | {a.underlying}-{a.interval} clip={a.clip} mint=${a.mint_usd} "
        f"band={a.min_quote}-{a.max_quote} skew_k={a.skew_k} requote≥{a.min_requote_s}s")
    sec = INTERVALS[a.interval]
    t0 = time.time(); last_sync = 0.0; last_status = 0.0
    while True:
        now = int(time.time()); ws = (now // sec) * sec
        try:
            m = feeds.fetch_updown_market(a.interval, ws, a.underlying)
        except Exception as e:
            m = None; log(f"market fetch err: {e}")
        if m and m.slug != mm.slug:
            roll_to(m)
        if not mm.dry and time.time() - last_sync > a.sync_every:
            mm._sync_onchain(); last_sync = time.time()
        if time.time() - last_status > 15:
            log(mm.status() + f" | mark={mm.mark:+.2f} realized={mm.realized:+.2f} rebate≈${0.014*mm.fill_pq:.3f}"
                + (" 🛑KILLED" if mm.killed else ""))
            (sd / "hft_state.json").write_text(json.dumps({
                "inv_up": round(mm.inv_up, 1), "inv_dn": round(mm.inv_dn, 1), "mark": round(mm.mark, 3),
                "realized": round(mm.realized, 3), "fills": mm.fills, "requotes": mm._requotes,
                "rebate": round(0.014 * mm.fill_pq, 3), "killed": mm.killed, "minted": mm.minted,
                "slug": mm.slug, "ts": int(time.time())}))
            last_status = time.time()
        if a.secs and time.time() - t0 >= a.secs:
            log("done (secs reached) — cancelling orders"); mm._cancel_all(); break
        time.sleep(1)


if __name__ == "__main__":
    main()
