# NEXT-STEPS — step-by-step resumption plan

We stopped right at the point of **deploying the Gnosis Safe** (the self-submittable, std0-style wallet). Everything else is decoded/built. Here is the exact continuation.

## Context in one sentence
Our current wallets (0x32D3, 0x09CE) are **POLY_1271 deposit-wallets = relayer-only = quota** → this blocks the harvester's merges. **The Gnosis Safe** (the owner EOA executes directly via `execTransaction`) removes the quota. That is what is missing.

## Step 1 — Deploy the Gnosis Safe (THE resumption point)
On the box (`ssh -i ~/<ssh-key>.pem ubuntu@<AWS_IP>`):
```bash
cd ~/mint && set -a && . ~/.poly_env_safe && set +a && node deploy_safe.js
```
- `deploy_safe.js` computes `deriveSafe(EOA, SafeFactory=0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b)` and deploys the Safe via `RelayClient.deploy()` (one one-time relayer op).
- ⚠️ **THIS is the command that has not run yet** (interrupted at the pause). EOA used = the new one (0x9Af6, key in `~/.poly_env_safe`).
- Expected result: a **Safe address** (≠ 0x09CE), deployed on-chain.

## Step 2 — Validate SELF-SUBMIT (the quota unblock)
Have the EOA execute a `splitPosition` **directly via `Safe.execTransaction`** (not the relayer) → verify the Safe receives the tokens. Standard Gnosis Safe: `execTransaction(to=adapter, value=0, data=splitData, operation=0, ...signatures)` signed by the owner EOA. **Write `~/mint/safeops.js`** (mint/merge/redeem via execTransaction) — analogous to `eoaops.js` but through the Safe. If it works → **zero quota confirmed.**

## Step 3 — Validate the CLOB on the Safe (sig type 2)
Test a maker order with `POLY_SIG_TYPE=2` + `POLY_FUNDER=<Safe address>` + the owner EOA's key. Reuse `~/pmlab/test_clob_bid.py`.
- ⚠️ First: set the **Safe's allowances** (the Safe approves the CLOB exchanges `0xE111…`, `0xd91E…`, `0xe2222…` for pUSD + `setApprovalForAll` on the CTF) via `execTransaction`.
- If the CLOB accepts (sig 2 + a REAL Safe) → **both walls fall** (zero quota + CLOB OK). (Reminder: sig 2 had been rejected on 0x09CE/0xE138 because those were NOT Safes; a real Safe should pass.)

## Step 4 — Fund the Safe
Move ~$60 from 0x09CE (POLY_1271) to the Safe (via relayer, 1 transfer) OR deposit fresh funds. For genuine std0-style neutrality, aim for **more capital** ($100-500+ blocks).

## Step 5 — Adapt the harvester to the Safe
In `pmlab/rewardmm.py`: `_setop()` must call **`safeops.js`** (self-submit) instead of `setops.js` (relayer). Broker in **sig type 2** + funder = Safe. Remove the quota rate-limiting (no longer needed). The `op_cooldown` can drop to ~2s (just block time).

## Step 6 — Launch on btc-5m (the REAL market) + co-location
- **5m, not 15m**: that is where 64% of std0's volume/rebate is (cf. the user's critique — I had compromised on 15m).
- **Fast poll (sub-second, e.g. 0.5-1s)**: we have co-location (Dublin box, 24ms RTT) → use it to avoid getting picked off on fast 5m.
- Enable the **spot anchor** (`--spot-anchor`, already built) to front-run the CLOB's lag.
- The **cap fix** (gate asks AND bids) is already in the code.
- ⚠️ **Persistence**: launch via cron-watchdog (not a manual setsid — `systemd-logind` kills session processes; `enable-linger` + cron already in place for 09ce, to redo for the Safe). See [STATE.md].

## Step 7 — Measure net-positive PROPERLY
The open question: **adverse selection vs rebate — is it net-positive?** Measure the **wallet's total value** (pUSD + marked positions) before/after several windows = true churn P&L; + fills × `0.014·p(1−p)` = rebate. If NET ≥ 0 → scale the capital. (All previous measurements were confounded by the quota/min-size/cap bug, NOW fixed.)

## Guardrails (already paid for, cf. FINDINGS)
- **Always ONE single runner** (check `pgrep -cf '[r]un_rewardmm.py'`). My double launches created conflicts.
- **Never conclude "it's bleeding" from confounded accounting** (an unrecycled mint ≠ a loss). Measure wallet value.
- **Tight kill-switch** during testing (real money).
- **Min order = 5 shares** on the CLOB.
