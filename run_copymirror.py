#!/usr/bin/env python3
"""Paper copy-mirror: follow a skilled wallet's THRESHOLD-market fills.

Watches a wallet's /activity and replicates each new "<asset> above $X" BUY in paper
(proportional, scaled + capped), holds to settlement, settles on the real oracle. The
honest forward-test of copy-trading a skilled crypto-threshold forecaster — the slow
markets (entry ~48h out) make mirroring feasible where the 5m/15m up/down windows do
not. Read-only on the real APIs; no money — pure simulation. See copymirror.py.

    python3 run_copymirror.py --who coinman2 --state-dir state_copy_coinman2 --poll 120
    python3 run_copymirror.py --target 0x55be...dca3 --name copy-x --state-dir state_copy_x
"""
from __future__ import annotations

import argparse
import time

from pmlab.copymirror import CopyMirror, COPY_TARGETS


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--who", choices=list(COPY_TARGETS), default=None,
                   help="a known target key (coinman2 | 06dc | king)")
    p.add_argument("--target", default=None,
                   help="explicit full 42-char wallet address (overrides --who)")
    p.add_argument("--name", default=None, help="runner name (default copy-<who>)")
    p.add_argument("--state-dir", default=None)
    p.add_argument("--bankroll", type=float, default=2000.0)
    p.add_argument("--scale", type=float, default=0.1,
                   help="our stake = their_notional * scale (ROI is scale-invariant)")
    p.add_argument("--stake-cap", type=float, default=50.0)
    p.add_argument("--poll", type=float, default=120.0)
    p.add_argument("--ticks", type=int, default=None)
    a = p.parse_args()

    if a.target:
        target, who = a.target, (a.name or a.target[:10])
    elif a.who:
        target, who = COPY_TARGETS[a.who], a.who
    else:
        p.error("need --who or --target")
    name = a.name or f"copy-{who}"
    state_dir = a.state_dir or f"state_copy_{who}"

    m = CopyMirror(state_dir=state_dir, target=target, name=name, target_name=who,
                   bankroll=a.bankroll, scale=a.scale, stake_cap=a.stake_cap, log=log)
    log(f"copy-mirror up — {name} → {who} ({target[:10]}…) | "
        f"scale {a.scale} cap ${a.stake_cap:.0f} | {m.status()}")
    n = 0
    while a.ticks is None or n < a.ticks:
        try:
            m.tick()
            log(m.status())
        except Exception as e:
            log(f"tick error: {type(e).__name__}: {e}")
        n += 1
        if a.ticks is None or n < a.ticks:
            time.sleep(a.poll)


if __name__ == "__main__":
    main()
