# Codename glossary

This research journal is dated: strategies appear under their historical codenames
(1st esoteric generation, then 2nd alchemical generation). All of these lineages were
**falsified and archived** (`pmlab/presets.py`, `_ARCHIVE`); the current strategy is the
**zlead** family (buying the extreme favorite conditioned on a vol-normalized price-lead
floor, z-score). Mapping table:

| Codename | Description | Status |
|---|---|---|
| `gurdjieff` | Directional model (bets the side favored by the diffusion model) | Falsified (lookahead artifact) |
| `iching` | Directional model (signal variant) | Falsified (lookahead artifact) |
| `kabbalah` | Buy the favorite confirmed by the model (`model_p_up − mid ≥ 0.10`) | Falsified (edge ≈0 + churn) |
| `tao` | Trend-following executed as maker ("maker overreaction") | Falsified (loses big out-of-regime) |
| `hermetic` | Delta-neutral market-making (passive quotes on both sides) | Falsified (adverse selection) |
| `coagula` | Buy the extreme 5m favorite (timing + vol filter) | Falsified (cherry-pick, live −1.6%) |
| `rubedo` | Bare extreme favorite, 15m, held to settlement | Archived → basis of zlead |
| `rubedowide` | Rubedo with a widened entry band (0.80+) | Falsified |
| `citrinitas` | Favorite + volatility filter | Falsified (beaten by the z-floor) |
| `albedo` | Late-entry favorite (higher frac) | Rejected OOS |
| `fixatio` | Favorite + lead floor in bps (not normalized) | Falsified (dies OOS) |
| `coniunctio` | Favorite + vol filter + lead floor combined | Archived (zlead precursor) |
| `aurum` | Cheapest favorite in the band | Falsified |
| `lapis` | Conviction sizing (size ∝ extremity × lead-z) | Archived (sizing not carried over) |
| `mk` suffix (`rubedomk`, `coniunctiomk`) | Same gate, maker entry (resting bid + taker fallback) | Mechanism kept (zlead's `mk` type) |

The current engine lives in `pmlab/engine.py`; the archived presets are prefixed `favorite_*`.

# Retroactive backtest — verdict on the edges (2026-06-14)

## Why
Verify strategies without waiting through 12h live runs. We discovered that Polymarket
serves **for free and retroactively** (≥ 7 days) everything needed:

- **Gamma** `/events?slug=btc-updown-5m-{ws}` → closed markets + `outcomePrices` = the **true
  resolution** (label).
- **CLOB** `/prices-history` → price trajectory (~1 pt/min).
- **data-api** `/trades` → the **real tape** (side/price/size/ts), ~2000 trades/window.
- **Binance** klines → spot + vol.

Tools: `dataset.py` (per-window cache, `--no-tape`/`--skip`/`--force`), `analyze.py`
(experiments A–F + equity tests). Sample: **6 days, 1494 5m windows** (06-09→06-14).

## The central result: most "edges" are artifacts

### 1. The real resolution is **Chainlink**, not Binance
Market description: *"resolution source … Chainlink BTC/USD … Up if end ≥ start"*
(tie → **Up**). The live engine settles on Binance `close > open` (tie → **Down**).
→ **3.5%** of windows diverge. A genuine engine defect to fix.

### 2. The model's directional edge = **temporal lookahead artifact**
Betting the side favored by the diffusion model *looked* profitable:

| threshold \|p_model−mid\| | EV/share (fresh spot) | spot delayed 30s | delayed 120s |
|---|---:|---:|---:|
| > 0.10 | **+0.164** | +0.011 | −0.050 |
| > 0.20 | **+0.231** | −0.002 | −0.069 |

Cause: the latest market price from `prices-history` is **median 56s stale** (sampled
~1/min). The "fresh" Binance spot therefore sees BTC ~56s ahead of the stale quote — the
model "predicts" a move that has already happened. Aged by 30s, the edge evaporates; by
120s, it becomes anti-predictive. **The market is efficient at this scale.** This kills the
directional premise of gurdjieff and iching.

### 3. The favorite/longshot bias = **regime-dependent momentum** (≈0 net)
"Buy the 0.55–0.88 favorite, hold" per day (fees + spread included):

| day | 06-09 | 06-10 | 06-11 | 06-12 | 06-13 | 06-14 |
|---|---:|---:|---:|---:|---:|---:|
| EV/share | −0.026 | −0.053 | +0.038 | −0.031 | −0.010 | +0.095 |

Negative 4 days out of 6. This is exposure to trend persistence: positive when BTC trends,
negative when it chops. Its "model-confirmed" version (which looked robust every day,
+0.16/share) was the **same lookahead artifact** — it collapses from 250 to 33 trades when
the spot is delayed.

### 4. tao (maker overreaction) = **momentum executed as maker, NET NEGATIVE out-of-regime**
tao is legitimate (it fills against the actual *future* tape and holds to settlement, no
exposure to the stale quote). With the full tape of the choppy day 06-10, per day
(margin 0.18): **06-13 +236, 06-14 +122, 06-10 −395** → **total −37** (and −93 to −114 at
the other margins). Its massive loss in choppy regimes erases everything. The initial +256
was just a sample restricted to trending days; the +35 live was regime luck. **Not an
edge — a momentum bet that loses big when BTC chops.**

## What is robust and real (non-artifact)
- **Market ~efficient** on 5m: 6-day calibration close to the diagonal, Brier 0.207
  (coin-flip floor 0.25).
- **Churn cost** (model-independent): taker exits (stop/TP) cost ~**+0.027/share**
  vs holding. The bleeding brains lose through **double taker fees + churn**, not through
  bad signals. → tao (maker, no churn) ≈ breakeven+; the taker churners bleed.
- **Chainlink oracle** (cf. §1).

## Consequences for the strategies
- **iching / gurdjieff**: directional premise with no tradeable alpha → do not expect
  profit from them. At best, stop bleeding.
- **kabbalah**: good idea (favorite) but (a) the favorite edge is ≈0 net, (b) churn destroys it.
  The only defensible fix = hold to settlement, never cross the book twice.
- **tao**: net **negative** once tested out-of-regime (loses big on choppy days). The +35 live
  was regime luck, not an annuity.
- **hermetic**: silent control — consistent.

### 5. Delta-neutral market-making = **killed by adverse selection**
Idea: post BUY Up @ mid−δ and BUY Down @ (1−mid)−δ; each *matched* share locks in 2δ at
settlement, no fees, no direction. Tested on the real tape (exp. G): **−1240 to −1701**
depending on δ. Reason: the **residual one-sided exposure** (7000–9000 shares, as large as
the matched volume) is **toxic** — in a fast market, symmetric passive quotes fill
*asymmetrically* on the side that informed flow is crashing (the side that will lose). The
locked-in spread does not cover the adverse-selection losses. The classic "picked off"
market-maker problem.

## Overall verdict
All **four families** of approach fail on the 5m: directional (efficient + lookahead),
momentum/favorite (regime-dependent, negative), one-sided maker (tao, negative
out-of-regime), two-sided maker (adverse selection). This is a competitive, efficient and
adversarial micro-market. No simple accessible edge.

## Follow-up — deeper hunt for the favorite edge, and 15m (06-14)

### The longshot bias exists… but is not cleanly tradeable
- **At the level of real trades** (exp. I, ~800k trades, no lookahead): 0.55-0.90
  favorites are *underpriced* (buying = +EV), longshots *overpriced*. Textbook longshot bias.
- **BUT it is a volume-weighting artifact**: trade volume explodes during directional
  surges (e.g. one 06-10 window: Up climbs 0.45→0.90 with hundreds of BUYs, and Up wins).
  The tape therefore oversamples momentum that completes.
- **Honest test (exp. J, 1 trade/window at a fixed time, real ask, unweighted)**: the edge
  evaporates — **marginal and not robust**. Favorite 0.55-0.72 at ws+150: +0.009/share
  (negative on some days); strong favorite 0.85-0.97 late: ≈0, mixed by day. The market has
  largely arbitraged away the longshot bias on these liquid BTC markets.

### 15m: same story
Model directional (exp. F on 15m): lag=0 looks +0.16/+0.18, but **aged 60s → −0.20/−0.27**.
Same staleness artifact, same verdict. No real directional alpha there either.

## UPDATE 2026-06-15 — a real edge found (2nd wave, `explore2.py` + `validate_fav.py`)

The verdict below (1st wave) remains correct for the directional/momentum/MM angles. But
a 2nd wave, on the **real tape** (1189 5m windows, executed price of the last trade — NOT
the stale `price_track` quote) isolated **one edge that survives real fees AND the
day-by-day regime filter**: the **favorite-longshot bias, conditioned on time and volatility**.

- At ~3 min before the end of a 5m window (`frac_elapsed≈0.40`), a favorite that is already
  extreme (price ≥ 0.78) is **undervalued**: realized win rate > price, with the gap growing
  with extremity (0.80→0.88; 0.85→0.93; monotone, cf. per-bucket calibration).
- **Timing is essential**: the gap dissipates toward 1.5 min remaining (`frac 0.70` ≈ +0.003,
  dead). This is why the 1st wave (late / unconditioned entry) missed it.
- **The vol filter is essential**: remove the top tercile of σ (vol_per_min > ~0.0006,
  backward-looking EWMA) — otherwise the storm windows (favorite flipping) kill it.
- Net of a 2-cent execution haircut + taker fees: **≈ +5%/$, ~21% of windows
  qualify (~47/day), positive 4 days out of 5**, n=245 over 5.2 days.

**Two methodology traps corrected** (never repeat): (1) the cached `tape` is sorted
**most-recent-first** (t descending); a naive ascending scan silently falls back on the
stale `price_track` quote → manufactures a fake *late*-entry edge (the spot-lag artifact,
again). (2) Verified freshness-invariant (identical from age≤300s to age≤8s), because these
books print ~11 trades/s → this is NOT a staleness artifact.

Deployed as a forward test as the 6th brain **`coagula`** (`pmlab/coagula.py`,
`state_coagula/`). Distinct from `kabbalah` (also favorite-based), which gates on
`model_p_up−mid ≥ 0.10` and almost never fires in the 0.78-0.88 zone where the edge lives;
`coagula` ignores the model (the edge is in the PRICE).

## FINAL VERDICT (1st wave — directional/momentum/MM angles)
After ~17 distinct experiments on 5m **and** 15m, on real data, without lookahead:
**no robustly profitable directional strategy emerges.** Every candidate is either (a) a
lookahead artifact (fresh spot vs the 1/min quote stale by 56s), or (b) volume-weighted /
regime-dependent momentum that nets ≈0 when traded honestly (once per window). The BTC
up/down 5-15 min market is **efficient and adversarial**; the only structural advantage
(maker fee = 0) is eaten by adverse selection. The only directional edge that would
remain — **speed** (reacting to Binance sub-second, before the book reprices) — is
**out of reach for a paper bot on public APIs** (1s simulated latency, 1/min data). *(The
favorite-longshot bias above is not directional: it exploits a price miscalibration, not a
direction prediction — which is why it escapes this verdict.)*

## Remaining leads (honest but uncertain / currently out of reach)
1. **Sub-second speed**: the real edge, but requires co-location/WebSocket, not this setup.
2. **Chainlink↔Binance divergence** near expiry — requires Chainlink data.
3. **1h** (untested) — even less noise; apply the same protocol.

## Limitations
- `prices-history` at 1/min is coarse (hence the spot-lag test, always to be run).
- 6 days (5m) / ~5 days (15m) remain a short sample; conclusions = strong direction, not
  absolute certainty.

## Limitations
- `prices-history` at 1/min is coarse (hence the spot-lag test, always to be run).
- Full tape only on 06-10/06-13/06-14; the rest in light mode (no tape).
- 6 days remain a short sample; conclusions = direction, not certainty.

## AUTOPSY 2026-06-17 — the favorite edge (coagula) was FALSE; 3rd research wave

### The favorite-longshot edge was a cherry-pick. FALSIFIED live + backtest.
Live forward test of `coagula` (97 trades, ~28h): win 84.5% vs break-even **85.2%**
(avg gain +3.51, avg loss −20.25, asymmetry **1:5.8**) → PnL **−16.09 (−1.6%)**. Decisive
signal: realized win **0.845 = price paid 0.842** → the favorite wins *exactly* at its
price = efficient market, **zero mispricing**. The claimed "+8.6 pts" (win 0.93 at price
0.84) is rejected at ~3.2σ.

**The leak (two stacked cherry-picks):**
1. The "+5%/$, 4 days out of 5" came from **a single cell**: `exp3 FOLLOW, 3 min left, thr 0.78,
   **ZERO haircut**`. The very same `validate_fav.py` harness in a realistic config (1.5 min
   left, +2c, vol-filtered) prints **−$10/day, days+ 17%** — negative. The writeup quoted the
   rosy cell, not the script's bottom line.
2. It is **regime momentum, not a mispricing.** Fresh-tape edge day by day: the only choppy
   in-sample day (06-10) is already NEGATIVE (win 82% < price); the 4 positive days were
   trending. The favorite wins more than its price **only when BTC trends** (it carries the
   trend to settlement). The live period 06-15→17 was choppy → win collapsed onto the price.
   coagula entered at the right timing (2.5–3.25 min left) and at a good price (0.842) →
   **not a deployment bug; the edge itself does not exist.**

**Lesson carved in stone: "days+ N/N in-sample" is worth NOTHING — only out-of-sample
fresh-tape / live decides. Always report the realistic config, never the best cell.**

### 3rd wave — new angles, all tested at the realistic bar (data extended to 06-17)
- **Tie→Up rule = NULL.** Over 1544 5m windows: base P(up_won)=0.4948 (≈coin flip). Exact
  Binance ties (close==open) **never** happen (0/1544); near-ties |Δ|<1 bps (n=169) resolve
  50/50. Outside a ±1 bps band, the outcome = sign of the Binance move (Δ≥1 bps → 97–100%
  Up). Chainlink↔Binance divergence = 3.7%, all concentrated in the near-tie band. No edge.
- **Outcome reversion = DEAD.** Betting against the previous outcome at the open:
  P(reverse)=0.5186 (n=2173, CI [0.498,0.540] — includes 0.50). Tradeable: **EV −0.019/$,
  days+ 3/8** (−0.088 on the live day 06-15). The fee at ≈0.50 (its maximum) eats the
  sub-1% skew.
- **Risk-free arbitrage (Up_ask+Down_ask < 1) = NONEXISTENT.** 4-minute live probe on the
  real books: 5m raw min 0.9900 (once in 147), **net after fees 1.0012**; 15m never < 1
  (net min 1.0137). The books are kept tight by HFT bots. No takeable arb.
- **1h does not exist** as a market (0/48 in the last hours; only 5m and 15m are offered) —
  the "1h untested" lead is obsolete.
- **15m favorite thr 0.85 frac 0.55**: the only surviving lead in-sample (+5.7 pts on fresh
  quotes <20s, n=166, days+ 6/6, +0.0388/$ net of 2c). BUT in-sample, on midpoint (not the
  ask), 6 days — exactly the coagula profile. Verdict suspended pending the out-of-sample
  fresh-tape test (in progress).

### Consolidated verdict
The BTC up/down 5m market is **efficient**, confirmed **five times** independently
(directional, favorite, tie-rule, reversion, MM) + **no arb**. The only real structural
edge remaining would be **sub-second speed**, out of reach at this tier (1s latency, public
data). The 15m is the last open angle, handled with the out-of-sample discipline that
coagula taught us.

### Review of what others trade (web, 06-17) + "the rest"
Researched what others are trading (github, 2026 articles). Two real edges exist but are
**out of reach by design**: (a) **latency arbitrage** (reacting to Binance/Coinbase before
the book reprices; WebSocket + co-located VPS; one bot cited $313→$414k/month, 98% win on
15m) — exactly the "speed" we had identified as inaccessible; (b) **cross-venue arbitrage**
(Polymarket↔Kalshi/Opinion, sum of both sides < $1) — needs a 2nd account. The only clean
retail angle others exploit = favorite/late entry = that is rubedo. **Fees: verified, our
`0.07·p·(1−p)` model is correct** (the headlines' "dynamic 3.15% fee at 50¢" = that same fee
as a % of the contract; in effect since Jan 2026, hence already in our June data). rubedo
is measured net of real fees.

The 3 remaining leads, all **closed**:
- **1h markets: do not exist.** Enumerating the Gamma series: only `btc-up-or-down-5m`
  (vol24h $20M), `-15m` ($3.6M) and `-4h` (recurrence "daily", $130k) exist. No 1h/30m/1m.
- **4h market (`btc-updown-4h-{ws}`, 4h ET-aligned windows, 6/day, low volume): no favorite
  edge.** 179 windows (30 days, real tape), favorite-FOLLOW +2c grid: **negative or flat
  everywhere** (best cell +0.005 surrounded by negatives = noise, not a robust neighborhood
  like rubedo); day stability ≈ coin flip (57%). Mechanism: over 4h BTC has room to reverse,
  so a 0.85 favorite wins ~0.85 (efficient) — the 15m edge comes precisely from the short
  window *locking in* the lead. + structurally data-starved (6/day) → unvalidatable anyway.
- **The maker rebate does not save the Δ-neutral MM.** Re-tested exp_g with the 20% rebate:
  only +170 to +190 (≈0.003/share), against −3900 to −4800 of adverse selection. The rebate
  covers ~4% of the loss. hermetic stays dead.

**Conclusion: nothing new to deploy; rubedo (15m) remains the only validated edge. The
external review confirms we are not missing any edge accessible at this tier.**

## The poll rate (10s) is not a PnL lever (2026-06-22)

Question: we poll every 10s; between two ticks we "lose" information and would miss
opportunities. Dedicated backtest (`research/backtest_poll.py`): replicates the exact live
policy — **first qualifying tick** in the [0.55–0.65 elapsed] band (= enter 0.35–0.45
remaining), one-shot, +2c haircut, hold to settle, real fees — and sweeps the poll step
{continuous, 1, 5, 10, 30, 60, 90s}.

Methodological guardrail: a sub-10s poll can only change the outcome **where** the price
moves between two ticks. **94% of 15m windows (and 81% of 5m ones) have NO tape** → price =
price_track at 1 pt/min → any poll < 1 min is *by definition* identical. The sweep is thus
restricted to windows **with tape** (224 in 15m, of which 186 OOS; 1189 in 5m but **all
IS** — 0 OOS, worthless for decisions by our own discipline). This is the subset that gives
the *best* chance of finding an effect; null here ⇒ null a fortiori.

Verdict (15m OOS slice, the only valid one):
- **Polling faster than 10s = zero gain.** continuous→10s shifts EV/$ by ±0.003–0.004, with
  an **inconsistent** sign (worse on 15m rubedo, better on 5m rubedo), win rate unchanged
  (~0.95). The extra entry price paid at 10s vs continuous ≈ **+0.004 to +0.007** — below the
  haircut (0.02) and below the edge (~0.04). The favorite is *sticky* over those 90s,
  exactly as expected.
- **"Missed" opportunities at 10s vs continuous**: 0–2% in 15m, ~7–9% in 5m (30s band, ~3
  ticks). But missing them **costs no EV** (EV at 10s ≥ continuous): these are not
  overrepresented winners.
- The *slow* polls (30/60s) sometimes show a higher EV/$ — a **late-entry artifact**
  (favorite more converged → win rate↑, price↑, n↓), confounded with a smaller n; that is
  the albedo axis (already rejected OOS), not a poll effect. On 5m the band is only 30s:
  poll ≥ 60s = a single sample (degenerate).
- **Adaptive**: the premise holds (slow poll out-of-band = free, rubedo no-op), but the
  payoff of a fast poll *inside* the band is null ⇒ adaptive is just an **API request
  saving**, not a PnL gain. The "near tau→0" half is off-topic: rubedo never acts there
  (5–7 min remaining).

**Conclusion: keep 10s. Neither generic fine polling nor "5m at 5s" is justified (the
former is flat OOS, the latter has no OOS data at all). No change deployed.**

## Entry price: timing = nothing; taker→maker = a LEAD (not OOS-validated) (2026-06-22)

Follow-up to the poll backtest. Two meanings of "optimizing the entry price".

**1. Timing (WHEN, the frac): nothing robust — 0.60 confirmed.** Sweep of frac {0.50…0.75}
over the FULL dataset (3792/6364 windows, true OOS). **5m: 0.60 is an isolated peak** —
rubedo-5m REAL only at 0.60 (+0.0128 OOS), DEAD everywhere else; coniunctio-5m also peaks
at 0.60 (+0.0695 OOS). **15m suggests 0.65** (rubedo OOS +0.0466 vs +0.0316)
but small-n, uncorroborated, and frac 0.75 gives win=1.000 (overfit). 15m and 5m
**disagree on direction** ⇒ noise, not a lever. The "later pays" from the poll backtest was
indeed the small-n artifact of the tape windows. → do not touch frac 0.60.

**2. Fill (HOW): taker→maker = a serious lead, unproven.** rubedo CROSSES the ask
(taker: pays the 0.02 haircut + taker fee ≈ 0.025/$). First cut: rest a bid at the
favorite's price (maker, zero fee), filled if the favorite prints ≤ that price before
window_end. The mechanism is consistent on 5m AND 15m AND both gates:
  - **fill 86–97%** (enough tick noise to be touched within 5–6 min);
  - **adverse selection ≈ nil**: Δwin(maker−taker) = −0.002…0.000 (winners fill too,
    because the favorite wobbles below its entry price at least once);
  - **gain ≈ +0.025/$** = haircut + fees recovered; dollars ~2–3× (5m rubedo maker +261
    vs taker +90).

CAVEATS (why this is a lead, not a deployment): (a) **no clean OOS** — the 15m tape is a
3-day slot with no loser (win=1.000, EV levels unusable), the 5m tape is 100% IS; only the
MECHANISM (fill, Δwin) is regime-robust. (b) **Optimistic** fill model: "touched = filled
at L", ignores the queue and the `MultiBroker`'s pessimistic rule (70% fill, trade-through
≥0.01). (c) Trades the CERTAINTY of entering for price: ~10% of windows unfilled. → Redo
under the broker's realistic fill model BEFORE any forward test; it is a surgical change
(resting bid instead of the cross), but the edge lies in BEING in position, so non-fill
risk matters.

## Maker entry IMPLEMENTED — rubedomk / coniunctiomk variants (2026-06-22)

Following the taker→maker lead, re-backtested under the broker's REAL fill model
(`research/backtest_maker_entry.py`: trade-through 0.01, fill 0.7, zero fee;
fallback time swept). The adverse selection turns out to sit **on the MISSED windows**
(a favorite that climbs straight up never crosses back through the bid → we miss it, and
those misses are ~100% winners). ⇒ **pure maker is net-negative** on coniunctio (gate at
win 0.97). The sound design = **maker + late taker fallback** (cross if unfilled when
< 15% of the window remains): NEVER misses a winner.

Per-$ EV (the fair metric, size-invariant) **beats taker in ALL configs and in the
15m-OOS slice**: 15m rubedo OOS +0.082 vs +0.075; coniunctio +0.078
vs +0.075; 5m rubedo +0.024 vs +0.013; 5m coniunctio +0.044 vs +0.041. Direction
robust everywhere; magnitude modest and regime-dependent (rubedo, with its loose gate,
benefits more than coniunctio, whose very established favorites barely wobble).

Implemented: `Rubedo.maker_entry`/`maker_fb_frac` + place→fill→
fallback→cancel state machine; `MultiBroker.cancel`; `rubedomk` family (rubedo gates) &
`coniunctiomk` (coniunctio gates). The resting maker order is oversized by 1/0.7 so that
a fill ≈ the target stake. Taker twins UNCHANGED (maker_entry=False, tested). 2 new
15m paper runners on the watchdog + dashboard.

CAVEAT (as always): the EV LEVEL is not OOS-validatable on the cache (only the tape
windows carry the flow the fill model needs; 15m tape = a no-loser regime, 5m tape = 100%
IS). The dashboard prior therefore does NOT pre-credit the gain: the paper forward test
must prove it. Worst case = taker. On promotion to real money, revisit the
oversizing (a real fill > 0.7 over-stakes).
