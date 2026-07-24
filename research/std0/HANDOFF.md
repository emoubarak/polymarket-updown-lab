# std0 Market-Maker Replication — Complete Strategy & Handoff

**Purpose.** Everything learned replicating the profitable Polymarket wallet **std0** (a maker-rebate
harvester on crypto Up/Down markets). Written to rebuild the project cleanly from scratch. Self-contained.
Dated 2026-07-01. Currency = pUSD (Polymarket USDC). All P&L must be measured **on-chain**, never engine marks.

---

## 0. TL;DR

- **The edge is a REBATE, not a price bet.** Polymarket pays makers a rebate = **0.014·p·(1−p) per filled
  maker share** (= 20% of the 0.07 taker fee, crypto). Taker fills are ALSO rebated (~20%). No minimum order
  size, no spread requirement, $1 minimum payout, credited **daily ~00:45 UTC** in pUSD.
- **std0 harvests it by staying delta-NEUTRAL**: mint complete sets, quote both sides near 0.5, churn, and
  **never end up holding one side directionally** — it offloads the residual and holds a MATCHED block to redeem.
- **The whole game at small scale = stay neutral (no directional residual) so the rebate is free profit.**
  std0 did this from a tiny start ($4–35 blocks, ~$3.5/day) and compounded to $2500 blocks / ~$1200–2000/day.
- **Our bleed (~−$50 of a $103 start) was EXECUTION, not capital**: we kept getting stuck holding the losing
  side (directional slips of −$3–5) because we didn't offload the residual reliably. Fixes below get median
  imbalance to 0%; the last open lever is **reliable residual offload (taker sell at the bid, which is rebated)**.
- **Wallet blocker is SOLVED**: the user's Polymarket account **0xBbe3… is a Gnosis Safe** (owner EOA 0xE8dc)
  that the CLOB accepts (sig_type 2) AND that self-submits mint/merge/redeem on-chain with **zero relayer quota**.

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
**~0.014·p·(1−p) per your filled share, independent of other makers** (no dilution — rebate scales linearly
with YOUR filled volume). Eligibility: any maker fill; **no min order/fill size, no max-spread** (those belong
to the *separate* Liquidity-Rewards/Qmin program, which crypto Up/Down is NOT in). Min payout $1, daily in pUSD.

## 3. std0's evolution (it bootstrapped from small)

| Phase | Dates | Block | Volume | Rebate/day |
|---|---|---|---|---|
| START | Mar21–Apr30 | tiny | ~1,000 sh/day | **~$2.8** (max $6) |
| SCALE-UP 1 | May 1–15 | ↑ | ~35k sh/day | ~$99 (max $341) |
| small again (incl **26-05**) | May 16–28 | **$4–35** | ~1,450 sh/day | **~$4** (max $7) |
| SCALE-UP 2 | May 29–Jun 3 | ↑↑ | ~22k sh/day | ~$61 (max $156) |
| MATURE | Jun 4–30 | **$2,500** | ~444k sh/day | **~$1,244** (max $2,182) |

**26-05 (the small-scale proof), from its trade CSV:** $4–35 blocks, **0.5-share partial fills** (fine
granularity → tight control), sold Up med 5 / Down med 5 (balanced), peak imbalance 6–10 sh, ~$3.5 rebate/day.
Small scale WORKS — the key is neutrality (small residual), not capital.

## 4. The wallet (the solved blocker)

Earlier we spent days blocked because the Polymarket CLOB only accepts the maker address it *registered* at
onboarding. New accounts get a relayer-only "deposit wallet" (sig3, quota-limited, can't self-submit on-chain).
**RESOLVED:** the user's account **0xBbe3…7016 is a Gnosis Safe** (owner EOA **0xE8dc…**). Proven:
- CLOB **accepts sig_type 2** with `POLY_FUNDER=0xBbe3` (real $1 maker order rested).
- Safe **self-submits** splitPosition / mergePositions / redeem via `Safe.execTransaction` = **zero relayer quota**
  (proven $2 mint→merge round-trip). Tool: `~/mint/safeops.js` (`bal`, `baltoks`, `mint`, `merge`, `redeem`).

So: mint/merge/redeem are free & instant (self-submit), and the CLOB takes our maker+taker orders (sig2).

## 5. The strategy mechanics (what to build)

A WebSocket HFT market-maker loop, per window:
1. **Mint** a block of sets ~pre-open (adaptive: `min(target, pUSD−buffer)`; block sized to capital).
2. **Quote two-sided** resting maker orders near the CLOB mid (price at the mid so post-only rests; do NOT
   price off a spot model — that crosses the tight book → 0 fills). Fast re-quote (sub-second).
3. **Stay NEUTRAL** (section 6 — the crux).
4. **At settle**: MERGE the matched block → instant pUSD (no redeem lag), offload any residual, redeem the rest.
5. **Recycle** capital into the next window.

Constraints:
- **CLOB min order = 5 shares** to POST (partial fills can be smaller). This is the granularity floor — see §6/§9.
- **Fees:** taker fee 0.07·p(1−p)·shares IS charged on-chain (both frames); **maker fee = 0**. Rebate returns
  ~20% of the taker fee. Real per-trade cost ≈ half-spread (~0.5¢) + fee − rebate.
- **Latency:** co-locate (AWS Dublin /book ~22ms vs ~72ms Morocco). Matters for 5m fills, not the core edge.

## 6. THE key execution insight — stay neutral by OFFLOADING the residual

**This is the whole ballgame and where we bled.** On a trending window one side runs to 1, the other to 0.
The market hits your WINNER's ask (momentum) → you sell the winner → you're left holding the **loser**. If you
HOLD it, it settles directional = a −$3–5 slip. Over many windows the slips swamp the tiny rebate.

**std0 never holds the loser.** Two mechanisms, both needed:
1. **Cap the winner-selling (hold a MATCHED block).** The instant your imbalance hits `max_inv`, STOP selling
   the winner (cancel that ask immediately — do NOT wait for the throttled re-quote, or a burst oversells you
   to 100%). Then you hold Up≈Down = matched = neutral = redeems at $1.
2. **Offload the residual (the loser) reliably.** A resting maker ask on the loser must be pinned just above
   the best bid and chased down each re-quote (std0's "46% of sells <0.25"). But on a *decided* window there
   are no dip-buyers → the maker ask never fills → residual stuck. So the reliable offload is a **TAKER sell at
   the market bid** near settle: it always executes, and **taker fills are rebated** (std0's $8.9k taker rebate
   = it did exactly this). Selling the loser at 0.05 beats holding it to 0, and keeps you neutral. The ONLY
   caveat: do it ONCE near settle at the market bid — never a repeated intra-window dump at a hard floor (that
   dumped −$26 on a swinging mid once).

Net rule: **you should end every window ≈ delta-neutral** (matched block redeemed + residual offloaded). Then
P&L = rebate (small but positive) + overround − tiny offload spread ≈ **positive, at any scale**.

## 7. What went wrong (all found + fixed) + the safety harness

Bugs that cost real money/time, each now guarded:
| Bug | Symptom | Fix / guard |
|---|---|---|
| UserFeed parsed the **taker's** fill size (114) for our 5-share order | inventory desynced → oversold → −60% | parse `order` events' `size_matched` delta; `guard.check_fill(size, clip)` (a fill > clip is impossible) |
| Settle-lag **phantom** mark (−$block before redeem credits) | false KILLs | measure on-chain; `guard.check_mark` (mark can't be < −block); settle-zone + persisted baseline |
| **cap-dump** at floor 0.02, repeated intra-window | −$26 in 30min | removed; single offload near mid; `guard.check_sell_price` clamps sells ≫ below mid |
| `mint_usd > pUSD` → silent abort | "no orders" | adaptive mint `min(target, pUSD−buf)`; `preflight` refuses to arm on misconfig |
| clip 3 < CLOB min 5 | every order rejected | `preflight` asserts clip ≥ 5 |
| **keep-the-loser** slips | directional ±$5–10 swings | the §6 clamp + hard cap (median imbalance 83-100% → 0%); reliable taker offload = the open lever |
| guard halt left process inert | "no bets for 20min" | guard halt → `os._exit` → watchdog restarts fresh; reconcile HALTs only on a **persistent** desync (streak), tolerant of one in-flight clip |

**The harness (`pmlab/mm_guard.py`, stdlib):** `Guard` with `check_fill`, `reconcile_inv` (engine inv vs
on-chain, streak-based), `check_mark`, `check_sell_price`; `preflight(cfg, pUSD)` refuses to arm on misconfig.
**On-chain truth (`research/std0/pnl_tracker.py`)** is the ONLY P&L source — the engine `mark`/`rebate` counters
are suspect (phantomed + open-window marking inflates). **Dry gate (`deploy/mm_dry_gate.sh`)** runs the engine
DRY vs the live book and prints GO/NO-GO before any real money. **Watchdog** + 1G swap keep the box up.

## 8. The workflow (never break this — it cost $62 to learn)

**Never change code/config and arm real money in the same step.** For ANY change:
`edit → py_compile → dry-gate (GO/NO-GO) → preflight (refuses misconfig) → smallest mint first → measure
ON-CHAIN over enough windows → scale only after net≥0`. **One variable per test.** Report REALIZED on-chain
P&L (settled pUSD + rebate credit), never the engine mark, never open-window marking, never a few-hour sample
(that's noise). A "proven" verdict = neutrality holds + rebate credited + net≥0 over ~100+ windows.

## 9. Current state & the one open question

- **Neutrality is mostly fixed** (median imbalance 83-100% → 0% via the clamp + hard cap). No more wild ±$25 swings.
- **Open gap:** a residual **tail** — ~1 window in 4 still slips (imb 33-100% → −$3–5) when the market decides
  and no dip-buyer lifts the loser's resting maker ask. At $15 blocks with the 5-share CLOB-min clip, even a
  capped residual is a big % → the slip can exceed the per-window rebate (~$0.08) → net marginally negative on
  small samples. **The fix (not yet implemented): reliable TAKER offload of the residual at the market bid near
  settle** (always fills, rebated) — this is what std0 does. That should make small-scale net ≥ 0.
- **Funds:** ~$50 on the Safe 0xBbe3 (pUSD + a few settling blocks). Down ~−$50 from a $103 start (the R&D cost).
- **Verdict:** the strategy is SOUND and std0 proves it works small; our remaining work is purely the reliable
  residual offload + then measuring net≥0 over ~100 windows and scaling the block as the rebate compounds.

## 10. Code architecture (current repo, reusable)

- `pmlab/hftmm.py` — the WS engine: `LiveBook` (CLOB /ws/market), `SpotFeed` (Binance), `UserFeed`
  (CLOB /ws/user, parse `order` `size_matched` deltas), `_mint`/`_reclaim` (merge+offload), `_sync_onchain`
  (the truth), `_targets`/`_gates`/`requote` (quoting), `on_fill` (hard cap). Owns a `Guard`.
- `pmlab/mm_guard.py` — invariants + preflight (§7).
- `run_hft.py` — live runner (preflight-gated).
- `~/mint/safeops.js` — Safe self-submit ops (bal/baltoks/mint/merge/redeem), zero quota.
- `research/std0/pnl_tracker.py` — per-window ON-CHAIN P&L (the only truth). `expected_rebate.py` — rebate estimate.
- `deploy/mm_dry_gate.sh`, `deploy/launch_hft_measure.sh`, `deploy/hft_measure_watchdog.sh`.
- `research/std0/MM_WORKFLOW.md` — the process doc (§8 expanded).

For a clean rebuild you can keep `mm_guard.py`, `pnl_tracker.py`, `safeops.js` verbatim; re-implement the
engine around §5/§6 with the harness wired in from line 1.

## 11. Infra

- **AWS Dublin** `ubuntu@<AWS_IP>`, key `~/<ssh-key>.pem`. `~/pmlab/`, `.venv-live`
  (py3.14, `py-clob-client-v2`), `~/.poly_env_bbe3` (Safe key + `POLY_SIG_TYPE=2` + `POLY_FUNDER=0xBbe3`),
  `~/mint/` (node SDK + safeops.js). Low RAM → +1G swap added.
- **Wallet:** Safe 0xBbe3 (maker, CLOB sig2, self-submit), owner EOA 0xE8dc. ~$50 pUSD.
- The **zlead armed pilots + dashboard** also run here (webdash --pilot-api) — a SEPARATE, proven real-money
  system; do not conflate with the MM.

## 12. Constants reference

- Rebate: **0.014·p(1−p) per filled share** (maker & ~taker). Max at p=0.5 (0.0035/sh). $1 min payout, daily ~00:45 UTC.
- Taker fee: 0.07·p(1−p)·shares (charged). Maker fee: 0.
- **CLOB min order: 5 shares.** Partial fills can be < 5.
- Markets: btc/eth/sol/xrp × 5m & 15m. Slug `{coin}-updown-{5m|15m}-{open_ts}`. btc-5m = std0's 64%.
- Settlement: real Chainlink oracle (`resolve_market`), +~44s after close.

## 13. Next steps for the fresh project

1. Rebuild the engine (§5) with the harness (§7) wired from the start; keep `mm_guard`/`pnl_tracker`/`safeops.js`.
2. Implement the **reliable residual offload** (§6.2): near settle, TAKER-sell any unmatched residual at the
   market bid (rebated) so every window ends neutral.
3. Validate: dry-gate GO → preflight PASS → smallest block on the Safe → measure ON-CHAIN neutrality + net over
   ~100 windows + confirm the daily rebate credit. One variable per test.
4. Only once net ≥ 0 at small scale: scale the block as pUSD compounds (std0's path), add 15m + more coins.
5. Never trust the engine mark; never validate in production; report only realized on-chain numbers over enough
   windows to beat noise.
```
