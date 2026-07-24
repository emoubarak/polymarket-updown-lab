#!/usr/bin/env python3
"""Run the Polymarket maker-REBATE harvester (pmlab.rewardmm.RewardMM).

The income is the maker-rebate on FILLED maker volume near p≈0.5 (0.014·p(1−p) USDC/filled share),
NOT the spread (≈breakeven) and NOT the Qmin liquidity-rewards (crypto Up/Down isn't in that pool).
So: post tight two-sided resting maker quotes, get filled, stay neutral, redeem at settle. There is
NO min-size / max-spread gate (those gamma fields are vestigial for crypto Up/Down). DRY-RUN unless
armed. On-chain mint/redeem via ~/mint/setops.js + POLY_BUILDER_* in env.

    python3 run_rewardmm.py --interval 5m --underlying btc --mint-usd 15 --clip 5 --quote-dist 0.01
"""
from __future__ import annotations

import argparse
import signal
import time

from pmlab.live import LiveBroker, is_armed
from pmlab.rewardmm import RewardMM


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    p = argparse.ArgumentParser(description="Polymarket maker-rebate harvester")
    p.add_argument("--interval", choices=["5m", "15m"], default="5m")
    p.add_argument("--underlying", default="btc")
    p.add_argument("--mint-usd", type=float, default=15.0, dest="mint_usd",
                   help="$ of sets to mint as ask ammunition (recovered at redeem)")
    p.add_argument("--clip", type=float, default=5.0, help="shares per resting order")
    p.add_argument("--quote-dist", type=float, default=0.01, dest="quote_dist",
                   help="peg bids/asks this far off mid (tight = more fills = more rebate)")
    p.add_argument("--max-band", type=float, default=0.10, dest="max_band",
                   help="cap on quote distance (vestigial reward band; keep loose)")
    p.add_argument("--spot-anchor", action="store_true", dest="spot_anchor",
                   help="center quotes on the Binance spot-derived fair p (leads the CLOB) to cut pickoff")
    p.add_argument("--beta", type=float, default=0.5,
                   help="spot-anchor blend: 0=pure CLOB mid, 1=pure spot-fair")
    p.add_argument("--back-off", type=float, default=0.0, dest="back_off",
                   help="sit this far behind the touch — avoids post-only crosses, lets orders rest+fill")
    p.add_argument("--uptime-bids", action="store_true", dest="uptime_bids",
                   help="liquidity-REWARDS mode: rest bid AND ask simultaneously for two-sided uptime "
                        "(needed to score the Qmin rewards), vs the default replenish-only maker-rebate mode")
    p.add_argument("--max-inv", type=float, default=10.0, dest="max_inv",
                   help="max directional shares before skewing quotes to defend neutrality")
    p.add_argument("--min-quote", type=float, default=0.30, dest="min_quote")
    p.add_argument("--max-quote", type=float, default=0.70, dest="max_quote")
    p.add_argument("--recenter-eps", type=float, default=0.01, dest="recenter_eps")
    p.add_argument("--flatten-buf", type=float, default=45.0, dest="flatten_buf")
    p.add_argument("--kill-loss", type=float, default=8.0, dest="kill_loss")
    p.add_argument("--state-dir", default="rewardmm_btc_5m", dest="state_dir")
    p.add_argument("--poll", type=float, default=3.0)
    p.add_argument("--ticks", type=int, default=0)
    a = p.parse_args()

    broker = LiveBroker(log=log)
    if is_armed():
        broker.connect()
        broker.ensure_allowances()
    mode = "🔴 LIVE (real money)" if is_armed() else "DRY-RUN (disarmed)"
    mm = RewardMM(a, broker, log=log)

    # clean stop: cancel resting orders on SIGTERM/SIGINT, else GTD orders keep filling AFTER the kill
    # (observed: a "stopped" bot kept accumulating inventory until window_end).
    def _shutdown(signum, _frame):
        log(f"signal {signum} → cancelling resting orders + exit")
        try:
            mm._cancel_all()
        except Exception as e:
            log(f"cancel-on-exit err: {e}")
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log(f"reward-MM up — {mode} | mint=${a.mint_usd} clip={a.clip} dist={a.quote_dist} | {mm.status()}")

    i = 0
    while True:
        try:
            mm.on_tick()
        except Exception as e:
            log(f"tick error: {type(e).__name__}: {e}")
        i += 1
        if i % 20 == 0:
            log(mm.status())
        if a.ticks and i >= a.ticks:
            log(f"done {i} ticks | {mm.status()}")
            break
        time.sleep(a.poll)


if __name__ == "__main__":
    main()
