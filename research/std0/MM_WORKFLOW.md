# MM WORKFLOW — clean / durable / scalable (post-mortem of the −$62 session, 2026-07-01)

The std0-style maker-REBATE harvest on real money. This doc is the process that makes it safe to iterate.
**The meta-lesson that cost $62: we validated in PRODUCTION.** Every anomaly below was catchable offline.
Never change config/code and arm real money in the same step again.

## The pipeline for ANY change (no exceptions)
1. **Edit + `py_compile`** the touched files.
2. **DRY GATE** — `bash deploy/mm_dry_gate.sh <coin> <iv> <secs> <band> <maxinv> <skewk> <mint>`. Runs the
   engine DRY against the LIVE book (zero money) and prints **GO / NO-GO** (orders rest? imbalance bounded?
   guard clean?). Do NOT arm until it says ✅ GO for that exact config.
3. **PRE-FLIGHT** — on arming, `run_hft.py` calls `mm_guard.preflight()` and REFUSES to run real money on a
   misconfig (clip<5, mint>pUSD, bad band, kill≤0, max_inv<clip).
4. **SMALLEST SIZE FIRST** — start at the smallest mint that clears the 5-share granularity (~$15). Per-window
   risk is bounded by `max_inv` (a few $), not the whole pot. Scale the mint only after proven net≥0.
5. **ONE VARIABLE PER TEST.** Changing band+clip+mint+kill together (as in the −$62 session) makes bugs
   impossible to isolate. Change one thing, measure, then the next.
6. **MEASURE ON-CHAIN ONLY** — `research/std0/pnl_tracker.py <hours>`. The engine's `mark`/`rebate` counters
   are SUSPECT (they phantomed + the UserFeed misparsed). Truth = pUSD + settled/held tokens, per window.

## The runtime safety net — `pmlab/mm_guard.py` (HALTs, doesn't bleed)
Each invariant maps to a real incident that cost money:
| Invariant | Catches (incident) |
|---|---|
| `check_fill(size, clip)` | a fill > our clip is impossible → the UserFeed reading the **taker's** size (114 vs 5) that desynced inventory → **−60%** |
| `reconcile_inv(engine, chain)` | engine-tracked inv diverging from on-chain (the same desync) → HALT before trading on a lie |
| `check_mark(mark, block)` | a −mark deeper than the block = **phantom** (redeem-credit lag) → suppress the false KILL |
| `check_sell_price(px, mid)` | selling ≫ below mid = the flatten/cap **dump at 0.02** → −$26; clamps it |
| `preflight(cfg, pusd)` | clip<5 (0 fills), mint>pUSD (silent abort → "no orders"), bad band/kill |
On any HALT the engine cancels all + stops. **No silent `return`s** — every abort logs its reason.

## Files
- `pmlab/hftmm.py` — the WS engine (mint pre/at-open, quote two-sided across the book, sell/rebalance,
  MERGE matched at settle to recycle capital + flatten residual, on-chain sync as truth). Owns a `Guard`.
- `pmlab/mm_guard.py` — invariants + preflight (stdlib).
- `run_hft.py` — the live runner (preflight-gated).
- `deploy/launch_hft_measure.sh` — the launcher (env + params, one source of truth).
- `deploy/hft_measure_watchdog.sh` — cron `*/2 + @reboot`, restart-on-death (the box OOMs / Cloudflare-drops).
- `deploy/mm_dry_gate.sh` — the DRY GO/NO-GO gate.
- `research/std0/pnl_tracker.py` — per-window ON-CHAIN P&L (rebate vs trade vs imbalance). The ONLY P&L truth.
- `research/std0/eps_reader.py` — per-fill execution edge from the FILL log.

## Infra invariants (box: AWS Dublin, 908MB)
- **+1G swap** added (was OOM-killing runs). **Watchdog** restarts on death. Log is truncated each start +
  benign "post-only crosses" are NOT logged (a 442M/6M-line log helped kill a run).
- Cloudflare intermittently challenges the box IP on the CLOB (REST+WS) → runs die → watchdog resurrects.

## The strategy (what we're maturing) — std0 at SMALL scale (its 26-05 stage)
std0 bootstrapped from ~$4-35 blocks / $1-6/day rebate (Mar-Apr) → $2500 blocks / ~$2000/day (Jun). At our
scale the edge is thin and execution is everything. Open question still being measured on-chain: does the
fixed execution (accurate inventory + neutral flatten) net ≥0 over many windows at small size? Only the
on-chain tracker over a full day + the daily rebate credit decides. Scale mint only after that GATE passes.
