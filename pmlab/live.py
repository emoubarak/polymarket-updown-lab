"""LIVE execution on Polymarket CTF Exchange V2 — the ONLY module that touches money.

Migrated 2026-06-20 to the April-2026 V2 upgrade (py-clob-client-v2, pUSD collateral,
CTF Exchange V2, fees-at-match). Everything else in this repo is paper.
Read docs/SETUP-LIVE.md before running.

Safe by construction:
  - DISARMED by default. Posts a real order ONLY if both env vars are set:
        POLY_LIVE=1
        POLY_CONFIRM=I_UNDERSTAND_REAL_MONEY
    Otherwise every buy() is a DRY-RUN that logs the exact order it would send
    (priced off the public read-only feed — no client, no key needed).
  - NO BLIND ON-CHAIN TRANSFERS. Funding/wrapping (USDC→pUSD) is done in the
    Polymarket UI; the pUSD→exchange allowance goes through the OFFICIAL client;
    winning positions are redeemed in the UI. So a bug here can at most get an
    order REJECTED — it can never move funds to the wrong place.
  - HARD-CAPPED at MAX_CLIP_USD per order, no matter what the caller passes.
  - SECRET-FREE: the private key is read from POLY_PRIVATE_KEY env only.
  - REFUSES to trade below MIN_USDC_FLOOR of pUSD collateral.

Dependency: py-clob-client-v2 (isolated here; imported lazily so the paper side
stays stdlib-only). Auth is env-driven so the same code serves an EOA wallet
(signature_type 0) or a Polymarket proxy/email account (1 or 2) — see SETUP.
"""
from __future__ import annotations

import os

from .entry import ask_acceptable        # shared execution guard (mutualised with paper)

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137                      # Polygon mainnet
MAX_CLIP_USD = 300.0               # absolute safety ceiling per order
MIN_USDC_FLOOR = 5.0               # refuse to trade below this pUSD balance
ABANDON_BAND = 0.02                # don't pay more than +2c over the favorite mid


def is_armed() -> bool:
    return (os.environ.get("POLY_LIVE") == "1"
            and os.environ.get("POLY_CONFIRM") == "I_UNDERSTAND_REAL_MONEY")


def _attr(obj, name, default=None):
    """Read .name or ['name'] off a client response (dict or object)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class LiveBroker:
    """Thin, auditable wrapper over py-clob-client-v2 for marketable taker buys.
    One responsibility: turn a sized decision into a real (or dry-run) fill.
    It never sends an on-chain transaction — only signed CLOB orders + the
    official allowance call."""

    def __init__(self, dry_run: bool | None = None, log=print):
        self.dry_run = (not is_armed()) if dry_run is None else dry_run
        self.log = log
        self._client = None
        # 0=EOA, 1=POLY_PROXY (Magic), 2=POLY_GNOSIS_SAFE, 3=POLY_1271 (deposit
        # wallet). V2 requires 3 for accounts created via the web (the "deposit
        # wallet flow") — and the balance/allowance calls must carry it too.
        self._sig_type = int(os.environ.get("POLY_SIG_TYPE", "0"))

    # ----------------------------------------------------------- connect ---
    def connect(self) -> None:
        if self._client is not None:
            return
        from py_clob_client_v2 import ClobClient          # lazy import (V2)

        pk = os.environ.get("POLY_PRIVATE_KEY")
        if not pk:
            raise RuntimeError("POLY_PRIVATE_KEY not set — cannot sign orders")
        funder = os.environ.get("POLY_FUNDER") or None         # deposit-wallet addr
        c = ClobClient(HOST, chain_id=CHAIN_ID, key=pk,
                       signature_type=self._sig_type, funder=funder)
        # Derive the wallet's existing API creds (deterministic from the key's
        # signature). create_or_derive_api_key() tries CREATE first, which 400s
        # ("Could not create api key") on every (re)connect once a key exists —
        # noisy. Derive-first is quiet; fall back to create only for a brand-new
        # wallet that has no key yet.
        try:
            creds = c.derive_api_key()
        except Exception:
            creds = c.create_or_derive_api_key()
        c.set_api_creds(creds)
        self._client = c
        mode = "DRY-RUN" if self.dry_run else "🔴 LIVE (real money)"
        self.log(f"LiveBroker v2 connected — {mode} | sig_type={self._sig_type}")

    # ---------------------------------------------------------- balances ---
    def usdc_balance(self) -> float:
        """pUSD collateral balance (V2). The client abstracts pUSD behind
        AssetType.COLLATERAL; balance is returned in 6-decimal base units."""
        from py_clob_client_v2 import BalanceAllowanceParams, AssetType
        self.connect()
        bal = self._client.get_balance_allowance(BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL, signature_type=self._sig_type))
        return float(_attr(bal, "balance", 0) or 0) / 1_000_000

    def conditional_balance(self, token_id: str) -> float:
        """Shares of a conditional (outcome) token held on-chain (V2). Lets the
        runner mark a still-unredeemed winning position at ~$1 so the displayed
        capital anticipates the redeem instead of dipping until pUSD lands."""
        from py_clob_client_v2 import BalanceAllowanceParams, AssetType
        self.connect()
        bal = self._client.get_balance_allowance(BalanceAllowanceParams(
            asset_type=AssetType.CONDITIONAL, token_id=token_id,
            signature_type=self._sig_type))
        return float(_attr(bal, "balance", 0) or 0) / 1_000_000

    def ensure_allowances(self) -> None:
        """One-time: approve pUSD for the CTF Exchange V2 (handled by the official
        client — no raw web3). DRY-RUN skips it."""
        from py_clob_client_v2 import BalanceAllowanceParams, AssetType
        self.connect()
        if self.dry_run:
            self.log("ensure_allowances: DRY-RUN, skipping pUSD approval")
            return
        self._client.update_balance_allowance(BalanceAllowanceParams(
            asset_type=AssetType.COLLATERAL, signature_type=self._sig_type))
        self.log("pUSD allowance ensured for CTF Exchange V2")

    # ----------------------------------------------------------- the book ---
    def best_ask(self, token_id: str) -> tuple[float, float] | None:
        """(price, size_shares) at the top of the ask side, or None. In DRY-RUN
        the quote comes from the public read-only feed — testable before arming."""
        if self.dry_run or not is_armed():
            from . import feeds
            asks = (feeds.fetch_book(token_id) or {}).get("asks") or []
            return (asks[0][0], asks[0][1]) if asks else None
        self.connect()
        book = self._client.get_order_book(token_id)
        asks = _attr(book, "asks") or []
        if not asks:
            return None
        def px(lvl): return float(_attr(lvl, "price"))
        def sz(lvl): return float(_attr(lvl, "size"))
        best = min(asks, key=px)
        return px(best), sz(best)

    # --------------------------------------------------------------- buy ---
    def buy(self, token_id: str, mid: float, usd: float, direction: str,
            slug: str, min_fav: float = 0.0, max_fav: float = 1.0) -> dict | None:
        """Marketable FAK buy of the favorite (V2 market order, amount in $).
        FAK = fill what the touch allows, kill the rest — a thin book yields a
        PARTIAL fill instead of killing the whole order (the old FOK threw and
        stalled the loop when the $clip exceeded touch depth). Caps stake, meets
        the live ask, abandons if the executable ask left the favorite band or
        moved off the decision mid. Fill dict or None.

        The two band guards MIRROR the validated paper entry (favorite._taker_enter):
        the price ACTUALLY payable must still be an extreme favorite, and the book
        must not have moved > ABANDON_BAND off the mid in EITHER direction. A
        favorite whose ask has SLIPPED below the floor by fill-time is a weakening
        favorite (high reversal risk) — the paper skips it, so the pilot must too.
        Pass min_fav/max_fav to enable them (default off = old behaviour)."""
        usd = min(usd, MAX_CLIP_USD)                 # hard cap, always
        if usd < 1.0:
            return None
        quote = self.best_ask(token_id)
        if quote is None:
            self.log(f"buy {slug}: no ask, skip")
            return None
        ask, _ = quote
        if not ask_acceptable(ask, mid, min_fav, max_fav, ABANDON_BAND):   # shared guard (entry.py)
            self.log(f"buy {slug}: ask {ask:.3f} not acceptable — outside "
                     f"[{min_fav:.2f},{max_fav:.2f}] or moved > {ABANDON_BAND} from "
                     f"mid {mid:.3f}, skip")
            return None
        # Sweep depth up to the slippage budget we ALREADY accept (mid + ABANDON_BAND),
        # not just the touch: a FAK at the touch fills only the best-ask level (~$1 on a
        # thin book — the under-fill bug). Priced at mid+2c the FAK consumes every ask up
        # to the validated haircut then kills the rest, so we never pay more than the
        # paper assumes. Capped at max_fav so the sweep never lifts the favorite out of
        # its band. ask_acceptable already ensured ask <= mid+band AND ask <= max_fav,
        # so this price is always >= ask (it always crosses the touch).
        price = min(round(mid + ABANDON_BAND, 3), max_fav, 0.99)
        shares = round(usd / price, 2)               # estimate (DRY-RUN only)

        if self.dry_run or not is_armed():
            self.log(f"DRY-RUN buy {slug}: ~{shares} {direction} @ {price:.3f} "
                     f"(${usd:.2f}) — NOT sent")
            return {"slug": slug, "token_id": token_id, "direction": direction,
                    "shares": shares, "price": price, "usd": shares * price,
                    "dry_run": True}

        # --- real V2 order: spend up to `usd` of pUSD, FAK at the touch price ---
        bal = self.usdc_balance()
        if bal < MIN_USDC_FLOOR:
            self.log(f"buy {slug}: pUSD balance ${bal:.2f} below floor ${MIN_USDC_FLOOR}")
            return None
        usd = min(usd, bal)                          # never order more than held liquid
        from py_clob_client_v2 import (MarketOrderArgsV2, Side, OrderType,
                                       PartialCreateOrderOptions)
        try:
            resp = self._client.create_and_post_market_order(
                MarketOrderArgsV2(token_id=token_id, amount=usd, side=Side.BUY,
                                  price=price, order_type=OrderType.FAK),
                options=PartialCreateOrderOptions(tick_size="0.01"),
                order_type=OrderType.FAK)
        except Exception as e:                       # an order must NEVER crash the tick
            self.log(f"🔴 buy {slug} order error: {type(e).__name__}: {e}")
            return None
        if not _attr(resp, "success", False):
            self.log(f"🔴 buy {slug} REJECTED: {resp}")
            return None
        spent = float(_attr(resp, "makingAmount", 0) or 0)    # pUSD actually spent
        got = float(_attr(resp, "takingAmount", 0) or 0)      # shares actually received
        if got <= 0.0:                               # FAK matched nothing (empty touch)
            self.log(f"buy {slug}: FAK matched 0 at {price:.3f} (no liquidity), skip")
            return None
        fill_px = round(spent / got, 4) if got else price
        oid = _attr(resp, "orderID", "?")
        self.log(f"🔴 LIVE buy {slug}: {got:.2f} {direction} @ ~{fill_px:.3f} "
                 f"(${spent:.2f}) id={oid}")
        return {"slug": slug, "token_id": token_id, "direction": direction,
                "shares": round(got, 2), "price": fill_px, "usd": round(spent, 2),
                "order_id": oid, "dry_run": False}

    def sell_market(self, token_id: str, shares: float, slug: str, direction: str = "",
                    floor: float = 0.02) -> dict | None:
        """Marketable FAK SELL of `shares` you hold (cross the bids down to `floor`). Used to
        FLATTEN MM inventory before settle so we never carry a directional residual — accept a
        cent or two of slippage to be OUT. Fill dict (shares sold, avg price, pUSD) or None."""
        shares = round(shares, 2)
        if shares < 1:
            return None
        price = min(max(round(floor, 2), 0.01), 0.99)
        if self.dry_run or not is_armed():
            self.log(f"DRY-RUN sell-market {slug}: {shares} {direction} floor {price:.2f} — NOT sent")
            return {"slug": slug, "token_id": token_id, "direction": direction,
                    "shares": shares, "price": price, "dry_run": True}
        self.connect()                                    # ensure the client exists (idempotent)
        from py_clob_client_v2 import (MarketOrderArgsV2, Side, OrderType,
                                       PartialCreateOrderOptions)
        try:
            resp = self._client.create_and_post_market_order(
                MarketOrderArgsV2(token_id=token_id, amount=shares, side=Side.SELL,
                                  price=price, order_type=OrderType.FAK),
                options=PartialCreateOrderOptions(tick_size="0.01"),
                order_type=OrderType.FAK)
        except Exception as e:                            # never crash the tick
            self.log(f"🔴 sell-market {slug} error: {type(e).__name__}: {e}")
            return None
        if not _attr(resp, "success", False):
            self.log(f"🔴 sell-market {slug} REJECTED: {resp}")
            return None
        sold = float(_attr(resp, "makingAmount", 0) or 0)   # shares given up
        recd = float(_attr(resp, "takingAmount", 0) or 0)   # pUSD received
        if sold <= 0:
            self.log(f"sell-market {slug}: FAK matched 0 (no bids) — residual holds to settle")
            return None
        px = round(recd / sold, 4) if sold else price
        self.log(f"🔴 LIVE sell-market {slug}: {sold:.2f} {direction} @ ~{px:.3f} (${recd:.2f})")
        return {"slug": slug, "token_id": token_id, "direction": direction,
                "shares": round(sold, 2), "price": px, "usd": round(recd, 2),
                "order_id": _attr(resp, "orderID", "?"), "dry_run": False}

    # ------------------------------------------------------ maker entry ---
    # The maker counterpart of buy(): instead of crossing the ask (taker: pays the
    # haircut + a higher price), REST a passive limit bid at the favorite's price
    # and let the market trade down into it (no haircut). Mirrors the paper engine
    # (favorite._place_maker / paper.MultiBroker). Only favorite_leadzmk-style strategies
    # (params.maker_entry) ever call these — the taker pilots are untouched.
    def place_limit(self, token_id: str, price: float, usd: float, direction: str,
                    slug: str, expiration: int = 0) -> dict | None:
        """Post a passive GTD limit BUY at `price` — a MAKER order (post_only, so it
        is REJECTED rather than crossing the book; it can only ADD liquidity). GTD +
        `expiration` (unix s = window_end) auto-cancels it if the loop dies, so a
        stale bid can never survive into the next window. DRY-RUN logs the order it
        would post. Returns the resting-order dict, or None (rest failed → caller
        crosses taker so a runaway favorite is never missed)."""
        usd = min(usd, MAX_CLIP_USD)                  # same hard cap as taker
        if usd < 1.0:
            return None
        price = min(round(price, 2), 0.99)            # 0.01 tick grid (V2 tick_size)
        shares = round(usd / price, 2)
        if shares < 1:
            return None
        if self.dry_run or not is_armed():
            self.log(f"DRY-RUN rest {slug}: bid {shares} {direction} @ {price:.2f} "
                     f"(${usd:.2f}) maker — NOT sent")
            return {"slug": slug, "token_id": token_id, "direction": direction,
                    "shares": shares, "price": price, "order_id": f"dry-{slug}",
                    "dry_run": True}
        bal = self.usdc_balance()
        if bal < MIN_USDC_FLOOR:
            self.log(f"rest {slug}: pUSD ${bal:.2f} below floor ${MIN_USDC_FLOOR}")
            return None
        from py_clob_client_v2 import (OrderArgsV2, Side, OrderType,
                                       PartialCreateOrderOptions)
        otype = OrderType.GTD if expiration else OrderType.GTC
        try:
            resp = self._client.create_and_post_order(
                OrderArgsV2(token_id=token_id, price=price, size=shares,
                            side=Side.BUY, expiration=int(expiration)),
                options=PartialCreateOrderOptions(tick_size="0.01"),
                order_type=otype, post_only=True)
        except Exception as e:                        # never crash the tick
            if "cross" not in str(e).lower():         # post-only crossing is benign+expected on a fast
                self.log(f"🔴 rest {slug} order error: {type(e).__name__}: {e}")   # book (WS re-quotes
            return None                               # in ms) — DON'T spam it (6M-line logs killed a run)
        if not _attr(resp, "success", False):
            if "cross" not in str(resp).lower():
                self.log(f"🔴 rest {slug} REJECTED (post_only would cross?): {resp}")
            return None
        oid = _attr(resp, "orderID", "?")
        self.log(f"🔴 LIVE rest {slug}: bid {shares} {direction} @ {price:.2f} "
                 f"(${usd:.2f}) maker id={oid}")
        return {"slug": slug, "token_id": token_id, "direction": direction,
                "shares": shares, "price": price, "order_id": oid, "dry_run": False}

    def place_sell(self, token_id: str, price: float, shares: float, slug: str,
                   direction: str = "", expiration: int = 0) -> dict | None:
        """Post a passive GTD limit SELL of `shares` you HOLD at `price` — a MAKER ask
        (post_only, can only ADD liquidity). The two-sided MM offloads minted/acquired
        inventory through this; the caller MUST already own the shares. DRY-RUN logs the
        ask it would post. Returns the resting-order dict (side='SELL'), or None."""
        price = min(max(round(price, 2), 0.01), 0.99)    # 0.01 tick grid (V2)
        shares = round(shares, 2)
        if shares < 1:
            return None
        if self.dry_run or not is_armed():
            self.log(f"DRY-RUN ask {slug}: sell {shares} {direction} @ {price:.2f} maker — NOT sent")
            return {"slug": slug, "token_id": token_id, "direction": direction,
                    "shares": shares, "price": price, "order_id": f"dry-sell-{slug}",
                    "side": "SELL", "dry_run": True}
        self.connect()                                    # ensure the client exists (idempotent)
        from py_clob_client_v2 import (OrderArgsV2, Side, OrderType,
                                       PartialCreateOrderOptions)
        otype = OrderType.GTD if expiration else OrderType.GTC
        try:
            resp = self._client.create_and_post_order(
                OrderArgsV2(token_id=token_id, price=price, size=shares,
                            side=Side.SELL, expiration=int(expiration)),
                options=PartialCreateOrderOptions(tick_size="0.01"),
                order_type=otype, post_only=True)
        except Exception as e:                            # never crash the tick
            if "cross" not in str(e).lower():             # benign post-only cross — don't spam (see place_limit)
                self.log(f"🔴 ask {slug} order error: {type(e).__name__}: {e}")
            return None
        if not _attr(resp, "success", False):
            if "cross" not in str(resp).lower():
                self.log(f"🔴 ask {slug} REJECTED (post_only would cross?): {resp}")
            return None
        oid = _attr(resp, "orderID", "?")
        self.log(f"🔴 LIVE ask {slug}: sell {shares} {direction} @ {price:.2f} maker id={oid}")
        return {"slug": slug, "token_id": token_id, "direction": direction, "shares": shares,
                "price": price, "order_id": oid, "side": "SELL", "dry_run": False}

    def order_fill(self, order: dict) -> tuple[float, float, str]:
        """How much of a resting order has filled: (filled_shares, avg_price, status)
        with status in {'live','filled','gone'}. DRY-RUN simulates a fill once the
        public best ask trades down to the resting bid (the market came to us) —
        so the maker flow is fully observable before arming."""
        if order.get("dry_run") or not is_armed():
            from . import feeds
            book = feeds.fetch_book(order["token_id"]) or {}
            if order.get("side") == "SELL":           # a maker ASK fills when a bid rises to it
                best = max((b[0] for b in (book.get("bids") or [])), default=0.0)
                if best >= order["price"] - 1e-9:
                    return order["shares"], order["price"], "filled"
                return 0.0, order["price"], "live"
            best = min((a[0] for a in (book.get("asks") or [])), default=1.0)
            if best <= order["price"] + 1e-9:         # a maker BID fills when an ask drops to it
                return order["shares"], order["price"], "filled"
            return 0.0, order["price"], "live"
        self.connect()
        try:
            o = self._client.get_order(order["order_id"])
        except Exception as e:
            self.log(f"order_fill {order['slug']}: {type(e).__name__}: {e}")
            return 0.0, order["price"], "live"        # unknown → keep waiting
        if o is None:
            return 0.0, order["price"], "gone"
        matched = float(_attr(o, "size_matched", 0) or 0)
        status = str(_attr(o, "status", "") or "").upper()
        if matched > 0:
            done = (matched >= order["shares"] - 1e-6
                    or status in ("MATCHED", "FILLED"))
            return round(matched, 2), order["price"], ("filled" if done else "live")
        if status in ("CANCELED", "CANCELLED", "EXPIRED"):
            return 0.0, order["price"], "gone"
        return 0.0, order["price"], "live"

    def cancel(self, order: dict) -> bool:
        """Cancel a resting maker bid. DRY-RUN logs only. A cancel that errors is
        reported but never crashes the loop (the GTD expiry is the backstop)."""
        if order.get("dry_run") or not is_armed():
            self.log(f"DRY-RUN cancel {order['slug']} maker bid — NOT sent")
            return True
        self.connect()
        from py_clob_client_v2 import OrderPayload
        try:
            self._client.cancel_order(OrderPayload(orderID=order["order_id"]))
            self.log(f"🔴 cancelled {order['slug']} maker bid {order['order_id']}")
            return True
        except Exception as e:
            self.log(f"cancel {order['slug']}: {type(e).__name__}: {e}")
            return False

    # ----------------------------------------------------------- redeem ---
    def redeem(self, condition_id: str) -> bool:
        """V2: collateral is pUSD, settlement is native USDC. Redemption of a won
        position is left to the Polymarket UI (or its auto-redeem) — we do NOT
        fire a blind on-chain tx whose collateral arg could be wrong under V2.
        The won value sits safely in the position token until claimed; for a small
        pilot, claim it in the UI."""
        self.log(f"win {condition_id[:10]}… — réclame le gain dans l'UI Polymarket "
                 f"(valeur en sécurité, pas d'auto-redeem en V2)")
        return False
