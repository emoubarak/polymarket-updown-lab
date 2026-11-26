# ARCHITECTURE — wallets, on-chain, CLOB

std0's strategy depends entirely on the **wallet TYPE**. This file records the wallet and on-chain constraints
that shape it.

## The 3 Polymarket wallet types

| Type | CLOB sig_type | Created by | On-chain ops (split/merge/redeem) |
|---|---|---|---|
| **POLY_PROXY** | 1 | email/Magic login (legacy) | **relayer-only** (Magic-managed) |
| **GNOSIS_SAFE** | 2 | browser login (legacy) / manual deployment | **SELF-SUBMIT** via `execTransaction` (the owner EOA executes directly, zero relayer) |
| **POLY_1271** (deposit wallet) | 3 | current default onboarding (email AND MetaMask) | **relayer-only** — confirmed by the Polymarket docs: *"the owner EOA CANNOT directly execute on-chain transactions. ALL wallet actions require relayer submission."* It's a custom BeaconProxy. |

The current UI onboarding gives a POLY_1271 by default, even when connecting MetaMask. **std0 uses a
self-submittable wallet (a factory proxy on 0xaB45 or a GNOSIS_SAFE)** — a type you do NOT get via the current
UI onboarding; it has to be deployed explicitly (`deriveSafe` + `RelayClient.deploy()`).

## The relayer QUOTA
- The builder relayer rate-limits ops: `429 "quota exceeded: 0 units remaining, resets in ~1200s"` (~20 min).
- A market-making loop does **mint + merge per window** (2 ops). On 5m = 24 ops/h → quota blown. On 15m = 8 ops/h, that holds up better but it is a compromise.
- **std0 does NOT have this problem**: it mints **directly from its EOA** (`factory.proxy([split])` on 0xaB45 → its proxy 0xdf79), zero relayer, zero quota.

## Self-submit paths (direct on-chain)
- **EOA → adapter.splitPosition directly**: mint to the EOA, zero quota.
- **EOA → factory.proxy([split]) on 0xaB45**: mint to the factory proxy, zero quota. **This is std0's exact architecture.**
- **EOA → deposit-wallet bridge** (ERC1155 transfer): free.
- The CLOB, however, refuses addresses that are **not registered to the account** (bare EOA sig 0, unregistered factory proxy): `"maker address not allowed, please use the deposit wallet flow"`.

## The Gnosis Safe
A Gnosis Safe satisfies both requirements:
1. **Self-submittable**: a standard 1-of-1 Safe, the owner EOA calls `execTransaction(adapter, 0, splitData, …)` → mint/merge/redeem directly, **zero quota**.
2. **Accepted at the CLOB** (sig type 2): it is a recognized Polymarket wallet type (the official `Polymarket/safe-wallet-integration` repo deploys one and trades on it). Its address is **deterministic from the EOA** (`deriveSafe`), so Polymarket recognizes it.

## The CLOB
- Balance reads go through `get_balance_allowance(COLLATERAL, sig_type)`: **the sig_type must match the wallet type** (3 for POLY_1271, 2 for a Safe).
- Maker placement = `create_and_post_order(OrderArgsV2(...), post_only=True)`. **Min order = 5 shares.**

## The economics (why size matters)
- Min order 5 shares → directional steps of 5-10 shares. On a small block that is a large fraction of inventory; **std0 puts up $2500 → 5-10 shares = 0.4% → negligible → neutral.** Neutrality **requires large blocks**. See [STRATEGY.md] for the numbers.
