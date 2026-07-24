# Rebate — Project journal & documentation

> Ultra-low-latency market-making bot for Polymarket's "Bitcoin Up or Down"
> 5-minute binary markets. Goal: farm a **maximum of maker rebate** while
> staying **delta-neutral**, on small capital, competitively against
> Polymarket's best MM bots.

Last updated: 2026-07-03.

---

## 1. Objective

Build a strategy that **captures a maximum of maker rebate** on the
btc-updown-5m binaries **without taking directional risk** (delta-neutral),
**scalable with capital**. Reference model: the competitor bot **std0**
(wallet `0xdf7930…`), profitable and decoded (see §4).

The bot runs on an AWS eu-west-1 VPS. **No local Go toolchain**: all
build/test/deploy goes `rsync` → VPS → `go build`.

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
  `unix % 300 == 0` (generalized multi-series, see §7).

### 2.2 CLOB V2 (venue)

- 11-field EIP-712 order struct (salt, maker, signer, tokenId, makerAmount,
  takerAmount, side, signatureType, timestamp **ms**, metadata, builder).
- Domain `Polymarket CTF Exchange` v2, exchange `0xE111…996B`.
- **pUSD** collateral `0xC011…2DFB`.
- **Signature type 2 (POLY_GNOSIS_SAFE)**: `maker = funder` (Safe proxy),
  `signer = EOA`. Safe `execTransaction` with pre-validated owner signature.
- **L2 HMAC**: signs the path **WITHOUT** the query string (trap fixed, cf. 401).
- **Precision**: sizes max 2 decimals; sell-side USDC amount max 4 decimals.
- **No atomic cancel-replace**; maker is free, taker is costly.

### 2.3 Phase B — on-chain (CTF)

- `splitPosition` / `mergePositions` / `redeem` via `CtfCollateralAdapter`
  (`0xAdA1…FcE1f`). Selectors `72ce4275` / `9e7212ad` / `01b7037c`.
- **Split**: mint N pairs (Up∧Down) before the window → asks on both sides.
- **Merge**: converts N pairs back into pUSD (net-neutral, recycles collateral).
- **Redeem**: automatic/gasless (Polymarket batch-settles at expiry).
- EIP-1559, Safe execTransaction (v=1, r=owner, s=0). Live split test **PASSED**.

### 2.4 Robustness (resolved incidents)

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
| `neutral` | Bid Up + Bid Down (buys both sides) | Tested — **loses** (see §6) |
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

## 5. Rebate economics (measured on our wallet)

- **Actual rebates received**: $22.70 over 48h → **0.56% of maker volume**.
- Rebate per share sold (~$0.5): **+0.28 cents**.
- The crypto rebate program is **active in July 2026** (unlike May).
- Ratio ~30% of capital/day in continuous regime — **consistent with std0**.

**The economic model**: trading only has to be **breakeven**, the rebate
makes the profit. But that assumes trading **doesn't lose** — and that is
where everything is decided (§6).

---

## 6. Timeline of strategies tested & results

| Approach | Live result | Cause |
|----------|-------------|-------|
| **Hold** (classic MM bid+ask) | −$6.64 | Adverse selection on the bids |
| **Neutral** (buying both sides) | loses, markout **−5** | **Buying = adverse-selected** |
| **Sell-only spread 0.045** | dumps below fair, −$45 (continuous) | Too few fills → residual |
| **Sell-only spread 0.01, cap 8** | +$4/40min THEN **−$27** | Initial luck; pure spread **−1.52/window** |
| **Sell-only + guard** (current) | **+0.23/window** (2 windows) | Anti-convergence guard — under test |

### Key findings

1. **Buying is toxic, selling is not.** Neutral (buying): markout −5.
   Sell-only (selling): markout +0.4 to −0.11. Bids get picked off on these
   markets; asks much less so.

2. **The 5-share minimum clip is incompressible.** The directional residual
   is either 0 or ±5 — it cannot be made finer. At split 30, ±5 = 17% of
   inventory (huge variance); std0 at split 100+ diluted it to 5%.

3. **Scaling the split on ONE market dilutes the return** (normalized return
   +2.6/100p at split 40 vs +0.49 at split 400): we only capture ~8-9 fills/
   window (limited by the flow crossing our price levels). std0's real
   scaling = **multi-market**, not inflating the split.

4. **Auto-merge is for neutral, NOT sell-only.** In sell-only it eats the
   pairs we want to sell (bug fixed: settlement guard + disabled in
   sell-only).

5. **The fire-sale comes from CONVERGENCE, not immediate adverse selection.**
   Markout math: 10s adverse selection = −0.11 cents/share (small, covered
   by the +0.28 rebate). The real loss (−1.52/window at net=0) comes from a
   resting ask posted early (fair 0.51) filled late once fair has converged
   (0.70) → sold **below current fair**. Invisible in the 10s markout (slow
   convergence over 50-250s).

### Mistakes made (transparency)

- Declared a "working config" on 7 windows that were **luck** (winning
  residuals); reality over 47 windows: trading −0.64/window.
- **Monitoring turned off and never restarted** → the service bled −$27 over
  3h with no alert. A serious fault.
- Over-optimism on the rebate estimate (corrected: ~$0.13/window, not $0.6).

---

## 7. The current solution: anti-convergence guard

**Diagnosis**: the anti-pick-off guard (`main.go`, step 6 of the loop) was
**explicitly disabled in sell-only** (`!SellOnly`). Yet it cancels exactly
the asks that have gone stale (edge vs current fair collapsed) before they
get dumped on the convergence.

**Fix**: enabled in sell-only (guard `!MomentumMode` only). In up-space,
both slots are sells; the existing checks cover both:
- `sideBid` (SELL Down) is stale when its up-price is too **high**,
- `sideAsk` (SELL Up) is stale when too **low**.

**Final result (micro test, split 10, cap 5, 5 windows at net=0)**:
- pure spread: **−0.78/window** (+0.46 / −1.40 / −0.40 / −1.80) vs −1.52
  without the guard.
- The guard **reduces** the fire-sale (~2×) but does **NOT** make it positive.

**Conclusion: the guard fails.** Convergence was ONE cause (the guard
partially fixes it), but a 2nd, uncorrectable one remains: the **latency of
our fair** (Chainlink + VPS). Our asks are misplaced from the start; the
guard pulls *after* a faster trader has already picked us off. At our
latency, we are the "slow trader" — structurally adverse-selected. Markout
is sometimes positive, sometimes −1.85: the loss is not just slow
convergence.

**Last attempt — maximum anti-fire-sale combination** (aggressive 0.015
guard + 100ms requote + 0.02 spread). Pure spread over 4 windows at net≈0:
**−0.51/window** (+0.40 / +0.20 / −0.40 / −2.25). Better than the guard
alone (−0.78) but **still negative**. Fast requoting and the aggressive
guard reduce the fire-sale but do not eliminate it. Verdict confirmed: **no
delta-neutral config reaches a net-positive pure spread at our
latency/capital.**

---

## 8. Current status & next steps

**Status (2026-07-03 ~12:00 UTC)**:
- Capital: ~$65-75 equity (day started at ~$122, **−$50** in tests/mistakes).
- All live tests stopped. No active process.

**FINAL VERDICT (strict delta-neutral does not work at our scale)**:

After exhausting the approaches (neutral, sell-only, 4 spreads, several
caps, auto-merge, anti-convergence guard), in paper AND live over ~100
cumulative windows: **there is no net-positive delta-neutral config at ~$70
on the btc-5m binaries.** Three structural walls combine:

1. **5-share minimum clip** → incompressible directional residual (±5),
   17% of inventory at split 30. std0 diluted it to 5% via split 100+.
2. **Latency** (Chainlink + VPS) → we are the "slow trader",
   adverse-selected by faster actors. The guard reduces but does not remove
   the fire-sale.
3. **Rebate too small** (+0.13/window) to compensate the fire-sale (−0.5 to
   −0.8/window).

**The only real levers**:
- **More capital** (the 5-clip becomes negligible, the rebate scales —
  std0's trajectory). This is an injection DECISION, not a test to run.
- **Directional** (momentum, keeping the ITM side — std0's actual method).
  It is NOT delta-neutral, but it is what is proven profitable.
- **Multi-market** (eth/sol) to scale volume with spread-out capital
  (per-asset feed refactor).

What remains of value: a complete and correct bot (on-chain, signatures,
kill switch), std0 fully decoded, and the **exact understanding** of why
delta-neutral fails at small capital. A real result, even a negative one.

### Remaining coded lead (untested): adaptive markout gate

Analysis of the last test: the **markout** is the only discriminant between
winning windows (+0.21/share) and losing ones (−0.44/share); price movement
predicts nothing. → **adaptive gate** (`MARKOUT_GATE`, coded): EWMA of
realized markout; when it drops below −threshold (we are getting picked
off), quoting is suspended until recovery. **TESTED (MARKOUT_GATE=0.3,
micro)**: FAILURE. rebateEq 0.045/0.12 (volume near ZERO — the gate
suspends too much), pnl −0.97/−1.55. The gate avoids the pick-off by
**killing the volume** (hence the rebate, the whole objective) and still
loses. Unavoidable trade-off: tight gate = no volume, loose gate = no effect.

### DEFINITIVE VERDICT (8 delta-neutral configs tested, all fail)

neutral, sell-only ×4 spreads, guard alone, guard+requote, markout gate.
Over ~110 live+paper windows: **no delta-neutral config is net-positive at
~$60-70 of capital on btc-5m.** Irreducible structural cause = latency
(slow trader getting picked off) + 5-clip (incompressible ±5 residual) +
rebate too small (+0.13/window) to cover the fire-sale. Cost of the
exploration: ~$60 over the day ($122→$61), part of it due to mistakes
(monitoring off for 3h). The only real levers remain: CAPITAL (5-clip
negligible), DIRECTIONAL (momentum, std0's actual method), MULTI-MARKET.
None of them is "delta-neutral at $60".
**Recommendation: stop the delta-neutral tests.** (Qualification: this
verdict is latency-bound — see the note at the end of the structural proof
below; colocation would change it, but is not permitted from France.)

### Structural proof: strict delta-neutral is impossible ON a binary

This is not only a capital question — it is the nature of the asset. On a
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
> (keeping the ITM). Strict delta-neutral loses there structurally, at ANY
> capital.** Capital only reduces the magnitude of the relative loss; it
> does not remove it. The "best delta-neutral solution" does not exist on
> this asset; the best solution, period, is std0's slight directional
> (`STRATEGY=momentum`).

**Qualification (added later)**: this verdict holds at OUR latency. Strict
delta-neutral MM does become viable from Polymarket's colocation servers —
the latency needed to re-quote before being picked off — but colocation is
not permitted from France, so it could not be implemented in this project.

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
- `netpnl.py` — net P&L (trading + rebates) over N hours
- `dashboard.sh` / `volfarm.sh` — paper config comparison
- `early_pnl.py`, `aligned.py`, `scaling.py` — analysis suite

### VPS commands
```bash
# Build (on the VPS, TMPDIR to avoid the tmpfs quota)
export TMPDIR=$HOME/gotmp GOTMPDIR=$HOME/gotmp
go test -race -count=1 . && go build -o rebate .

# Launch a test aligned on the window boundary
STRATEGY=sellonly HALF_SPREAD=0.01 MAX_POSITION=8 SPLIT_PAIRS=30 \
  PNL_CSV=x.csv REBATE_LIVE=yes ./rebate --live

# Reliably stop a live process (the for loop fails — kill by PID)
sudo kill -9 <PID>

# Actual rebate + volume (on-chain)
python3 tools/netpnl.py 24
```

### Reference config (delta-neutral, to be confirmed)
```
STRATEGY=sellonly HALF_SPREAD=0.01 MAX_POSITION=8 (≥ QUOTE_SIZE!)
SPLIT_PAIRS=30 QUOTE_SIZE=5 GUARD_EDGE=0.005  (no MERGE in sell-only)
```
