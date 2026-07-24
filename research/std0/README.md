# Project std0 — replicating Polymarket's rewards market-making strategy

> Objective: replicate the **std0** wallet (`0xdf7930e89a2c47560165331863c31deca0733dcd`, +$80k in ~5 weeks) on Polymarket's crypto **Up/Down** markets, and generate a positive P&L that scales.

This folder is the complete state of the project so we can **resume right where we stopped** (paused 2026-06-28, resuming next week).

## 📍 WHERE WE ARE — the exact next action

**EVERYTHING is decoded and built. ONE brick is missing: a self-submittable wallet (Gnosis Safe) to mint without quota, like std0.**

➡️ **IMMEDIATE NEXT STEP:** deploy the **Gnosis Safe** for our new EOA + validate that it self-submits (mint without relayer/quota) AND that it's accepted at the CLOB (sig type 2). The script is ready: `~/mint/deploy_safe.js` on the box. See **[NEXT-STEPS.md](NEXT-STEPS.md)** for the step-by-step plan.

## The files in this folder

| File | Contents |
|---|---|
| **[NEXT-STEPS.md](NEXT-STEPS.md)** | 🎯 The step-by-step resumption plan (start here) |
| **[STATE.md](STATE.md)** | Exact state: wallets, balances, keys, box, what's running/deployed |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | The core: wallet types, self-submit vs relayer/quota, CLOB, the unblock |
| **[STRATEGY.md](STRATEGY.md)** | What std0 is (rewards harvester) + the quantified economics |
| **[TOOLS.md](TOOLS.md)** | All the scripts built + how to use them |
| **[FINDINGS.md](FINDINGS.md)** | The journal of discoveries, blockers and lessons (chronological) |

## Summary in 5 lines

1. **std0's edge = the MAKER REBATES** (fee redistribution), not the spread. ~$1778/day, 75% of its profit. Formula: `0.014 × p(1−p)` USDC per maker share **filled**, max at p=0.5.
2. **It mints a big block of sets** (ammunition) → posts **tight two-sided maker quotes** → gets filled → redeems at settle. Delta-neutral. Mostly **btc-5m** (64% of its volume).
3. **The wall = the relayer quota.** Our wallet (POLY_1271 deposit-wallet) can only do its on-chain ops via the relayer, which is rate-limited (429). That blocks the merges → it breaks the strategy.
4. **The unblock = a Gnosis Safe** (std0's wallet type): the owner EOA executes split/merge/redeem **directly via `execTransaction`, zero quota**. Built, ready to deploy.
5. **The rest is ready**: harvester (`rewardmm.py`), mint/merge/redeem pipeline, the complete decoding of std0, the AWS box (24ms colocation). Current capital ~$118 spread across 2 deposit-wallets (recoverable).
