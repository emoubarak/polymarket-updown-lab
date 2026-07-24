---
name: results
description: Status report of the strategies running on the VPS — PnL, trades, failure-mode diagnosis from the journals. Use when the user asks "where are we", "the results", "check the VPS".
---

# VPS results report

## Collection

Lineup = the zlead race: 4 variants (`zlead`/`zleadmk`/`zleadx`/`zleadn`) × 6 coins (btc/eth/sol/xrp/doge/bnb) × 2 frames (5m/15m), + events + copy-mirror. State dirs: `state_<variant>_<coin>_<frame>`.

```bash
# Simplest: everything in one call (lineup-agnostic, computed metrics)
curl -s http://<VPS_IP>:8420/all

# States + journals for a subset (e.g. the 15m majors)
ssh <vps-user>@<VPS_IP> 'cd ~/pmlab && \
  for d in state_zlead_btc_15m state_zlead_eth_15m state_zleadn_btc_15m; do \
    echo "=== $d ==="; cat $d/state.json; tail -12 $d/journal.csv; done'

# Run duration per folder
ssh <vps-user>@<VPS_IP> 'cd ~/pmlab && \
  for d in state_*; do echo "$d: $(head -2 $d/equity.csv | tail -1 | cut -d, -f1) -> $(tail -1 $d/equity.csv | cut -d, -f1)"; done'

# Previous runs + correlation
curl -s http://<VPS_IP>:8420/history
curl -s http://<VPS_IP>:8420/correlation-data
```

## Reading the journals — what matters

- **Tripwire (THE decisive signal)**: realized win-rate **vs average entry price**. If win ≤ price over ~150 trades, no edge → retire. The only judge.
- **~1:9 asymmetry**: each `SETTLE_LOSS` (−the stake) wipes out ~9 `SETTLE_WIN` (+~0.11/$). A tight cluster of losses = choppy regime, not necessarily a bug.
- **REAL fees**: the taker fee IS charged on-chain = `0.07 × (1−p) × amount staked` (verified 2026-06-29 on 226 rows, error in the 3rd decimal; = 0.07×p(1−p)×shares). That is 0.35% of the stake at p=0.95, 0.70% at p=0.90, 1.05% at p=0.85. **% of the stake, INDEPENDENT of bet size** (making the bet bigger dilutes nothing). Maker fee = 0. Real break-even ≈ `win > price + ~1 point` (fee + half-spread ~0.5¢). So the "win ≤ price" tripwire must be read as "win ≤ price + 1 pt".
- **z-floor / variants**: `zlead` (z≥1, band 0.85–0.95) vs `zleadn` (band 0.85–0.90, where the premium concentrates) vs `zleadx` (z≥1.5, fewer bets) vs `zleadmk` (maker entry — judge maker fill < mid + adverse selection, not win-rate alone).
- **15m > 5m, and alts variable**: on real on-chain money, btc-15m & eth-15m are net positive; 5m reprices too fast (live-negative), thin alts often lose. A negative 5m/alt PnL is expected, not a surprise. Split by coin/frame — the aggregate averages winners and losers.

## Comparing two variants/configs — ALWAYS go to the end (never stop at the aggregate)

The per-variant aggregate LIES: two variants don't enter the same windows (different bands/thresholds), so comparing their total PnLs mixes different baskets. `/all` doesn't give the trades → pull the **journals** (`cat state_*/journal.csv`, BUY = entry price/direction, SETTLE_WIN/LOSS = outcome/pnl), key by **slug** (= window), and do the RIGOROUS comparison, per coin AND per frame:

1. **Common windows** (both enter): count, check outcome agreement (`ok=n` expected — same favorite, same resolution → only the entry differs), compare the **PnL of each on the SAME windows**. That's the only apples-to-apples.
2. **Solo buckets**: what A takes that B refuses (e.g. the deep 0.90–0.95 band zleadn discards) — n, win, price, PnL, edge. That's where the differentiating effect lives.
3. **Reconcile**: `total(A) = common(A) + A-only` must land back on the runner's PnL (proof the decomposition is right).
4. **Per coin**: don't conclude "variant X better" globally — one effect can be universal (e.g. the cheaper entry wins everywhere on common windows) AND another coin-specific (e.g. the deep band is +EV on doge/bnb, −EV on btc/eth/sol/xrp-5m). Give the verdict coin by coin.
5. **Significance**: SE(win) ≈ √(w(1−w)/n); a thin edge (~+1 pt) on n<400 is often indistinguishable from 0 → say so. Cells with n<~30 = noise, flag them.

Principle: go to the end of the verification with the data we have (full journals), not stop at the first aggregate metric.

## Reporting

Table per runner (settled PnL, trades, win-rate **vs average price**, one-line diagnosis), grouped by coin/frame, cumulative total, duration, `/history` comparison. Always note that a few hours ≈ noise (~12 windows/h on 5m, ~4/h on 15m). Only propose code changes if a pattern is clear; don't apply them without a request.
