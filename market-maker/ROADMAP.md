# ROADMAP — status as of 2026-07-02 03:30 UTC (bot STOPPED on user's order)

## Status

- `rebate-live.service`: **stopped + disabled**. No orders on the book, no
  significant open position (~$5 in automatic settlement).
- Equity: ~$133.5 (CLOB balance ~$128 + residuals) vs $157.80 at the start.
- Code: iteration 10 + Phase A (30s opening silence, no taker flatten,
  T1=60s), tested (`go test -race` green), deployed on the VPS.
- Overnight rebates (~$20 of maker fee-equivalent volume): credited at the
  next daily payout (~00:10–00:45 UTC).

## What the night proved

1. The plumbing is production-grade: 2 adversarial audits incorporated,
   3 classes of race conditions discovered LIVE and fixed
   (tombstones, resync merge, REST resolution of orphan fills),
   kill switch, restart seeds, self-healing heartbeat.
2. Trading alone oscillates around zero with a negative tail in illiquid
   chop (+$10.2 / 11 windows in a favorable regime, −$16 / 10 windows
   in overnight chop).
3. The winning bots' model (see the std0 analysis in the project memory):
   **trading at zero by design, profit = rebates on volume**, made
   possible by the pre-window SPLIT that neutralizes directional risk.

## Phase A (deployed, not yet observed over time)

std0's temporal discipline: silence during [0,30)s, no new exposure
below T=60s, carry pairs to settlement (never pay the exit spread).
To be observed over 12+ windows in a liquid regime (EU/US hours).

## Phase B (designed, AWAITING DECISION)

Replicate the std0 architecture:
1. **Pre-window SPLIT**: mint complete pairs via the Safe proxy.
   BLOCKING prerequisites: either the Polymarket relayer (undocumented API,
   to be reverse-engineered), or a direct Safe `execTransaction` — which
   requires **POL for gas on the EOA (0xe8dcf1..., currently 0 POL)**.
2. Redeem loop (+70s post-expiry) → collateral recycling.
3. Multi-series (eth/sol 5m+15m) to multiply rebate volume.
4. Capital: std0 runs with ~$8.5k; at $133 the rebate expectation is
   symbolic. **Capital scaling is the dominant variable.**

## To restart

```bash
sudo systemctl start rebate-live        # Phase A config, caps 5/6/$15
ssh rebate-vps 'bash ~/rebate/run_live_window.sh'   # one $5 test window
```
