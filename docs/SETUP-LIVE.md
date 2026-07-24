# Going live — arming guide (Polymarket CTF Exchange V2)

> **UPDATE 2026-06-20 — Polymarket V2.** The April 28, 2026 upgrade migrated the
> collateral from **USDC.e → pUSD** (backed 1:1 by native USDC) and changed the
> contracts. The code is now on **`py-clob-client-v2`**. Consequences for you:
> you fund with **native USDC** (no more USDC.e needed), Polymarket converts it
> to **pUSD**; the bot performs **no blind on-chain transactions** (wrapping and
> claiming winnings happen in the Polymarket UI, the allowance via the official
> client); a bug can therefore only get an order *rejected*, never move funds.
> The real pilot arms the **`zlead` family** (the same edge as paper, via the
> shared entry rule `entry.EntryGate`), controllable from the dashboard's
> "Real pilot" tab.

⚠️ **Read this in full.** This folder contains the only code in the project that
sends real orders with real money. By default it is **disarmed**: without the
environment variables below, `run_live.py` runs indefinitely in **DRY-RUN**
(it logs the exact order it would send, without sending anything).

---

## First things first: the discipline prerequisite

The `zlead` edge is real but **unconfirmed at small n**: on most (variant, coin,
frame) pairs, the live sample is still short, and statistically "no edge" is not
ruled out. The system is therefore wired to **size small until the proof is in**:

- the stake = **a fraction of capital (default ~10%)**, capped at the coin's
  real favorite-book depth (`--bet-max`) **and** at the hard \$300/order cap;
- it **de-leverages on its own** in a drawdown (capital ↓ → stake ↓);
- a **tripwire** (`AdaptiveStake`) cuts to zero if, over ~150 trades, the
  realized win-rate falls to/below the average price paid = edge death
  (coagula's death).

**Honest recommendation: let it run in DRY-RUN first**, compare the dry-run
journal against the fills it *would have* had, and only arm after that — and
with capital you are willing to lose (the pilot starts at **\$20**). The 15m
(especially BTC/ETH) is the frame validated in forward-testing; the 5m is
backtest flattery (see `research/FINDINGS.md`).

---

## 1. The wallet (the decision that changes the config)

Polymarket has two account types. **Identify yours**:

| Your account | `POLY_SIG_TYPE` | `POLY_FUNDER` | Key |
|---|---|---|---|
| **Polymarket V2 account** (created on the web — email/Magic OR wallet): funds in pUSD in a *deposit wallet* (ERC-1967 proxy) | **`3`** (POLY_1271) | your **"Deposit address"** = the deposit wallet's address (UI: Deposit) | the exported key (UI/Magic) or the MetaMask key that signs |
| Raw **EOA wallet**, funds in pUSD that you wrap yourself | `0` | (empty) | that wallet's private key |

> **Validated in prod 2026-06-20.** For a V2 web account, it's `sig_type=3` (the
> "deposit wallet flow": ERC-1271 signature) + `funder` = your Deposit address.
> With `sig_type=2` the order is rejected `maker address not allowed` and the
> balance reads \$0 (wrong wallet). The `py-clob-client-v2` client handles the
> 1271 signing by itself once `sig_type=3` + `funder` are correct.
> (`POLY_SIG_TYPE` has no safe default in code: it is `0` if you don't export
> it — so **you MUST set `3` explicitly for a web account**, otherwise the
> client signs as an EOA and the order is rejected.)

👉 **On V2, the simplest option is the Polymarket UI account**: you deposit ~\$20
of **native USDC** via the UI, which handles the **wrap to pUSD** *and* the
allowances — the bot only has to post orders. An EOA also works (`sig_type=0`)
but you would then have to wrap USDC→pUSD yourself (the *Collateral Onramp*
contract), a notch more technical. To retrieve your key: polymarket.com →
Settings → Export Private Key (and note your "Deposit address" = the funder).

Whichever type: **use a dedicated account/wallet** containing *only* this bot's
stake (~\$20). Never your main account — the key lives on the server, so its
blast radius must be bounded to what you accept losing.

---

## 2. Installation (production machine only)

```bash
python3 -m venv .venv-live && . .venv-live/bin/activate
pip install -r requirements-live.txt
```

Paper trading (the runners on the VPS) **does not touch** this and remains pure
stdlib.

---

## 3. Test the loop WITHOUT money (do this first)

```bash
python3 run_live.py --strategy zlead --interval 15m --underlying btc --start 20 --ticks 20 --poll 8
```

Without env vars → DRY-RUN: the loop discovers the market, applies the `zlead`
rule (extreme favorite + lead floor `z`, shared with paper via
`entry.EntryGate`), computes the clip via weighted sizing, and **logs** the
order it would send. No heavy dependency required for this mode (the book comes
from the public feed). Verify that entries/settlements make sense in
`live_state/journal.csv` before going any further.

---

## 4. Arm (real money)

**Simplest: drive it from the dashboard** ("Real pilot" tab → DRY-RUN then
"Arm REAL"). The dashboard loads `~/pmlab/.live_env` (the key) and launches the
armed pilot for you (`run_live.py … --weighted`). Otherwise, manually:

```bash
. .venv-live/bin/activate               # the V2 client lives here
export POLY_PRIVATE_KEY=0x...           # NEVER committed, never logged
export POLY_SIG_TYPE=3                  # 3 for a V2 web account; 0 for an EOA
export POLY_FUNDER=0xYOUR_DEPOSIT_ADDRESS # your Deposit address (web V2); empty for EOA
export POLY_LIVE=1
export POLY_CONFIRM=I_UNDERSTAND_REAL_MONEY

python3 run_live.py --strategy zlead --interval 15m --underlying btc \
        --start 20 --weighted --weight-pct 0.10 --bet-max 1295
```

On the first armed launch, the bot **approves the pUSD allowance** for the CTF
Exchange V2 (once, via the official client — no web3), then trades. Without
**both** `POLY_LIVE=1` and `POLY_CONFIRM=...`, it refuses to post — period.
(No more `POLY_RPC`: redemption happens in the UI, see below.)

---

## Built-in guardrails (reminder)

- **Hard cap \$300/order** (`MAX_CLIP_USD`) — whatever the sizing asks for.
- **Stake capped at the coin's favorite-book depth** (`--bet-max`, measured
  live): we never pretend to fill more than the book contains.
- **Refusal below \$5 of pUSD balance**; refusal if the ask has run away > 2c
  above the favorite.
- **Tripwire**: win-rate ≤ average price paid over 150 trades → clip 0, stop.
- **De-leverage in drawdown**: since the stake is a % of capital, it shrinks by
  itself when capital falls (no separate brake to tune).
- **No automatic regime pause**: disabled on the live pilot — the operator
  arbitrates pauses (the dashboard's Stop button). The rolling-EV pause was
  triggering on ordinary variance (losses not autocorrelated + asymmetric
  payoff) and **froze itself** (clip 0 → no more settled trades → window never
  refreshed). The edge-death tripwire, however, stays active.
- **One position at a time**, held to settlement (never churn). The first taker
  fill may be depth-limited; the bot **tops it up** toward the target stake on
  the same window.

> **Fees: zero.** The `0.07 × p × (1−p)` flagged in the Polymarket metadata is
> **never charged on-chain** (verified 2026-06-26 on raw transaction receipts;
> the data-api displays it for nothing). The only real entry cost = the
> half-spread (~0.5¢ in calm conditions), slippage ~0 at the favorite's depth.

## Realizing a gain (redemption) — on V2, in the UI

A winning position holds tokens worth $1 each. **On V2, the bot does NOT do
on-chain redemption** (the collateral is pUSD, settlement is in native USDC,
and the collateral argument of `redeemPositions` is too uncertain to wire
blindly). Instead, it logs the gain and **you claim it in the Polymarket UI**
(or Polymarket auto-redeems it). The value is **safe** in the position token
until the claim — nothing is lost. For a \$20 pilot, manual claiming in the UI
is perfectly sufficient.

## What this system is not

Not an annuity. A disciplined instrument to **discover whether the edge is real
at real scale while protecting capital** — and to get you out cleanly
(tripwire, book-capped stake) when it isn't. The math *implies* growth;
reality (regime, adverse selection to size, arbitrage, book liquidity) will
decide. Start small, read the journal, only size the proof.
