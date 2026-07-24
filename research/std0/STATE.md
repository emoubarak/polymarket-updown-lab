# STATE — exact state at the moment of the pause (2026-06-28)

## Box (the "co-location")
- **AWS Dublin** (eu-west-1, trading-allowed + ~24-33ms RTT to the Polymarket CLOB).
- Access: `ssh -i ~/<ssh-key>.pem ubuntu@<AWS_IP>`
- Repo synced in `~/pmlab/`; venv `~/pmlab/.venv-live` (py-clob-client-v2 + web3 + py-builder-relayer-client). Node SDKs in `~/mint/`.
- `loginctl enable-linger ubuntu` is enabled (processes survive SSH disconnects). ⚠️ `systemd-logind` kills processes launched in an SSH session without linger.

## Wallets (EVERYTHING is a relayer-only POLY_1271 deposit-wallet for now)

| Role | EOA (signer) | Proxy / deposit-wallet (where the funds are) | Balance | Type |
|---|---|---|---|---|
| Old | `0x9C9A11397E70e2560145c8EDdeaC90209B008f15` | `0x32D30125710521fF3Bf174d6440374Cb39634029` | ~$58 pUSD | POLY_1271, relayer-only |
| **New** | `0x9Af60f632C0a82E88908C8eE71dd980f6dCdFA8E` | `0x09CEFb48630749dEd23eeE88fF33d364CE3da6c4` | ~$59 pUSD + $1 pos | POLY_1271, relayer-only |
| (to deploy) | 0x9Af6 (same) | **Gnosis Safe** = `deriveSafe(0x9Af6, …)` — NOT YET DEPLOYED | $0 | GNOSIS_SAFE, **self-submit** ✅ |

- The new EOA 0x9Af6 has **~68 POL** (gas for self-submit). The old 0x9C9A has ~69 POL.
- Total capital ~**$118** (recoverable, split across the 2 deposit-wallets).
- ⚠️ The new EOA's Safe is a **3rd address** (≠ 0x09CE). THAT is the one we want (self-submittable). To be deployed (cf. NEXT-STEPS step 1).

## Keys & creds (do NOT put the values here — they are on the box, chmod 600)
- `~/.poly_env_maker`: old EOA (POLY_PRIVATE_KEY 0x9C9A) + `POLY_FUNDER=0x32D3` + `POLY_SIG_TYPE` + the **builder creds** (`POLY_BUILDER_KEY/SECRET/PASS`).
- `~/.poly_env_safe`: new EOA (POLY_PRIVATE_KEY 0x9Af6) + `POLY_FUNDER=0x09CE` + `POLY_SIG_TYPE=3` + builder creds (copied).
- **Builder API key (public): `019f0b1b-9b99-7721-b40e-bc9c01248cc4`** (secret + passphrase in the env files + the conversation history). Obtained on `polymarket.com/settings?tab=builder`.
- The private keys are also in this conversation's history.

## What is RUNNING / stopped
- **Harvester `rewardmm`: STOPPED** (runner killed, cron removed at the pause). Nothing is trading anymore.
- All the old test runners (mm, mintmm, setarb, crypto zlead): stopped on this box.
- ⚠️ The **zlead prod** is on a DIFFERENT VPS (`<VPS_IP>`), **intact, untouched** by this entire std0 project.

## Contract addresses (Polygon, chain 137)
| Name | Address |
|---|---|
| Ctf Collateral Adapter (split/merge/redeem) | `0xAdA100Db00Ca00073811820692005400218FcE1f` |
| pUSD (V2 collateral) | `0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb` |
| ConditionalTokens (CTF, ERC1155) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` |
| **SafeFactory** (for deriveSafe + deploy) | `0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b` |
| SafeMultisend | `0xA238CBeb142c10Ef7Ad8442C6D1f9E89e07e7761` |
| ProxyWalletFactory (0xaB45, the factory-proxy route) | `0xaB45c5A4B0c941a2F231C04C3f49182e1A254052` |
| CLOB V2 Exchanges (to approve) | `0xE111180000d2663C0091e4f400237545B87B996B`, `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296`, `0xe2222d279d744050d28e00520010520000310F59` |
| Builder relayer | `https://relayer-v2.polymarket.com` |
| std0 (the reference) | proxy `0xdf7930e89a2c47560165331863c31deca0733dcd`, EOA `0x3Ec3577A6a22F9B4716C5AeFe0963a052BF703a6` |
