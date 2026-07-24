# ARCHITECTURE — wallets, on-chain, CLOB, and THE unblock

This is the most important file for understanding WHY we are where we are. std0's strategy depends entirely on the **wallet TYPE**.

## The 3 Polymarket wallet types

| Type | CLOB sig_type | Created by | On-chain ops (split/merge/redeem) |
|---|---|---|---|
| **POLY_PROXY** | 1 | email/Magic login (legacy) | **relayer-only** (Magic-managed) |
| **GNOSIS_SAFE** | 2 | browser login (legacy) / manual deployment | **SELF-SUBMIT** via `execTransaction` ✅ (the owner EOA executes directly, zero relayer) |
| **POLY_1271** (deposit wallet) | 3 | current default onboarding (email AND MetaMask) | **relayer-only** — confirmed by the Polymarket docs: *"the owner EOA CANNOT directly execute on-chain transactions. ALL wallet actions require relayer submission."* It's a custom BeaconProxy. |

**Our wallets (0x32D3, 0x09CE) are POLY_1271 → relayer-only.** The current UI onboarding gives you that by default, even when connecting MetaMask. **std0 uses a GNOSIS_SAFE (or a self-submittable 0xaB45 proxy)** — a type you do NOT get via the current UI onboarding; you have to **deploy it yourself** (`deriveSafe` + `RelayClient.deploy()`).

## The wall: the relayer QUOTA
- The builder relayer rate-limits ops: `429 "quota exceeded: 0 units remaining, resets in ~1200s"` (~20 min).
- The harvester does **mint + merge per window** (2 ops). On 5m = 24 ops/h → quota blown. On 15m = 8 ops/h, that holds up better but it's a compromise.
- The quota **confounded every measurement**: merges failed (429) → inventory stayed stuck → false "bleeding" that was just unrecycled mint.
- **std0 does NOT have this problem**: it mints **directly from its EOA** (`factory.proxy([split])` on 0xaB45 → its proxy 0xdf79), zero relayer, zero quota.

## What we PROVED works (self-submit, direct on-chain)
- ✅ **EOA → adapter.splitPosition directly** (`eoaops.js`): mint to the EOA, zero quota. Works.
- ✅ **EOA → factory.proxy([split]) on 0xaB45** (`factory_proxy_mint.js`): mint to the factory-proxy (0xE138), zero quota. Works. **This is std0's exact architecture.**
- ✅ **EOA → deposit-wallet bridge** (ERC1155 transfer): free.
- ❌ **BUT the CLOB rejects both the bare EOA (sig 0) AND the factory-proxy 0xE138 (sig 2)**: `"maker address not allowed, please use the deposit wallet flow"` — because those addresses are **not registered to the account**. Only the registered deposit-wallet (0x09CE/0x32D3) is accepted at the CLOB.

## The resolution: the Gnosis Safe
The Gnosis Safe checks **BOTH boxes**:
1. **Self-submittable**: it's a standard 1-of-1 Safe, the owner EOA calls `execTransaction(adapter, 0, splitData, …)` → mint/merge/redeem directly, **zero quota**.
2. **Accepted at the CLOB** (sig type 2): it's a recognized Polymarket wallet type (the official `Polymarket/safe-wallet-integration` repo deploys one and trades on it). Its address is **deterministic from the EOA** (`deriveSafe`), so Polymarket recognizes it.

➡️ **TO VALIDATE** (NEXT-STEPS): deploy the Safe + confirm the CLOB accepts it (sig 2) AND that self-submit works. If yes → **both walls (quota + CLOB) fall together**.

## The CLOB (what already works)
- The broker's `usdc_balance()` reads via `get_balance_allowance(COLLATERAL, sig_type)`. **You need the RIGHT sig_type**: sig 3 for our POLY_1271 (worked, sees the $60), sig 2 for a Safe.
- Maker placement = `create_and_post_order(OrderArgsV2(...), post_only=True)`. **Min order = 5 shares.**
- Allowances (the proxy approving the exchanges) must be set: via relayer for POLY_1271 (`ensure_allowances` doesn't work well in sig 2/3 for that), via `execTransaction` for a Safe. Until they're set, the CLOB sees balance 0.

## The economics (why capital matters)
- Min order 5 shares → directional steps of 5-10 shares. On a small block ($10-40) = 30-100% directional → it bleeds. **std0 puts up $2500 → 5-10 shares = 0.4% → negligible → neutral.** Neutrality **requires capital** (large blocks). See [STRATEGY.md] for the numbers.
