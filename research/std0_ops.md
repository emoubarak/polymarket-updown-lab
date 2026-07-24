# std0 market-maker — on-chain inventory-ops spec

Wallet (proxy): `0xdf7930e89a2c47560165331863c31deca0733dcd` ("std0", pseudonym *Traumatic-Heating*)
Signer EOA: `0x3Ec3577A6a22F9B4716C5AeFe0963a052BF703a6`

**Sample**: one dense busy window of **3 471 activity rows over 1.73 h** (`research/data/std0_activity.json`,
ts 1782592733–1782598969 = 2026-06-27 ~18:00 ET), cross-checked against a 30 h pagination scan.
Scripts: `std0_collect.py` (fetch) · `std0_analyze.py` (cadence/sizes) · `std0_tx.py` (RPC decode, batching/gas) ·
`std0_latency.py` (mint→sell).

---

## TL;DR — the operational pattern

std0 runs a classic **mint-set → two-sided maker → redeem-residual** loop on btc/eth/sol 5m+15m
up/down markets, and it **deliberately sidesteps any per-wallet relayer serialization**:

| Action | Path | Who submits | Gas to std0 | Serialized? |
|---|---|---|---|---|
| **SPLIT** (mint $X pUSD → X Up + X Down) | **Direct EOA tx** → `proxy()` on Factory `0xab45…` → `splitPosition` on Adapter `0xada1…` | **std0's own EOA** `0x3Ec3…` | **~0.107 POL ≈ $0.0076** | **No** — controls its own nonce, pipelines |
| **SELL** inventory | **Off-chain signed CLOB order**, settled by exchange operator to `0xe111…` | Polymarket operator `0x0077…` | **$0** (gasless) | No — REST API rate limit only |
| **REDEEM** winning side at settlement | **Relayer batch** (≈29 wallets/tx) → `0x84ba…` | Polymarket relayer fleet (rotating) | **~$0** (gasless meta-tx) | No — async fire-and-forget |
| **MERGE** | **NEVER USED** (0 over 30 h / 253 inventory ops) | — | — | — |

**The only thing std0 does as its own on-chain tx is the MINT, and it does it from its EOA — not
the relayer.** That is the whole trick.

---

## Q1 — Cadence & ratio

- **SPLIT : MERGE : REDEEM ≈ 0.95 : 0 : 1.** Over 30 h: 112 SPLIT / 0 MERGE / 141 REDEEM
  (truncated scan); over the clean 1.73 h window: **63 SPLIT, 0 MERGE, 66 REDEEM**.
- It mints a set and, at window settlement, redeems the residual winning side ≈1:1 with mints.
  **It never merges** unsold inventory back — it just lets each window settle and redeems.
- Rates (clean window): **SPLIT 0.61/min (36/h), REDEEM 0.64/min (38/h)**, all inventory ops
  **1.24/min (74/h)**. CLOB trade fills **32/min (1 929/h)**.
- **Inter-op gap (consecutive SPLIT/REDEEM, n=128): median 5 s, mean 48 s — bimodal.**
  - A tight cluster at **2–5 s** (55/128): ops fired back-to-back inside one "window-open" burst.
  - A second cluster at **60–120 s+** (42/128): the idle gap between 5 m/15 m window cycles.
  - **11 % are ≤1 s apart** (true back-to-back). It bursts, then waits for the next window.

## Q2 — Sizes ($ usdcSize)

| op | n | median | mean | p10 | p90 | max | total |
|---|---|---|---|---|---|---|---|
| SPLIT | 63 | **$1 000** | $1 611 | $1 000 | $2 500 | **$2 500** | $101.5 k |
| REDEEM | 63 | $1 038 | $1 612 | $998 | $2 588 | **$2 713** | $101.5 k |

- SPLIT sizes are **quantised**: 35 of 63 are $500–1 000, 21 are $2 000–2 500. Effectively a
  **$1 000 base clip, bumped to $2 000–2 500** on higher-conviction windows. Nothing below ~$500,
  nothing above $2 500.
- **Throughput in $: ≈$58.6 k/h minted ≈$58.6 k/h redeemed** (balanced loop).

## Q3 — Batching

- **No batching.** Each SPLIT tx carries `proxy(ProxyCall[])` with **exactly ONE** sub-call
  (`typecode=1`, to=Adapter `0xada1…`, selector `0x72ce4275`=splitPosition, 260-byte calldata).
  Verified across the sample + the data-api groups every SPLIT/REDEEM as **1 op per txHash**
  (max ops/tx = 1).
- The `proxy(ProxyCall[])` array **could** hold many calls, but std0 does not use it that way — it
  fires **one splitPosition per tx** and gets parallelism from **nonce pipelining** instead (Q5),
  not from in-tx batching.
- (The only multi-row txs in the stream are CLOB **match** txs that fill std0 against 2 makers at
  once — the exchange's batching, not std0's.)

## Q4 — Gas & submission path

- **SPLIT: paid by std0's EOA directly.** 63/63 splits `from = 0x3Ec3…` (the signer), `to = Factory`.
  gasUsed **354 k–428 k** (median ~384 k), gas price **~280 gwei** (high — it overpays for fast
  inclusion), = **~0.107 POL ≈ $0.0076 per split** at POL=$0.0706.
  → **Total gas ≈ $6.6/day** if 36 splits/h is sustained 24 h. Trivial vs $1 000+ clip size.
- **REDEEM: gasless to std0.** Each redeem rides a **rotating relayer** (`from` differs every time:
  `0xafa7…`, `0x7ce1…`, `0xe2da…`, …) calling `0x84ba…` (selector `0x765e827f`). gasUsed 1.8 M–3.4 M
  with **180–213 logs across ~29 distinct recipient wallets** in a single tx → it is a
  **multi-wallet batch redemption**; std0 is one of ~29 beneficiaries and pays ≈0.
- **SELL: gasless to std0.** Settled by the Polymarket operator `0x0077…` to the new pUSD exchange
  `0xe111…` (selector `0x3c2b4399`); std0 only signs orders off-chain.
- **Net: std0 only ever pays gas on mints**, and only in cheap POL.

## Q5 — Throughput / tempo

- **Inventory ops: max 5/min, p95 5/min, mean 1.2/min.** CLOB fills max **71/min**, mean 32/min
  (these are resting-order fills settled by the operator, gasless — std0 doesn't "spend" a slot on
  each).
- **Split pipelining (the key number):** splits are submitted **back-to-back with sequential nonces
  without waiting for confirmation**. Observed bursts of **up to 5 splits** at nonces N…N+4 landing
  in **consecutive Polygon blocks ~1–3 s apart** (e.g. nonce 25991→25994, blocks 89258703→708, 7 s).
  Max 1 split per block (same-sender ordering), so the **sustained ceiling ≈ 1 split / 2 s block
  ≈ 30/min** — but std0 never needs it; it does ~5 per window-open then idles. The binding tempo is
  the **window cadence** (a 5 m/15 m window opening per coin), not any infra limit.

## Q6 — Mint → sell latency

- **First matched SELL on the same condition: median ~100 s after the mint** (p10 84 s, p90 231 s,
  max 399 s); **0 %** of the minted set sells within the first 60 s.
- Interpretation: std0 **does NOT dump after minting**. It mints early in the window, **posts
  resting two-sided maker orders, and they fill gradually over the next 1–7 min** as takers arrive
  (the data-api SELL timestamp is the *fill* time, not the order-*post* time — the post is a near-
  instant gasless REST call). Mint and sell are **decoupled and asynchronous**.

---

## What this means for OUR setup (Builder Relayer serialization)

Our constraint: the Builder Relayer **serializes on-chain actions per wallet** ("wallet busy: active
action exists", ~5–10 s each). std0 shows that constraint only bites if you route **mints** through
the relayer — and **std0 doesn't**. Concrete recommendations, in priority order:

1. **Mint via direct EOA txs with self-managed nonces — bypass the relayer for splits.**
   Fund our signer EOA with POL, build `proxy([splitPosition(...)])` calldata, and send txs
   `eth_sendRawTransaction` ourselves, incrementing nonce **without waiting for the previous receipt**
   (pipeline). This is exactly std0's pattern and removes the 5–10 s/action wall. Expected throughput
   **~1 mint per 2 s block (~30/min) per wallet** — far above the ~5/window we actually need.
   - Overpay priority fee (~200–300 gwei like std0) so the mint lands in 1–2 blocks before the
     window price drifts. Cost is ~$0.008/mint — negligible.

2. **Don't batch multiple splits per tx** (std0 doesn't, and it's unnecessary). Nonce pipelining is
   simpler and already gives block-rate throughput. Keep `proxy()` = one `splitPosition` per tx so
   each mint can be retried/replaced independently.

3. **Sells are not a relayer problem at all.** Post resting two-sided maker orders to the **CLOB REST
   API** (off-chain EIP-712 signatures) — gasless, settled by the operator, limited only by API rate.
   The "wallet busy" lock does **not** apply to off-chain order placement. Build inventory first
   (mint), then place orders; don't try to atomically mint-and-sell.

4. **Redeems: fire-and-forget via Polymarket's redeem relayer.** They get batched (~29 wallets/tx),
   are gasless, and run async at settlement — no serialization pressure. Just submit the redeem request
   per settled condition; don't block the loop on it.

5. **Only fall back to multiple wallets if mint demand exceeds ~30/min** (it won't at the std0 scale:
   ~36 splits/h). A single self-submitting EOA already matches std0's full tempo. If we ever need
   genuinely concurrent same-instant mints across many coins, multiple EOAs sidestep the
   single-sender nonce ordering — but that's a scale we're nowhere near.

**Bottom line:** match std0 by (a) **self-submitting splitPosition from our own funded EOA with
pipelined nonces** (not the Builder Relayer), (b) **placing sells as off-chain CLOB maker orders**,
(c) **letting redeems batch async**. The relayer serialization becomes irrelevant because the only
latency-critical on-chain action — the mint — never touches the relayer.
