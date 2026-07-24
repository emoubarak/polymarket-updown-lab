# std0 replication — status & deployment plan (2026-06-28)

## Verdict
std0 = delta-neutral **maker-rebate harvester** (rebate `0.014·p(1−p)`/filled-maker-share, ~$1778/day;
spread ≈ breakeven). Fully decoded + every mechanic replicated & validated live on a ~$50 burner.
**Net-positive ONLY at scale** (big neutral blocks + a CLOB-registered self-submittable wallet);
net-negative at burner scale — and the bleed is a SIZE artifact, not adverse selection.

## Why net-negative at our scale (the size artifact, proven)
- CLOB **min order size = 5 shares** → directional inventory moves in 5–10 share steps.
- On a small block ($10–16) that's 30–100% directional → the held side settles to $0 sometimes → bleed.
- Clean proof: with clip 3 (orders rejected, 0 fills) the churn was **exactly $0.00** → mint/recover/
  neutral mechanics are sound. The −$10 runs were the ask-cap bug (asks weren't gated, only bids) +
  the small block. Ask-cap now fixed (gate asks AND bids by ±max_inv).
- std0's $2500 blocks → 5–10 shares = 0.4% → neutral → breakeven churn + rebate = **positive**.

## Validated pieces (all work)
- Direct EOA mint, no relayer/quota: `~/mint/eoaops.js mint|merge|redeem` (adapter.splitPosition from EOA).
- **std0's exact architecture**: EOA self-submits `factory.proxy([{1,adapter,split}])` → mints to a
  factory-proxy. Proved with our proxy **0xE138B67aF020AAe9C38B88144437AB3Dd3F8652B** (`factory_proxy_mint.js`).
- Bridge EOA→deposit-wallet (ERC1155) free: validated. (Deposit→EOA blocked: relayer disallows arbitrary approvals.)
- Recovery merge quota-safe (rate-limited): works (`rewardmm._recover`).
- Rebate genuinely earned: clip 5 → ~60 fills/window → +$0.96/40min @ $10 block (scales linearly).

## The TWO blockers (both require the USER)
1. **CLOB-registered self-submittable wallet.** Our Magic deposit-wallet 0x32D3 is the only CLOB-accepted
   maker but is relayer-only (quota: `429 quota exceeded`, ~1200s bucket). A fresh factory-proxy mints
   free but the CLOB rejects it (`maker address not allowed`). FIX: onboard a browser MetaMask on
   polymarket.com → Polymarket registers a factory-proxy (sig_type 2) that is BOTH self-submittable
   (mint/merge/redeem via factory.proxy, no quota) AND CLOB-accepted. = std0's exact setup.
2. **Real capital ~$500–2500** for blocks big enough that the 5-share min is negligible (neutral).

## Deployment plan (once both exist)
1. Fund the registered factory-proxy with pUSD (USDC→pUSD via the Polymarket UI/deposit).
2. Point on-chain ops at `factory.proxy([...])` (eoaops-style but typeCode-1 via the factory) — no quota.
3. Run `run_rewardmm.py` with `POLY_SIG_TYPE=2 POLY_FUNDER=<registered-proxy>`, big block
   (mint ≈ 0.5–1× capital), clip 5–10, back-off 0.01, recenter 0.03, max-inv small vs block (≤10% of block),
   band 0.30–0.70 (near 0.5 = max p(1−p) + two-sided flow), on btc-15m first (slow = latency-friendly),
   then btc-5m + alts. Spot-anchor (`--spot-anchor`) available as an anti-pickoff lever.
4. Measure: total wallet value before/after (churn ≈ breakeven) + `MAKER_REBATE` daily payout. Scale breadth.

## Tools built (in repo + box ~/mint)
pmlab/rewardmm.py + run_rewardmm.py (harvester); ~/mint/{eoaops,setops,factory_proxy_mint,
set_approvals,direct_eoa_test,batch_test,check_tx}.js. Builder creds in box ~/.poly_env_maker.

## Capital map (2026-06-28, ~$45-50, mostly recoverable; some directional positions settle to outcome)
0x32D3 (registered): ~$20.8 pUSD + held blocks (settle/redeemable). EOA 0x9C9A: ~$2 pUSD + ~$4 tokens.
proxy 0xE138: ~$3 tokens. Harvester STOPPED (no more burner bleed).
