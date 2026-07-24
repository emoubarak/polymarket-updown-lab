# STRATEGY — what std0 is and how much it earns

Decoded by 2 subagents (~235k lines of activity + live book capture + RPC). Full details in the neighboring files of the `research/` folder:
- `research/std0_strategy.md` — the complete sell-side strategy + distributions
- `research/std0_ops.md` — the mechanics of the on-chain ops (mint/redeem cadence/sizes)
- `research/polymarket_rewards.md` — the exact rewards mechanism
- `research/rewards_economics.md` — the scaling economics

## TL;DR — what std0 is
**A delta-neutral two-sided LIQUIDITY-REWARDS harvester on the crypto Up/Down markets.** NOT a favorite-longshot bettor, NOT an overround skimmer.

The machine, every window (btc-5m = 64% of its volume, then btc-15m 20%, eth/sol/xrp; 5m+15m only):
1. **~50s BEFORE the open**: `SPLIT`-mints a big block of sets (btc-5m median **2500 sets = $2500**) = ammunition/resting size. **Mints DIRECTLY from its EOA** (pipelined nonces, ~$0.008 gas), not via relayer → no quota.
2. **During the window (first half)**: posts **resting two-sided maker quotes on both Up AND Down**, pegged tight (~0.5-1¢ off the mid live), clips ~$3-4, refresh every 1-6s, ~78 fills/window.
3. Sells only **~15%** of the inventory; **redeems ~100%** at settle. **NEVER merges.** Mint+redeem ≈ net-zero round-trip. 0% of the 3941 windows end net-short → **genuinely delta-neutral, zero settlement risk**.
4. Stops quoting 30-60s before settle.

## The EDGE = the rewards, NOT the spread
- **The spread is ~breakeven**: median +$1.06/window ≈ +$550/day; btc-5m ≈ 0, eth-5m/xrp-5m NEGATIVE.
- **The money = `MAKER_REBATE` + `TAKER_REBATE`**: $31,131 + $6,799 lifetime, **~$1,778/day since 06-15 = ~75% of the profit**. $550 spread + $1,778 rebates ≈ $2,330/day → **$80k in 34 days.** ✓
- **Rebate formula**: `0.014 × p(1−p)` USDC per maker share **FILLED** (= 20% of the taker fee 0.07·p(1−p)). Max at p=0.5 (0.35¢/share), p=0.65 → 0.32¢, p=0.85 → 0.18¢.
- **So the objective = MAXIMIZE FILLED maker volume near p=0.5.** Not resting size, not the spread. Get filled, a lot, near 0.5.
- **No signup/KYC/min-size for the program** (gamma's `rewardsMinSize=50` is VESTIGIAL — that's the Qmin system, which crypto is NOT in). Just quote and get filled as a maker.

## The scaling economics (how much for how much)
| Target $/day | filled maker shares/day | filled notional/day (@~$0.6) |
|---|---|---|
| $100 | ~31k | ~$19k |
| $500 | ~156k | ~$94k |
| $1,000 (≈ std0 maker) | ~312k | ~$190k (~$8k/h) |

- **Working capital = MODEST**: std0 mints ~$2,500/window, recovered ~100% at redeem (net-zero round-trip) → capital ~$2,500-5,000, **churned ~288×/day**. You do NOT need $190k, just ~$5k of float + the VOLUME of fills.
- **Ceiling**: the btc-5m pool ≈ $1,000/day (20% of the market's taker fees); the whole crypto-updown universe ≈ $1,500-2,000/day = std0's level. Replicable up to ~its level; not beyond without new markets.

## The 3 constraints to do it (and where we stand)
1. **Self-submittable wallet** (mint without quota) → **the Gnosis Safe to deploy** (see ARCHITECTURE). 🔴 missing
2. **Low latency** (no pickoff on fast 5m) → **the AWS Dublin box (24ms colocation)** + sub-second poll + spot-anchoring. 🟡 to exploit (I had set poll 3s = too slow)
3. **Capital** (big neutral blocks) → target $500-2500+. 🟡 currently ~$118
