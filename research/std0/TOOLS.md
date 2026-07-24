# TOOLS — scripts built and how to use them

## In the repo (`pmlab/`)
| File | Role | Status |
|---|---|---|
| `pmlab/rewardmm.py` | **The harvester**: two-sided maker, mint via `_setop()`, recovery merge, cap fix (gate asks+bids), spot-anchoring (`_fair_p`), kill-switch, honest cash-flow accounting | ✅ ready; `_setop` points at `setops.js` (relayer) — **to be rewired to `safeops.js` (self-submit)** |
| `run_rewardmm.py` | CLI runner for the harvester. Args: `--interval --underlying --mint-usd --clip --back-off --recenter-eps --max-inv --min-quote --max-quote --flatten-buf --kill-loss --poll --spot-anchor --beta --state-dir` | ✅ |
| `pmlab/live.py` | CLOB broker (LiveBroker): `connect`, `usdc_balance` (via get_balance_allowance + sig_type), `place_limit`/`place_sell` (post_only maker), `place_sell`/`sell_market`, `order_fill`, `cancel`, `ensure_allowances`. Reads `POLY_PRIVATE_KEY/POLY_FUNDER/POLY_SIG_TYPE/POLY_LIVE` | ✅ ; supports sig_type 0/1/2/3 |
| `pmlab/setarb.py`, `mm.py`, `mintmm.py` | Earlier attempts (delta-neutral set-arb, one-sided MM, sell-side mint-MM). All **abandoned** in favor of `rewardmm`. Re-runnable if needed. | archive |
| `research/std0_*.py` | std0 analysis scripts (collect/analyze/tx/latency/live) | ✅ |

## On the box (`~/mint/`, node + Polymarket SDKs)
| File | Role | Status |
|---|---|---|
| `setops.js` | mint/merge/redeem via **relayer** (`RelayClient.executeDepositWalletBatch`). `node setops.js <mint\|merge\|redeem> <cid> <usd/shares>` | ✅ works (quota-limited) |
| `eoaops.js` | mint/merge/redeem **direct from the EOA** (`adapter.splitPosition` etc.) + `mintbridge`/`pullmerge` (EOA mint → bridge to deposit-wallet). Zero quota but the EOA is not accepted at the CLOB | ✅ works on-chain |
| `factory_proxy_mint.js` | mint into the **factory-proxy** (0xaB45) via `factory.proxy([{1,adapter,split}])` = std0's architecture. Zero quota | ✅ proven (0xE138 minted) |
| **`deploy_safe.js`** | **deploys the Gnosis Safe** (`deriveSafe` + `RelayClient.deploy`). THE next one to run | ✅ ready, ⚠️ **not yet executed** |
| `set_approvals.js` | attempt at approvals via relayer (blocked: the relayer forbids arbitrary `setApprovalForAll`) | ❌ blocked |
| `batch_test.js`, `check_tx.js`, `direct_eoa_test.js`, `activate_safe.js` | miscellaneous on-chain tests (batching, tx status, etc.) | tests |
| `TO WRITE → safeops.js` | mint/merge/redeem via **`Safe.execTransaction`** (self-submit, zero quota). Analogous to `eoaops.js` but through the Safe. **Step 2 of NEXT-STEPS** | 🔴 to do |

Env on the box: `~/.poly_env_maker` (old EOA + builder creds), `~/.poly_env_safe` (new EOA + builder creds). Always `set -a && . ~/.poly_env_<x> && set +a` first.

## CLOB tests (`~/pmlab/`)
- `test_clob_bid.py` / `test_clob_sig0.py`: test placing a maker order with a given sig_type/funder. Run with `POLY_LIVE=1 POLY_CONFIRM=I_UNDERSTAND_REAL_MONEY POLY_SIG_TYPE=<n> POLY_FUNDER=<addr>`.

## Watchdog / persistence
- `~/rewardmm_09ce_watchdog.sh` (cron `*/5`): relaunches the harvester if dead + dedup by state-dir. **The cron runs out-of-session → persists** (unlike a manual `setsid` that `systemd-logind` kills). `enable-linger` enabled. Recreate an equivalent watchdog for the Safe.
- Bracket pattern mandatory for pkill/pgrep: `[r]un_rewardmm.py`.

## Honest measurement command (wallet value)
```python
# liquid pUSD
LiveBroker().usdc_balance()
# + marked positions (data-api)
requests.get('https://data-api.polymarket.com/value', params={'user': WALLET}).json()[0]['value']
# true P&L = (pUSD + positions) - V0 ; rebate ≈ 0.014 * Σ p(1-p)·filled_shares
```
