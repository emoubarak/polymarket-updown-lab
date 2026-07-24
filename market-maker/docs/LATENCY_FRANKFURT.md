# Results — BTC feed speed optimization from Frankfurt

> AWS VPS **eu-central-1b (Frankfurt)**, `3.79.190.157`. Measurements 2026-07-03.
> Goal: capture the BTC price as fast as possible + verify that Polymarket
> trading is allowed from Frankfurt.

## TL;DR

- **Optimal feed found: Bitstamp `order_book_btcusd` = 10 ms median, 4 ms min,
  9.7 updates/s.** 5× faster than Coinbase, 12× faster than Binance.
- **Trading allowed from Frankfurt**: Polymarket auth OK, balance retrieved,
  IP not geo-blocked.
- **Frankfurt is justified only via Bitstamp** (co-located at AWS Frankfurt) —
  for the other feeds (Cloudflare), the region changes almost nothing.

## 1. Feed comparison (from Frankfurt, message latency)

| Feed | median latency | min | updates/s |
|------|----------------|-----|-----------|
| **Bitstamp order_book** | **10 ms** | **4 ms** | 9.7 |
| Bitstamp trades | 17 ms | 12 ms | 0.8 |
| Bitstamp diff_book | 19 ms | 18 ms | 3.1 |
| Coinbase matches | 47 ms | 45 ms | 8.5 |
| Coinbase ticker | 50 ms | 47 ms | 8.5 |
| Bybit | 82 ms | 81 ms | 2.4 |
| OKX | 114 ms | 98 ms | 6.2 |
| Binance spot | 118 ms | 113 ms | 47.8 |
| Coinbase advanced-trade | 139 ms | 68 ms | 10.2 |

## 2. Key lessons

1. **The CHANNEL matters more than the feed.** Bitstamp order_book (10 ms)
   vs Bitstamp trades (17 ms) vs diff (19 ms): same exchange, a 2× gap
   depending on the channel. The full order_book is both the fastest AND
   the densest.

2. **Frankfurt vs Dublin: marginal gain except for Bitstamp.** Latency is
   dominated by the exchanges' **internal processing**, not the network.
   Coinbase (Cloudflare) is identical everywhere (~50 ms). Only Bitstamp,
   physically at AWS Frankfurt, benefits from co-location (32 ms Dublin →
   10 ms Frankfurt).

3. **Binance was the dead weight**: 118 ms (streaming servers in Tokyo).
   It was the real cause of the "slow trader" problem on Dublin.

4. **We are at the accessible frontier**: 4 ms min = pure intra-datacenter
   network. Going below that requires a FIX/pro feed (institutional
   account) or dedicated hardware (kernel bypass, FPGA) — not our turf.

## 3. Strategic bonus: Bitstamp ≈ a Chainlink source

Bitstamp has historically been one of the sources of the **Chainlink
BTC/USD** price, which is **the settlement price of Polymarket's btc-updown
markets**. Using Bitstamp therefore doesn't just give us a fast feed — it
brings us closer to the **settlement source of truth**. Double advantage.

## 4. Trading allowed from Frankfurt ✅

`./rebate -balance` from `3.79.190.157`:
- L2 auth (HMAC) accepted, `balance:68713747` ($68.71) returned, allowances OK.
- The CLOB API does not geo-block the Frankfurt IP (same as Dublin). Order
  placement will go through (same auth barrier as the GETs, which work).

## 5. Next step: integrate the Bitstamp order_book into the bot

The bot currently uses Binance (118 ms) as its fast price feed. Rewiring it
to the **Bitstamp order_book** = going from 118 ms to 10 ms of feed latency
→ gaining ~108 ms of anticipation on every move. This is the lever that
transforms the adverse selection (see `JOURNAL.md`).

VPS config: SSH alias `fra`, Go + venv installed, bot compiled, `bbe3` key
in place.

## 6. Integration done & confirmed (2026-07-03)

`bitstamp.go` client added (order_book, same interface as Binance, 64KiB
buffer). Wired into the pricing loop: Bitstamp is preferred over Binance as
the fast signal whenever it is fresher, with automatic Binance fallback.
Config: `USE_BITSTAMP=yes` (default), `BITSTAMP_URL`.

Confirmed in dry-run from Frankfurt:
```
cl=61939.45(0.3s)  bn=62004.01(0.1s)  bs=61944.76(0.0s)
```
- **bs (Bitstamp) is the freshest** (0.0s vs bn 0.1s vs cl 0.3s).
- **bs tracks cl** (5-point gap) vs **bn offset by 65 points**
  (BTC/USDT ≠ BTC/USD) → Bitstamp predicts the settlement oracle far better
  than Binance.

No real trades executed (dry-run only, as requested).
