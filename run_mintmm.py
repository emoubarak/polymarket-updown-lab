#!/usr/bin/env python3
"""Run the std0-style complete-set market-maker (pmlab.mintmm.MintMM).
Mint sets -> rest two-sided maker SELLS at a premium -> recover leftovers. DRY-RUN unless armed
(POLY_LIVE=1 POLY_CONFIRM=I_UNDERSTAND_REAL_MONEY POLY_PRIVATE_KEY + builder creds). On-chain ops
(mint/merge/redeem) need ~/mint/setops.js + POLY_BUILDER_* in env.

    python3 run_mintmm.py --interval 5m --underlying btc --mint-usd 8 --sell-edge 0.015
"""
from __future__ import annotations

import argparse
import time

from pmlab.live import LiveBroker, is_armed
from pmlab.mintmm import MintMM


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    p = argparse.ArgumentParser(description="std0-style complete-set market-maker")
    p.add_argument("--interval", choices=["5m", "15m"], default="5m")
    p.add_argument("--underlying", default="btc")
    p.add_argument("--mint-usd", type=float, default=8.0, dest="mint_usd",
                   help="$ of complete sets to mint per window")
    p.add_argument("--sell-edge", type=float, default=0.015, dest="sell_edge",
                   help="post each ask this far ABOVE its mid (the premium captured)")
    p.add_argument("--max-imbalance", type=float, default=2.0, dest="max_imbalance",
                   help="cap on (sold_up - sold_dn) shares — bounds directional exposure")
    p.add_argument("--sell-floor", type=float, default=0.51, dest="sell_floor",
                   help="never sell a side below this price (no fire-sale of the falling side)")
    p.add_argument("--min-quote", type=float, default=0.15, dest="min_quote")
    p.add_argument("--max-quote", type=float, default=0.85, dest="max_quote")
    p.add_argument("--enter-lo", type=float, default=0.5, dest="enter_lo")
    p.add_argument("--recenter-eps", type=float, default=0.01, dest="recenter_eps")
    p.add_argument("--flatten-buf", type=float, default=45.0, dest="flatten_buf")
    p.add_argument("--kill-loss", type=float, default=10.0, dest="kill_loss")
    p.add_argument("--state-dir", default="mintmm_btc_5m", dest="state_dir")
    p.add_argument("--poll", type=float, default=5.0)
    p.add_argument("--ticks", type=int, default=0)
    a = p.parse_args()

    broker = LiveBroker(log=log)
    if is_armed():
        broker.connect()
        broker.ensure_allowances()
    mode = "🔴 LIVE (real money)" if is_armed() else "DRY-RUN (disarmed)"
    mm = MintMM(a, broker, log=log)
    log(f"mint-MM up — {mode} | mint=${a.mint_usd} edge={a.sell_edge} | {mm.status()}")

    i = 0
    while True:
        try:
            mm.on_tick()
        except Exception as e:
            log(f"tick error: {type(e).__name__}: {e}")
        i += 1
        if i % 10 == 0:
            log(mm.status())
        if a.ticks and i >= a.ticks:
            log(f"done {i} ticks | {mm.status()}")
            break
        time.sleep(a.poll)


if __name__ == "__main__":
    main()
