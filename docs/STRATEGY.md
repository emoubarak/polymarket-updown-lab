# zlead — harvesting the longshot premium, but only when the lead is established

*Explanatory document, June 2026. Paper trading (plus an optional real-money pilot, disarmed by
default): everything is wired read-only to the real APIs.*

> **Lineage note.** This document (formerly `STRATEGIE-RUBEDO.md`) keeps references to the
> historical codenames of falsified strategies — see the **codename glossary** at the top of
> `research/FINDINGS.md`. `rubedo` was the **BTC-only** ancestor of the active strategy: the
> *bare* favorite-longshot harvest. It was **falsified** (the extreme favorite wins *exactly*
> at its price outside a trending regime — see §7 and `research/FINDINGS.md`) and **withdrawn
> from deployment**. What survives is its descendant, **`zlead`**: the same engine
> (`engine.Engine`, `pmlab/engine.py`), plus the **vol-normalized lead floor**, the only
> filter that generalizes out-of-sample, and generalized to multiple cryptos. That is what
> this document describes.

---

## 1. The market we exploit

Polymarket lists **"Up or Down"** markets on several cryptos (BTC, ETH, SOL, XRP, DOGE, BNB):
for each time window, will the underlying close above or below its opening price? Two
outcomes, `Up` and `Down`, each quoted between $0 and $1. If you're right, your share is worth
$1; otherwise, $0. A share's price = the implied probability the market assigns it. Resolution
via the **Chainlink oracle**.

Windows actually offered: **5 minutes** and **15 minutes**. *The 1h and 30m do not exist*
(verified live); the 4h exists but is dead for our angle (see §7).

We do **not** predict the underlying's direction. At this scale the market is efficient:
impossible to beat the coin flip on "up or down". We exploit something else.

---

## 2. The edge: favorite-longshot *conditioned on an established lead*

An empirical fact as old as horse racing: **the crowd overpays for longshots** (the unlikely
outcomes, the "lottery ticket") and **underpays favorites** (the near-certain outcomes).
Translated to these markets:

> With ~4–6.75 min left in a window, an **already extreme** favorite (price 0.85–0.95) **whose
> lead is statistically established** wins **more often than its price says**. The near-dead
> side opposite it is overpriced.

**This is not a directional prediction — it's a price calibration error.** That's why it
escapes the "the market is efficient on direction" verdict.

**But an extreme price is not enough.** The lesson the ancestors paid dearly for (`rubedo` &
co, §7): a 0.90 favorite *without* a real move behind it wins *exactly* 0.90 — that's
efficient, zero edge. The premium only appears when the favorite **carries a move that has
already happened** and *locks in* the window. Hence the filter that makes `zlead`:

> **Vol-normalized lead floor.** We only enter if the underlying's lead since the window open,
> *scaled by its expected noise* `σ·√τ`, exceeds a threshold `z ≥ 1`. In other words: a move
> of at least ~1 standard deviation — a favorite whose lead is **signal, not noise**.

It is this `z` that generalizes out-of-sample where the bare favorite fails, and that carries
over to the other cryptos (whereas the raw *bps* version only held on BTC).

---

## 3. The strategy: `zlead`

A single engine (`engine.Engine`, `pmlab/engine.py`), parameterized by a `Preset`
(`pmlab/presets.py`). The same entry rule (`pmlab/entry.EntryGate`) is **shared** between
paper and the real pilot — no drift possible between the two paths.

The rule, brutally simple:

1. **When**: within the entry window of **4–6.75 min remaining** (fraction of the window
   remaining between `enter_lo` and `enter_hi`). The lower bound was widened to maximize
   **cumulative daily PnL**, not per-trade EV: profit/day keeps rising as you go down to ~4 min
   remaining, then the later cohorts become EV-negative.
2. **What**: the favorite that is manifest *by price* (the side quoting within **0.85–0.95**),
   **filtered by the lead floor `z ≥ 1`**. The edge is in the PRICE + the lead, not in a
   prediction.
3. **How much**: a single buy, stake = % of capital capped at the book's depth (see §8).
4. **Then**: we **hold to settlement. No exit.** Exiting = a second round-trip that re-pays the
   spread + feeds informed flow (a lesson the previous versions paid dearly for).

### Execution realism (non-negotiable)

- Every decision re-fetches the real book with 1 s of simulated latency and fills against the
  real asks; **abandon if the best ask has run away by more than 2 cents** (the 2c "haircut").
- **Taker fees: real.** A first audit (2026-06-26) had concluded the fee was a "phantom" never
  charged; the on-chain re-verification of 2026-06-27 (new pUSD/relayer system) **proved the
  opposite**: the crypto taker fee `0.07 × price × (1 − price)` IS charged, on both window
  durations. Paper and backtest therefore model `FEE_RATE = 0.07` (maker fee = 0). Real taker
  cost ≈ half-spread (~0.5¢ in calm conditions) + fee (~0.5–1% of notional); slippage ~0 at
  the favorite's depth. Lesson documented in FINDINGS.

### The payoff is brutally asymmetric

Buying at 0.90: if you win, +0.11 per $; if you lose, **−1.00 per $** (the share is worth 0).
The break-even win rate = **the price itself**. A cluster of reversals hurts: one loss wipes
out ~8 wins. That is the core of the risk, and the reason for the tripwire (§6) and the
conservative sizing (§8).

---

## 4. The variants (one engine, composable modifiers)

`zlead` is a base; each variant is only a **delta of fields**, never a hand-rewritten strategy
(`presets.py`):

| Variant | Type | Difference |
|---|---|---|
| **zlead** | (base) | favorite 0.85–0.95, `z ≥ 1`, taker entry — the flagship |
| **zleadmk** | `mk` | **maker entry**: post a passive bid (zero fees), cross as taker late if unfilled. Execution twin of the flagship |
| **zleadx** | `x` | **strengthened lead floor `z ≥ 1.5`**: fewer bets, safer ones |
| **zleadn** | `n` | **narrowed band 0.85–0.90**: the premium concentrates there; 0.90+ favorites are arbitraged away (dead) |

The types **compose**: `zlead('n','mk')` = maker with a narrowed band. A new dimension = one
more entry in `TYPES`. The entry window can also be tuned per coin (`COIN_SLOTS`: bnb wants
~5 min instead of ~4, its book dies earlier).

All of this runs in paper on **6 cryptos × 2 frames** to let the live tape decide which
(variant, coin, frame) pair actually carries the edge — the *breadth* is the method, not a
detail.

---

## 5. How we validate (the method, which is everything)

Polymarket serves **for free and retroactively** (≥ 7 d) everything needed: the resolutions
(Gamma + oracle), the price trajectory (CLOB), **the actual executed tape** (data-api), the
Binance spot. So we can backtest without waiting days for live data.

The protocol, disciplined:

1. **Replicate the live decision exactly**: favorite price + `z` floor + 2c haircut, hold to
   settlement, one trade/window, `fee = 0`.
2. **In-sample / out-of-sample split**: calibrate on the first days, judge on days the strategy
   has never seen — *including the choppy days* that kill fake edges.
3. **Day-by-day regime filter**: an edge that flips sign from one day to the next is regime
   noise, not an edge.

**The engraved lesson** (from several falsified predecessors — `coagula`, then bare `rubedo`):

> "N/N days positive in-sample" is worth **nothing**. Only out-of-sample on fresh tape and live
> decide. Always report the realistic config, never the best cell.

---

## 6. The results — honestly

The **15m** (especially BTC and ETH) is the core **validated in real forward-testing**: net
positive after real costs (`fee = 0` + half-spread). The **5m** is backtest flattery — the
ground-truth tape shows the favorite has already converged by the time we could enter, and the
frame reprices too fast; it runs in paper as falsification, expected flat to negative.

*We do not reproduce a numeric EV table here: the magnitudes per (variant, coin, frame) move
with regime and sample. The live numbers are on the dashboard; the detailed falsification
verdict is in `research/FINDINGS.md`.*

**The tripwire (death condition)**: if, over ~150 trades, the realized win rate falls **to/below
the average entry price**, the edge does not exist → withdraw. (That is exactly how coagula,
then bare rubedo, died.)

---

## 7. What is dead (so we don't go back)

- **The *bare* favorite-longshot (the BTC-only `rubedo` family)**: `rubedo` (0.85–0.95 with no
  filter), `citrinitas` (+ volatility filter), `rubedowide` (+ 0.80 floor), `fixatio` (lead
  floor in *bps*), `coniunctio`, `lapis`, `aurum`. All **falsified**: without the `z` floor,
  the favorite wins *exactly* at its price as soon as you leave a trending regime.
  `citrinitas`'s volatility filter *seemed* to add edge in-sample but did not hold. `fixatio`'s
  *bps* floor did not transfer beyond BTC. Kept **constructible** in `presets._ARCHIVE` (to
  replay their backtests), **never deployed nor armable**.
- **5m**: favorite-longshot largely arbitraged away on the fast frame; backtest flattery
  (deployed as a live falsification).
- **4h**: no favorite edge. Over 4h the underlying has time to reverse → a 0.85 favorite wins
  ~0.85 (efficient). The 15m edge comes precisely from the short window *locking in* the lead.
- **1h / 30m**: do not exist as markets.
- **Directional** (predicting up/down with a model): a lookahead artifact — the "fresh" Binance
  spot sees the underlying ~56 s before the stale 1/min quote. Aged properly, the edge
  evaporates.
- **Delta-neutral market-making**: killed by adverse selection (passive quotes fill on the
  wrong side). The maker rebate covers only a fraction of the loss.

---

## 8. The economics: how much, really

Gain ≈ `EV/$ × stake × qualifying-windows/day`, and **the stake is capped by liquidity** —
that is the dominant constraint, not a detail.

### The actual sizing

Each bet = **10% of capital, capped at the coin's favorite-book depth**, never more than the
available cash. These caps are **measured live**, not guessed (`presets.COIN_BET_MAX`, the
quantity a taker sweep absorbs at +2c):

| Coin | btc | eth | sol | xrp | doge | bnb |
|---|---:|---:|---:|---:|---:|---:|
| Absorbable book / bet | ~\$1295 | ~\$351 | ~\$519 | ~\$227 | ~\$76 | ~\$9 |

The bnb book is **effectively dead** (~\$9). This is why compounding never runs away: past a
small capital, the clip caps out on the book and growth becomes linear again. Sizing beyond
these caps means becoming visible, informed flow → the market-makers widen (adverse selection
to size, **untested above these thresholds**).

### The honest order of magnitude

At micro-size on a $100 base: on the order of **a few hundred $/month if the edge holds**,
regime-dependent, capped by the books above. **Not an annuity.** Any projection beyond the
first month is *unknowable* (regime, arbitrage, adverse selection).

---

## 9. The risks (read this before dreaming)

1. **Regime (the dominant one).** EV is an average. The favorite carries the *trend*: when the
   underlying trends, it wins more than its price; when it chops, it wins *exactly* at its
   price (EV ≈ 0) or less. Entire weeks can be flat. **This is what killed the predecessors.**
   The `z` floor mitigates this risk (it requires a real lead) but does not eliminate it.
2. **1:8 asymmetry.** +0.11 per win, −1.00 per loss. The average hides losing afternoons. The
   trajectory is jagged, not a straight line.
3. **Adverse selection to size.** EV is measured on small fills. At the book cap, several times
   a day, you become informed flow → the market-makers widen.
4. **n still small** on most (variant, coin, frame) pairs, and **the edge can get arbitraged
   away** — something that prints attracts competition.

---

## 10. In one sentence

> There is a real edge, small and measurable, exploiting the fact that the crowd overpays for
> longshots **when the extreme favorite carries a statistically established lead** (`z ≥ 1`) on
> Polymarket's short crypto windows; with no real fees (the flagged `0.07` is never charged),
> it yields on the order of **a few hundred $/month on a $100 base at micro-size**, it is
> **regime-dependent and liquidity-capped**, and the entire art consists of **never** mistaking
> a pretty in-sample curve for an annuity.

*Edge, not break-even — but we don't manufacture fake edges. That is the whole discipline: two
entire lineages died to isolate this one filter that holds.*
