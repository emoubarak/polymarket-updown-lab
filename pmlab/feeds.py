"""Market data feeds: Binance spot (BTC reference) + Polymarket Gamma/CLOB.

All endpoints are public / read-only — no API key needed for paper trading.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import dataclass, field

import requests

from .coins import SYMBOL   # coin→Binance symbol map (THE single registry: pmlab/coins.py)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
BINANCE = "https://api.binance.com"
DATA_API = "https://data-api.polymarket.com"   # per-wallet on-chain activity feed

INTERVALS = {"5m": 300, "15m": 900, "1h": 3600}

# SYMBOL (coin→Binance proxy symbol) now lives in the single coin registry
# (pmlab/coins.py) and is imported above — each window settles on its own Chainlink
# oracle so the proxy MUST use the matching symbol. See coins.py for the per-coin notes.

_session = requests.Session()
_session.headers["User-Agent"] = "pmlab/0.1 (paper-trading)"


def _get(url: str, **params) -> object:
    r = _session.get(url, params=params or None, timeout=10)
    r.raise_for_status()
    return r.json()


# ------------------------------------------------ Polymarket data-api ---

def wallet_activity(address: str, limit: int = 200) -> list[dict]:
    """Recent on-chain activity for a wallet, newest first (data-api.polymarket.com).
    TRADE rows carry side/size/usdcSize/price/outcome/outcomeIndex/asset/conditionId/
    slug/title/timestamp/transactionHash. Read-only; the copy-mirror runner uses it to
    follow a skilled wallet's threshold-market fills. Returns [] on any failure (the
    runner just retries next tick). The address MUST be the full 42-char proxy wallet —
    a truncated address silently returns the GLOBAL feed (garbage)."""
    try:
        rows = _get(f"{DATA_API}/activity", user=address, limit=limit)
    except requests.RequestException:
        return []
    return rows if isinstance(rows, list) else []


# ---------------------------------------------------------------- Binance ---

def btc_klines_1m(limit: int = 90, symbol: str = "BTCUSDT") -> list[dict]:
    """Last `limit` 1-minute candles: [{t, open, close}, ...] (oldest first).
    Name kept for back-compat; `symbol` selects the underlying."""
    raw = _get(f"{BINANCE}/api/v3/klines", symbol=symbol, interval="1m", limit=limit)
    return [{"t": k[0] // 1000, "open": float(k[1]), "close": float(k[4])} for k in raw]


def btc_spot(symbol: str = "BTCUSDT") -> float:
    raw = _get(f"{BINANCE}/api/v3/ticker/price", symbol=symbol)
    return float(raw["price"])


def btc_lead_fraction(symbol: str, window_start: int) -> float | None:
    """BTC's signed directional displacement over the window SO FAR = (spot-open)/open, for
    the BTC-align veto (the only signal that survived the deep 90-day OOS backtest: an ALT
    favourite is ~6pt more likely to LOSE when BTC's move opposes it). Returns None for BTC
    itself (no self-veto) or on any data gap — the gate then simply doesn't veto. SHARED by
    paper (runner._build_ctx) and real (run_live) so the rule can't drift (always-modularize)."""
    if symbol == "BTCUSDT":
        return None
    try:
        kl = btc_klines_1m(90, "BTCUSDT")
        bopen = next((k["open"] for k in kl if k["t"] == window_start), None)
        if not bopen:
            return None
        return (kl[-1]["close"] - bopen) / bopen
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None


def longshot_flow(condition_id: str, fav_outcome: str) -> float | None:
    """Σ of LONGSHOT-side (the NON-favourite outcome) BUY $volume in this window SO FAR, for the
    #5 longshot-flow fade veto. Heavy pre-entry longshot accumulation = informed upset bet → the
    favourite is ~3× more likely to LOSE (deep 90d backtest, alts). The caller divides by the coin's
    depth cap to get a self-normalized ratio. Returns None on failure (gate then doesn't veto).
    Pre-now is automatic: each {coin}-updown-{frame}-{ts} market = ONE window, so its trades are
    this window only. SHARED by paper + real (always-modularize)."""
    try:
        rows = _get(f"{DATA_API}/trades", market=condition_id, limit=1000)
    except requests.RequestException:
        return None
    if not isinstance(rows, list):
        return None
    ls = 0.0
    for t in rows:
        try:
            if t.get("side") == "BUY" and t.get("outcome") != fav_outcome:
                ls += float(t["price"]) * float(t["size"])
        except (TypeError, ValueError, KeyError):
            pass
    return ls


def btc_price_at(ts: int, symbol: str = "BTCUSDT") -> float | None:
    """Open of the 1m candle starting at `ts` (proxy for the oracle snapshot)."""
    raw = _get(f"{BINANCE}/api/v3/klines", symbol=symbol, interval="1m",
               startTime=ts * 1000, limit=1)
    if not raw or raw[0][0] // 1000 != ts:
        return None
    return float(raw[0][1])


def realized_vol_per_min(klines: list[dict], lam: float = 0.94) -> float:
    """EWMA vol of 1m log returns (fraction per sqrt-minute).

    Exponential weighting reacts to regime shifts within the window instead
    of averaging a vol spike away over 90 quiet minutes.
    """
    closes = [k["close"] for k in klines]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0]
    if len(rets) < 10:
        return 0.0
    var = statistics.pstdev(rets) ** 2          # seed with the simple estimate
    for r in rets:
        var = lam * var + (1 - lam) * r * r
    return math.sqrt(var)


# ------------------------------------------------------------- Polymarket ---

@dataclass
class UpDownMarket:
    slug: str
    question: str
    condition_id: str
    window_start: int          # unix, window open
    window_end: int            # unix, window close / resolution
    token_up: str
    token_down: str
    accepting_orders: bool
    maker_fee_rate: float = 0.0   # makers never pay (docs), kept configurable
    taker_fee_rate: float = 0.0   # crypto category: 0.07 × p × (1−p) × shares
    mid_up: float | None = None
    book_up: dict = field(default_factory=dict)
    book_down: dict = field(default_factory=dict)

    @property
    def tau_min(self) -> float:
        """Minutes remaining until resolution."""
        return max(0.0, (self.window_end - time.time()) / 60.0)


def window_bounds(interval: str, now: float | None = None) -> tuple[int, int]:
    sec = INTERVALS[interval]
    now = now or time.time()
    start = int(now // sec) * sec
    return start, start + sec


def fetch_updown_market(interval: str, window_start: int,
                        underlying: str = "btc") -> UpDownMarket | None:
    """Fetch the up/down market for a given window (slug is deterministic)."""
    slug = f"{underlying}-updown-{interval}-{window_start}"
    events = _get(f"{GAMMA}/events", slug=slug)
    if not events:
        return None
    m = events[0]["markets"][0]
    tokens = json.loads(m["clobTokenIds"])
    outcomes = json.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
    by_outcome = dict(zip(outcomes, tokens))
    # FEE: the 0.07 crypto taker fee IS charged on-chain — re-verified 2026-06-27 from raw tx
    # receipts (wallet 0xd630…, NEW pUSD relayer 0xe111…). At ENTRY the wallet debits exactly
    # `usdcSize` = size×price + fee in ONE pUSD transfer; the relayer then forwards the fee
    # (= 0.07×p×(1−p)×shares) in pUSD to collector 0x115f48dc — on BOTH 5m AND 15m, never at
    # settlement (redeem returns the full shares×$1). The 2026-06-26 "4/4 zero fee" read was
    # WRONG: it tracked USDC.e transfers and missed the pUSD fee leg (the fee was always there;
    # the fee transfer doesn't touch the wallet address so it's invisible in its tx list).
    taker_rate = 0.07
    return UpDownMarket(
        slug=slug,
        question=m["question"],
        condition_id=m["conditionId"],
        window_start=window_start,
        window_end=window_start + INTERVALS[interval],
        token_up=by_outcome["Up"],
        token_down=by_outcome["Down"],
        accepting_orders=bool(m.get("acceptingOrders")),
        maker_fee_rate=0.0,
        taker_fee_rate=taker_rate,
    )


def resolve_market(slug: str) -> bool | None:
    """Real settlement of a window: True=Up won, False=Down, None=not yet closed.

    Reads the market's own `outcomePrices` (the Chainlink oracle result) — the
    actual money-settling outcome, NOT the Binance close>open proxy the paper
    engine uses (the two diverge ~3.7% of windows). For live P&L only the oracle
    counts."""
    events = _get(f"{GAMMA}/events", slug=slug)
    if not events:
        return None
    m = events[0]["markets"][0]
    outcomes = m["outcomes"]
    prices = m["outcomePrices"]
    outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
    prices = json.loads(prices) if isinstance(prices, str) else prices
    up = float(dict(zip(outcomes, prices))["Up"])
    # Short (5m/15m) markets keep closed=False / acceptingOrders=True for a long
    # time AFTER the oracle has resolved — but outcomePrices converge to ~1/0 once
    # settled. The only caller (run_live._settle) invokes this AFTER the window's
    # time has passed (window_end+65), so a DECISIVE price IS the resolution,
    # whatever the lagging `closed` flag says.
    if m.get("closed") or up >= 0.99 or up <= 0.01:
        return up > 0.5
    return None


def exec_book(token_id: str, latency_s: float = 1.0) -> dict:
    """Book as the matching engine will see it: re-fetched *after* the time a
    real order takes to travel and match. The world moves while we are in
    flight — fills must price against this book, not the decision book."""
    time.sleep(latency_s)
    return fetch_book(token_id)


def fetch_book(token_id: str) -> dict:
    """Orderbook with best levels first: {'bids': [(p, sz)...], 'asks': [(p, sz)...]}."""
    raw = _get(f"{CLOB}/book", token_id=token_id)
    bids = sorted(((float(l["price"]), float(l["size"])) for l in raw.get("bids", [])),
                  key=lambda x: -x[0])
    asks = sorted(((float(l["price"]), float(l["size"])) for l in raw.get("asks", [])),
                  key=lambda x: x[0])
    return {"bids": bids, "asks": asks}


def fetch_midpoint(token_id: str) -> float | None:
    try:
        return float(_get(f"{CLOB}/midpoint", token_id=token_id)["mid"])
    except Exception:
        return None


def hydrate(market: UpDownMarket) -> UpDownMarket:
    """Attach live midpoint + both orderbooks."""
    market.mid_up = fetch_midpoint(market.token_up)
    market.book_up = fetch_book(market.token_up)
    market.book_down = fetch_book(market.token_down)
    return market
