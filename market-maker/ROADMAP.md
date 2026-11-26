# ROADMAP — research directions

This is a research codebase with a Go paper simulator (`paper.go`). It also
draws on the public on-chain activity of a reference market maker (std0); see
`docs/JOURNAL.md`.

## What is in place

1. The plumbing: two adversarial audits incorporated; race conditions in
   order tracking found and fixed (tombstones, resync merge, REST resolution
   of orphan fills); kill switch, restart seeding, self-healing heartbeat.
2. The model of the reference market maker (std0, see `docs/JOURNAL.md`):
   trading at roughly zero by design, profit coming from maker rebates on
   volume, made possible by the pre-window SPLIT that neutralizes directional
   risk.

## Phase A (implemented in code)

std0's temporal discipline: silence during [0,30)s, no new exposure below
T=60s, carry pairs to settlement (never pay the exit spread).

## Phase B (designed)

Replicate the std0 architecture:
1. **Pre-window SPLIT**: mint complete pairs through the Safe proxy
   (`ctf.go`, `safe.go`, `evm.go`: calldata, Safe `execTransaction`,
   EIP-1559 transactions).
2. Redeem loop (+70s post-expiry) for collateral recycling.
3. Multi-series (eth/sol 5m+15m) to multiply rebate volume.
4. Capital: std0 runs with roughly $8.5k of capital; the rebate scales with
   capital, which is the dominant variable.

## Out of scope

Delta-neutral market making on a 5-minute binary is latency-bound and would
need colocation next to the venue (see the structural analysis in
`docs/JOURNAL.md`). Colocation is not permitted from France, so it was never
implemented.
