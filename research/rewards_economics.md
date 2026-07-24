# Maker-rebate harvester — scaling economics (honest)

Source: research/std0_strategy.md + std0_ops.md + polymarket_rewards.md (decoded 2026-06-28).

## The unit economics
Maker rebate = **0.014 · p(1−p) USDC per FILLED maker share** (= 20% of the 0.07·p(1−p) taker fee).
- at p=0.50 → 0.0035 $/share (0.35¢)  ·  at p=0.65 → 0.0032 $/share  ·  at p=0.85 → 0.0018 $/share.
- So income = **(filled maker shares/day) × ~0.003**, maximised by filling near p≈0.5.
- The **spread P&L is ≈ breakeven** (std0 btc-5m +$0.09/window); it is NOT the income and must merely
  not bleed. The rebate is the whole profit.

## What each $/day requires (filled maker volume)
| target $/day | filled shares/day | filled notional/day (@~$0.6) |
|---|---|---|
| $100 | ~31k | ~$19k |
| $500 | ~156k | ~$94k |
| $1,000 (≈ std0 maker) | ~312k | ~$190k |

## Capital needed = MODEST (the turnover comes from churn, not balance)
std0 mints ~$2,500/window as ammunition and **recovers ~100% at redeem each window** (net-zero round-
trip). Working capital ≈ **$2,500–5,000**, churned ~288×/day (one btc-5m window every 5 min) → the
$190k/day notional. **You do NOT need $190k of capital — you need ~$5k working + the FILL VOLUME.**

## The real constraints (why it's not free money)
1. **Fill capture** — income ∝ your share of the maker side of each taker order. std0 is the dominant
   btc-5m maker (~100% of the pool). To earn you must win top-of-book fills (latency + size + uptime),
   splitting the pool with std0. Realistic capture competing head-on: a fraction of the pool.
2. **Spread must stay ≈ breakeven** — tight two-sided quotes get adversely selected (filled right
   before the price moves). If adverse-selection bleed > rebate, net-negative. This is THE hinge — the
   live `mark` P&L (ex-rebate) over many windows decides viability. std0 keeps it ~0 via fast re-peg +
   neutrality + the −60s settle cutoff.
3. **Finite pool** — btc-5m maker pool ≈ $1,000/day (it's 20% of that market's taker fees). The whole
   crypto-updown rebate universe ≈ $1,500–2,000/day (std0's level). You can match std0 with capital +
   execution; you cannot greatly exceed the pool without new markets.

## Path for us
- **$40 (now)**: proof-of-mechanism only — mint $15, quote, measure fills + mark. Earns cents.
- **~$2,500–5,000 working capital**: run the harvester at std0's per-window size on btc-5m (+ eth/sol
  15m where competition is thinner); earn a share of ~$1,000–2,000/day **iff** mark stays ≈ breakeven.
- Scale breadth across coins/frames once btc-5m mark P&L is proven non-negative live.

**Verdict:** replicable and it scales with capital + fill-share, to ~std0's $1–2k/day ceiling — but it
is a genuine latency/inventory MM operation, not a passive edge. Go/no-go = the live `mark` P&L.
