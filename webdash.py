#!/usr/bin/env python3
"""Live web dashboard for pmlab paper trading — stdlib only.

One tab per strategy plus an overview tab. Each strategy maps to a state
dir and a log file:

    python3 webdash.py --port 8420 \
        --strat favorite-15m:state_rubedo:favorite.log \
        --strat favorite_vol-15m:state_favorite_vol:favorite_vol.log

Built for the OPERATOR (the person running the race): light neo-brutalist
theme, LIVE paper-trading results first, simulation-vs-real and the explainer
below. Backtest result shown in DOLLARS. Jargon is kept — the reader knows it.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# The surviving strategy family + the full preset table, MUTUALIZED from
# pmlab.presets (the single source of truth), so /config can serve each gate's params
# (band, lead floor, entry slot) to the front-end with no parallel hand-kept list.
from pmlab.presets import (LIVE_STRATEGIES as FAMILY, ALL_PRESETS as _PRESETS,
                                   COIN_SLOTS as _COIN_SLOTS, COIN_BET_MAX as _COIN_BET_MAX,
                                   COIN_DEPTH as _COIN_DEPTH, bet_max_for,
                                   WEIGHT_PCT as _WEIGHT_PCT, START_CAPITAL as _START_CAPITAL)
# Journal classification (entry/close/win) + the cross-strategy correlation matrix
# live in their own modules — the SINGLE definition both this file and the matrix read
# (cf. always-modularize).
from pmlab.journal import is_entry, is_close, won as won_of
from pmlab.correlation import correlation_matrix, pearson as _pearson, MIN_OVERLAP as _MIN_OVERLAP
from pmlab import feeds
from pmlab.coins import COIN_KEYS as _COIN_KEYS, SYMBOL as _SYMBOL

STRATS: dict[str, dict] = {}        # crypto runners (the favorite race) -> {state_dir, log}
EVENT_STRATS: dict[str, dict] = {}  # event-market harvesters -> {state_dir, log}
COPY_STRATS: dict[str, dict] = {}   # wallet copy-mirror runners -> {state_dir, log}

# Legacy flat reference stake (fallback for the front-end when a coin has no measured
# cap). Since 2026-06-26 the paper runners size WEIGHTED (WEIGHT_PCT of capital, capped
# at COIN_BET_MAX[coin]) from a $100 START — the dashboard reads those per-coin numbers
# from /config and the recomputed states/curves; this flat value is just the fallback.
STAKE_REF = 25.0

# One-paragraph TECHNICAL summary per brain (base name; the JS strips any -5m/
# -15m frame suffix to find it). Tucked into "technical details". Keep in sync
# with pmlab/engine.py.
DESCRIPTIONS = {
    "favorite":
        "EDGE VALIDATED OUT-OF-SAMPLE — favorite-longshot on 15m. The crowd "
        "overpays the nearly-dead side (the longshot); we buy the already-extreme "
        "favorite (price 0.85–0.95) ~6 min before the close, taker fees, carried to "
        "settlement, NO exit. It is a bet on PRICE calibration, not on BTC's "
        "direction. Brutally asymmetric payoff (win +~0.11 / loss "
        "−1.00, break-even = the price itself) → a cluster of flips hurts. "
        "Tripwire: if the realized win rate sticks to the entry price over ~150 trades, "
        "the edge isn't real and we retire it (coagula's death).",
    "favorite_vol":
        "RUBEDO + VOLATILITY FILTER. A high-vol favorite is a "
        "“storm favorite” about to flip — the blind spot that price "
        "alone doesn't show. We skip windows whose 1m BTC vol exceeds "
        "~0.00056 (66th pct). The filter ADDS edge; it is not just a "
        "safety rail.",
    "favorite_wide":
        "RUBEDO + VOL FILTER + FLOOR LOWERED TO 0.80. More windows "
        "qualify (volume), at the cost of a weaker per-trade edge "
        "(OOS +0.018/$). The bet: the favorite premium still exists at 0.80–0.85.",
    "favorite_lead":
        "RUBEDO + LEAD FLOOR. The live autopsy (20/06): favorite's losses "
        "concentrate on SOFT favorites — priced 0.85–0.91 in the book "
        "but BTC barely moved at entry ($12–56 of movement), near-coin-flips "
        "that the book overprices (the 2–6 bps zone wins only 0.92 vs ~0.97 "
        "beyond). We require the favorite's move to be ESTABLISHED: lead ≥ 6 bps "
        "from the window open, on the right side. 40-DAY RECKONING (20/06): on BTC the "
        "floor adds almost nothing over the bare favorite (OOS +0.003/$) — the "
        "10-day window was favorable. BUT the floor VALIDATES out-of-sample on "
        "ETH (an independent underlying where the bare favorite is DEAD): +0.042/$ — proof "
        "that the mechanism is real, not a BTC artifact. And it RESURRECTS the 5m "
        "(OOS +0.060/$, ~3× the volume). The filter holds; it is the bare favorite "
        "that is thin.",
    "zlead":
        "EXTREME FAVORITE WITH A VOL-NORMALIZED LEAD THRESHOLD (z-score). Instead of a "
        "fixed lead floor in bps, we require the move to be large "
        "RELATIVE to vol (lead / (σ·√τ) ≥ 1) — the ONLY edge that survives the "
        "multi-month audit (everything price-only is dead) and that generalizes "
        "cross-asset (BTC/ETH robust, alts in forward-test). ENTRY WINDOW "
        "WIDENED 2026-06-25: enter_lo 0.35 → 0.27 (entry with 4–6.75 min left instead "
        "of 5.25–6.75). The marginal backtest (objective = cumulative $/day, not "
        "EV/bet) shows cumulative profit rises until ~4 min left then "
        "FALLS BACK: the 2-3 and 1-2 min cohorts are EV-negative and real slippage "
        "worsens late. 0.27 is the slippage-robust bound that maximizes daily "
        "PnL; widening UPWARD (earlier entries) is strictly worse. "
        "All parameters live in pmlab/presets.py (one preset = one "
        "strategy, shared by the paper runners and this dashboard).",
    "zleadx":
        "FIXATIOZ WITH A STRENGTHENED z THRESHOLD (z≥1.5 instead of 1.0). The deep sweep shows "
        "that raising the threshold beats z≥1.0 in EV/$ AND in day-level stability everywhere, especially "
        "on 5m: btc-5m +0.039 / eth-5m +0.043, positive days 90% (vs ~85% at z≥1.0). Fewer "
        "bets (stricter gate). Forward-test to confirm the net gain live.",
    "zleadn":
        "FIXATIOZ WITH A NARROWED BAND 0.85–0.90. Same engine as zlead (extreme favorite + "
        "vol-normalized lead floor z≥1, widened entry window 4–6.75 min), but "
        "the favorite cap drops from 0.95 to 0.90: the per-coin band sweep shows "
        "the favorite-longshot premium CONCENTRATES on the 0.85–0.90 favorite — deep "
        "favorites 0.90+ are dead (you overpay a side that is nearly settled). The "
        "0.85–0.90 band beats 0.85–0.95 out-of-sample on both BTC and ETH on 15m. Same "
        "lead floor, narrower band → fewer bets but better targeted. "
        "Paper forward-test.",
    "favorite_cheap":
        "RETIRED 20/06 — narrowed band 0.85–0.88. Looked the most profitable "
        "(10 d: +0.121/$) but it was a 64-sample artifact: over 40 d "
        "it is DEAD (OOS −0.007/$, win = price) AND dead on ETH (−0.008). Living "
        "proof that the large-n tripwire is the only judge — coagula's death, "
        "caught before it bled.",
    "favorite_vollead":
        "FIXATIO + VOL FILTER — the union of the price-only harvester's two blind "
        "spots: the storm favorite (high vol, about to flip) "
        "AND the soft favorite (move not established). Best OOS EV/$ of the backtest, "
        "at the cost of lower volume. The conjunction of the two purifications.",
    "favorite_conviction":
        "Same bets as favorite_vollead (clean favorite, Bitcoin already committed, calm "
        "market), but the STAKE varies: bigger when the bet is safer "
        "(cheaper favorite = fatter premium, well-established move) and smaller "
        "otherwise. The sizing is backtested; it improves the dollar curve "
        "without changing favorite_vollead's per-dollar edge.",
    "favorite_mk":
        "RUBEDO WITH MAKER ENTRY. Instead of crossing the ask (taker: 2¢ haircut + "
        "fees), it posts a passive bid at the favorite's price; the book fills it (0.7 "
        "of the size, no fees) when the market crosses, otherwise we cross as "
        "taker late in the window — so we never MISS a favorite that ran straight "
        "up (the missed side = 100% winners, the pure-maker trap). Backtest "
        "26/06: per-$ EV beats the taker twin in every config, including the "
        "15m OOS slice; worst case = taker. Paper forward-test — the magnitude isn't "
        "OOS-validatable on the cached tape (loser-free regime on 15m, pure IS "
        "on 5m). research/backtest_maker_entry.py.",
    "favorite_vollead_mk":
        "CONIUNCTIO WITH MAKER ENTRY. The validated gates (clean favorite + 6 bps lead + "
        "vol filter) with the same maker+fallback entry as favorite_mk. Tests the execution "
        "gain (~2.5¢/$) on the actually-deployed edge. The selective gate "
        "captures a bit LESS of the maker gain (very established favorites = little wobble "
        "below the entry price → fewer passive fills), but stays ≥ taker.",
    "zleadmk":
        "FIXATIOZ WITH MAKER ENTRY. The vol-normalized lead threshold (z ≥ 1, the "
        "cross-asset generalizer driving the race) with favorite_mk's maker+"
        "fallback entry: passive bid at the favorite's price (0.7 of the size, "
        "no fees), taker cross late in the window if unfilled — never "
        "miss a favorite that ran straight up. Paper forward-test on BTC+ETH of the "
        "execution gain (~2.5¢/$) on the deployed edge; taker twin = zlead. The "
        "magnitude isn't OOS-validatable on the cached tape (FINDINGS) — the "
        "oracle-settled forward-test is the judge. research/backtest_maker_entry.py.",
    "scalp":
        "LIQUIDITY PROVISION — not a bet on the outcome, a bet on the PRICE. The "
        "thin book OVER-REACTS to hurried flow: after the mid dives ~5¢ below "
        "its short EWMA reference, it BOUNCES back +6.5¢ over 30s on 5m (research 2026-06-25, "
        "n=4082, ~9σ, FILL-INDEPENDENT on the executed tape; +3.5-5.5¢ on 15m) vs "
        "a baseline ≈0. We post a passive bid below the reference on BOTH sides (a Down "
        "dip = an Up pop); when a hurried seller crosses (0.7 of the size, no "
        "maker fees — the SAME anti-selection model that unmasked the directional "
        "maker at −0.015 live), we are long the dip. We sell back on the rebound "
        "(maker, no fees); if no revert before MAX_HOLD or the close, we cut "
        "as taker — never carried to settlement. WHY THIS IS NOT THE KILLED "
        "DIRECTIONAL MAKER: that one bought-and-held the favorite (adverse fills right "
        "as it weakens); here we EXIT within seconds, settlement anti-selection "
        "doesn't apply. HONEST RISK: the +6.5¢ is real and "
        "fill-independent, but the CAPTURE is not — it needs maker fills on "
        "a thin book at ~8s poll (fast dips that revert in one tick are "
        "missed). Only live paper, which fills against the real exec_book, tells whether the "
        "revert is capturable. Tripwire: if net round-trips ≤ 0 over ~150 "
        "closes, the signal doesn't survive execution — we retire it. pmlab/scalp.py.",
    "scalpx":
        "SCALP WITH DEEPER DIPS (enter 6¢ / target 4¢ instead of 4/3). Targets "
        "the more pronounced over-reactions: fewer round-trips, bigger rebound "
        "aimed for each. Same mechanics, same tripwire (net realized P&L over ~150 closes).",
}

# Per-strategy priors from the OOS backtest (research/backtest_engine.py +
# backtest_lead.py + backtest_eth.py): net EV/$ and qualifying windows/day. The
# dashboard's projection shows this as the "📊 backtest" lens, beside the "🟡 en
# direct" lens (the live-realized EV) — two separate lenses, no shrinkage/blend.
# ev == the OOS EV/$ in BACKTEST below, so the page is self-consistent.
# Resolved by match_keyed (full -> brain-token -> brain-frame -> brain), so a
# deployed strat-token-frame name finds its specific backtest. A strat with no
# matching key has NO backtest (has_backtest=False) — no fabricated DEFAULT.
PRIORS = {
    # DEEP RE-AUDIT 2026-06-24 (research/backtest_eth.py): re-fetched the full free
    # history — BTC 15m 145 DAYS (01-29..06-23, n~14000), ETH 15m 91 DAYS — fixed the
    # 60s spot LOOKAHEAD (spot_at now = price AT t, what live sees), balanced OOS split
    # (btc @ 04-23, eth @ 05-08). The depth is DECISIVE: on multi-month data the bare
    # favorite, the vol gate AND the bps lead floor are all DEAD (regime artifacts);
    # ONLY the vol-normalised z-lead floor (zlead) survives — thin but stable (eth
    # the robust leg, btc thin). ev = deep OOS EV/$. SOL/5m are still shallow (not
    # deep-fetched) — noted. (The earlier 40-day numbers were already lookahead-fixed
    # but too short; the deep sample is the honest verdict.)
    "favorite":      {"ev": -0.0058, "tpd": 23.5},   # DEAD on 145d (win==price: efficient)
    "favorite_vol":  {"ev": -0.0040, "tpd": 15.2},   # DEAD on 145d (vol gate = regime)
    "favorite_wide":  {"ev": 0.0149, "tpd": 20.7},    # not deep-fetched (not deployed); 40d-era, likely dead
    "favorite_lead":     {"ev": 0.0014, "tpd": 21.4},    # ~zero on 145d (bps floor)
    "favorite_vollead":  {"ev": 0.0178, "tpd": 13.2},    # not deep-fetched (not deployed); 40d-era
    "favorite_conviction":       {"ev": 0.0178, "tpd": 13.2},    # not deep-fetched (not deployed); 40d-era
    "favorite_cheap":       {"ev": -0.0067, "tpd": 5.8},
    "favorite_lead-5m":   {"ev": 0.0052, "tpd": 42.7},   # SHALLOW 22d (5m not deep-fetched) ~zero
    "favorite_lead-eth":  {"ev": 0.0006, "tpd": 17.9},   # ~zero on 91d (bps floor dead off btc)
    "zlead-sol": {"ev": -0.0293, "tpd": 12.3},  # DEAD; SHALLOW 11d (sol not deep-fetched)
    "zlead-btc": {"ev": 0.0109, "tpd": 18.3},   # thin REAL on 145d — the survivor
    "zlead-eth": {"ev": 0.0224, "tpd": 17.7},   # REAL & robust on 91d (days+ 74%) — best survivor
    # z-floor on the 5m FRAME (btc) — DEEP-CONFIRMED 45d (n=1593): +0.0270 days+ 85%,
    # the STRONGEST + most stable edge, ~3x the volume of 15m -> compounds fastest. The
    # z floor, NOT the dead bps one. Keyed by FULL name so it isn't shadowed by
    # zlead-btc (15m).
    "zlead-btc-5m": {"ev": 0.0270, "tpd": 50.0},
    "zlead-eth-5m": {"ev": 0.0387, "tpd": 50.0},   # 18d, strongest edge/$ but carnet $57
    # sol/xrp ALIVE on 5m (DEAD on 15m!) — the z-floor generalizes on the fast frame. 18d.
    "zlead-sol-5m": {"ev": 0.0298, "tpd": 50.0},
    "zlead-xrp-5m": {"ev": 0.0372, "tpd": 50.0},
    # zleadx = stricter z>=1.5 floor — forward-test (z>=1.5 beats z>=1.0 on the sweep).
    "zleadx-btc-5m": {"ev": 0.0391, "tpd": 32.0},  # deep 45d, days+ 90%
    "zleadx-eth-5m": {"ev": 0.0431, "tpd": 32.0},  # 18d, days+ 90%, carnet mince
    # zleadn = z-floor + TIGHT band 0.85-0.90 (deep 15m sweep 2026-06-25): the longshot
    # premium concentrates in 0.85-0.90; deep favs 0.90+ DEAD. OOS beats the 0.85-0.95 zlead.
    "zleadn-btc": {"ev": 0.0362, "tpd": 7.1},   # 145d, OOS win 0.938/px 0.898
    "zleadn-eth": {"ev": 0.0375, "tpd": 6.2},   # 91d, OOS win 0.936/px 0.896
    # zleada = BTC-align veto (alts) ; zleadp = veto + longshot-flow size-tilt. SAME GATE, so the
    # same per-window backtest (the tilt only lifts the $-curve). Corpus-derived (entry-faithful px
    # @frac0.45, REAL settlement, alts only ; short sample 2-7d). Keyed by frame (no-op on btc).
    "zleada-15m": {"ev": 0.0954, "tpd": 8.0}, "zleada-5m": {"ev": 0.0920, "tpd": 24.0},
    "zleadp-15m": {"ev": 0.0954, "tpd": 8.0}, "zleadp-5m": {"ev": 0.0920, "tpd": 24.0},
    # maker brains (…mk) get NO prior — the maker EXECUTION gain isn't OOS-backtestable;
    # the dashboard shows the GATE's backtest (the taker twin) as a labelled reference.
}

# Full OOS backtest validation per strategy. DEEP RE-AUDIT 2026-06-24: one harness
# (research/backtest_eth.py), +2c haircut, real taker fee, ORACLE label, hold to settle,
# spot_at LOOKAHEAD-FREE, on the DEEP cache (BTC 145d / ETH 91d), balanced OOS split.
# ev == PRIORS ev. verdict = tripwire (OOS win-rate beat the avg price paid).
# VERDICT: depth kills the regime artifacts — bare favorite, vol gate, bps floor all
# DEAD on multi-month data; only the z-lead floor (zlead) survives, thin (eth robust,
# btc thin, sol dead). n here = the deep OOS tranche. SOL/5m still shallow (not deep-fetched).
BACKTEST = {
    "favorite":      {"ev": -0.0058, "n": 1500, "daysp": 0.50, "win": 0.924, "px": 0.924},
    "favorite_vol":  {"ev": -0.0040, "n": 1159, "daysp": 0.54, "win": 0.925, "px": 0.924},
    "favorite_wide":  {"ev": 0.0149, "n": 369, "daysp": 0.68, "win": 0.924, "px": 0.905},
    "favorite_lead":     {"ev": 0.0014, "n": 1382, "daysp": 0.60, "win": 0.932, "px": 0.926},
    "favorite_vollead":  {"ev": 0.0178, "n": 243, "daysp": 0.67, "win": 0.947, "px": 0.926},
    "favorite_conviction":       {"ev": 0.0178, "n": 243, "daysp": 0.67, "win": 0.947, "px": 0.926},
    "favorite_cheap":       {"ev": -0.0067, "n": 131, "daysp": 0.67, "win": 0.885, "px": 0.885},
    "favorite_lead-5m":   {"ev": 0.0052, "n": 992, "daysp": 0.71, "win": 0.933, "px": 0.924},
    "favorite_lead-eth":  {"ev": 0.0006, "n": 1085, "daysp": 0.55, "win": 0.932, "px": 0.927},
    "zlead-sol": {"ev": -0.0293, "n": 83,  "daysp": 0.33, "win": 0.904, "px": 0.926},
    "zlead-btc": {"ev": 0.0109, "n": 1148, "daysp": 0.68, "win": 0.943, "px": 0.929},
    "zlead-eth": {"ev": 0.0224, "n": 857, "daysp": 0.74, "win": 0.956, "px": 0.931},
    "zlead-btc-5m": {"ev": 0.0270, "n": 1593, "daysp": 0.85, "win": 0.953, "px": 0.924},  # DEEP 45d — confirmed
    "zlead-eth-5m": {"ev": 0.0387, "n": 325, "daysp": 0.90, "win": 0.960, "px": 0.920},   # 18d (deeper pending)
    "zlead-sol-5m": {"ev": 0.0298, "n": 388, "daysp": 0.90, "win": 0.954, "px": 0.922},   # 18d — alive on 5m
    "zlead-xrp-5m": {"ev": 0.0372, "n": 386, "daysp": 0.80, "win": 0.961, "px": 0.922},   # 18d — alive on 5m
    "zleadx-btc-5m": {"ev": 0.0391, "n": 1042, "daysp": 0.90, "win": 0.967, "px": 0.927}, # z>=1.5, deep 45d
    "zleadx-eth-5m": {"ev": 0.0431, "n": 216, "daysp": 0.90, "win": 0.968, "px": 0.923},  # z>=1.5, 18d
    "zleadn-btc": {"ev": 0.0362, "n": 64, "daysp": 0.70, "win": 0.938, "px": 0.898},  # band 0.85-0.90, deep 145d
    "zleadn-eth": {"ev": 0.0375, "n": 47, "daysp": 0.67, "win": 0.936, "px": 0.896},  # band 0.85-0.90, 91d
    # zleada/zleadp = BTC-align veto (+ flow-tilt for zleadp). Corpus-derived (entry px @frac0.45,
    # REAL settlement, ALTS only, SHORT sample). Same gate → same record; the tilt lifts the curve.
    "zleada-15m": {"ev": 0.0954, "n": 905, "daysp": 1.0, "win": 0.950, "px": 0.868},  # veto-alt, 7d (shallow)
    "zleada-5m": {"ev": 0.0920, "n": 586, "daysp": 1.0, "win": 0.951, "px": 0.871},   # veto-alt, ~2d (shallow)
    "zleadp-15m": {"ev": 0.0954, "n": 905, "daysp": 1.0, "win": 0.950, "px": 0.868},  # veto+tilt, 7d (shallow)
    "zleadp-5m": {"ev": 0.0920, "n": 586, "daysp": 1.0, "win": 0.951, "px": 0.871},   # veto+tilt, ~2d (shallow)
    # NO maker (…mk) entries — a maker has no own OOS backtest; the dashboard shows its
    # GATE's backtest (the taker twin) as a labelled reference instead.
}

# FEE: the PRIORS/BACKTEST ev above were computed WITH the 0.07 taker fee, and that fee IS
# really charged on-chain (re-verified 2026-06-27 from raw tx receipts — pUSD relayer 0xe111…
# forwards 0.07×p×(1−p)×shares to collector 0x115f48dc at entry, BOTH 5m & 15m). So the ev is
# shown as-is, fee included — NO add-back. The 2026-06-26 "fee=0 phantom" add-back was the
# error (it tracked USDC.e and missed the pUSD fee leg). The +2c slippage haircut also stays
# (real, just smaller than the backtest models → conservative).


# Deployed runners are named brain[-token][-frame] (zlead-sol-15m, favorite_lead-btc-5m).
# The backtest tables (PRIORS/BACKTEST) are keyed by whatever specificity the backtest
# had: brain ("favorite"), brain-frame ("favorite_lead-5m"), or brain-token ("zlead-sol").
# Match most- to least-specific so a deployed name finds ITS backtest instead of
# silently collapsing to the generic 0.03 DEFAULT (the old split("-")[0] bug:
# zlead-sol-15m -> "zlead" -> no key -> DEFAULT; favorite_lead-btc-5m -> "favorite_lead" ->
# the 15m generic, not the 5m number).
_TOKENS = set(_COIN_BET_MAX)    # the canonical coin set (single registry coins.py) — was a
_FRAMES = ("5m", "15m")         # stale 4-coin literal that broke doge/bnb name resolution


def name_parts(name: str) -> tuple:
    seg = name.split("-")
    return (seg[0],
            next((s for s in seg if s in _TOKENS), None),
            next((s for s in seg if s in _FRAMES), None))


def match_keyed(d: dict, name: str):
    """Best value in a name-keyed table for a deployed runner, trying
    full -> brain-token -> brain-frame -> brain. None if nothing matches, so the
    caller can tell 'no backtest' apart from a real one (vs a silent DEFAULT)."""
    brain, token, frame = name_parts(name)
    cands = [name]
    if token:
        cands.append(f"{brain}-{token}")
    if frame:
        cands.append(f"{brain}-{frame}")
    cands.append(brain)
    for k in cands:
        if k in d:
            return d[k]
    return None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion — honest small-sample bounds
    on the win-rate, so the tripwire isn't read off a point estimate alone."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return ((c - h) / d, (c + h) / d)


def max_drawdown(equity: list) -> float:
    """Worst peak-to-trough on the equity curve, in dollars (>= 0)."""
    peak = -1e18
    dd = 0.0
    for _ts, v in equity:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return dd


def live_metrics(name: str, state: dict | None, trades: list, equity: list) -> dict:
    """Per-strategy live edge metrics, read straight from the journal. EV/$ =
    realized P&L per dollar actually staked on SETTLED positions (matched
    BUY→SETTLE by slug). Also a ROLLING window (catches a regime turn before the
    cumulative figure does) and a Wilson CI on the win-rate (honest small-n)."""
    matched_prior = match_keyed(PRIORS, name)
    has_backtest = matched_prior is not None
    open_stake: dict[str, float] = {}
    open_px: dict[str, float] = {}
    settled_stake = settled_pnl = fees = 0.0
    wins = losses = 0
    entry_px: list[float] = []
    win_amt: list[float] = []
    loss_amt: list[float] = []
    seq: list[dict] = []                       # ordered settled records
    for t in trades:
        kind = t.get("kind", "")
        slug = t.get("slug", "")
        # Entry/close/win classification lives in pmlab.journal — the SINGLE
        # definition shared with the correlation matrix (cf. always-modularize). It was a
        # maker-fill bug magnet: FILL_BUY doesn't start with "BUY", REST_BUY must not count.
        if is_entry(kind):
            try:
                stk = float(t["shares"]) * float(t["price"])
                px = float(t["price"])
                fees += float(t.get("fee") or 0.0)
            except (KeyError, ValueError):
                continue
            # a top-up ("BUY+", or a 2nd taker BUY) ADDS to the same window's stake —
            # accumulate, don't overwrite, or the BUY→SETTLE-by-slug denominator drops
            # the first fill and inflates EV/$. Entry price is recorded once per window
            # (the first fill) so the win<=price tripwire stays one row per trade.
            if slug in open_stake:
                open_stake[slug] += stk
            else:
                open_stake[slug] = stk
                open_px[slug] = px
                entry_px.append(px)
        # A close is an outcome-bet SETTLE *or* a scalp round-trip exit (SELL =
        # taker bail, FILL_SELL = the resting maker exit that caught the revert).
        # The scalp (liquidity provision) NEVER settles — it always exits intra-
        # window — so without counting its sells n_settled stays 0 and the whole
        # dashboard reads "en attente" while only $/jour shows its realized money.
        elif is_close(kind):
            stk = open_stake.pop(slug, None)
            px = open_px.pop(slug, None)
            try:
                pnl = float(t.get("pnl") or 0.0)
            except ValueError:
                pnl = 0.0
            fees += float(t.get("fee") or 0.0)     # taker bail fee (0 for a settle/maker exit)
            won_flag = won_of(kind, pnl)   # outcome bet by kind; scalp round-trip by sign
            if stk:
                settled_stake += stk
                settled_pnl += pnl
            (win_amt if won_flag else loss_amt).append(pnl)
            wins, losses = wins + won_flag, losses + (not won_flag)
            seq.append({"won": won_flag, "px": px or 0.0, "pnl": pnl, "stake": stk or 0.0})
    n = wins + losses
    run_days = ((equity[-1][0] - equity[0][0]) / 86400) if len(equity) >= 2 else 0.0
    recent = seq[-30:]
    r_stake = sum(s["stake"] for s in recent)
    r_wins = sum(s["won"] for s in recent)
    ev_live = (settled_pnl / settled_stake) if settled_stake > 0 else 0.0
    lo, hi = wilson(wins, n)
    avg_price = (sum(entry_px) / len(entry_px)) if entry_px else 0.0
    fee_per_stake = (fees / settled_stake) if settled_stake > 0 else 0.0
    # result windows off the equity curve (equity = cash + settled, never reserved)
    def _eq_at(ts: float) -> float:
        val = equity[0][1]
        for t, v in equity:
            if t <= ts:
                val = v
            else:
                break
        return val
    pnl_day = pnl_hour = per_hour = 0.0
    if len(equity) >= 2:
        now_ts, cur_eq = equity[-1][0], equity[-1][1]
        lt = time.localtime(now_ts)
        midnight = now_ts - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)
        pnl_day = cur_eq - _eq_at(midnight)            # since local midnight
        pnl_hour = cur_eq - _eq_at(now_ts - 3600)      # rolling last hour
        span_h = (equity[-1][0] - equity[0][0]) / 3600.0
        per_hour = ((cur_eq - equity[0][1]) / span_h) if span_h > 0 else 0.0
    return {
        "ev_live": ev_live,
        "ev_prior": matched_prior["ev"] if matched_prior else None,   # None => no real backtest (e.g. maker)
        "tpd_prior": matched_prior["tpd"] if matched_prior else None,
        "has_backtest": has_backtest,
        "tpd_live": (n / run_days) if run_days > 0 else 0.0,
        "n_settled": n,
        "win_rate": (wins / n) if n else 0.0,
        "win_lo": lo, "win_hi": hi,            # 95% Wilson on the win-rate
        "avg_price": avg_price,
        "avg_win": (sum(win_amt) / len(win_amt)) if win_amt else 0.0,
        "avg_loss": (sum(loss_amt) / len(loss_amt)) if loss_amt else 0.0,
        "run_days": run_days,
        # rolling last-30
        "n_recent": len(recent),
        "win_recent": (r_wins / len(recent)) if recent else 0.0,
        "px_recent": (sum(s["px"] for s in recent) / len(recent)) if recent else 0.0,
        "ev_recent": (sum(s["pnl"] for s in recent) / r_stake) if r_stake > 0 else 0.0,
        # risk / cost
        "max_dd": max_drawdown(equity),
        "fees": fees,
        "fee_share": (fee_per_stake / (ev_live + fee_per_stake))
                     if (ev_live + fee_per_stake) > 0 else 0.0,
        # result windows (realized equity deltas)
        "pnl_day": pnl_day, "pnl_hour": pnl_hour, "per_hour": per_hour,
    }


# --------------------------------------------------------------- assets ---
# The front-end is a small Preact SPA living in webdash_assets/ (no build step):
#   index.html  shell + import map     style.css  the design system
#   vendor/     preact · htm · chart.js (ESM/UMD, vendored — never a pip dep)
#   fonts/      Fraunces · IBM Plex Mono (woff2)
#   app/        main.js, api.js, format.js, components/*, pages/*
# Static files are served under /a/<path>; the SPA fetches its static config
# from /config and live data from the JSON endpoints below.
ASSETS = Path(__file__).resolve().parent / "webdash_assets"

# boot-critical assets — the dashboard refuses to start without these
REQUIRED_ASSETS = ("index.html", "style.css", "app/main.js",
                   "vendor/preact.mjs", "vendor/htm.mjs", "vendor/hooks.mjs",
                   "vendor/chart.umd.js")

_CTYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json", ".woff2": "font/woff2", ".svg": "image/svg+xml",
    ".png": "image/png", ".ico": "image/x-icon", ".map": "application/json",
}


# Per-strategy BACKTEST EQUITY CURVES, keyed by deployed strat name
# (research/gen_backtest_curves.py → JSON). A faithful replay of each deployed
# gate over the cached tape: cumulative $ P&L at STAKE_REF, IS/OOS split marked so
# the dashboard can colour the out-of-sample tranche. Absent file → no curves
# shown (graceful: the dashboard runs fine without it; regenerate offline).
def _load_curves() -> dict:
    p = Path(__file__).resolve().parent / "research" / "backtest_curves.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


BTCURVES = _load_curves()


def site_config() -> dict:
    """The static config the SPA reads once from /config (window.CONFIG)."""
    return {
        "STRATS": list(STRATS), "DESCS": DESCRIPTIONS,
        "BACKTEST": BACKTEST, "STAKE": STAKE_REF,
        "FAMILY": FAMILY, "EVENTS": list(EVENT_STRATS), "COPY": list(COPY_STRATS),
        "BTCURVE": BTCURVES,
        # preset gate params (band, lead floor, entry slot enter_lo/hi) — the front-end
        # renders the human "entry window" + gate summary from this, no JS duplicate.
        "PRESETS": {k: p.config() for k, p in _PRESETS.items()},
        # per-coin entry-slot (enter_lo) overrides — so the UI shows the REAL slot per coin
        "COIN_SLOTS": _COIN_SLOTS,
        # book-depth bet ceiling + the weighted-sizing model the paper runners use (WEIGHT_PCT of
        # capital, capped at the per-(coin,frame) depth, START starting capital). COIN_DEPTH =
        # {coin:{frame:$}} (the real per-frame ceilings) ; COIN_BET_MAX = conservative min-across-
        # frames scalar (legacy, frame-agnostic fallback).
        "COIN_DEPTH": _COIN_DEPTH, "COIN_BET_MAX": _COIN_BET_MAX,
        "WEIGHT_PCT": _WEIGHT_PCT, "START": _START_CAPITAL,
        # the canonical coin + frame lists (single registry) — the front-end derives its
        # dropdowns/orders from these instead of re-hardcoding TOKENS/FRAMES/COIN_ORDER.
        "COINS": list(_COIN_BET_MAX), "FRAMES": list(_FRAMES),
    }


def serve_asset(rel: str):
    """Resolve a /a/<rel> request to a file under ASSETS. Returns (body, ctype,
    cacheable) or None on miss / path-traversal attempt. Vendored libs and fonts
    are cacheable; app code and the shell are not (so a redeploy shows at once)."""
    rel = rel.lstrip("/") or "index.html"
    target = (ASSETS / rel).resolve()
    if not str(target).startswith(str(ASSETS.resolve()) + os.sep) or not target.is_file():
        return None
    ctype = _CTYPES.get(target.suffix.lower(), "application/octet-stream")
    cacheable = rel.startswith(("vendor/", "fonts/"))
    return target.read_bytes(), ctype, cacheable


def collect(name: str, light: bool = False) -> dict:
    """Snapshot a runner's state for the dashboard.

    light=True (used by /all, polled every 10 s for the sidebar + overview) keeps
    state / metrics / tick / equity but DROPS the bulky journal, decisions and log
    arrays — those are only read by the per-strategy page (/data, full collect),
    not by /all's consumers. With 13+ runners this cut the /all payload ~10× (it
    was ~0.5 MB/poll). Trades are still READ here (live_metrics needs them); they
    are simply not serialised into the light response.
    """
    meta = STRATS.get(name) or EVENT_STRATS.get(name) or COPY_STRATS[name]
    sd = Path(meta["state_dir"])
    state, trades, equity, log = None, [], [], []
    f = sd / "state.json"
    if f.exists():
        state = json.loads(f.read_text())
    f = sd / "journal.csv"
    if f.exists():
        with f.open() as fh:
            rows = list(csv.DictReader(fh))
        # A hard reboot (VPS, 2026-07-15) leaves ext4 blocks allocated but
        # zero-filled at the tail of an appended file: some rows come back as
        # NUL runs. Skip what does not parse instead of killing /all.
        for t in rows:
            try:
                t["ts"] = int(t.get("ts") or "")
            except (TypeError, ValueError):
                continue
            trades.append(t)
    f = sd / "equity.csv"
    if f.exists():
        rows = f.read_text().strip().splitlines()
        points = []
        for r in rows:
            a, _, b = r.partition(",")
            try:
                points.append([int(a), float(b)])
            except ValueError:                     # same NUL-run corruption
                continue
        step = max(1, len(points) // 600)          # cap points sent to the page
        equity = points[::step]
    tick = None
    f = sd / "tick.json"
    if f.exists():
        try:
            tick = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            tick = None
    metrics = live_metrics(name, state, trades, equity)
    if light:
        return {"state": state, "equity": equity, "tick": tick, "metrics": metrics}
    f = Path(meta["log"])
    if f.exists():
        log = f.read_text().splitlines()[-25:]
    decisions = []
    f = sd / "decisions.csv"
    if f.exists():
        with f.open() as fh:
            decisions = list(csv.DictReader(fh))
    return {"state": state, "trades": trades, "equity": equity, "log": log,
            "tick": tick, "decisions": decisions, "metrics": metrics}


def collect_events() -> list[dict]:
    """Per event-harvester: settled metrics + open positions + recent settlements."""
    out = []
    for name in EVENT_STRATS:
        d = collect(name)
        st = d["state"] or {}
        m = d["metrics"]
        pos = st.get("positions", {}) or {}
        positions = [{"slug": p.get("slug"), "fav": p.get("fav_outcome"),
                      "price": p.get("price"), "shares": p.get("shares"),
                      "opened": p.get("opened")} for p in pos.values()]
        settled = [t for t in d["trades"]
                   if str(t.get("kind", "")).startswith("SETTLE")][-20:]
        out.append({
            "name": name,
            "realized_pnl": float(st.get("realized_pnl", 0) or 0),
            "cash": float(st.get("cash", 0) or 0),
            "n_settled": m["n_settled"], "win_rate": m["win_rate"],
            "avg_price": m["avg_price"], "ev_live": m["ev_live"],
            "open": len(positions),
            "deployed": sum((p.get("price") or 0) * (p.get("shares") or 0)
                            for p in pos.values()),
            "positions": positions, "settled": settled})
    return out


def collect_copy() -> list[dict]:
    """Per copy-mirror runner: settled metrics straight from state, open mirrored
    positions, recent settlements, and how many of the target's fills we've replicated.
    win/n/PnL come from state.json (not slug-matched live_metrics) because several fills
    average into one position — slug matching would undercount the staked dollars."""
    out = []
    for name in COPY_STRATS:
        d = collect(name)
        st = d["state"] or {}
        pos = st.get("positions", {}) or {}
        n = int(st.get("n_trades", 0) or 0)
        wins = int(st.get("n_wins", 0) or 0)
        scost = float(st.get("settled_cost", 0) or 0)
        realized = float(st.get("realized_pnl", 0) or 0)
        positions = [{"slug": p.get("slug"), "title": p.get("title"),
                      "outcome": p.get("outcome"),
                      "price": (p.get("cost", 0) / p["shares"]) if p.get("shares") else 0,
                      "cost": p.get("cost"), "shares": p.get("shares"),
                      "n_fills": p.get("n_fills"), "opened": p.get("opened")}
                     for p in pos.values()]
        settled = [t for t in d["trades"]
                   if str(t.get("kind", "")).startswith("SETTLE")][-20:]
        out.append({
            "name": name,
            "target": st.get("target_name") or st.get("target", ""),
            "realized_pnl": realized,
            "cash": float(st.get("cash", 0) or 0),
            "initial": float(st.get("initial", 0) or 0),
            "n_settled": n, "win_rate": (wins / n) if n else 0.0,
            "roi": (realized / scost) if scost else 0.0,    # P&L per settled $ — vs their +24%
            "n_mirrored": int(st.get("n_mirrored", 0) or 0),
            "open": len(positions),
            "deployed": sum((p.get("cost") or 0) for p in pos.values()),
            "started": int(st.get("started", 0) or 0),
            "positions": positions, "settled": settled})
    return out


# Structural spot lens: the underlyings' co-movement DRIVES simultaneous favorite-flips, so
# it's the leading indicator of clustered losses — and unlike the realized loss-phi (which
# needs months of rare losses), it's measurable NOW from spot. Computed once per TTL (6
# klines fetches) and cached, since the SPA polls /correlation-data every few seconds.
_COIN_CORR_CACHE: dict = {"ts": 0.0, "corr": None}
_COIN_CORR_TTL = 300.0          # 5 min — coin betas drift slowly; don't hammer Binance per poll
_STRUCT_BUCKET = 900            # 15m return grid (the live-proven frame), aligned to epoch
_STRUCT_KLINES = 1000           # 1m candles per coin ≈ 16.6 h → ~66 aligned 15m anchors


def _coin_returns(symbol: str) -> dict:
    """{anchor_ts: simple return} of `symbol` over consecutive _STRUCT_BUCKET-second anchors
    aligned to the epoch grid (so every coin shares the same grid → comparable). Built from
    1m klines; returns {} on any fetch failure (that coin just drops out of the matrix)."""
    try:
        kl = feeds.btc_klines_1m(limit=_STRUCT_KLINES, symbol=symbol)
    except Exception:
        return {}
    close_at = {k["t"]: k["close"] for k in kl}
    anchors = sorted(t for t in close_at if t % _STRUCT_BUCKET == 0)
    out = {}
    for a0, a1 in zip(anchors, anchors[1:]):
        if a1 - a0 == _STRUCT_BUCKET and close_at[a0] > 0:   # consecutive, no gap
            out[a1] = close_at[a1] / close_at[a0] - 1.0
    return out


def _coin_spot_corr() -> dict:
    """Cached coin×coin spot-return correlation {coin: {coin: rho}} over the recent 15m grid.
    The STRUCTURAL co-movement that bounds simultaneous-flip risk (see correlation.py). Self
    rho omitted (the matrix forces same-coin struct to 1.0). Returns {} if <2 coins fetch."""
    now = time.time()
    if _COIN_CORR_CACHE["corr"] is not None and now - _COIN_CORR_CACHE["ts"] < _COIN_CORR_TTL:
        return _COIN_CORR_CACHE["corr"]
    rets = {c: _coin_returns(_SYMBOL[c]) for c in _COIN_KEYS if c in _SYMBOL}
    rets = {c: r for c, r in rets.items() if r}
    corr: dict = {c: {} for c in rets}
    coins = list(rets)
    for i, a in enumerate(coins):
        for b in coins[i + 1:]:
            common = sorted(set(rets[a]) & set(rets[b]))
            if len(common) >= _MIN_OVERLAP:
                rho = _pearson([rets[a][t] for t in common], [rets[b][t] for t in common])
                if rho is not None:
                    corr[a][b] = corr[b][a] = rho
    _COIN_CORR_CACHE.update(ts=now, corr=corr)
    return corr


def _coin_of(name: str) -> str | None:
    """Underlying coin of a crypto runner name 'variant-coin-frame' (e.g. zlead-btc-15m ->
    'btc'), or None if the middle token isn't a known coin (defensive — never guess)."""
    parts = name.split("-")
    return parts[1] if len(parts) >= 3 and parts[1] in _COIN_KEYS else None


def collect_correlation() -> dict:
    """Live cross-strategy correlation across the crypto race (the candidate pool a
    strategy-ETF would draw from), through three lenses: simultaneous-loss phi (primary,
    realized but slow), full-P&L rho (secondary), and the STRUCTURAL spot co-movement
    (immediate — coin betas lifted onto the strat grid). Reads each runner's journal and
    hands {name: trades} + the coin maps to correlation.correlation_matrix. Scoped to
    STRATS: the window-ts alignment is meaningful only for the deterministic
    {coin}-updown-{frame}-{ts} slugs (events use a different slug shape)."""
    runners: dict[str, list] = {}
    for name, meta in STRATS.items():
        f = Path(meta["state_dir"]) / "journal.csv"
        if f.exists():
            with f.open() as fh:
                runners[name] = list(csv.DictReader(fh))
        else:
            runners[name] = []
    coin_of = {name: _coin_of(name) for name in runners}
    return correlation_matrix(runners, coin_of=coin_of, coin_corr=_coin_spot_corr())


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype, cache=False):
        # The SPA polls every few seconds then navigates away / closes the tab,
        # resetting the socket mid-response. The stdlib would log a full traceback
        # for that benign disconnect — swallow it (see also DashServer.handle_error).
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # live data + the shell never cache (a redeploy must show at once);
            # vendored libs + fonts are immutable, so let the browser keep them.
            self.send_header("Cache-Control",
                             "public, max-age=86400" if cache else "no-store, no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        # --- live JSON endpoints ---
        if path == "/data":
            name = parse_qs(url.query).get("strat", [next(iter(STRATS))])[0]
            if name not in STRATS:
                self.send_error(404)
                return
            return self._send(json.dumps(collect(name)).encode(), "application/json")
        if path == "/all":
            # light=True: sidebar + overview need state/metrics/tick/equity, not the
            # per-strat journal/decisions/log (those load on the /data page only).
            return self._send(json.dumps({n: collect(n, light=True) for n in STRATS}).encode(), "application/json")
        if path == "/history":
            f = Path("history/runs.json")
            return self._send(f.read_text().encode() if f.exists() else b"[]", "application/json")
        if path == "/events-data":
            return self._send(json.dumps(collect_events()).encode(), "application/json")
        if path == "/copy-data":
            return self._send(json.dumps(collect_copy()).encode(), "application/json")
        if path == "/correlation-data":
            return self._send(json.dumps(collect_correlation()).encode(), "application/json")
        if path == "/config":
            return self._send(json.dumps(site_config()).encode(), "application/json")
        # --- static front-end ---
        if path == "/" or path == "/index.html":
            got = serve_asset("index.html")
        elif path.startswith("/a/"):
            got = serve_asset(path[3:])
        else:
            got = None
        if got is None:
            self.send_error(404)
            return
        body, ctype, cacheable = got
        return self._send(body, ctype, cacheable)

    def log_message(self, *a):   # keep stdout quiet
        pass


class DashServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A polling client that resets the socket is normal here, not a fault;
        # the stdlib default dumps a traceback for it. Swallow connection resets,
        # surface anything genuinely unexpected.
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            super().handle_error(request, client_address)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--strat", action="append", default=[],
                   help="crypto runner name:state_dir:log_file (repeatable)")
    p.add_argument("--estrat", action="append", default=[],
                   help="event-market harvester name:state_dir:log_file (repeatable)")
    p.add_argument("--cstrat", action="append", default=[],
                   help="wallet copy-mirror runner name:state_dir:log_file (repeatable)")
    p.add_argument("--port", type=int, default=8420)
    p.add_argument("--host", default="0.0.0.0")
    a = p.parse_args()
    # Fail loud if the front-end assets did not ship (e.g. an rsync that forgot
    # webdash_assets/) — better a clear boot error caught by the watchdog than a
    # blank dashboard serving 500s.
    missing = [f for f in REQUIRED_ASSETS if not (ASSETS / f).exists()]
    if missing:
        sys.exit(f"front-end assets missing in {ASSETS}: {', '.join(missing)} "
                 "— resync webdash_assets/ (see CLAUDE.md, deployment)")
    # The favorite race: the favorite-longshot harvester + backtest-derived
    # variants. The VPS passes explicit --strat; this is the local default.
    specs = a.strat or ["favorite-15m:state_rubedo:favorite.log",
                        "favorite_vol-15m:state_favorite_vol:favorite_vol.log",
                        "favorite_wide-15m:state_favorite_wide:favorite_wide.log"]
    for spec in specs:
        name, sd, lg = spec.split(":")
        STRATS[name] = {"state_dir": sd, "log": lg}
    for spec in a.estrat:
        name, sd, lg = spec.split(":")
        EVENT_STRATS[name] = {"state_dir": sd, "log": lg}
    for spec in a.cstrat:
        name, sd, lg = spec.split(":")
        COPY_STRATS[name] = {"state_dir": sd, "log": lg}
    print(f"dashboard on http://{a.host}:{a.port}/ — strats: {', '.join(STRATS)}"
          + (f" | events: {', '.join(EVENT_STRATS)}" if EVENT_STRATS else "")
          + (f" | copy: {', '.join(COPY_STRATS)}" if COPY_STRATS else ""))
    DashServer((a.host, a.port), Handler).serve_forever()
