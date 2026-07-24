# Specification — Polymarket Rebate Farming, maximum optimization

> Complete blueprint of a market-making system designed to **farm a maximum
> of maker rebate** on Polymarket's crypto binaries, profitably and at
> scale. Incorporates the lessons of the R&D phase (see `JOURNAL.md`): why
> std0 wins, where delta-neutral fails, and what it takes to play in the
> top league.

Version 1.0 — 2026-07-03.

---

## 0. Guiding principles (the 5 laws)

1. **Slightly directional, not delta-neutral.** Strict delta-neutral loses
   structurally on binaries (theta convergence). The winning method (std0)
   = sell the OTM, keep the ITM. We accept a bounded directional bias.
2. **The rebate is the product, trading is the production cost.** We don't
   trade to win on price; we trade to generate qualified maker volume.
   Objective: trading ≥ breakeven, rebate = profit.
3. **Volume comes first, but not at any price.** Maximize maker volume
   WITHOUT adverse selection exceeding the rebate. There is an optimum.
4. **Latency is an asset.** Every millisecond gained reduces adverse
   selection. We invest in infrastructure like an HFT.
5. **Scale via markets, not via the split.** Inflating the split on one
   market dilutes the return (limited flow). We replicate across N markets.

---

## 1. Trading strategy (the core)

### 1.1 Base method: the "directional std0"

- **Split** N pairs (Up∧Down) at the start of each window.
- **Aggressively sell the OTM side** (the one falling) — it ends at 0, so
  every sale at p>0 is pure gain.
- **Keep the ITM side** (the one rising) to settlement (redeems at $1).
- **Identify ITM/OTM in real time** via the momentum of the fair: the side
  whose fair is rising = presumed ITM (slight momentum edge over 5 minutes).
- **Target**: profit `≈ OTM_sale_price + 1.00 − 1.00 = OTM_sale_price` per
  pair (std0: ~+0.27/pair) + rebate.

### 1.2 Controlling the directional bias

- The directional bet (keeping the ITM) is **bounded**: cap on `|net|` per
  market, and an aggregate cap on total BTC exposure (the markets are
  correlated).
- **Adaptive flatten**: if the kept side reverses (the ITM becomes OTM),
  cut the loss (sell/hedge) before the convergence accelerates.
- Directional Sharpe objective: don't depend on the bet (the momentum edge
  is weak), but don't just absorb it either.

### 1.3 Window selection (don't trade just any time)

- **Volatility gate**: suspend if σ is out of band (too quiet = zero
  spreads, too volatile = adverse selection).
- **Adaptive adverse-selection gate**: EWMA of realized markout; suspend
  the market when we are getting picked off (already coded:
  `MARKOUT_GATE`).
- **Liquidity gate**: only trade if the book has a minimum depth (otherwise
  we are alone and get run over).
- **Opening silence**: 0 trades during [0, 30)s (the reference price
  settles; std0 does this).

---

## 2. Capital & sizing

### 2.1 Minimum viable tier

- **Target: $2,000 – $5,000 per active market.** Below that, the 5-share
  minimum clip is too large a fraction of the inventory → incompressible
  variance and residual (the hard lesson of the R&D phase at $60).
- At $2,000, split 200-400 pairs → a residual of 5 = 1-2.5%, negligible.

### 2.2 Collateral recycling

- **Automatic merge/split** to keep the cash turning (already coded). In
  directional mode, the kept ITM side ties up capital until redeem
  (gasless, automatic at expiry) — size for ~2-3 windows of capital tied
  up simultaneously.
- **Batch redeem**: Polymarket settles automatically; no gas.

### 2.3 Multi-market allocation

- Distribute capital across N markets (§5) in proportion to each market's
  **taker flow** (more flow = more capturable rebate).
- Free-collateral buffer (~20%) to absorb spikes and avoid "not enough
  balance" rejections.

---

## 3. Infrastructure & latency (the HFT advantage)

> This is where we were weakest. A "slow trader" gets picked off; the
> rebate doesn't compensate. High priority.

### 3.1 Compute

- **VPS/bare-metal as close as possible to Polymarket's servers** (measure
  the RTT to the CLOB API and the WS; aim for < 10 ms). Test several
  regions/providers.
- Compiled Go process, tuned GC, no allocation in the hot path.
- Synchronized clock (NTP/PTP) for order timestamps (ms timestamp).

### 3.2 Price feeds (the lifeblood)

- **Do NOT depend on the public Chainlink RTDS alone** (delay). Multiple
  sources:
  - Binance WS (spot BTC/ETH/SOL) — fast reference.
  - Coinbase/OKX WS — redundancy and divergence detection.
  - On-chain Chainlink — for **resolution** (it is the settlement source of
    truth), but not for the intra-window fair.
- **Fuse** the feeds (median/latency-weighted average) for a fair value
  fresh to the millisecond.
- Detect and **freeze** on a stale feed (already coded: chainlink stale
  gate).

### 3.3 Connections

- Persistent WS with heartbeat, reconnection < 250 ms, connection
  pre-warming.
- **No atomic cancel-replace** on Polymarket → optimize the
  cancel-then-place sequence and manage the risk window in between.

### 3.4 Measurement

- Instrument the **end-to-end latency** (feed → decision → order confirmed)
  and the **adverse-selection latency** (time between our quote and the
  fill).
- Markout per latency bucket: identify the threshold at which we become
  profitable.

---

## 4. Fair value & model

### 4.1 Model

- `fair = Φ(d2)`, `d2 = ln(S/K)/(σ√T)`. Solid base, already in place.
- **Vol**: double-EWMA calibrated per series (BTC ≠ SOL). Add an
  intra-window realized-vol term (the 5-min markets have their own regime).
- **Convergence**: explicitly model the theta near expiry so that resting
  asks do NOT get overtaken (the cause of the delta-neutral fire-sale).

### 4.2 Continuous calibration

- Backtest the fair against the actual settlement price (Chainlink close)
  to detect and correct any systematic bias.
- A/B test the vol parameters in shadow (paper) before live.

---

## 5. Multi-market coverage (the scaling)

### 5.1 Universe

- **BTC, ETH, SOL** × **5m and 15m** = 6 base series. Extend to the other
  listed assets (the 15m has more spread, fewer HFTs — often better).
- Each series = an independent taker flow → additive rebate.

### 5.2 Architecture

- **Option A (recommended): one multi-market process** sharing one wallet,
  one collateral pool, and aggregated risk management. Avoids cancel-all
  conflicts (a single owner of the wallet).
- Option B: N processes, separate wallets (simpler but fragmented capital).
- **Wire the feeds per asset** (Binance ethusdt/solusdt, strike endpoints,
  slug patterns `{asset}-updown-{5m|15m}`). The code is already
  multi-series for BTC; generalize to the asset.

### 5.3 Aggregated risk

- **Cross-market** exposure cap (BTC 5m and 15m are correlated → don't
  stack the same directional bet).
- Dynamically prioritize capital toward the markets with the best observed
  net-rebate/hour.

---

## 6. Execution engine (order management)

### 6.1 Live ladder (volume multiplier)

- Post **N laddered orders per side** (not 1) → capture more of the flow
  that crosses several price levels. This is THE identified volume/rebate
  lever (paper: +36-47% volume with the ladder). **Refactor needed**: order
  management from `[2]restingQuote` → N orders/side, indexed by orderID.

### 6.2 Adaptive requoting

- Requote to **follow the fair** (prevent the convergence from overtaking a
  resting ask). Short interval (~100 ms), gated by a min-move so as not to
  spam the rate limit.
- **Anti-pick-off guard** (already coded, to be generalized): instant pull
  of an order whose edge vs the current fair has collapsed.

### 6.3 Fine management of committed inventory

- **Known bug to fix**: the bot re-quotes a side whose inventory is already
  committed to an active order → "not enough balance" rejections in a loop
  → wastes the rate limit and caps the volume. Count the shares already in
  orders before re-offering.

### 6.4 Rate limit & token bucket

- Fine-grained per-second call budget; prioritize cancels (risk) over
  places (opportunity).

---

## 7. Rebate optimization (the specific point)

### 7.1 Understanding the program

- **Map the exact rules** of the crypto maker rebate program:
  - What % of the taker-fee pool? (observed ~0.56% of our volume, but it is
    a pool share, not a fixed rate).
  - Are there **qualification conditions** (proximity to the mid, minimum
    size, time present on the book)? Many programs require "useful"
    liquidity (near the mid) — verify.
  - Pro-rata distribution → **our share drops if the pool grows.** Track
    the market's total maker volume to estimate our share.
- **Timing**: rebate credited daily (~00:10-00:45 UTC). Measure actual vs
  estimated.

### 7.2 Maximizing qualified volume

- Be **top-of-book** as long as possible (time present is often a
  qualification criterion).
- **Ladder + multi-market + fast requote** = the three multipliers.
- Do NOT generate toxic volume (fills sold below fair) just for the rebate:
  the fire-sale cost > rebate. The markout gate protects against this.

### 7.3 Economic optimization

- Objective function: **maximize `Σ(rebate − fire_sale −
  adverse_selection)`** across all markets, not raw volume.
- Instrument **net rebate per $ of capital per hour** as the master KPI.

---

## 8. Risk management

- **Kill switch** (already robust): trips on dropped fills, unverifiable
  inventory, panics.
- **Caps**: per market (`|net|`), aggregate (BTC exposure), and total
  notional.
- **Equity guardrail**: automatic stop if equity drops below a threshold
  (lesson: NEVER leave it running without active monitoring — we bled −$27
  in 3h without monitoring).
- **Emergency flatten**: taker liquidation if a residual becomes dangerous
  near expiry.
- **Reconciliation**: on-chain inventory seed at restart, venue resync with
  fencing (already in place).

---

## 9. Observability & monitoring

- **Real-time metrics**: equity, net per market, volume, rebateEq, markout
  EWMA, latency, rejection rate.
- **Dashboards**: per-market and aggregated (industrialize the existing
  `dashboard.sh`/`volfarm.sh`).
- **Proactive alerting**: equity guardrail, markout drift, stale feed,
  abnormal rejection rate, kill switch. Immediate push (no manual polling).
- **Journal**: every fill, cancel, split, merge, redeem timestamped and
  replayable.
- **Actual net P&L** (`netpnl.py`): trading (CSV) + rebate (on-chain),
  reconciled daily against the wallet balance.

---

## 10. KPIs (what we optimize)

| KPI | Target |
|-----|--------|
| **Net rebate / $capital / day** | Master KPI — maximize |
| Trading P&L / window | ≥ breakeven (rebate = profit) |
| Average markout / share | ≥ 0 (no net adverse selection) |
| Maker volume / day | maximize under the constraint markout ≥ 0 |
| Top-of-book time | maximize (rebate qualification) |
| Feed→order latency | < 10 ms |
| Directional Sharpe | > 1 (bounded bet, not absorbed) |
| Order rejection rate | < 1% |
| Uptime / monitoring | 100% under guardrail |

---

## 11. Implementation roadmap

### Phase 1 — Validate the directional (modest capital)
- Switch to `STRATEGY=momentum`, test the std0 method (sell OTM / keep ITM)
  on BTC-5m, measure trading + actual rebate. Validate the momentum edge.

### Phase 2 — Latency infrastructure
- Benchmark/optimize the VPS location, fuse the fast feeds, measure latency
  and markout per latency bucket. Target < 10 ms.

### Phase 3 — Live ladder
- Refactor order management for N orders/side. Multiply volume/rebate. Fix
  the committed-inventory bug.

### Phase 4 — Multi-market
- Generalize the feeds to ETH/SOL, wire 5m+15m, one multi-market process,
  aggregated risk. Go from 1 to 6 series.

### Phase 5 — Capital scaling
- Raise capital in tiers ($2k → $5k → $10k+), verify that net-rebate/$/day
  holds (watch for pool dilution).

### Phase 6 — Continuous optimization
- Auto-calibration of the fair, dynamic capital allocation toward the best
  markets, A/B testing in shadow, gate tuning.

---

## 12. Return estimate (order of magnitude)

std0 reference: ~28% of capital/day in rebate at ~$8500 (multi-market,
directional). Our estimate at observed volume: ~0.56% of volume as rebate.

- **$2k tier, 1 market, directional**: target ~5-15%/day (rebate + momentum
  edge), to be confirmed live.
- **$5k tier, 6 markets, ladder, optimized infra**: target the std0 regime
  (~20-30%/day), provided the pool doesn't dilute and the latency holds.

⚠️ **These figures are targets, not guarantees.** The rebate is a pool
share (dilutes with competition), the momentum edge is weak and noisy, and
latency is a hard ceiling. Every phase must be **validated live at small
scale** before scaling — the most expensive lesson of the R&D.

---

## 13. What already exists (R&D achievements)

- Complete Go bot: CLOB V2, Safe signatures (sig type 2), Phase B on-chain
  (split/merge/redeem), robust kill switch, inventory seed/resync.
- Fair model Φ(d2) + double-EWMA + Avellaneda-Stoikov skew.
- Multi-series (5m/15m) wired for BTC.
- Gates: vol, chainlink stale, opening silence, anti-pick-off guard,
  adaptive markout.
- Auto-merge, analysis tools (netpnl, dashboard, volfarm, std0 decoding).
- std0 fully decoded (directional, 76% asymmetric, 67/33 maker/taker).

## 14. What remains to build (the gap to "top tier")

| Workstream | Impact | Effort |
|-----------|--------|--------|
| **Latency infra** (fast feeds, location) | ★★★ | High |
| **Live ladder** (N orders/side) | ★★★ | Medium-high |
| **Multi-market** (ETH/SOL, 6 series) | ★★★ | Medium |
| **Capital** ($2k+) | ★★★ | Decision (injection) |
| Committed-inventory fix (rejections) | ★★ | Low |
| Explicit convergence model | ★★ | Medium |
| Rebate rules mapping | ★★ | Low (research) |
| Dynamic capital allocation | ★ | Medium |
| Industrialized monitoring | ★★ | Medium |

**The critical path**: capital + directional (phase 1) → infra + ladder
(phases 2-3) → multi-market (phase 4). The first three unlock most of it;
the rest is marginal optimization.
