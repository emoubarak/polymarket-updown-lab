"""Scalp — a liquidity-provision brain. A DIFFERENT PARADIGM from the favorite family.

Engine bets on the OUTCOME (buy the extreme favorite, hold to settlement). This
brain bets on the PRICE: it never holds to settlement, it harvests the CLOB price's
own mean-reversion intra-window.

THE SIGNAL (research/ scratchpad scalp.py + revert2.py, 2026-06-25, FILL-INDEPENDENT
on the real executed-trade tape): after the Up mid dips ~5c below its short EWMA ref,
it reprices UP by +0.065 over the next 30s on 5m (n=4082, ~9σ; +0.035-0.055 on 15m),
vs a baseline ≈0. The thin CLOB OVERSHOOTS on impatient flow, then refills. So you
get PAID to provide liquidity to the impatient seller and close on the recovery.

  Rest a passive bid ENTER below the mid's EWMA ref (both sides — a Down-dip is an
  Up-pop). When an impatient seller trades THROUGH it (paper.match_orders: 0.7 of
  size, no maker fee — the SAME anti-selection model that exposed the directional
  maker at −0.015 live), you are long the dip. Rest a passive sell TARGET above the
  fill to capture the revert (no fee). If it has not reverted within MAX_HOLD or the
  window is about to close, BAIL as taker — never carry a random mid-window position
  to settlement.

WHY THIS IS NOT THE KILLED DIRECTIONAL MAKER (memory: maker-entry-lead, −0.015 live):
that maker rested a bid to BUY-AND-HOLD the favorite to settlement, so its fills were
adverse (filled exactly when the favorite weakened toward a loss). This brain EXITS
in seconds on the revert — the settlement-weakening anti-selection does not apply; the
only question is whether the +6.5c revert survives the 0.7 passive fill + the spread.

HONEST RISK / why this is a FORWARD-TEST, not a proven edge: the +6.5c reversion is
REAL and fill-independent, but the CAPTURE is not — it needs maker fills on a thin
book at an ~8s poll (fast dips that revert within one tick are missed). The tape
backtest of any maker is OPTIMISTIC about fills (proven by the directional maker:
tape +0.05 → live −0.015). So ONLY the live paper run, filling against the real
exec_book, can say if the revert is capturable. Tripwire: if realized round-trips
net ≤ 0 over ~150 closes, the signal does not survive execution — kill it.

Restricted to the genuinely-oscillating mid-range (ref in [RANGE_LO, RANGE_HI]); an
extreme favorite's "dip" is an informative weakening, not the liquidity noise the
signal is about.
"""

from __future__ import annotations

from .paper import MultiBroker, LimitOrder
from .runner import Ctx


class Scalper:
    """Intra-window mean-reversion / liquidity-provision scalper. Reads its whole
    state from the broker (positions + resting orders, both persisted), so it is
    restart-safe: no decision lives only in memory."""

    def __init__(self, stake: float = 25.0, enter: float = 0.04, target: float = 0.03,
                 max_hold_s: float = 90.0, end_buf_s: float = 30.0,
                 ref_lambda: float = 0.7, range_lo: float = 0.25, range_hi: float = 0.75,
                 name: str = "scalp"):
        self.name = name
        self.stake = stake          # $ per scalp (one open position at a time)
        self.enter = enter          # rest the bid this far below the EWMA ref
        self.target = target        # rest the exit this far above the fill (revert)
        self.max_hold_s = max_hold_s  # bail (taker) if no revert in this long
        self.end_buf_s = end_buf_s    # bail before the window closes — never settle
        self.ref_lambda = ref_lambda  # EWMA weight on the old ref (per tick)
        self.range_lo = range_lo      # only scalp where the price genuinely oscillates
        self.range_hi = range_hi
        self._ref: float | None = None
        self._ref_slug: str | None = None

    # ------------------------------------------------------------- brain ---

    def on_tick(self, ctx: Ctx, broker: MultiBroker, log) -> None:
        mid = ctx.mid_up
        if mid is None:
            return
        slug = ctx.market.slug
        # EWMA reference of the Up mid; reset on a new window (no cross-window ref)
        if slug != self._ref_slug:
            self._ref, self._ref_slug = mid, slug
        else:
            self._ref = self.ref_lambda * self._ref + (1 - self.ref_lambda) * mid
        ref = self._ref
        now = ctx.ts

        mine = [p for p in broker.positions_for(slug) if p.tag == self.name]
        buys = [o for o in broker.orders if o.slug == slug and o.tag == self.name
                and o.side == "buy"]
        sells = [o for o in broker.orders if o.slug == slug and o.tag == self.name
                 and o.side == "sell"]
        near_end = (ctx.market.window_end - now) < self.end_buf_s

        # ---- HOLDING a scalp: manage the exit -------------------------------
        if mine:
            pos = mine[0]
            for o in buys:                          # a fill cancels the hunt
                broker.cancel(o)
            cur = mid if pos.direction == "Up" else 1 - mid
            reverted = cur >= pos.avg_price + self.target
            held = now - pos.opened_at
            if near_end or held > self.max_hold_s or ctx.kill_switch:
                for o in sells:                     # bail as taker — cut, don't settle
                    broker.cancel(o)
                pnl = broker.sell(pos, ctx.book_for(pos.direction)["bids"],
                                  fee_rate=ctx.taker_rate)
                if pnl is not None:
                    log(f"⚡ {self.name}: BAIL {pos.direction} @~{cur:.3f} "
                        f"(in {pos.avg_price:.3f}, {pnl:+.2f}, held {held:.0f}s)")
            elif reverted and sells:                # let the resting maker sell take it,
                pass                                # but if it lags, lock the revert taker
            elif not sells:                         # rest the maker exit at fill+target
                px = round(min(pos.avg_price + self.target, 0.99), 2)
                order = LimitOrder(slug, pos.token_id, pos.direction, "sell", px,
                                   pos.shares, ctx.market.window_end,
                                   expire_ts=ctx.market.window_end, tag=self.name)
                broker.place_limit(order)
            return

        # ---- FLAT: hunt for a dip both sides --------------------------------
        for o in sells:                             # no position -> no stray exit order
            broker.cancel(o)
        if near_end or ctx.kill_switch or not (self.range_lo < ref < self.range_hi):
            for o in buys:
                broker.cancel(o)
            return
        if buys:                                    # bids already resting (re-centre on expiry)
            return
        for direction, p in (("Up", ref - self.enter), ("Down", (1 - ref) - self.enter)):
            price = round(p, 2)
            if not (0.05 < price < 0.95):
                continue
            shares = (self.stake / broker.passive_fill_ratio) / price
            order = LimitOrder(slug, ctx.token_for(direction), direction, "buy",
                               price, shares, ctx.market.window_end,
                               expire_ts=min(now + 30, ctx.market.window_end), tag=self.name)
            broker.place_limit(order)
        log(f"⚡ {self.name}: rest bids Up@{round(ref-self.enter,2):.2f} / "
            f"Down@{round((1-ref)-self.enter,2):.2f} (ref {ref:.2f})")

    def status(self, ctx: Ctx) -> str:
        if ctx.mid_up is None:
            return "scalp —"
        r = self._ref if self._ref is not None else ctx.mid_up
        return f"ref {r:.2f} | mid {ctx.mid_up:.2f}"
