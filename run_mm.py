#!/usr/bin/env python3
"""Run the two-sided market-making quoter (pmlab.mm.MarketMaker).

DRY-RUN unless armed — same arming as run_live:
    POLY_LIVE=1 POLY_CONFIRM=I_UNDERSTAND_REAL_MONEY POLY_PRIVATE_KEY=0x...
Flavor A: NO complete-set minting, NO web3, NO POL. The cheapest test of whether a
co-located two-sided maker captures spread net-positive (vs scalp.py's non-colo bleed).

    python3 run_mm.py --interval 15m --underlying eth --edge 0.01 --max-inv 20 --kill-loss 10
"""
from __future__ import annotations

import argparse
import time

from pmlab.live import LiveBroker, is_armed
from pmlab.mm import MarketMaker


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    p = argparse.ArgumentParser(description="two-sided market-making quoter (Flavor A)")
    p.add_argument("--interval", choices=["5m", "15m"], default="15m")
    p.add_argument("--underlying", default="eth")
    p.add_argument("--edge", type=float, default=0.01,
                   help="quote this far (price) from mid on each side")
    p.add_argument("--max-inv", type=float, default=20.0, dest="max_inv",
                   help="inventory cap in shares (never short)")
    p.add_argument("--min-quote", type=float, default=0.15, dest="min_quote",
                   help="only quote when mid is above this (skip deep longshots near $0)")
    p.add_argument("--max-quote", type=float, default=0.85, dest="max_quote",
                   help="only quote when mid is below this (skip deep favorites near $1)")
    p.add_argument("--min-margin", type=float, default=0.01, dest="min_margin",
                   help="never post an ask below avg cost + this (don't lock losses)")
    p.add_argument("--recenter-eps", type=float, default=0.01, dest="recenter_eps",
                   help="re-post a quote only when it drifts >= this from desired")
    p.add_argument("--flatten-buf", type=float, default=60.0, dest="flatten_buf",
                   help="stop acquiring / offload inventory this many seconds before settle")
    p.add_argument("--kill-loss", type=float, default=10.0, dest="kill_loss",
                   help="HARD STOP (cancel all) if mark-to-market P&L <= -this")
    p.add_argument("--state-dir", default="mm_state_eth_15m", dest="state_dir")
    p.add_argument("--poll", type=float, default=6.0)
    p.add_argument("--ticks", type=int, default=0, help="0 = run forever")
    a = p.parse_args()

    broker = LiveBroker(log=log)
    if is_armed():
        broker.connect()
        broker.ensure_allowances()
    mode = "🔴 LIVE (real money)" if is_armed() else "DRY-RUN (disarmed)"
    mm = MarketMaker(a, broker, log=log)
    log(f"market-maker up — {mode} | edge={a.edge} max_inv={a.max_inv} "
        f"kill=-{a.kill_loss} | {mm.status()}")

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
