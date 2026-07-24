# rebate — Polymarket BTC 5-Minute Up/Down Market Maker

Ultra-low-latency Go market maker for Polymarket's "Bitcoin Up or Down"
5-minute binary markets. Maintains one passive bid and one passive ask per
window, priced from `Phi(d2)` with EWMA volatility and Avellaneda–Stoikov
inventory skew. Runs on AWS eu-west-1 (Dublin).

## Verified venue facts (live, 2026-07-01/02)

| Item | Value |
|---|---|
| Resolution | Chainlink BTC/USD data stream; **Up wins on close ≥ open** |
| Settlement feed | RTDS `wss://ws-live-data.polymarket.com`, topic `crypto_prices_chainlink` (~1 msg/s) |
| Strike ("price to beat") | `polymarket.com/api/crypto/crypto-price?symbol=BTC&variant=fiveminute&...` → `openPrice`; equals previous window's close |
| Market slug | `btc-updown-5m-{unix}` with `unix % 300 == 0` (Gamma `/events?slug=`) |
| Tick / min size | 0.01 / 5 shares (`negRisk=false`) |
| Order protocol | **CLOB V2** (since 2026-04-28): 11-field EIP-712 order, domain version "2", exchange `0xE111...996B`. V1 / go-order-utils no longer accepted |
| Heartbeat | REST `POST /v1/heartbeats` every 5s with chained `heartbeat_id`; miss ~15s → all orders auto-cancelled |
| Fees | Maker 0; taker `0.07·p·(1−p)·shares` (crypto rate) |
| Rate limits | POST /order 5,000/10s burst, 120,000/10min sustained; throttled (queued), not rejected |

Signing (EIP-712 order digest + signature, ClobAuth L1, L2 HMAC) is
cross-validated byte-for-byte against `eth-account`
(`REBATE_PRINT_VECTORS=1 go test -run TestPrintSigningVectors -v` piped into
`verify_signing.py`), and validated against production via `--order-test`.

## Architecture

```
binance.go            wss btcusdt@bookTicker — fast LEADING signal only
polymarket_market.go  RTDS Chainlink mirror (settlement anchor S_t) +
                      Gamma window discovery + strike fetch + positions API
polymarket_book.go    CLOB market channel: live books both tokens (placement)
wireparse.go          zero-alloc JSON field extraction + atomic price cells
math_engine.go        Phi(d2), dual-EWMA vol (seeded prior), AS skew, endgame
                      regimes, CompetitiveBid/Ask book placement
inventory.go          fill-reconciled position; atomic reads on hot path
eip712.go             CTF Exchange V2 order signing, ClobAuth (decred+sha3)
polymarket_clob.go    L2 REST gateway, heartbeat, token bucket, cancels,
                      balance-allowance refresh
polymarket_userws.go  user channel: fills (MATCHED→…→CONFIRMED/FAILED)
risk.go               limits, LOUD kill switch, fee-aware liquidation planner
config.go             env-driven config; dry-run default
main.go               single-goroutine event loop owning ALL strategy state
```

Key invariants (post-audit):
- Order-op goroutines do HTTP only and report `{placed|rejected|unknown|
  cancelFailed}`; every registry/quote mutation is on the loop goroutine.
- Cancel failure aborts the replace (never double-quote); unknown post
  outcome → cancel-market + registry resync (only provable state).
- Fills: replay dedupe BEFORE registry lookup; unknown-order fills are
  deferred, never side-guessed; out-of-window fills dropped; dropped WS
  events trip the kill switch.
- Model fair is the LIMIT, live book is the PLACEMENT (top-of-book, improve
  by exactly one tick, post-only). Anti-pick-off guard pulls un-throttled
  when a resting quote loses its edge vs the skewed center.
- Sell-mode (shares as collateral, pair unwinding > $1) only after 12s
  settlement delay + venue balance-cache refresh.
- riskUSDC = usdcOut − min(up,dn): paired inventory is riskless; the cap
  bounds worst-case expiry loss, not turnover — that is what scales.

Pricing model: `d2 = ln(S/K)/(σ·√T)`, `mid = Φ(d2)`, seconds-based σ
(no drift term — intentional at this horizon). Quotes:
`center = mid − λ·I`, bid/ask = center ∓ half-spread, tick-rounded outward.

Endgame (explicit regime switch, independent of the AS term):
- `T < 10s` and `|d2| < 2` → **pull both quotes** (gamma flip risk)
- `T < 10s` and `|d2| ≥ 2` → **defensive** (3× half-spread)

Both sides are implemented as BUYs (bid = buy Up @ p, ask = buy Down @ 1−p)
so only USDC collateral is needed; `I = up − down`. Orders are GTC +
`postOnly` — the bot can never accidentally take.

Requote gate (all must pass): theoretical moved ≥ `REQUOTE_MIN_MOVE` (1 tick)
· ≥ `REQUOTE_INTERVAL_MS` since last requote on that side · token bucket
(burst 6, 3/s) · no in-flight order op on that side.

## Running

```bash
# on the VPS
source ~/.poly_env_bbe3        # POLY_PRIVATE_KEY / POLY_FUNDER / POLY_SIG_TYPE
./rebate                       # DRY-RUN (default): full pricing, no orders
REBATE_LIVE=yes ./rebate -order-test   # one 5¢ post-only order: post→rest→cancel
REBATE_LIVE=yes ./rebate --live        # LIVE trading (both gates required)
```

Key env knobs (defaults in parentheses): `QUOTE_SIZE` (5), `HALF_SPREAD`
(0.02), `LAMBDA` (0.0004), `MAX_POSITION` (50), `MAX_OPEN_NOTIONAL` (25),
`REQUOTE_MIN_MOVE` (0.01), `REQUOTE_INTERVAL_MS` (500), `TAKER_FEE_RATE`
(0.07).

Tests (race detector on): `go test -race ./...`
Benchmark: `go test -run='^$' -bench=. .` → full pricing path ~114 ns, 0 allocs.

## Build/deploy loop

Developed locally, built/tested on the VPS (`rebate-vps` SSH alias):

```bash
rsync -az --exclude .git ./ rebate-vps:~/rebate/
ssh rebate-vps 'cd ~/rebate && export TMPDIR=$HOME/gotmp GOTMPDIR=$HOME/gotmp \
  PATH=$PATH:/usr/local/go/bin && go test -race ./... && go build -o rebate .'
```

(`/tmp` on the VPS is a tiny tmpfs — keep Go's temp dirs on `$HOME`.)

## Safety properties

- Dry-run default; live needs `--live` AND `REBATE_LIVE=yes`.
- Kill switch is one-way per process; trips on strike/oracle divergence >1%.
- Quote gate blocks on: stale Chainlink (>8s) / stale Binance (>5s) /
  3 consecutive heartbeat failures / cold vol / unknown strike / caps.
- Position caps are one-sided (long cap kills the bid, not the ask), so the
  bot can always quote its way back toward flat.
- Liquidation is fee-aware (`0.07·p(1−p)`): never crosses unconditionally;
  holds near-worthless positions to resolution instead of paying fees.
- Shutdown: cancel-all; if that fails, heartbeat lapse kills orders in ~15s.
- Inventory truth only from user-channel fills (dedupe by trade id, reversal
  on FAILED); REST resync of open orders on every WS reconnect.

## Known limitations / next steps

- Dry-run does not simulate fills (no market-book subscription yet), so it
  validates pricing/plumbing, not expected P&L.
- Strike sanity check is against our RTDS view at fetch time (1% band);
  a Polygon RPC Chainlink read would be a stronger independent source.
- `crypto-price` strike endpoint is undocumented (reverse-engineered from
  the site bundle); watch for schema drift.
- Maker rebates (20% pool on crypto markets) not modeled in P&L.
- Tick size can change dynamically near price extremes (`tick_size_change`
  event on the market WS channel) — currently only read at window fetch.
