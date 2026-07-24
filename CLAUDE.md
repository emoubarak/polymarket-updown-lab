# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## The project

Paper trading of Polymarket's **crypto Up or Down** markets (**5m/15m** windows; the 1h doesn't exist), across **multiple underlyings** (btc/eth/sol/xrp/doge/bnb), driven by the **zlead family**: ONE parameterized engine that buys the **extreme favorite** (0.85–0.95) with a **vol-normalized lead floor (z≥1)** and holds to settlement — the favorite-longshot premium. It is the only edge that survived multi-month AND multi-coin OOS. **Two earlier lineages were falsified and withdrawn** (see `research/FINDINGS.md` + git log): the six original esoteric brains (gurdjieff/hermetic/iching/kabbalah/tao/coagula), then the BTC-only bare-rubedo lineage (rubedo/citrinitas/rubedowide/fixatio/coniunctio/lapis/aurum). They only survive in `presets._ARCHIVE` (re-constructible for backtests, **not deployed, not armable**). Everything is read-only against the real APIs (no keys); **no real order is sent by default**. An optional, disarmed real-money path exists (`run_live.py`, `pmlab/live.py` — see `docs/SETUP-LIVE.md`).

## Commands

```bash
# Run one brain of the family (zlead | zleadmk | zleadx | zleadn) × coin × frame
python3 main.py --strategy zlead --interval 15m --underlying btc   # weighted sizing by default

# Quick smoke test (3 live ticks, throwaway state) — do this for touched variants before any deploy
python3 main.py --strategy zlead --interval 15m --underlying btc --state-dir /tmp/smoke_zlead --ticks 3 --poll 5

# Syntax check of the whole package
python3 -m py_compile pmlab/*.py main.py webdash.py archive.py run_live.py

# Local dashboard
python3 webdash.py --port 8420

# Archive the current run BEFORE any state-dir reset (otherwise the data is lost)
python3 archive.py --label "<short-sha> <description>"

# REAL trading (disarmed by default — runs in DRY-RUN without the env vars). See docs/SETUP-LIVE.md.
python3 run_live.py --strategy zlead --interval 15m --underlying btc --start 100
```

No test framework: validation = compile + live smoke test of every touched variant + re-reading the journals. For a new variant: **IS/OOS backtest first** (`research/backtest_lead.py` / `gen_backtest_curves.py`).

## Architecture

**zlead family = ONE engine + composable Presets.** `engine.Engine` is the only engine; `presets.py` generates each variant with `zlead(*types, **overrides)`: zlead base + composable TYPE modifiers (`n` band 0.85–0.90 · `x` z≥1.5 · `mk` maker entry) + per-deployment field overrides (e.g. `enter_lo` per coin). Adding a dimension = one `TYPES` entry; adding a coin = one `Coin(...)` in `coins.py`. Paper (`engine`), the real pilot (`run_live`) and the dashboard (`/config`) all read the SAME `Preset` → zero drift.

**Single sources (do NOT duplicate):**
- `pmlab/coins.py` — THE coin registry (`Coin`: key/symbol/bet_max/slot). `feeds.SYMBOL`, `presets.COIN_BET_MAX/COIN_SLOTS`, the `--underlying` choices, `webdash._TOKENS`, and the front-end (via `/config` `COINS`/`FRAMES`) all DERIVE from it.
- `pmlab/entry.py` — `EntryGate` (the entry decision, shared paper+live) + `lead_z()`, `favorite_of()`, `ask_acceptable()`, `topup_remaining()`.
- `pmlab/staking.py` — `weighted_clip()` (the weighted sizing: pilot + paper + backtest + recompute).
- `pmlab/journal.py` — `JOURNAL_COLUMNS` schema + `append_row()` writer (events/copy) + `is_entry/is_close/won/window_ts` classification (dashboard + correlation).

**Paper**: `runner.py` is the generic runner. It builds a `Ctx` per tick (spot, σ, model p via `model.model_p_up` — telemetry only, the edge is in the PRICE), multi-position broker `MultiBroker` (`paper.py`). It settles expired windows, matches resting orders, and replays every observed window via `on_resolution`. State dirs: `state_<variant>_<coin>_<frame>/`. A runner strategy exposes `name`, `on_tick(ctx, broker, log)`, optionally `on_resolution(slug, went_up, log)` and `status(ctx)`; check `ctx.kill_switch` before any new risk.

**Real** (separate, disarmed): `run_live.py` is a minimal, auditable loop reusing the SAME `EntryGate` + `staking.py` + `live.py` (the ONLY module that touches money, via py-clob-client). State dirs: `live_state_*/`. **Never touch armed pilots or their `live_state_*`.**

**`feeds.py`** centralizes data: Binance (1m klines = spot, EWMA vol, window open, close>open→Up proxy), Gamma API (deterministic slug `{coin}-updown-{5m|15m}-{start_ts}` + `resolve_market()` = the real Chainlink oracle), CLOB (books, midpoint), data-api (a wallet's on-chain activity).

**Execution realism** (do not bypass it):
- every buy decision re-fetches the book via `ctx.exec_book()` (1 s simulated latency), fills against that book, abandons if the best ask has run away by > 0.02; tops up toward the target stake if the 1st fill is depth-limited;
- **FEES = 0**: the 0.07 taker fee is a PHANTOM — flagged in the Polymarket metadata (`feesEnabled=True`) but NEVER charged by the on-chain CTF Exchange (verified 2026-06-26 on raw transaction receipts; the data-api's `usdcSize` displays it for nothing). `feeds.taker_rate=0`, `explore2.FEE_RATE=0`, `webdash` adjusts PRIORS/BACKTEST fee-free. `paper.fee_for()` stays generic (rate passed = 0). Real cost = half-spread ~0.5¢ in calm conditions. Re-verify on-chain if Polymarket activates the fee.
- adverse selection on resting orders: the market must cross the price by ≥ 0.01 and only 70% of the size fills (`MultiBroker`).

**Sizing**: each bet = `staking.weighted_clip(capital, WEIGHT_PCT=0.10, COIN_BET_MAX[coin])` from a $100 start — never more than the coin's book depth. Identical paper / pilot / backtest.

**Per-strategy state**: `state.json` (cash, P&L, positions, orders), `journal.csv` (every fill/settlement), `equity.csv`.

**`webdash.py`**: stdlib server. JSON endpoints (`/config`, `/all`, `/data?strat=`, `/history`, `/pilot-data`, `/events-data`, `/correlation-data`, POST `/pilot`) + static under `/a/<path>` (app code `no-store`). The **front-end is a Preact SPA** in `webdash_assets/` (zero build): `index.html` (import map) · `style.css` · `vendor/` (preact/htm/chart.js **vendored, never a pip dep**) · `app/` (`main.js` router, `api.js`, `format.js`, `components/`, `pages/`). **The front-end DERIVES from `/config`** (PRESETS, COINS, FRAMES, COIN_BET_MAX, …) — don't re-hardcode lists on the JS side. `archive.py` snapshots each run into `history/runs.json`.

## VPS — production

**Two-host architecture (2026-06-28).** The **paper + dashboard** run in **🇲🇦 Morocco**; the **real pilots** (+ the MM, later) run on **🇮🇪 AWS Dublin** — dedicated to latency/execution (`/book` ~22 ms vs ~72 ms in Morocco). The private key lives **only on AWS** ⇒ **a single armed host, double-run impossible**. AWS = single authority (key + `pilot_registry.json` + `live_state_*` + execution + control API); Morocco only **proxies** the Pilots tab (display AND arming). `webdash.py` runs in two modes: `--pilot-api` (AWS) / `--pilot-remote <url>` (Morocco).

- **🇲🇦 Morocco** — `<vps-user>@<VPS_IP>` (Tailscale IP), `~/pmlab/`, dashboard http://<VPS_IP>:8420/. Runs the **60 paper runners** (5 variants × 6 coins × 2 frames) + 3 events + 3 copy + the **dashboard**. **Does NOT hold the key, supervises nothing** (the supervisor is gated behind `--pilot-api`, OFF here).
  - **Watchdog**: `deploy/watchdog.sh`, cron `*/5` + `@reboot` — matrix (`COINS`/`VARIANTS`/`FRAMES` at the top) → restarts dead paper runners + launches the dashboard via `deploy/morocco_dash.sh` (**single source** of the Morocco webdash launch, with `--pilot-remote http://127.0.0.1:8430`).
  - **Tunnel**: `deploy/morocco_tunnel.sh`, cron `*/2` + `@reboot` — SSH tunnel Morocco→AWS (`127.0.0.1:8430 → AWS 127.0.0.1:8420`, key `~/.ssh/aws_tunnel` **restricted** `permitopen="127.0.0.1:8420"`, no-shell). **`AWS_IP` hardcoded**: if the AWS public IP changes (stop/start without an Elastic IP), fix it there (or move the box to Tailscale).
  - **Paper backup**: `deploy/backup_paper.sh`, cron `0 */3` — rolling tarball of the `state_*` + `history/runs.json`. **Restore** = `tar xzf …/paper-<stamp>.tar.gz -C ~/pmlab/`.
- **🇮🇪 AWS Dublin** — `ubuntu@<AWS_IP>`, local key `~/<ssh-key>.pem`, `~/pmlab/`. **Pilot engine** = `webdash --pilot-api --host 127.0.0.1` (serves the `/pilot-data` API + POST `/pilot`, **supervises** dead pilots, **arms**). Holds `.live_env` (the key; **duplicates last-wins** → `load_live_env` fixed + a launch that **sources** `.live_env`: otherwise `sig_type` reads the 1st, stale value → wrong account → $0 capital), `.venv-live` (python3.14, `py-clob-client-v2`), the **authoritative** `pilot_registry.json`, the `live_state_*`. **Engine watchdog**: `deploy/aws_engine_watchdog.sh`, cron `*/2` + `@reboot`. The MM (`mintmm`) is there too but **DRY** (its launcher doesn't source the key).

### Redeploy workflow

1. Compile + smoke test the touched variants locally (`/tmp/smoke_*`). New variant: IS/OOS backtest first.
2. `rsync -az --exclude '__pycache__' pmlab webdash_assets main.py webdash.py archive.py <vps-user>@<VPS_IP>:~/pmlab/` (+ `scp deploy/watchdog.sh …` if modified). **`webdash_assets/` is mandatory** (dashboard boot guard).
3. **A redeploy = sync + RESTART, NOT a reset.** Only `rm` a state dir on explicit request. For a model change (sizing/fee): correction IN PLACE via an exact tool + backups (`tools/recompute_sizing.py`, `tools/defee_paper.py`), watchdog paused meanwhile, never a reset.
4. Restart: `pkill -f "[m]ain.py"` (crypto runners) then `bash watchdog.sh` (restarts with the new code). Front-only (webdash_assets): app code `no-store` → rsync + reload, no restart.
5. Verify from the outside: `curl http://<VPS_IP>:8420/all`, logs error-free, history intact.

For a multi-step script on the VPS, upload it via `scp` and run it — no inline heredoc in the ssh command.

### Known traps (already paid for)

- **`pkill -f "main.py"` on the VPS kills the SSH session** (the pattern matches the remote shell). Always the bracket pattern: `pkill -f "[m]ain.py"`, `pgrep -f "[w]ebdash.py"`.
- Launching a persistent process remotely: `setsid nohup python3 -u … >> x.log 2>&1 < /dev/null &`.
- Re-running `recompute_sizing` reads from `.orig` (frozen) → **loses the trades accumulated since**. For a correction on a living journal, use a tool that reads the CURRENT journal in place (`defee_paper.py`), not `.orig`.
- `equity.csv` must never count the cash reserved by resting orders (already in the cash) — historical double-counting bug.
- A position from a past window must **never** be valued with the next window's books: settlement only.
- **`pkill -f`/dedup self-match**: the bracket pattern (`[w]atchdog.sh`) only protects against the *pattern itself*. If the SAME command contains the **verbatim name** elsewhere (e.g. the `~/.ssh/aws_tunnel` path, or `bash ~/watchdog.sh`), `pkill -f` matches your own shell → kills the session (exit 255), and the watchdog's dedup `pgrep -fc '[w]atchdog.sh'` counts you twice → it exits without relaunching anything. Always isolate the kill/trigger in a SEPARATE command that doesn't reference the verbatim name. Triggering the watchdog by hand = `deploy/morocco_dash.sh` (neutral name), not `bash ~/watchdog.sh`.
- **`.live_env` with duplicates + $0 capital**: the file has **duplicated** `POLY_SIG_TYPE`/`POLY_FUNDER` entries (1st=stale, 2nd=correct). `source` (watchdog) = last-wins → good; `load_live_env` read first-wins → wrong `sig_type` → `get_balance_allowance` queries an empty account → **$0 capital → clip 0 → stand-down** (no loss, but an inert pilot). Fixed (load_live_env last-wins); and the AWS engine **sources** `.live_env` at launch. Symptom to recognize: `capital synchronisé on-chain : $0.00` while the funds are there.
- **Two-host redeploy**: paper/dashboard code → rsync **Morocco**; pilot code/`live.py`/`run_live.py`/`webdash.py` → rsync **BOTH** (`-e "ssh -i ~/<ssh-key>.pem"` for AWS). Restart: Morocco = `deploy/morocco_dash.sh`; AWS = `pkill -f "[w]ebdash.py"` then `deploy/aws_engine_watchdog.sh` (which sources `.live_env`). **Never** launch a pilot on both hosts (double-run on the shared wallet).

## Conventions

- Documentation and all user-facing content in English (README, dashboard, analysis logs); code and its docstrings in English too.
- **Paper = stdlib only** (urllib/requests, http.server) — no **pip** dependency. The **real path** (`live.py`) has isolated deps (`requirements-live.txt`), lazily imported. The **front-end** may use JS libs but **vendored in `webdash_assets/vendor/`** (zero build, zero npm runtime).
- **One source, no hand-kept parallel dict.** Coins → `coins.py`; gate/strat → `presets.py`; sizing → `staking.weighted_clip`; journal schema/classification → `journal.py`; the front-end DERIVES from `/config`. Any hardcoded dict duplicating a source DRIFTS and ends up lying (already seen: `webdash._TOKENS` stuck at 4 coins, `LABELS` listing dead strategies). Add the coin/strat to the SOURCE, never in N places.
- **Truth = raw measurement, not a derived field.** An on-chain fact is read from raw transfers (Polygonscan / RPC receipts), not from a computed API field (`usdcSize` added a phantom fee). Same for an edge: never-seen data (OOS / fresh window) decides, not the narrative. Don't rewrite backtest/paper to flatter — fix a REAL measured defect, with backups.
- **Every back-end change has a front-end shadow.** A data/strategy change has an SPA mirror (dropdown, labels via /config, PRIORS/BACKTEST, columns) — handle it in the SAME pass. And a front-end change is only shipped once **rsync'd to the VPS** (no-store → reload, no restart).
- Every guard/threshold added must answer a failure mode actually observed in the journals; note it in a comment/docstring.
- Results over a few hours are statistical noise (~12 windows/h on 5m): report them honestly as such.
