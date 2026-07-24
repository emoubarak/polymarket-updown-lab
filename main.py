#!/usr/bin/env python3
"""pmlab — paper-trade Polymarket BTC/alt Up/Down with the zlead family.

The deployable brains live in pmlab/presets.py as named Presets of ONE
engine (engine.Engine): the vol-normalized lead-floor harvester (zlead) and its
maker / strict-z / narrow-band variants. The price-only ancestors (favorite,
favorite_vol, favorite_lead, …) and the esoteric brains (gurdjieff / hermetic / iching /
kabbalah / tao) were all falsified — kept in presets.ARCHIVE as re-runnable
hypotheses; see research/FINDINGS.md and the git history.
"""

import argparse
from pathlib import Path

# The favorite/zlead family — ONE engine, named Presets (gate params + the entry-slot
# enter_lo/hi + execution switches) in pmlab/presets.py, so paper, the real
# pilot AND the dashboard all read the same source. ALL_PRESETS = active + archived
# (re-runnable); LIVE_STRATEGIES = the armable survivors. Add a strategy = add a Preset.
from pmlab.presets import (ALL_PRESETS, add_preset_args, preset_from_args, COIN_KEYS,
                                   bet_max_for, WEIGHT_PCT, START_CAPITAL)

# SCALP family — a DIFFERENT PARADIGM (pmlab/scalp.py): not outcome-betting
# but PRICE mean-reversion / liquidity provision. Harvests the CLOB overshoot-then-
# revert (research 2026-06-25: +6.5c after a 5c dip, fill-independent, ~9σ). Paper
# FORWARD-TEST only — the signal is real but the maker capture is unproven (the tape
# flatters maker fills; only the live exec_book judges). NOT in LIVE_STRATEGIES.
SCALP_FAMILY = {
    "scalp":  dict(enter=0.04, target=0.03),                 # the validated dip depth
    "scalpx": dict(enter=0.06, target=0.04),                 # deeper dips, fewer/bigger
}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--strategy", default="zlead",
                   choices=list(ALL_PRESETS) + list(SCALP_FAMILY))
    p.add_argument("--interval", choices=["5m", "15m"], default="15m",
                   help="market window (default 15m — the edge is 15m-native; "
                        "5m is backtest-dead bare, alive only with the lead floor)")
    p.add_argument("--underlying", choices=COIN_KEYS, default="btc",
                   help="up/down underlying (default btc). alts = same edge, independent "
                        "sample. The coin list is the single registry pmlab/coins.py "
                        "(add a coin there). Forward-test in live paper (alt backtests are thin)")
    p.add_argument("--bankroll", type=float, default=START_CAPITAL,
                   help=f"starting capital $ (default {START_CAPITAL:.0f})")
    p.add_argument("--stake", type=float, default=25.0,
                   help="flat $ per bet when --no-weighted")
    # Weighted sizing (default ON, 2026-06-26) — the EXACT pilot model: each bet is
    # --weight-pct of capital, capped at the coin's book depth --bet-max. --bet-max
    # defaults to COIN_BET_MAX[underlying]. Pass --no-weighted for the old flat clip.
    p.add_argument("--weighted", action=argparse.BooleanOptionalAction, default=True,
                   help="size each bet as --weight-pct of capital, capped at --bet-max")
    p.add_argument("--weight-pct", type=float, default=WEIGHT_PCT,
                   help=f"fraction of capital per bet when --weighted (default {WEIGHT_PCT})")
    p.add_argument("--bet-max", type=float, default=None,
                   help="per-bet ceiling $ when --weighted (default = book depth for the coin)")
    p.add_argument("--poll", type=float, default=10.0, help="seconds between ticks")
    p.add_argument("--state-dir", type=Path, default=None,
                   help="default: state_<strategy>")
    p.add_argument("--ticks", type=int, default=None,
                   help="stop after N ticks (default: run forever)")
    add_preset_args(p)      # --type / --enter-lo / --max-fav / --maker … (zlead customization)
    a = p.parse_args()

    state_dir = a.state_dir or Path(f"state_{a.strategy}")
    state_dir.mkdir(exist_ok=True)

    from pmlab.runner import Runner, RunnerConfig
    cfg = RunnerConfig(interval=a.interval, bankroll=a.bankroll,
                       poll_s=a.poll, state_dir=state_dir, underlying=a.underlying)
    if a.strategy in SCALP_FAMILY:
        from pmlab.scalp import Scalper
        brain = Scalper(stake=a.stake, name=a.strategy, **SCALP_FAMILY[a.strategy])
    else:
        from pmlab.engine import Engine
        bet_max = a.bet_max if a.bet_max is not None \
            else bet_max_for(a.underlying, a.interval)
        brain = Engine(preset=preset_from_args(a.strategy, a), stake=a.stake,
                       weighted=a.weighted, weight_pct=a.weight_pct, bet_max=bet_max)
    runner = Runner(brain, cfg)

    try:
        runner.run(max_ticks=a.ticks)
    except KeyboardInterrupt:
        print("\nstopped — state saved in", state_dir)


if __name__ == "__main__":
    main()
