# Polymarket Up/Down Lab

**Case study: hunting — honestly — for a trading edge on Polymarket's crypto prediction markets.**

I'm a software developer with **no background in finance**. This project started from a common-sense question: *are Polymarket's "Bitcoin Up or Down" markets (binary bets on 5- and 15-minute windows) actually efficient, or can a methodical developer find a measurable advantage there?*

The short answer, learned the hard way: **the market is remarkably efficient, and almost every "edge" you think you've found is a measurement artifact**. This repo documents the whole journey — the hypotheses, the backtests, the falsifications, the real dollars lost and won, and two complete engines built along the way (a Python paper-trading framework and a high-frequency market maker in Go).

> ⚠️ Nothing here is investment advice. The real-money path is disarmed by default; this project is published as an engineering case study.

---

## The journey at a glance

| Stage | What I did | Verdict |
|---|---|---|
| **1. Naive strategies** | 6 directional "brains" (momentum, trend-following, price models…) live paper trading | ❌ All losing or artifacts |
| **2. Retroactive backtesting** | Backtest harness over Polymarket's free history + Binance | 💡 The "directional edge" was a **lookahead artifact**; real settlement is the Chainlink oracle, not Binance |
| **3. Favorite-longshot premium** | Buy the extreme favorite (0.85–0.95) late in the window and hold to settlement | ⚠️ Real but thin; the naive version is falsified out-of-sample |
| **4. The `zlead` family** | ONE parameterized engine: extreme favorite + a **volatility-normalized price-lead floor** (z ≥ 1) + measured vetoes (BTC alignment, longshot flow) | ✅ The only signal that survives multi-month AND multi-coin OOS — but net edge after fees ≈ 0 |
| **5. Real money** | Small-stake real pilots on a two-host architecture: co-located execution in Dublin (~22 ms), display in Morocco | 📏 Autopsy of 55 real losses → one validated defensive veto; real execution costs measured on-chain |
| **6. ML outlier hunt** | ~25 signal ideas, ML over every feature, strict OOS | ❌ **No public information beats the price.** Measured ceiling: +4.6 pts on one niche |
| **7. Market making (rebates)** | On-chain reverse-engineering of a +$80k wallet → its edge is Polymarket's **liquidity rewards**, not the spread. Replica: Python MM, then an **ultra-low-latency Go rewrite** | ✅ The rebate is real and measured on-chain; strict delta-neutral MM on a binary market is **out of reach without colocation** — it becomes viable from Polymarket's colocation servers, but colocation is not permitted from France, so it couldn't be implemented (analysis in the journal) |

The methodological through-line: **every conclusion rests on a raw measurement** (on-chain transactions, fill journals, never-seen OOS data) — never on a derived API field or a flattering backtest. Whenever the data contradicted a strategy I liked, the strategy died.

---

## The (honest) numbers

- **~12,000 paper trades journaled** across 60 concurrent runners (7 variants × 6 coins × 2 window lengths), settled against the real Chainlink oracle.
- Cumulative net result of the last run ($100 starting stake per runner): **+$55 on 15-minute windows** (36 runners), **−$520 on 5-minute windows** (39 runners) — the 5-minute granularity is a fee/spread trap.
- Real money: a few dozen dollars of accepted losses (−$42, −$62), each autopsied down to root cause (execution bugs, not lack of capital) and turned into coded safety invariants (`pmlab/mm_guard.py`).
- Polymarket's taker fee (`0.07·p·(1−p)`) was first believed to be phantom, then **proven charged on-chain** by reading raw transaction receipts — and the whole cost model recomputed in place, with backups.
- The market-maker wallet I studied earns **~$1,700/day in liquidity rewards**; our replica banked its first rebate dollars, measured on-chain.

---

## Architecture

```
pmlab/                  The Python package (stdlib only — zero pip dependencies)
  engine.py             THE strategy engine (extreme favorite + z-lead floor)
  presets.py            ONE zlead base + composable modifiers (n/x/mk/a/f/p)
  entry.py              EntryGate — the entry decision, SHARED paper + real
  feeds.py              Binance klines, Gamma API, CLOB books, on-chain data-api
  paper.py              Multi-position paper broker (simulated adverse selection)
  runner.py             Generic paper-trading loop (state, settlement, journal)
  live.py               The ONLY module that touches money (disarmed by default)
  hftmm.py, mm_guard.py WebSocket MM engine + safety invariants (HALT on anomaly)
  coins.py, staking.py, journal.py   Single registries: coins, sizing, journal schema
market-maker/           HFT market maker in Go (BTC 5-min): Φ(d2) pricing + EWMA vol
                        + Avellaneda-Stoikov inventory skew, hand-rolled EIP-712
                        CLOB V2 signing cross-validated against eth-account. 42 tests.
webdash.py              Dashboard: stdlib server + no-build Preact SPA (webdash_assets/)
research/               ~40 backtest/falsification scripts + FINDINGS.md (the dated
                        research journal: every hypothesis, every verdict)
docs/                   Strategy deep-dive, real-money setup, archived results (docs/results/)
deploy/                 Watchdogs, tunnels, GO/NO-GO gates for the VPS
```

Engineering principles held end to end:

- **One source of truth per decision**: the entry gate, sizing, coin registry and journal schema are each ONE module shared by paper, real, backtest and dashboard — drift between code paths cost two real losses before this rule became absolute.
- **Non-bypassable execution realism**: book re-fetch with simulated latency before every fill, abandon if the price ran away, partial fills against real depth, adverse selection on resting orders.
- **The front end derives from the back end**: the SPA reads `/config`; no hand-maintained duplicate lists.
- **Guardrails born from real failures**: every threshold in the code answers a failure mode actually observed in the journals, documented in a comment.

## The lab's instruments — the details I'm proud of

**Risk & portfolio**
- **Cross-strategy correlation matrix, three lenses** (`pmlab/correlation.py`) — the question isn't "how many bets?" but "how many *independent* bets?". Because a favorite's payoff is asymmetric (+0.11 win / −1.00 loss), full-P&L Pearson hides the real risk, so the primary lens is the **simultaneous-loss φ** (do two runners lose in the *same* window?), backed by a **structural spot-β** proxy (estimate co-loss risk *now* instead of waiting months for rare losses) and a union-find count of effectively independent strategy groups. Small-sample honesty built in: not enough overlap or losses → `None`, never noise.
- **Confidence-tied sizing ladder** (`pmlab/staking.py`) — stake fraction grows only after n trades confirm the edge; a tripwire (win-rate ≤ average entry price = edge death) zeroes the stake; a drawdown brake halves it. Includes a documented decision to *disable* one safety (the regime pause) because on a strategy where losses don't autocorrelate it froze itself forever — the analysis is in the docstring.
- **Per-(coin, frame) capacity caps from single-actor depth** (`pmlab/coins.py`) — bet ceilings measured as the *largest single wallet's* in-band buy volume per window, not aggregate flow: a conservative answer to "how much can ONE actor deploy here?" on a venue that publishes no historical order book.

**Execution realism (two independent fill simulators)**
- Python paper broker (`pmlab/paper.py`): resting orders fill only when the market trades *through* the price (never first in queue) and only 70% of size fills — informed-flow adverse selection. This model is what correctly predicted the directional maker would bleed live.
- Go simulator (`market-maker/paper.go`): queue-position-aware laddered fills (a print consumes the liquidity resting *ahead* of you first), built because a single top-of-book quote only explained ~58% of the reference wallet's fill volume.
- **`research/flattery_audit.py`** — a meta-tool that detects when a backtest is lying: it measures the gap between the cached tape price and what was actually executable ("a lesson learned seven times in one session").

**Other paradigms explored (each with its own honest verdict)**
- **Price-reversion scalping** (`pmlab/scalp.py`) — bet on the *price*, not the outcome: the thin order book overshoots on impatient flow and mean-reverts +6.5¢/30s (n=4082, ~9σ). The signal is real; the capture bled live — documented and shelved.
- **Copy-trading with a negative control** (`pmlab/copymirror.py`) — mirrors skilled event-market wallets (+$1.15M, +$516k track records) strictly *forward* from a watermark (replaying backlog would be circular), and runs a known anti-calibrated wallet in parallel: copy-P&L only counts if the control stays flat.
- **Event markets** (`pmlab/events.py`) — the same favorite-longshot harvest pointed at slow, emotional markets (sports, politics) where the premium is fatter than on arbitraged crypto windows.
- **Four distinct market-making approaches**, cleanly separated: complete-set arbitrage on the book (`setarb.py`, both sides sum < $1 → settles at exactly $1), on-chain set minting + overround asks (`mintmm.py`), liquidity-*rewards* harvesting (`rewardmm.py` — the income is the rebate program's size×proximity×uptime score, not the spread), and sub-second WebSocket re-quoting (`hftmm.py`).
- **Wallet forensics toolkit** (`research/std0/`) — full reconstruction of the +$80k reference wallet: time-window bisection to defeat API pagination caps, per-window P&L decomposition separating structural edge from outcome luck, and a per-fill execution-edge metric (`eps_reader.py`: the wallet earns +0.22¢/fill where we broke even — the single number that decides if speed beats the pickoff).

**Go engine internals** (`market-maker/`)
- Zero-allocation WebSocket wire parsing (`wireparse.go`) — hand-rolled field extraction, no `encoding/json` on the hot path; single-goroutine state loop, audited concurrency invariants, dropped-event kill switch.
- A **three-feed race**: Bitstamp Frankfurt (~4–10 ms, the fastest lead), Binance (backup lead), Chainlink RTDS (the settlement anchor) — each with a distinct, non-interchangeable role.
- **Predictive position caps** (`risk.go`) — a side may quote only if its *worst-case* fill stays within the cap (the reactive version let resting + in-flight orders stack to 2× cap — the dominant measured loss channel), plus a liquidation planner that refuses to cross the spread when the fee exceeds the inventory's risk value.

**Operations with an audit trail**
- **The fee saga as two tools** (`tools/defee_paper.py` / `refee_paper.py`) — the taker fee was first proven phantom (no fee transfer in USDC.e receipts), the whole ledger corrected in place; the next day a deeper read found the fee *is* charged on a pUSD leg the first audit missed — and a second tool re-applied it. Both are idempotent, backed up, and self-verifying (`--check` diffs a full rebuild against the frozen ledger). Being wrong is fine; being un-auditable is not.
- **Retroactive re-sizing via P&L linearity** (`tools/recompute_sizing.py`) — historical runs re-sized under a new staking model *without* historical order books, because net P&L is linear in stake.
- **GO/NO-GO dry gate** (`deploy/mm_dry_gate.sh`) — the real MM engine must run dry against the live book (~1.5 windows: re-quote rate, inventory imbalance p90, zero guard halts) before any real arming. It would have caught, offline, the bugs that cost −$62.
- **Two-host key-authority model** (`webdash.py`) — the execution host owns the only private key, supervises and arms pilots; the display host merely proxies the pilot tab over a `permitopen`-restricted, no-shell SSH tunnel. Double-running against the shared wallet is impossible by construction. Telegram tripwires alert on edge-death (state-change-only, no spam).

## Quickstart (paper trading — no keys, no risk)

```bash
git clone https://github.com/emoubarak/polymarket-updown-lab && cd polymarket-updown-lab

# One live paper runner: zlead strategy, 15-minute windows, BTC underlying
python3 main.py --strategy zlead --interval 15m --underlying btc

# Disposable smoke test (3 ticks):
python3 main.py --strategy zlead --interval 15m --underlying btc \
  --state-dir /tmp/smoke --ticks 3 --poll 5

# The dashboard (http://localhost:8420):
python3 webdash.py --port 8420
```

Python ≥ 3.10, **zero pip dependencies** for paper trading. Everything is read-only against public APIs (Binance, Polymarket Gamma/CLOB). Each runner writes its state to `state_<strat>_<coin>_<frame>/` (cash, fill journal, equity curve).

The Go market maker: `cd market-maker && go build ./... && go test ./...` — see `market-maker/README.md` (real mode needs keys and a funded account; paper mode does not).

The Python real-money path (`run_live.py`) stays **disarmed without explicit environment variables** — see `docs/SETUP-LIVE.md`.

## What this project demonstrates

- **The scientific method applied to trading**: hypothesis → IS/OOS backtest → live paper trading → falsification or a small real stake. Dead strategies are archived, not hidden (`research/FINDINGS.md` is the full journal, failures included).
- **Measurement-driven polyglot engineering**: Python stdlib where latency doesn't matter (the network dominates: a 22 ms RTT makes Rust/Go pointless for the 15-min signal), zero-alloc Go where it does (the MM's sub-second re-quoting). Stack choices are justified with numbers, not fashion.
- **Concrete skills**: real-time WebSockets, hand-rolled EIP-712/on-chain signing (byte-for-byte cross-validated), blockchain forensics (reverse-engineering wallets from raw transfers), a distributed two-host architecture with a single key authority, watchdogs/idempotency, a no-build SPA dashboard.
- **Discipline against bias**: I wanted this to work. What the repo really shows is how not to lie to yourself when it doesn't.

## Where to read next

- `research/FINDINGS.md` — the complete dated research journal, with the glossary of historical codenames.
- `market-maker/docs/JOURNAL.md` — the Go MM's journal: incidents, owned mistakes, and the analysis showing delta-neutral MM on a binary market requires colocation (possible on Polymarket's colocation servers, but not permitted from France — hence never implemented here).
- `docs/STRATEGY.md` — the zlead strategy in detail.
- `docs/results/` — the final snapshot of all 109 paper runners.
