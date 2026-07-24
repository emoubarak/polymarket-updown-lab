# std0 reverse-engineered — scalpel map (2026-06-29)

Source: 16 480 data-api `/activity` rows over 10.2h (09:36→19:50 UTC), proxy `0xdf79…3dcd`, time-paginated
via `end=<ts>`. Tool: `research/std0/reverse_std0.py`. Daily rebate cross-checked via `?type=MAKER_REBATE`.

## 1. Markets it targets — EXACTLY
Crypto Up/Down ONLY, frames **5m + 15m ONLY**, 4 assets. In ~EVERY window of each (123/123 5m windows in 10h).
**No doge, no bnb, no events, no other markets.**

| market | vol share | | market | vol share |
|---|---|---|---|---|
| **btc-5m** | **64.3%** | | eth-15m | 3.0% |
| btc-15m | 15.3% | | xrp-15m | 2.1% |
| eth-5m | 6.2% | | sol-15m | 1.5% |
| xrp-5m | 3.9% | | sol-5m | 3.7% |

→ **btc-5m is the whole game (64%+15% btc = 80%)**. btc is where the taker flow (⇒ the rebate pool) is.

## 2. Mechanics — how it operates each window
- **MINT ~49s BEFORE window open** (median −49s), block = **1000 sets median (1000–2500)**, self-submitted from its EOA (no relayer/quota). Pre-positioned ask ammunition.
- **Two-sided tight maker quotes near 0.5**, small clips, refreshed fast (sub-second–few s), **fills front-loaded** (peak +30–90s into the 300s window, taper to ~0 by +270s = stops ~30s before settle).
- **Sells only ~11% of the minted block** (gross), **buys back ~74% of what it sells** → it CHURNS for fills, net-offloads ~3% of the block. Holds ~89% as a matched block.
- **Never merges; lets the block AUTO-REDEEM at settle** (redeem ≈ mint, net-zero round-trip). [Our Safe auto-redeems too.]
- **Stays ~delta-neutral**: median ending |imbalance|/mint = **2.0%** overall (btc ~5–6%, eth/xrp/sol ~1.5–2%).

## 3. How it enters Yes/No (size-weighted avg fill px, 10h)
| side | Up(Yes) | Down(No) |
|---|---|---|
| SELL | 0.480 | 0.469 |
| BUY | 0.574 | 0.619 |

Raw "buys high / sells low" is a **cross-window artifact** (it sells when a side is cheap/balanced, the small
buy-backs cluster when it rallied). It is NOT directional alpha. The real per-instant economics live in §4.

## 4. Does it lose in its windows? — per-window trading P&L (ex-rebate), 648 settled windows
| market | win | med P&L | mean | %neg | med\|imb\|/mint |
|---|---|---|---|---|---|
| btc-5m | 122 | +22.98 | +7.12 | 39% | 5.8% |
| btc-15m | 40 | +7.49 | +7.43 | 38% | 4.6% |
| eth-5m | 122 | +1.00 | +0.36 | 44% | 1.9% |
| xrp-5m | 122 | +0.99 | +1.20 | 41% | 1.2% |
| sol-5m | 122 | +0.89 | +0.26 | 43% | 1.5% |
| xrp-15m | 39 | **−2.08** | −3.03 | 65% | 1.8% |
| **ALL** | 648 | +1.20 | +2.09 | **43%** | 2.0% |

**It loses in ~43% of windows** (xrp-15m net-negative). Matched-sold **overround = +1.0¢/set median** (sells a
synthetic set for >$1 = a small risk-free skim).

### The rigor verdict (outcome-split — median P&L by which side won)
| market | Up-won | Dn-won | reading |
|---|---|---|---|
| **btc-5m** | **+1.95** | **+31.71** | ASYMMETRIC ⇒ directional, not neutral |
| **btc-15m** | **−12.46** | +20.41 | loses if Up wins ⇒ pure directional |
| eth-5m | +0.34 | +1.44 | ~symmetric ⇒ clean neutral edge |
| xrp-5m | +1.42 | +0.70 | ~symmetric ⇒ clean neutral edge |
| sol-5m | +1.06 | +0.89 | ~symmetric ⇒ clean neutral edge |

**btc's big +$16–23/window is a SESSION-DIRECTIONAL ARTIFACT** (std0 ran a 5–6% net-short-Up residual on btc;
btc fell this 10h ⇒ it looks profitable, but it would have LOST if Up won — see btc-15m −$12.46). **NOT a
repeatable edge.** The clean, repeatable trading edge is the **overround on the neutral markets ≈ +$1/window**,
positive whichever side wins. **The dominant, reliable income is the REBATE, not the trading.**

## 5. The income — verified rebate (the actual engine)
`/activity?type=MAKER_REBATE` (paid daily ~00:45 UTC, wallet-aggregate, $1 min): **$702–$1120/day** this week.
`TAKER_REBATE` (~00:10 UTC): **$294–$600/day**. Combined **~$1.0–1.7k/day**. = `0.014·p(1−p)` per FILLED maker
share (20% of the 0.07 taker fee), max at p=0.5. Crypto Up/Down is NOT in the Qmin liquidity-rewards pool
(`rewardsMinSize=50` is vestigial). No KYC/registration — quote + get filled near 0.5 = earn.

## 6. Synthesis — what std0 actually IS
A **rebate-farming engine disguised as a delta-neutral MM**: mint a big block pre-open → churn tight two-sided
fills near 0.5 to MAXIMIZE filled maker volume (the rebate base) → stay ~neutral → auto-redeem at settle. The
TRADING is ~breakeven-to-slightly-positive (overround skim ~+$1/window on neutral markets; btc's apparent edge
is directional session-luck). **~75% of profit = the daily rebate.** The whole skill is: get filled a LOT near
0.5 while NOT bleeding the spread/adverse-selection faster than the rebate pays.

## 7. Implications for US (why our btc-5m run bled −$10.6 in ~20min)
- The rebate is REAL and our Safe is eligible (§5 + a historical $0.03 MAKER_REBATE row).
- BUT btc-5m is the HARDEST place to stay neutral + un-picked-off:
  - std0 mints **1000 sets** ⇒ 5% imbalance = 50 shares = many clips of slack. We mint **80** ⇒ one 8-share
    clip = 10% imbalance ⇒ we breach neutrality in ONE fill ⇒ directional bleed.
  - std0 re-quotes **sub-second**; we poll **2s** ⇒ adverse selection on fast btc-5m.
  - Result: −$10.6 marked bleed > the ~$1.1 rebate earned. The kill_loss=12 backstop fired correctly.
- **The hinge is execution, exactly as the autopsy warned.** To replicate net-positive we must either:
  1. Start on the CLEANER markets (eth/xrp/sol-5m or the 15m frame) where adverse selection is gentler and
     the per-window trading is genuinely ~neutral (both outcomes +), accepting a smaller rebate pool; and/or
  2. Scale the block (finer neutrality) + speed the re-quote (sub-second, co-located) + spot-anchor to cut
     pickoff — i.e. actually match std0's machine before touching btc-5m at size.
- **Do NOT re-run the naive btc-5m/mint-80/clip-8/poll-2s config — it is a measured money-loser for us.**
