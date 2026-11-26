# std0 Market-Maker Replication: Strategy and Design Notes

**Purpose.** Everything learned decoding the profitable Polymarket wallet **std0** (a maker-rebate
harvester on crypto Up/Down markets), plus the design of a replica engine. Self-contained.
Dated 2026-07-01. Currency = pUSD (Polymarket USDC). Every std0 figure is read on-chain (data-api),
never from an engine's own marks.

---

## 0. TL;DR

- **The edge is a REBATE, not a price bet.** Polymarket pays makers a rebate = **0.014·p·(1−p) per filled
  maker share** (= 20% of the 0.07 taker fee, crypto). Taker fills are ALSO rebated (~20%). No minimum order
  size, no spread requirement, $1 minimum payout, credited **daily ~00:45 UTC** in pUSD.
- **std0 harvests it by staying delta-NEUTRAL**: mint complete sets, quote both sides near 0.5, churn, and
  **never end up holding one side directionally** — it offloads the residual and holds a MATCHED block to redeem.
- **The whole game at small scale = stay neutral (no directional residual) so the rebate is free profit.**
  std0 did this from a tiny start ($4–35 blocks, ~$3.5/day) and compounded to $2500 blocks / ~$1200–2000/day.
- **The hinge is execution, not capital**: any engine that ends windows holding the losing side takes
  directional slips that swamp the tiny rebate. The design below targets a median imbalance of 0%; the key lever
  is **reliable residual offload (taker sell at the bid, which is rebated)**.
- **Wallet type matters**: a Gnosis Safe (CLOB signature type 2) is accepted by the CLOB AND can self-submit
  mint/merge/redeem on-chain with **zero relayer quota** (see ARCHITECTURE.md).

---

## 1. The target: std0 decoded

Wallet `0xdf7930e89a2c47560165331863c31deca0733dcd` (+$80k since 2026-05-25). Per crypto Up/Down window:

1. **~50s before open**: SPLIT-mint a block of complete sets (1 set = 1 Up + 1 Down = $1). Mature size ≈ 2500.
2. **In-window**: post **two-sided resting maker quotes** on Up and Down, pegged tight near the mid, small
   clips, refresh every 1–6s, many fills/window. Buys its bid, sells its ask.
3. **Offloads the residual** so it stays balanced — including selling the losing side cheap (**46% of its
   26-05 sells were at price <0.25**) and via **taker** sells (it earned $8.9k TAKER_REBATE lifetime).
4. **Holds a MATCHED block to auto-redeem** at settle (~89%), i.e. equal Up+Down = redeems at $1 = neutral.
   Never ends net-directional. Stops quoting ~30–60s before settle.

Markets: **btc-5m = 64% of volume**, btc-15m ~20%, then eth/sol/xrp. 5m + 15m only (no doge/bnb, no 1h).

## 2. The income (verified on-chain, 2026-07-01)

data-api `/activity` totals for std0:
| type | lifetime |
|---|---|
| **MAKER_REBATE** | **~$34,000** (the core) |
| **TAKER_REBATE** | **~$8,900** (taker fills are rebated too) |
| REWARD / liquidity / Qmin | **$0** (there is NO separate liquidity-mining program here) |
| TRADE (net) | ~$3,900 (trading itself ≈ breakeven; the rebate is the profit) |

**Rebate formula (from docs.polymarket.com/market-makers/maker-rebates):** `fee_equivalent = shares·feeRate·p·(1−p)`,
`rebate = your_fee_equiv / total_fee_equiv × pool`. Since pool ≈ 20% of total fees, this simplifies to
**~0.014·p·(1−p) per filled share, independent of other makers** (no dilution — rebate scales linearly
with the maker's own filled volume). Eligibility: any maker fill; **no min order/fill size, no max-spread** (those belong
to the *separate* Liquidity-Rewards/Qmin program, which crypto Up/Down is NOT in). Min payout $1, daily in pUSD.

## 3. std0's evolution (it bootstrapped from small)

| Phase | Dates | Block | Volume | Rebate/day |
|---|---|---|---|---|
| START | Mar21–Apr30 | tiny | ~1,000 sh/day | **~$2.8** (max $6) |
| SCALE-UP 1 | May 1–15 | ↑ | ~35k sh/day | ~$99 (max $341) |
| small again (incl **26-05**) | May 16–28 | **$4–35** | ~1,450 sh/day | **~$4** (max $7) |
| SCALE-UP 2 | May 29–Jun 3 | ↑↑ | ~22k sh/day | ~$61 (max $156) |
| MATURE | Jun 4–30 | **$2,500** | ~444k sh/day | **~$1,244** (max $2,182) |

**26-05 (the small-scale case), from its trade CSV:** $4–35 blocks, **0.5-share partial fills** (fine
granularity → tight control), sold Up med 5 / Down med 5 (balanced), peak imbalance 6–10 sh, ~$3.5 rebate/day.
Small scale WORKS for std0 — the key is neutrality (small residual), not capital.

## 4. Strategy mechanics (the design of a replica)

A WebSocket HFT market-maker loop, per window:
1. **Mint** a block of sets ~pre-open (adaptive: `min(target, pUSD−buffer)`; block sized to capital).
2. **Quote two-sided** resting maker orders near the CLOB mid (price at the mid so post-only rests; do NOT
   price off a spot model — that crosses the tight book → 0 fills). Fast re-quote (sub-second).
3. **Stay NEUTRAL** (section 5 — the crux).
4. **At settle**: MERGE the matched block → instant pUSD (no redeem lag), offload any residual, redeem the rest.
5. **Recycle** capital into the next window.

Constraints:
- **CLOB min order = 5 shares** to POST (partial fills can be smaller). This is the granularity floor: on a
  block of a few dozen sets, a 5–10 share step is a large fraction of inventory (versus 0.4% on std0's
  $2500 block).
- **Fees:** taker fee 0.07·p(1−p)·shares IS charged on-chain (both frames); **maker fee = 0**. Rebate returns
  ~20% of the taker fee. Per-trade cost ≈ half-spread (~0.5¢) + fee − rebate.
- **Latency:** /book round-trip measured ~22ms from Dublin vs ~72ms from Morocco. Matters for 5m fills, not the
  core edge. (Colocation is not permitted from France.)

## 5. THE key execution insight: stay neutral by OFFLOADING the residual

On a trending window one side runs to 1, the other to 0. The market hits the WINNER's ask (momentum) → a maker
sells the winner → it is left holding the **loser**. If it HOLDS it, the window settles directional = a
slip. Over many windows such slips swamp the tiny per-window rebate on a small block.

**std0 never holds the loser.** Two mechanisms, both needed:
1. **Cap the winner-selling (hold a MATCHED block).** The instant imbalance hits `max_inv`, STOP selling
   the winner (cancel that ask immediately — do NOT wait for the throttled re-quote, or a burst oversells to
   100%). Then hold Up≈Down = matched = neutral = redeems at $1.
2. **Offload the residual (the loser) reliably.** A resting maker ask on the loser must be pinned just above
   the best bid and chased down each re-quote (std0's "46% of sells <0.25"). But on a *decided* window there
   are no dip-buyers → the maker ask never fills → residual stuck. So the reliable offload is a **TAKER sell at
   the market bid** near settle: it always executes, and **taker fills are rebated** (std0's $8.9k taker rebate
   = it did exactly this). Selling the loser at 0.05 beats holding it to 0, and keeps the book neutral. The ONLY
   caveat: do it ONCE near settle at the market bid — never a repeated intra-window dump at a hard floor.

Net rule: **every window should end ≈ delta-neutral** (matched block redeemed + residual offloaded). Then
P&L = rebate (small but positive) + overround − tiny offload spread.

## 6. Design hazards and the invariants that guard them

| Hazard | Guard |
|---|---|
| User-feed events report the **taker's** fill size, not the maker order's | parse `order` events' `size_matched` delta; `guard.check_fill(size, clip)` (a fill > clip is impossible) |
| Settle-lag **phantom** mark (−$block before redeem credits) | measure on-chain; `guard.check_mark` (mark can't be < −block); settle-zone + persisted baseline |
| Repeated intra-window **dump at a floor price** | single offload near mid; `guard.check_sell_price` clamps sells far below mid |
| `mint_usd > pUSD` → silent abort | adaptive mint `min(target, pUSD−buf)`; `preflight` refuses to start on misconfig |
| clip 3 < CLOB min 5 | every order rejected → `preflight` asserts clip ≥ 5 |
| **keep-the-loser** slips | the §5 clamp + hard cap |
| a guard halt leaving the process inert | guard halt → `os._exit` → a supervisor restarts it fresh; reconcile HALTs only on a **persistent** desync (streak), tolerant of one in-flight clip |

**The harness (`pmlab/mm_guard.py`, stdlib):** `Guard` with `check_fill`, `reconcile_inv` (engine inv vs
on-chain, streak-based), `check_mark`, `check_sell_price`; `preflight(cfg, pUSD)` refuses to start on misconfig.
Principle: **on-chain truth is the ONLY P&L source** — an engine's own `mark`/`rebate` counters are suspect
(phantom marks and open-window marking inflate them).

## 7. Code architecture (reusable)

- `pmlab/hftmm.py` — the WS engine: `LiveBook` (CLOB /ws/market), `SpotFeed` (Binance), `UserFeed`
  (CLOB /ws/user, parse `order` `size_matched` deltas), `_mint`/`_reclaim` (merge+offload), `_sync_onchain`
  (the truth), `_targets`/`_gates`/`requote` (quoting), `on_fill` (hard cap). Owns a `Guard`.
- `pmlab/mm_guard.py` — invariants + preflight (§6).
- `run_hft.py` — the runner (preflight-gated).

## 8. Constants reference

- Rebate: **0.014·p(1−p) per filled share** (maker & ~taker). Max at p=0.5 (0.0035/sh). $1 min payout, daily ~00:45 UTC.
- Taker fee: 0.07·p(1−p)·shares (charged). Maker fee: 0.
- **CLOB min order: 5 shares.** Partial fills can be < 5.
- Markets: btc/eth/sol/xrp × 5m & 15m. Slug `{coin}-updown-{5m|15m}-{open_ts}`. btc-5m = std0's 64%.
- Settlement: the Chainlink oracle (`resolve_market`), +~44s after close.
