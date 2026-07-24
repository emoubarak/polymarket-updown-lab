# FINDINGS — discoveries, blockers, lessons (chronological)

The journal of everything we learned during R&D (session 2026-06-28). So we don't repeat the same mistakes.

## Decoding std0
1. **std0 is NOT a price bettor** — it's a rewards harvester (see STRATEGY). First hypothesis "sell-side overround" → FALSE. It's the rebate.
2. The rebate = `0.014·p(1−p)`/filled share. Objective = **maker volume filled near 0.5**.
3. std0 mints directly (EOA), never merges, redeems at settle. ~78 fills/window btc-5m.

## On-chain pipeline (what works)
4. **mint/merge/redeem via the builder relayer work** (with the 019f0b1b builder creds). Batching N calls into 1 action = ~0.4s.
5. **Direct EOA mint works** (eoaops) — the EOA just needed pUSD (the split pulls the pUSD from msg.sender). Zero quota.
6. **Factory-proxy mint works** (factory.proxy([split]) on 0xaB45 → 0xE138) = std0's exact architecture. Zero quota.

## THE WALLS (in the order we hit them)
7. **The CLOB rejects the bare EOA (sig 0) and the factory-proxy 0xE138 (sig 2)**: *"maker address not allowed, use the deposit wallet flow"* — not registered to the account. Only the registered deposit-wallet (0x32D3/0x09CE) is accepted.
8. **The relayer QUOTA** (`429 quota exceeded, resets ~1200s`) is THE killer: the harvester spammed merge every tick → quota exhausted → merges fail → inventory stuck → **false "bleeding"**. Fix: cooldown + merge 1×/window. But the quota remains insufficient for the throughput (especially 5m).
9. **The relayer blocks arbitrary `setApprovalForAll`** ("operator not in allowed list") → no return bridge deposit→EOA.
10. **The UI onboarding (even with MetaMask) gives a POLY_1271 = relayer-only**, NOT a self-submittable Gnosis Safe. Confirmed by the Polymarket docs. The browser wallet did NOT remove the quota → it was the same type as the old one.
11. **The SOLUTION = deploy a Gnosis Safe** (`deriveSafe` + `RelayClient.deploy`): self-submit (execTransaction) + accepted at the CLOB (sig 2). That's where we stopped.

## Harvester execution lessons
12. **The CLOB has a 5-share min order.** clip 3 → every order rejected → 0 fills (this confounded a "0 fills breakeven" measurement).
13. **The inventory cap must gate asks AND bids** (not just bids) — otherwise the asks drain one side → directional 14/0. Fixed.
14. **Neutrality requires a BIG block**: min-5-shares = directional steps of 5-10 shares. On a $10-40 block = 30-100% → it bleeds. std0 $2500 → 0.4% → neutral. **It's a SIZE/CAPITAL artifact, not adverse-selection.**
15. **5m vs 15m**: I COMPROMISED by going 15m (less pickoff + fewer quota ops). But **std0 does 64% on 5m** — that's where the rebate is. The right move = 5m + solve latency (colocation, fast poll, spot-anchoring) + the quota (self-submit). Do NOT flee the 5m.
16. **Latency**: btc-5m moves 5.4¢/3s, our 2-3s poll → stale quotes → pickoff (60 crosses). **We have colocation (Dublin box, 24ms) → sub-second poll.** I wasn't exploiting it.
17. **`systemd-logind` kills processes launched in an SSH session** → the runners died in ~60s (not a crash). Fix: `enable-linger` + cron-watchdog (out-of-session).

## Method lessons (user feedback — IMPORTANT)
18. **Don't conclude "it's bleeding" from confounded accounting** (unrecycled mint = claimable value, not a loss). Always measure the **total wallet value** (pUSD + positions).
19. **Don't compromise** when the objective is clear — attack the real problems (latency → colocation, quota → self-submit), don't work around them.
20. **When I don't know, SEARCH the internet** (that's how we found the `safe-wallet-integration` repo and the Safe path).
21. **Be very careful with real capital**: always ONE single runner (my double-launches created conflicts), tight kill-switch, check balances before/after.
22. **The R&D cost ~$10 of the $50 burner** (directional bug + marginal spread) — acceptable given what it decoded. Current capital ~$118, recoverable.

## The still-OPEN question (to settle on resumption)
**Adverse-selection vs rebate: is it net-positive for US?** All past measurements were confounded (quota/min-size/cap bug, now fixed). We need a CLEAN run on the Safe (zero quota) + big block + 5m + fast poll, and to measure wallet value. std0 proves it's positive AT ITS SCALE; it remains to confirm that our execution keeps the spread ≈ breakeven.
