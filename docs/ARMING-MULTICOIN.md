# Multi-coin arming — zlead on all tokens (15m), risk model & sizing

> Analysis 2026-06-25. Goal: model what arming `zlead` for REAL yields (and risks), in
> parallel on all 15m up/down coins, with a per-coin entry cap (anti-slippage) and a
> weighted % of capital. Everything is conditional on **the edge existing** — see the
> ruin verdict.

## 0. The one-line verdict
The payoff is asymmetric (**+0.11 if win / −1.00 if lose**, break-even = the price paid ~0.90).
Monte-Carlo consequence: **if the edge is real (win-rate > price), ruin ≈ 0% and we saturate
capacity; if there is NO edge, ruin ≈ 100% (no soft landing).**
There is no gray zone. Everything rests on the per-coin `win > price` tripwire.

## 1. Book depths MEASURED (not guessed)
Probed live on Polymarket: $ sweepable as taker in the favorite band 0.85–0.93,
at +2c above the best ask (= the realistic entry cap; `live.py` abandons beyond 2c).

| coin | cap ($@+2c) | full band 0.85–0.95 | trades/day (live) |
|------|----------------:|-------------------------:|-------------------:|
| BTC  | **1 295** | 4 195 | 34 |
| ETH  | 351 | 1 921 | 29 |
| SOL  | **519** | 1 243 | 28 |
| XRP  | 227 | 297 | 26 |
| DOGE | 76 | 211 | 26 |
| BNB  | **9 — DEAD** | 94 | 24 |

BTC carries ~2/3 of the capacity. **BNB is untradeable at size** (book ~$9 at the favorite).

## 2. EV/$ per coin (assumptions, anchored on live + judgment)
- **Live (measured, n~90, optimistic):** BTC +0.078, ETH +0.059.
- **Realistic (half, conservative):** BTC +0.025, ETH +0.022, SOL/XRP +0.015, DOGE/BNB +0.012.
- **If live holds:** BTC +0.050, ETH +0.045, SOL/XRP +0.030, DOGE/BNB +0.025.
- **Alts = unknown** (zero live data yet). The z floor is vol-normalized → the edge
  generalizes; the favorite-longshot bias is *fatter* on retail alts. But the 15m alt
  backtest is thin and ~flat → the live tape decides (runners on probation).

## 3. IDEAL config (conservative) — 5 coins, BNB dropped
Sizing = ¼-Kelly de-correlated (ρ=0.5) on the realistic edge → safe even if the edge is only half there.

| coin | cap | % capital | trades/d | saturated throughput/d (realistic) |
|------|--------:|----------:|---------:|--------------------------:|
| BTC  | 1 295 | 1.9% | 34 | 1 101 |
| ETH  | 351 | 1.7% | 29 | 224 |
| SOL  | 519 | 1.1% | 28 | 218 |
| XRP  | 227 | 1.1% | 26 | 89 |
| DOGE | 76 | 0.9% | 26 | 24 |
| **Σ** | | **6.7%/window** | 143 | **1 655/d** (realistic) → 3 322/d (live) |

**Monte-Carlo (4000 paths, ρ=0.5, $100 start):**
- Realistic: P(ruin)=0%, median max drawdown 27% (P90 36%), 1-year capital **$219k** (P10 154k / P90 284k).
- If live holds: P(ruin)=0%, DD 12% (P90 16%), 1 year **$916k**.

## 4. SCALE-FAST config — 6 coins, $380 real start
Aggressive (10% on the proven BTC/ETH, tapering on the unproven/thin alts).
Money we are prepared to lose + a $165 P&L buffer. All coins = breadth test.

| coin | % capital | cap | trades/d |
|------|----------:|--------:|---------:|
| BTC  | 10% | 1 295 | 34 |
| ETH  | 10% | 351 | 29 |
| SOL  | 6% | 519 | 28 |
| XRP  | 6% | 227 | 26 |
| DOGE | 4% | 76 | 26 |
| BNB  | 2% | 9 (capped de facto) | 24 |
| **Σ** | **38%/window** | | 167 |

**Monte-Carlo (3500 paths, ρ=0.5, $380 start):**
| scenario | P(ruin<$40) | P(dips into deposit, <$215) | max DD (med/P90) | 1 month | 6 months | 1 year (median) |
|----------|---:|---:|---:|---:|---:|---:|
| Realistic | 2% | **33%** | **71% / 89%** | $10k | $206k | **$444k** |
| If live holds | 0% | 2% | 37% / 52% | $74k | $554k | **$1.13M** |
| No edge | 100% | 100% | 100% | $5 | $0 | **$0** (in ~3 months) |

⚠️ **Scale-fast scales fast BUT the drawdown is brutal**: 71% median in the realistic case
(the $165 buffer ≈ 43% does NOT cover it). 33% chance of dipping into the deposit early.
That is the price of aggressiveness — acceptable in test/losable-money mode, provided you
know it.

## 5. Sensitivity to correlation (your intuition: they aren't all correlated)
Realistic edge, ¼-Kelly de-correlated sizing. LOWER correlation does not reduce the
drawdown (the sizing adjusts to it) — it **lets you safely bet bigger** → more upside:

| ρ | safe %/window | median DD | median final |
|---|---:|---:|---:|
| 0.2 | 11% | 32% | $318k |
| 0.4 | 8% | 26% | $227k |
| 0.6 | 6% | 24% | $130k |
| 0.8 | 5% | 23% | $49k |

→ The real value of spreading over 6 lowly-correlated coins = being able to deploy more
capital at the same risk. That's the right move **if each coin really has an edge**.

## 6. Operational notes (before arming real money)
- **Drop BNB** (dead $9 book).
- **Cap = hard limit per entry** (FAK sweeps up to the cap, abandons >2c).
- **BTC is the engine** (2/3 of throughput); ETH the 2nd; the alts are the conditional
  amplifier.
- **Per-coin kill-switch MANDATORY**: tripwire `win-rate > price paid` at n≥100. Any coin
  sticking to its price is in the "$0" scenario → cut it. (No per-trade stop: the
  asymmetry makes it counterproductive — see `stop-loss-doesnt-apply`.)
- **Working capital** ~$1.5k to saturate all caps simultaneously; below that, the % of
  capital dominates (fast-compounding phase = the scaling phase).
- **BTC/ETH are already proven live** (+$171/+$115, win 0.98/0.96); SOL/XRP/DOGE are on
  PROBATION (runners deployed 2026-06-25) — only commit real money on them after
  `win > price` confirmation.

## 7. Method (reproducible)
Depths: sampler `scratchpad/depth_sampler.py` (live book, band 0.85–0.93).
Ruin/PnL: Monte-Carlo `scratchpad/mc.py` (Gaussian copula, real caps, ¼-Kelly).
Trades/day: read from the live runners (dashboard `/all`).
