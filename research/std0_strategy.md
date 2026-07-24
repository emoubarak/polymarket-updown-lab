# std0 — reverse-engineered strategy (wallet `0xdf7930e89a2c47560165331863c31deca0733dcd`)

Proxy alias **std0** / "Traumatic-Heating". Signer EOA `0x3Ec3577A6a22F9B4716C5AeFe0963a052BF703a6`.
Headline ~**+$80k** since ~2026-05-25.

Data: full per-wallet `data-api/activity` history pulled via `&start=&end=` time-window
bisection (`std0_fetch.py`, adaptive to beat the ~3500-row offset cap = the truncation
trap), analysed by `std0_mm.py` / `std0_detail.py`; rebates pulled with the `&type=`
filter; live CLOB-book confirmation by `std0_live.py`. Cached activity =
**235k rows, 2026-06-22 → 06-27 (~5.5 days)**; the per-window analysis below was run on the
180k-row / ~4-day / ~5000-window snapshot (numbers are stable); rebate series = full lifetime.
(The wallet does ~40k rows/day, so a full back-to-05-25 pull is ~1.4M rows — the recent
multi-day sample is statistically ample for the medians/distributions reported here.)

---

## TL;DR — what std0 actually is

**std0 is a delta-neutral, two-sided liquidity-rewards harvester on Polymarket crypto
Up/Down windows.** It is NOT a favourite-longshot bettor and NOT an overround skimmer.
The machine, repeated every single window across btc/eth/sol/xrp × 5m/15m:

1. **~50 s BEFORE the window opens**, `SPLIT`-mint a big block of sets (btc-5m median
   **2500 sets** = $2500) → equal Up+Down inventory = *ammunition* to post resting size.
2. **During the window** (concentrated in the **first half**), post **two-sided resting
   quotes on BOTH outcomes**, pegged **TIGHT near the touch** (live: fills land **~0.5–1¢
   off the mid**; the ~4.3¢ btc → ~7.4¢ xrp *window-VWAP* spread is mid-drift, not a wide
   quote), in **tiny ~$3–4 clips**, **refreshing ~every 1–6 s** (median **78 fills/window**
   on btc-5m). It buys at its bid and sells at its ask, staying near the top of book.
3. It **only sells ~15–20 % of the minted inventory**; the rest is never touched.
4. It **stops quoting ~30–60 s before settlement** (last 30 s ≈ 0.2 % of volume).
5. **~44 s AFTER the window closes**, `REDEEM` the held winning shares at $1 → recovers
   the mint cost. Mint+redeem is a ~net-zero round-trip.

**The profit is NOT mainly the spread.** On the most recent ~4-day sample the realised
spread edge is **thin: median +$1.06 / window, ~+$550/day**, and btc-5m (its biggest
market) is **≈ break-even**. The real engine is **Polymarket MAKER/TAKER rebates
(liquidity rewards)** that show up as `MAKER_REBATE` / `TAKER_REBATE` rows in the feed:
**~$1,300–2,000 / day recently (~$1,778/day avg since 06-15)**. Spread (~$550/day) +
rebates (~$1,778/day) ≈ **~$2,330/day → ~$80k over 34 days, matching the headline.**

> Replication implication: the two-sided quoting is a **rebate-score maximiser**. Posting
> large resting size as close to the mid as the reward band allows, two-sided, with high
> uptime during the active first-half of each window, is what earns the money. The spread
> roughly covers adverse selection. **Without access to the Polymarket rewards program the
> raw edge is marginal-to-negative** (btc-5m ≈ 0, eth-5m/xrp-5m < 0 on this sample).

---

## Evidence by question

### Q4 — markets & timing
By $-volume traded: **btc-5m 64 %**, btc-15m 20 %, eth-15m 7 %, sol-5m 3.5 %, xrp-5m 2.6 %,
sol-15m 1.6 %, xrp-15m 1.2 %. (No doge/bnb; 5m + 15m only.) **btc-5m is the core market.**

Seconds-into-window concentration ($-weighted):
- **5m**: **69 % of volume in the first half (0–150 s)**, median fill **~104 s**, last 60 s
  4.2 %, **last 30 s 0.2 %** → quotes are live early and pulled before settlement.
- **15m**: 60 % first half, median ~370 s, last 60 s 1.1 %.
- `SPLIT` mint timing: median **−50 s (5m) / −47 s (15m)** vs window open, **99 % pre-open**.
- `REDEEM` timing: median **+44 s after close** (p90 +103–163 s).

### Q1 — SELL vs mid (the maker signature)
Recovered without historical books two ways, cross-confirmed by a live book capture:
- **Self-quoted spread** (same token, BUY-VWAP=its bid vs SELL-VWAP=its ask, per window):
  it **sells a median ~2.3–2.7¢ ABOVE its own mid** and symmetrically buys below — i.e. it
  is the resting maker on both sides. Full posted spread (askVWAP−bidVWAP): btc-5m **0.043**,
  btc-15m 0.053, eth-15m 0.065, sol-5m 0.060, xrp-5m **0.074** (wider where books are thinner).
- **Overround** (VWAP(sell Up)+VWAP(sell Down)−1) ≈ **+0.0001 median** → it does **NOT**
  dump both sides above par; the edge is **intra-token bid/ask spread**, not overround.
- **LIVE BOOK CAPTURE (16 min, real CLOB mid, 475 matched fills):** SELL median
  **+0.5¢ ABOVE the live mid** (60 % above; Up 67 %, Down 52 %), BUY median **−0.5¢ BELOW
  the live mid** (64 % below). SELL at/above best ask **53 %**, BUY at/below best bid **58 %**
  → it is predominantly the **resting maker getting hit on BOTH sides, ~0.5–1¢ off the
  touch**. (This instantaneous ±0.5¢/fill is *tighter* than the ±2.3¢ window-VWAP figure
  because the mid drifts within a window — i.e. it actually quotes TIGHT, right near the top
  of book, and the wider VWAP gap is mid-drift, not a wide posted spread.)
- Inventory disposal: minted ~1.79M sets, **sold only ~15 %** of shares, **redeemed ≈100 %**
  of minted → most inventory is held & redeemed, not sold. The mint is *ammunition + reward
  size*, recovered at par, not a position.

### Q3 — two-sidedness / neutrality
- Median **net (Up sold $ − Down sold $) ≈ 0**; |net|/gross median 0.49; ~50 % of windows
  "balanced" (|net|/gross < 0.5).
- End-of-window **directional lean** (invUp−invDown)/mint: median **0.000**, p10/p90 **±0.06**.
- **0 % of 3,941 minted windows ever end net-SHORT either outcome** → because it stays long
  the balanced minted block, it always holds positive shares on the winner → **zero
  settlement/short risk**. Genuinely delta-neutral.

### Q2 — representative btc-5m window (timeline shape)
`SPLIT 2500 @ t−50s` → from ~t+32 s a dense burst of paired fills: `BUY Up @0.71 / SELL
Down @0.29`, `SELL Up @0.70 / BUY Down @0.30`, … ~5–40-share clips, same-second bursts,
trailing off by ~t+200 s → `REDEEM ~2600 @ t+~350s`. Ends slightly net-long the minted
block on whichever side flow pushed; redeems it. Typical window net cash a few $.

### Q5 — effective edge per round-trip / per window
- Fully-bracketed (has both SPLIT & REDEEM in sample) **4,416 windows**: net cash
  **median +$1.06, mean +$0.37, 56 % win**, total **+$1,646 (~$550/day)**.
- Per-coin-frame net (complete windows): **btc-15m +$6.07/win** (the real spread winner),
  sol-5m/15m small +, **btc-5m +$0.09/win (≈0)**, xrp-5m −$0.27, **eth-5m −$2.87/win**.
- Per-$ minted edge ≈ **0.03 % of notional** — wafer-thin; consistent with a maker that
  prices ~at fair value and lives off rebates.
- **Rebates dominate**: `MAKER_REBATE` lifetime $31,131 (68 payouts, avg $458),
  `TAKER_REBATE` $6,799 (22 payouts, avg $309); **~1–2 payouts/day, ~$1,778/day since 06-15.**

### Q6 — clip sizes
- **SELL** median **$3.02** (mean $5.04, p90 $12), ~5–6 shares.
- **BUY** median **$3.88** (mean $5.75, p90 $13).
- **SPLIT** median **1000 sets** overall, **2500 on btc-5m** (p90 2500).
- **REDEEM** median **~$1015** (tracks the minted block).
- Cadence: btc-5m **78 fills/window** (p10 39, p90 123), inter-fill gap **median 1.0 s**
  (same-second bursts, p90 6 s).

---

## IMPLEMENTABLE SPEC

Per `<coin>-<frame>` Up/Down window (run the same loop on every window you want size in;
weight capital toward btc-5m & btc-15m where std0 concentrates):

**Pre-window (t ≈ −50 s before open):**
- `SPLIT`-mint **N sets** of inventory. N ≈ **2500 for btc-5m / btc-15m**, ~**1000** for
  alts (scale to ~the resting size you intend to show, not to a directional position; it
  is recovered at redeem). This funds two-sided resting *size*, which is the rewards
  multiplier.

**In-window quoting (open → ~frame−60 s; quote only in the FIRST ~half, then thin out):**
- Maintain a **two-sided resting quote on BOTH Up and Down**:
  - post **SELL (ask)** on Up at **mid_Up + a**, **SELL (ask)** on Down at **mid_Down + a**;
  - post **BUY (bid)** on Up at **mid_Up − b**, **BUY (bid)** on Down at **mid_Down − b**.
  - **Peg TIGHT, near the touch.** Live-measured, fills land **~0.5–1¢ off the mid** (it
    sits at/just inside the top of book); the per-coin window-VWAP "spread" (btc ~4.3¢ …
    xrp ~7.4¢) overstates this because the mid drifts during the window. Practical setting:
    **a ≈ b ≈ 0.005–0.01 on btc**, widening to **~0.01–0.02 on sol/eth/xrp** (thinner books
    → step back a touch for protection). The **binding constraint is the Polymarket reward
    max-spread band** around the mid — quote as close to the mid as the band allows to
    maximise reward score; that, not the spread P&L, is what you are optimising.
  - **Clip size S ≈ $3–5 (5–10 shares)** per order; ladder a few clips if you want depth.
- **Refresh T ≈ every 1–6 s** (re-peg to the moving mid; cancel/replace stale quotes).
  Track the Binance spot of the underlying as the fair-value anchor for the mid.
- Let fills accumulate; **stay roughly balanced** (skew your quote to lean *against*
  inventory — widen/cancel the side you're getting too long, like classic MM inventory
  control). You will end each window slightly net-long the minted block on one side; that
  is fine and carries **no short risk**.
- **Stop quoting ~30–60 s before settlement** and pull all resting orders (avoid being run
  over by the late, information-rich flow when the outcome is nearly decided).

**Post-window (t ≈ +44 s after close):**
- `REDEEM` the held winning shares → recovers the mint, realises the spread you captured.
- Do **not** MERGE (std0 never does; redeem is cheaper/simpler here).

**Targets / sanity:**
- Expected **raw spread P&L: ~$1/window median, ~break-even on btc-5m** — do **not** expect
  to get rich on spread alone.
- **Real income = Polymarket maker rewards**: design quotes to **maximise reward score**
  (size × closeness-to-mid × uptime, two-sided), not spread. std0 books ~**$1,500–1,800/day
  in rebates** at its current size; that is ~75 % of its profit.
- Delta-neutral by construction (0 % short-the-winner across 3,941 windows). Main real risk
  = adverse selection on fills near settlement (mitigated by the −60 s cutoff) and
  competition compressing both spread and your share of the reward pool.

---

## Caveats / honesty
- The raw-spread edge is **thin and partly negative by market** (btc-5m≈0, eth-5m/xrp-5m<0)
  on the recent 4-day sample — consistent with the favourite-longshot/5m-flattery lessons in
  this repo. **The replicable, durable edge here is the rewards program, not a price edge.**
- A few hours of windows are noise (~12/h on 5m); figures above are over thousands of windows.
- Rebate dates run back to 03-21 (operator made markets before the crypto-updown push);
  treat the per-day rebate rate (recent) as the live signal, not the lifetime total.
- **Verify the rewards mechanics directly** before committing: read Polymarket's current
  liquidity-rewards program (max-spread band, min-size, scoring, per-market reward pools)
  and confirm the `MAKER_REBATE`/`TAKER_REBATE` cadence on this wallet via
  `data-api/activity?...&type=MAKER_REBATE`. The strategy's profitability *is* the rewards.
