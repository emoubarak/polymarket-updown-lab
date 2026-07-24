"""THE single registry of tradable underlyings — one `Coin` entry per asset, everything
else derives. Adding a coin = ONE line here (and a per-coin slot/depth if measured).

This kills the coin-list duplication that had already drifted across the codebase
(webdash._TOKENS was stuck at 4 coins while feeds/format.js/presets carried 6):
  feeds.SYMBOL · presets.COIN_BET_MAX · presets.COIN_SLOTS · main/run_live --underlying
  choices · webdash._TOKENS · research/dataset.SYMBOL · front-end (format.js TOKENS,
  About COIN_ORDER, Pilot dropdown) — all now derive from COINS (the front-end via /config).

Leaf module: it imports NOTHING from the package, so both feeds.py (low-level data) and
presets.py (strategy config) can import it without a cycle. The legacy dict names
(SYMBOL / COIN_BET_MAX / COIN_SLOTS / DEFAULT_BET_MAX) are re-exported as derived views, so
`feeds.SYMBOL`, `presets.COIN_BET_MAX`, etc. keep working unchanged for every importer.

Polymarket up/down markets exist for several underlyings (eth/sol/xrp confirmed 2026-06-20;
doge/bnb added 2026-06-25 — the wallet-hunt found a $119k pro [df0d] farming the favourite
across ALL updown alts). Each settles on its OWN Chainlink oracle, so the Binance spot/
settlement proxy MUST use the matching symbol — routing ETH windows through BTCUSDT would
silently corrupt every resolution. `hype` is EXCLUDED: it has updown markets but no Binance
feed (HYPEUSDT invalid), so the spot lead-z can't be computed.

MEASURED depths (`depth`): per-(coin, FRAME) SINGLE-ACTOR capacity, because (a) the 5m and 15m
up/down markets are SEPARATE books — one number per coin would confound them (it did until
2026-06-27) — and (b) the book is shared: many buyers compete for the same in-band asks, so what
ONE actor can fill is a FRACTION of the total flow (~15-35% on liquid coins; the aggregate is split
across 9-185 simultaneous buyers). Each value = median over recent windows of the LARGEST single
wallet's in-band (0.85–0.95) favourite-BUY total — i.e. the most one real actor accumulated WHILE
competing (tools/measure_capacity.py, from Polymarket trade history; Polymarket serves no historical
order BOOK, only trades). It's a conservative figure (typical top buyer, not a whale; realized volume
not resting depth; adding fresh demand could push price, so don't size above it). Re-run the tool to
refresh. `bet_max_for(coin, frame)` is the accessor; COIN_BET_MAX (legacy {coin: scalar}) is kept as
the conservative min-across-frames fallback for frame-agnostic callers. `slot` = per-coin entry_lo
override (None = the base WIDE_LO); bnb wants ~0.33 (≈5 min left,
everything later is dead there), the marginal-cohort sweep confirmed btc/eth/doge at base 0.27.
"""
from __future__ import annotations

from dataclasses import dataclass


FRAMES: tuple[str, ...] = ("5m", "15m")   # the two live up/down windows (each its own book)


@dataclass(frozen=True)
class Coin:
    key: str                    # the canonical short id used everywhere (btc, eth, …)
    symbol: str                 # Binance proxy symbol (spot reference + settlement proxy)
    depth: dict                 # {frame: per-bet $ ceiling} — measured in-band capacity, PER FRAME
    slot: float | None = None   # per-coin entry_lo override (None = base WIDE_LO)


# THE registry — order = display order. Add a coin = one Coin(...) line.
# depth = per-(coin, frame) SINGLE-ACTOR in-band (0.85–0.95) capacity $ = median of the LARGEST
# single-wallet favourite-BUY total per window, measured 2026-06-27 by tools/measure_capacity.py
# (n=60 windows 5m / 40 windows 15m, paginated). NOT the aggregate flow (that's split across many
# competing buyers — one actor gets only ~15-35% of it on liquid coins). btc dominates; its 5m
# book is ~3× its 15m. Re-run the tool to refresh.
COINS: dict[str, Coin] = {c.key: c for c in (
    Coin("btc",  "BTCUSDT",  {"5m": 981.0, "15m": 353.0}),
    Coin("eth",  "ETHUSDT",  {"5m": 104.0, "15m": 110.0}),
    Coin("sol",  "SOLUSDT",  {"5m":  59.0, "15m": 100.0}),
    Coin("xrp",  "XRPUSDT",  {"5m":  21.0, "15m":  83.0}),
    Coin("doge", "DOGEUSDT", {"5m":   9.0, "15m":  13.0}),
    Coin("bnb",  "BNBUSDT",  {"5m":   9.0, "15m":  10.0}, slot=0.33),
)}

DEFAULT_SYMBOL = "BTCUSDT"   # fallback spot symbol for an unknown/None underlying
DEFAULT_BET_MAX = 50.0       # prudent per-bet ceiling for a coin/frame with no measured depth


def bet_max_for(coin: str, frame: str | None = None, fallback: float = DEFAULT_BET_MAX) -> float:
    """THE per-(coin, frame) book-depth ceiling — the single source every sizer derives from.
    Falls back to the conservative MIN across frames when frame is unknown/missing, then to
    `fallback` for an unknown coin (so a frame-agnostic caller is never over-sized)."""
    c = COINS.get(coin)
    if c is None or not c.depth:
        return fallback
    if frame and frame in c.depth:
        return c.depth[frame]
    return min(c.depth.values())


# ---- derived views (the legacy names other modules already import) ----
COIN_KEYS: list[str] = list(COINS)                                      # canonical ordered list
SYMBOL: dict[str, str] = {k: c.symbol for k, c in COINS.items()}       # feeds.SYMBOL
COIN_DEPTH: dict[str, dict] = {k: dict(c.depth) for k, c in COINS.items()}   # per-frame, for /config + front
# legacy scalar view = the CONSERVATIVE min across frames (frame-agnostic callers / display fallback)
COIN_BET_MAX: dict[str, float] = {k: min(c.depth.values()) for k, c in COINS.items()}
COIN_SLOTS: dict[str, float] = {k: c.slot for k, c in COINS.items() if c.slot is not None}
