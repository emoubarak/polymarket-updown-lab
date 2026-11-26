# FINDINGS: discoveries and lessons from decoding std0 (chronological)

Notes from the std0 analysis (2026-06-28).

## Decoding std0
1. **std0 is NOT a price bettor** — it's a rewards harvester (see STRATEGY). First hypothesis "sell-side overround" → FALSE. It's the rebate.
2. The rebate = `0.014·p(1−p)`/filled share. Objective = **maker volume filled near 0.5**.
3. std0 mints directly (EOA), never merges, redeems at settle. ~78 fills/window btc-5m.

## Platform facts (on-chain pipeline and CLOB)
4. **mint/merge/redeem work through the builder relayer** (batching N calls into 1 action = ~0.4s), or directly from an EOA (the split pulls the pUSD from msg.sender): zero quota.
5. **A factory-proxy mint works** (`factory.proxy([split])` on 0xaB45 → proxy) = std0's exact architecture. Zero quota.
6. **The CLOB only accepts the maker addresses registered to the account**: a bare EOA (sig 0) or an unregistered factory proxy is refused with *"maker address not allowed, use the deposit wallet flow"*.
7. **The relayer has a QUOTA** (`429 quota exceeded, resets ~1200s`), too small for a per-window mint/merge loop at 5m throughput.
8. **The relayer blocks arbitrary `setApprovalForAll`** ("operator not in allowed list").
9. **UI onboarding (email or MetaMask) yields a POLY_1271 deposit wallet = relayer-only**, confirmed by the Polymarket docs. A self-submittable **Gnosis Safe** (`deriveSafe` + `RelayClient.deploy`) is accepted at the CLOB with sig type 2 and self-submits via `execTransaction`.

## Execution lessons (design)
10. **The CLOB has a 5-share min order.** A clip below 5 means every order is rejected.
11. **The inventory cap must gate asks AND bids** (not just bids) — otherwise the asks drain one side → directional drift.
12. **Neutrality requires a BIG block**: min-5-shares = directional steps of 5-10 shares. On a block of a few dozen sets that is a large share of inventory; on std0's $2500 it is 0.4% → neutral. The granularity argument is arithmetic; it says nothing about adverse selection.
13. **5m vs 15m**: std0 does 64% of its volume on 5m — that's where the rebate is. 15m is easier (less pickoff, fewer on-chain ops) but carries a smaller pool. The right comparison needs 5m + latency (fast poll, spot-anchoring) + a quota-free wallet.
14. **Latency**: btc-5m moves 5.4¢/3s, so a 2-3s poll leaves stale quotes exposed to pickoff. Sub-second polling is the baseline; colocation is not permitted from France.

## The open question
**Adverse selection vs rebate: is it net-positive for a small replica?** std0 proves it is positive AT ITS SCALE; whether a small, slower execution keeps the spread ≈ breakeven is not established here.
