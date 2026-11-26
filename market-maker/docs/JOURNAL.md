# Rebate — Project journal & documentation

> Research on ultra-low-latency market making for Polymarket's "Bitcoin Up
> or Down" 5-minute binary markets. Question studied: can a **maximum of maker
> rebate** be harvested while staying **delta-neutral**, as a small participant,
> against the venue's best MM bots.

Last updated: 2026-07-03.

---

## 1. Objective

Study a strategy that **captures a maximum of maker rebate** on the
btc-updown-5m binaries **without taking directional risk** (delta-neutral),
**scalable with capital**. Reference model: the competitor bot **std0**
(wallet `0xdf7930…`), decoded from its public on-chain activity (see §4).

---

## 2. Technical architecture

### 2.1 Overview

```
Chainlink RTDS (S_t) ─┐
Binance WS (reference)─┼─→ fair-value engine (Phi(d2)) ─→ quotes ─→ CLOB V2
Gamma API (windows)   ─┘         │                                  (maker)
                          Avellaneda-Stoikov skew            user WS (fills)
                                 │                                    │
                          on-chain split/merge (CTF) ←── inventory ───┘
```

- **Fair value**: `fair = Phi(d2)`, `d2 = ln(S/K)/(σ√T)`, vol via double-EWMA.
  The drift `−σ²T/2` is deliberately ignored (T ≤ 300s, negligible).
- **Inventory skew** (Avellaneda-Stoikov): `center = fair − λ·I`, re-centers
  the quotes to de-risk the inventory.
- **Resolution**: via **Chainlink** (not Binance), `close ≥ open → Up`.
- **Window discovery**: deterministic slug `btc-updown-5m-{unix}` with
  `unix % 300 == 0` (generalized multi-series).

### 2.2 CLOB V2 (venue)

- 11-field EIP-712 order struct (salt, maker, signer, tokenId, makerAmount,
  takerAmount, side, signatureType, timestamp **ms**, metadata, builder).
- Domain `Polymarket CTF Exchange` v2, exchange `0xE111…996B`.
- **pUSD** collateral `0xC011…2DFB`.
- **Signature type 2 (POLY_GNOSIS_SAFE)**: `maker = funder` (Safe proxy),
  `signer = EOA`. Safe `execTransaction` with pre-validated owner signature.
- **L2 HMAC**: signs the path **WITHOUT** the query string (a trap, handled in the client).
- **Precision**: sizes max 2 decimals; sell-side USDC amount max 4 decimals.
- **No atomic cancel-replace**; maker is free, taker is costly.

### 2.3 Phase B — on-chain (CTF)

- `splitPosition` / `mergePositions` / `redeem` via `CtfCollateralAdapter`
  (`0xAdA1…FcE1f`). Selectors `72ce4275` / `9e7212ad` / `01b7037c`.
- **Split**: mint N pairs (Up∧Down) before the window → asks on both sides.
- **Merge**: converts N pairs back into pUSD (net-neutral, recycles collateral).
- **Redeem**: automatic/gasless (Polymarket batch-settles at expiry).
- EIP-1559, Safe execTransaction (v=1, r=owner, s=0).

### 2.4 Robustness

- Anti-double-fill kill switch: replay dedupe before registry lookup
  (`inv.Seen`), 60s tombstones, REST resolution (`GetOrder`), resync with
  `PlacedAt` fencing.
- WS reconnections (book/user/binance) with backoff.
- On-chain inventory seed after a mid-window restart.

---

## 3. Modularity — the 3 strategies

`STRATEGY` parameter:

| Mode | Behavior | Intended use |
|------|----------|--------------|
| `sellonly` | Split, then sell the pairs as asks, never bid | Small capital (std0's early method) |
| `neutral` | Bid Up + Bid Down (buys both sides) | Defensive maker, both sides |
| `momentum` | Keeps the ITM side, taker re-buy, ladder | Large capital (directional) |

Other key params: `HALF_SPREAD`, `MAX_POSITION` (cap), `SPLIT_PAIRS`,
`QUOTE_SIZE`, `LAMBDA`, `REQUOTE_INTERVAL_MS`, `FILL_COOLDOWN_MS`,
`MERGE_THRESHOLD`, `GUARD_EDGE`, `SERIES` (btc-updown-5m|15m).

---

## 4. Reverse-engineering std0

Decoded on-chain (data-api + OrderFilled events).

- **Trading edge ≈ 0**: t-stat < 2 on every series. Its profit is **not**
  directional trading.
- **67% maker / 33% taker** (verified via `OrderFilled` events, topic
  `0xd543…d8ee`, maker=topics[2] taker=topics[3]).
- **May 26, 2026 (its beginnings, ~$100 capital)**: trading **+$10.52/day**,
  rebates **$0** (the crypto rebate program was not active in May).
  Median split **100 pairs/window**.
- **Recent period**: rebates ~$2400/day on ~$8500 capital ≈ **28%/day**.

### std0's real mechanics (a late, crucial discovery)

std0 is **NOT delta-neutral**. Analysis of its May 26 CSV:

- **Sell asymmetry: 76%** (50% = neutral, 100% = one side only).
- Only **13%** of windows are symmetric.
- Average price of the over-sold side: **0.268** (= the OTM side).

→ std0 **sells the OTM side** (the one falling, at ~0.27) and **KEEPS the ITM
side** (the one rising, redeems at $1). It is a **slight directional bet**
(momentum), not strict delta-neutral. Profit: `0.27 (OTM sale) + 1.00 (ITM
redeem) − 1.00 (cost) = +0.27/pair`.

---

## 5. Rebate economics

- The crypto maker rebate program is **active in July 2026** (unlike May):
  std0's rebate credits appear on-chain as daily payouts (see
  `tools/rebates.py`, `tools/day_pnl.py`).
- Rebates are a **pool share** (pro-rata by qualified maker volume), not a
  fixed rate: they dilute as the pool grows.

**The economic model**: trading only has to be **breakeven**, the rebate
makes the profit. That assumes trading **doesn't lose**, and that is where
everything is decided (§6).

---

## 6. Structural findings on the strategy space

1. **The 5-share minimum clip is incompressible.** The directional residual
   is either 0 or ±5 shares and cannot be made finer. At split 30, ±5 is
   17% of inventory (large variance); at split 100+ it is 5%, which is how
   std0 dilutes it.

2. **Scaling the split on ONE market dilutes the return per pair**: the
   fills captured per window are limited by the flow crossing our price
   levels. std0's real scaling is **multi-market**, not a bigger split.

3. **Auto-merge is for neutral, NOT sell-only.** In sell-only it would eat
   the pairs we want to sell (settlement guard added, disabled in
   sell-only).

4. **Convergence, not only immediate adverse selection.** A resting ask
   posted early and filled late, once fair has converged upward, is sold
   **below current fair**. This is invisible in a short (10s) markout, since
   the convergence is slow, but it is the main cost of resting quotes on a
   binary close to expiry.

---

## 7. Anti-convergence guard (design)

**Diagnosis**: the anti-pick-off guard (`main.go`, step 6 of the loop) was
**explicitly disabled in sell-only** (`!SellOnly`). Yet it cancels exactly
the asks that have gone stale (edge vs current fair collapsed) before they
get dumped on the convergence.

**Fix**: enabled in sell-only (guard `!MomentumMode` only). In up-space,
both slots are sells; the existing checks cover both:
- `sideBid` (SELL Down) is stale when its up-price is too **high**,
- `sideAsk` (SELL Up) is stale when too **low**.

**Limit of the guard**: it reduces the exposure to convergence
but cannot remove it. A second cause remains that a guard cannot correct:
the **latency of the fair value** (Chainlink feed + network path). A quote
misplaced from the start is pulled only *after* a faster participant has
already traded against it. Fast requoting and a tighter guard shrink the
window of exposure without closing it.

## 8. Conclusions and open leads

### Structural proof: strict delta-neutral is impossible ON a binary

This is not only a capital question; it is the nature of the asset. On a
5-min binary, the fair **converges** to 0 or 1 at expiry (enormous theta).
Consequence:
- Selling the ITM side (heading to 1) gives it up below its final value → loss.
- Keeping the OTM side (heading to 0) → it is worth 0 → loss.
- The market makes us sell the side in DEMAND (ITM) and keep the unsold one
  (OTM): we give up the winner and keep the loser. A loss both ways.

**The ONLY way not to lose on the convergence = keep the ITM side** (the
winner) and sell only the OTM. That is precisely what std0 does (76%
asymmetric, sells the OTM at 0.27, keeps the ITM). But keeping the ITM = a
**directional bet**. Therefore:

> **Profitable rebate farming on a binary REQUIRES a directional bias
> (keeping the ITM). Strict delta-neutral loses there structurally.**
> Capital only reduces the magnitude of the relative loss; it does not
> remove it. The best solution analysed is std0's slight directional
> (`STRATEGY=momentum`).

### Latency and colocation

Three structural walls combine against a small participant:

1. **5-share minimum clip** → incompressible directional residual (±5).
2. **Latency** (Chainlink + network path) → without colocation the market
   maker is the "slow trader", adverse-selected by faster actors; the guard
   reduces but does not remove the fire-sale.
3. **Rebate small relative to the cost of that adverse selection.**

Strict delta-neutral MM becomes viable only from Polymarket's colocation
servers, where quotes can be re-pulled before being picked off. Colocation
is not permitted from France, so it was never implemented in this project.

### Levers analysed

- **More capital** (the 5-clip becomes negligible, the rebate scales: std0's
  trajectory).
- **Directional** (momentum, keeping the ITM side: std0's actual method).
  Not delta-neutral.
- **Multi-market** (eth/sol) to scale volume with spread-out capital
  (per-asset feed refactor).

### Coded lead: adaptive markout gate

The **markout** is the natural discriminant between windows where quotes
are picked off and windows where they are not; price movement alone
predicts nothing. **Adaptive gate** (`MARKOUT_GATE`, coded): EWMA of
realized markout; when it drops below −threshold (quotes are being picked
off), quoting is suspended until recovery.

What the project delivers: a complete and correct bot implementation
(on-chain split/merge, EIP-712 signatures, kill switch, paper simulator),
std0 decoded from public data, and the analysis of why delta-neutral market
making on a binary is latency-bound.

---

## 9. Quick reference

### Main files (26 .go)
- `main.go` — loop, quotes, syncSide, guard, fills, split/merge triggers
- `math_engine.go` — Phi(d2), ComputeQuotes, CompetitiveBid/Ask
- `config.go` — all params (env)
- `polymarket_*.go` — CLOB, book WS, user WS, market discovery
- `ctf.go` / `safe.go` / `evm.go` — on-chain split/merge, Safe, EIP-1559

### Analysis tools (`tools/`)
- `day_pnl.py` — std0 daily P&L (trading + rebates)
- `maker_taker.py` — on-chain maker/taker ratio
- `dashboard.sh` / `volfarm.sh` — paper config comparison
- `early_pnl.py`, `aligned.py`, `scaling.py` — analysis suite

### Build and paper run
```bash
go test -race -count=1 . && go build -o rebate .

# Paper simulation aligned on the window boundary
STRATEGY=sellonly HALF_SPREAD=0.01 MAX_POSITION=8 SPLIT_PAIRS=30 \
  PNL_CSV=x.csv ./rebate --paper
```

