# Project std0: understanding Polymarket's rebate market-making strategy

> Objective: decode the **std0** wallet (`0xdf7930e89a2c47560165331863c31deca0733dcd`, +$80k in ~5 weeks) on Polymarket's crypto **Up/Down** markets, using read-only on-chain data and public APIs, and document the economics of its strategy.

This folder is the research record of that analysis. Everything about std0 is measured from the public data-api and on-chain transactions.

## The files in this folder

| File | Contents |
|---|---|
| **[STRATEGY.md](STRATEGY.md)** | What std0 is (rebate harvester) + the quantified economics |
| **[REVERSE_ENGINEERED.md](REVERSE_ENGINEERED.md)** | The scalpel map: markets, per-window mechanics, P&L decomposition, rebate income |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | Wallet types, self-submit vs relayer/quota, CLOB constraints |
| **[HANDOFF.md](HANDOFF.md)** | Strategy and design notes for a replica engine |
| **[FINDINGS.md](FINDINGS.md)** | Discoveries and lessons from the decoding |

## Summary in 5 lines

1. **std0's edge = the MAKER REBATES** (fee redistribution), not the spread. ~$1778/day, 75% of its profit. Formula: `0.014 × p(1−p)` USDC per maker share **filled**, max at p=0.5.
2. **It mints a big block of sets** (ammunition) → posts **tight two-sided maker quotes** → gets filled → redeems at settle. Delta-neutral. Mostly **btc-5m** (64% of its volume).
3. **Wallet type matters.** A POLY_1271 deposit wallet can only do its on-chain ops via the relayer, which is rate-limited (429). That is incompatible with a per-window mint/merge loop.
4. **std0 uses a self-submittable wallet** (a factory proxy / Gnosis Safe type): the owner EOA executes split/merge/redeem **directly, zero quota**.
5. **Neutrality needs size.** The CLOB minimum order is 5 shares, so on a small block each fill is a large fraction of inventory; std0's $2500 blocks make it negligible. The design is covered in HANDOFF.md.
