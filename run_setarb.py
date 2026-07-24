#!/usr/bin/env python3
"""Run the delta-neutral complete-set ARB (pmlab.setarb.SetArb).
Buys cheap complete sets on the CLOB (maker bids on Up+Down summing < $1) → each matched
pair settles for a guaranteed $1, market-neutral. DRY-RUN unless armed (same as run_live).

    python3 run_setarb.py --interval 5m --underlying btc --edge 0.015 --set-usd 10 --max-unmatched 8
"""
from __future__ import annotations

import argparse
import time

from pmlab.live import LiveBroker, is_armed
from pmlab.setarb import SetArb


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    p = argparse.ArgumentParser(description="delta-neutral complete-set arb")
    p.add_argument("--interval", choices=["5m", "15m"], default="5m")
    p.add_argument("--underlying", default="btc")
    p.add_argument("--edge", type=float, default=0.015,
                   help="bid this far below each side's mid (matched set costs ~1-2*edge under $1)")
    p.add_argument("--set-usd", type=float, default=10.0, dest="set_usd",
                   help="$ of complete-sets to target acquiring per window")
    p.add_argument("--max-unmatched", type=float, default=8.0, dest="max_unmatched",
                   help="cap on |inv_up - inv_dn| shares (the only directional risk)")
    p.add_argument("--recenter-eps", type=float, default=0.01, dest="recenter_eps")
    p.add_argument("--flatten-buf", type=float, default=45.0, dest="flatten_buf",
                   help="stop acquiring this many s before settle (then hold to redeem)")
    p.add_argument("--state-dir", default="setarb_btc_5m", dest="state_dir")
    p.add_argument("--poll", type=float, default=4.0)
    p.add_argument("--ticks", type=int, default=0)
    a = p.parse_args()

    broker = LiveBroker(log=log)
    if is_armed():
        broker.connect()
        broker.ensure_allowances()
    mode = "🔴 LIVE (real money)" if is_armed() else "DRY-RUN (disarmed)"
    sa = SetArb(a, broker, log=log)
    log(f"set-arb up — {mode} | edge={a.edge} set=${a.set_usd} max_unmatched={a.max_unmatched} | {sa.status()}")

    i = 0
    while True:
        try:
            sa.on_tick()
        except Exception as e:
            log(f"tick error: {type(e).__name__}: {e}")
        i += 1
        if i % 10 == 0:
            log(sa.status())
        if a.ticks and i >= a.ticks:
            log(f"done {i} ticks | {sa.status()}")
            break
        time.sleep(a.poll)


if __name__ == "__main__":
    main()
