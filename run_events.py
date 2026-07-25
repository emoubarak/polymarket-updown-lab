#!/usr/bin/env python3
"""Paper-trade the favourite-longshot harvest on Polymarket EVENT markets.

Slow cadence (markets resolve in hours→weeks), so this is a minimal loop, not the
5m/15m Runner. Writes dashboard-compatible state. Read-only on the real APIs; no
money — pure simulation to see whether the bias is fat enough off crypto.

    python3 run_events.py --name events-fav --state-dir state_events_fav --poll 600
    python3 run_events.py --name events-sport --match "win on" --days-max 5 ...
"""
from __future__ import annotations

import argparse
import time

from pmlab.events import EventHarvester, ScanCfg


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", default="events-fav")
    p.add_argument("--state-dir", default="state_events_fav")
    p.add_argument("--fav-lo", type=float, default=0.85)
    p.add_argument("--fav-hi", type=float, default=0.95)
    p.add_argument("--liq-min", type=float, default=50_000.0)
    p.add_argument("--liq-max", type=float, default=2_000_000.0)
    p.add_argument("--days-min", type=float, default=0.0)
    p.add_argument("--days-max", type=float, default=400.0)
    p.add_argument("--match", default="", help="substring required in event/question")
    p.add_argument("--exclude", default="", help="substring that disqualifies")
    p.add_argument("--stake", type=float, default=25.0)
    p.add_argument("--bankroll", type=float, default=1000.0)
    p.add_argument("--max-positions", type=int, default=40)
    p.add_argument("--poll", type=float, default=600.0)
    p.add_argument("--ticks", type=int, default=None)
    a = p.parse_args()

    cfg = ScanCfg(fav_lo=a.fav_lo, fav_hi=a.fav_hi, liq_min=a.liq_min, liq_max=a.liq_max,
                  days_min=a.days_min, days_max=a.days_max, match=a.match, exclude=a.exclude)
    h = EventHarvester(state_dir=a.state_dir, scan_cfg=cfg, bankroll=a.bankroll,
                       stake=a.stake, max_positions=a.max_positions, log=log)
    log(f"event harvester up — {a.name} | fav {a.fav_lo}-{a.fav_hi} | "
        f"liq ${a.liq_min:,.0f}-${a.liq_max:,.0f} | {a.days_min}-{a.days_max}d"
        f"{f' | match={a.match!r}' if a.match else ''} | {h.status()}")
    n = 0
    while a.ticks is None or n < a.ticks:
        try:
            h.tick()
            log(h.status())
        except Exception as e:
            log(f"tick error: {type(e).__name__}: {e}")
        n += 1
        if a.ticks is None or n < a.ticks:
            time.sleep(a.poll)


if __name__ == "__main__":
    main()
