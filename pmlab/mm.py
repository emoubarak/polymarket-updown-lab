"""Two-sided market-making quoter — Flavor A (NO complete-set minting / NO web3).

The cheapest test of the core MM hypothesis: does a CO-LOCATED two-sided maker
capture spread net-positive — the thing scalp.py failed at, non-colo? We quote a
maker BID + ASK on ONE token (the current window's Up token) around fair value,
acquire inventory via the bid and offload via the ask. Co-located (Dublin) so the
quotes stay fresh (re-centred on drift). No split/web3/POL needed — if THIS prints
money, only then do we invest in complete-set minting (Flavor B) to source inventory
at $0.50 and scale.

Constraints / honest MVP limits (HARDEN before arming with real size):
  • Inventory is bounded [0, max_inv] — we never short (can't, without minting).
  • HARD KILL on mark-to-market bleed (cancel everything, stop).
  • Near settle we STOP acquiring and offload via a maker ask; whatever doesn't fill
    holds to settlement (a small directional tail, bounded by max_inv). TODO before
    arming: a taker-flatten (live.py has no taker SELL yet) + oracle settlement
    accounting (reuse feeds.resolve_market) so residual P&L is exact, not mid-marked.
  • Quotes are mid±edge on the penny grid; a competitive maker would JOIN the touch
    (best_bid/best_ask). TODO: touch-relative quoting.
Stdlib only; the broker (live.LiveBroker) is the one money-touching dependency.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import feeds
from .feeds import INTERVALS


def book_tob(book: dict):
    """(best_bid, best_ask, mid) from a CLOB book dict, or None if a side is empty."""
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None
    bb = max(b[0] for b in bids)
    ba = min(a[0] for a in asks)
    return bb, ba, round((bb + ba) / 2, 4)


class MarketMaker:
    def __init__(self, args, broker, log=print):
        self.iv = args.interval
        self.underlying = args.underlying
        self.win_min = INTERVALS[self.iv] / 60
        self.edge = args.edge                   # quote this far (price) from mid, each side
        self.max_inv = args.max_inv             # inventory cap (shares); never short
        # only quote where there's two-sided flow + spread room — NOT on an established
        # favorite/longshot near the $1/$0 rails (no edge there, and the bid would chase the
        # price toward the ceiling). The MM window is the EARLY, near-0.50 part of the window.
        self.min_quote = getattr(args, "min_quote", 0.15)
        self.max_quote = getattr(args, "max_quote", 0.85)
        self.min_margin = getattr(args, "min_margin", 0.01)  # never ASK below avg cost + this
        self.recenter_eps = args.recenter_eps   # re-post a quote only if it drifted >= this
        self.flatten_buf_s = args.flatten_buf   # stop acquiring / offload this long before settle
        self.kill_loss = args.kill_loss         # hard stop if mark-to-market P&L <= -this
        self.broker = broker
        self.log = log
        self.sd = Path(args.state_dir)
        self.sd.mkdir(exist_ok=True)
        st = self._load()
        self.inv = st.get("inv", 0.0)           # Up-token shares currently held
        self.cash = st.get("cash", 0.0)         # cumulative net cash from MM (negative = net spent)
        self.avg_cost = st.get("avg_cost", 0.0) # vol-weighted cost basis of current inventory
        self.realized = st.get("realized", 0.0) # realized round-trip P&L (locked)
        self.last_mid = st.get("last_mid", 0.5) # last seen mid (to mark a residual on roll)
        self.cur_token = st.get("cur_token")
        self.cur_slug = st.get("cur_slug")
        self.window_end = st.get("window_end", 0)
        self.bid = st.get("bid")                # resting maker bid order dict (or None)
        self.ask = st.get("ask")                # resting maker ask order dict (or None)
        self.killed = st.get("killed", False)
        self.round_trips = st.get("round_trips", 0)

    # ----------------------------------------------------------- state ---
    def _load(self) -> dict:
        p = self.sd / "mm_state.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _persist(self):
        (self.sd / "mm_state.json").write_text(json.dumps({
            "inv": self.inv, "cash": self.cash, "avg_cost": self.avg_cost,
            "realized": self.realized, "last_mid": self.last_mid,
            "cur_token": self.cur_token, "cur_slug": self.cur_slug,
            "window_end": self.window_end, "bid": self.bid, "ask": self.ask,
            "killed": self.killed, "round_trips": self.round_trips}))

    def _journal(self, kind: str, px, sh, note: str = ""):
        f = self.sd / "mm_journal.csv"
        new = not f.exists()
        with f.open("a") as fh:
            if new:
                fh.write("ts,kind,slug,price,shares,inv,cash,note\n")
            fh.write(f"{int(time.time())},{kind},{self.cur_slug},{px},{sh},"
                     f"{round(self.inv, 2)},{round(self.cash, 2)},{note}\n")

    # ------------------------------------------------------------ tick ---
    def on_tick(self):
        if self.killed:
            self._cancel_all()
            return
        sec = INTERVALS[self.iv]
        now = int(time.time())
        ws = (now // sec) * sec
        m = feeds.fetch_updown_market(self.iv, ws, self.underlying)
        if m is None:
            return
        if m.token_up != self.cur_token:            # a new window opened — roll onto it
            self._roll(m)
        book = feeds.fetch_book(self.cur_token) or {}
        tob = book_tob(book)
        if tob is None:
            return
        bb, ba, mid = tob
        self._check_fills()                          # 1. account fills on our resting orders
        pnl = self.cash + self.inv * mid             # 2. mark-to-market + HARD KILL
        if pnl <= -self.kill_loss:
            self.log(f"🛑 KILL: mtm P&L {pnl:+.2f} <= -{self.kill_loss} — cancel all + stop")
            self._journal("KILL", "", "", f"pnl={pnl:.2f}")
            self.killed = True
            self._cancel_all()
            self._persist()
            return
        remaining = self.window_end - now
        # 3. stop conditions — near settle, OR the mid left the tradeable band (favorite
        #    established): pull quotes and offload; never MM a deep favorite near the rail.
        if remaining <= self.flatten_buf_s or not (self.min_quote <= mid <= self.max_quote):
            self._flatten(bb, ba)
            self._persist()
            return
        # 4. desired two-sided quotes — bid skews DOWN as inventory grows (slow acquisition in a
        #    downtrend); ask NEVER below cost+margin (don't lock losses on adverse fills).
        bid_px = round(mid - self.edge * (1 + self.inv / self.max_inv), 2)
        ask_px = round(max(mid + self.edge, self.avg_cost + self.min_margin), 2)
        self._ensure_bid(bid_px, self.max_inv - self.inv)
        self._ensure_ask(ask_px, self.inv)
        self.last_mid = mid
        self._persist()

    def _roll(self, m):
        """Move onto the new window's Up token. Any residual inventory on the OLD window
        settles on its own (a small directional tail); we mark it off here at face — TODO
        (before arming): resolve the oracle for exact residual P&L instead of dropping it."""
        if self.inv >= 1:
            val = self.inv * self.last_mid           # mark the residual at the last mid (it holds to
            self.cash += val                         # settle; ~its risk-neutral value) — TODO oracle-exact
            self.log(f"↻ roll: residual {self.inv:.1f} on {self.cur_slug} marked @ "
                     f"{self.last_mid:.3f} (+{val:.2f})")
            self._journal("ROLL_MARK", round(self.last_mid, 3), round(self.inv, 2), f"+{val:.2f}")
            self.inv = 0.0
            self.avg_cost = 0.0
        self._cancel_all()
        self.cur_token, self.cur_slug, self.window_end = m.token_up, m.slug, m.window_end
        self.log(f"▶ quoting {self.cur_slug} (Up token)")

    # ------------------------------------------------------- quoting ---
    def _expiry(self) -> int:
        """GTD expiration for a new order, or 0 (=GTC) when the window is too close to settle:
        Polymarket rejects GTD orders whose expiration is < ~1 min out. A GTC order has no
        auto-expiry, so we rely on _cancel_all (re-center / roll) to clean it up."""
        return self.window_end if self.window_end - time.time() > 65 else 0

    def _ensure_bid(self, px: float, size_shares: float):
        if size_shares < 1 or px < 0.02:             # no room to acquire (at cap) — pull the bid
            self._drop("bid")
            return
        if self.bid and abs(self.bid["price"] - px) < self.recenter_eps:
            return                                   # current bid still well-placed
        self._drop("bid")
        usd = round(size_shares * px, 2)
        o = self.broker.place_limit(self.cur_token, px, usd, "Up", self.cur_slug,
                                    expiration=self._expiry())
        if o:
            o["filled_acct"] = 0.0
            self.bid = o

    def _ensure_ask(self, px: float, size_shares: float):
        if size_shares < 1 or px > 0.98:             # nothing to offload — pull the ask
            self._drop("ask")
            return
        if self.ask and abs(self.ask["price"] - px) < self.recenter_eps:
            return
        self._drop("ask")
        o = self.broker.place_sell(self.cur_token, px, size_shares, self.cur_slug,
                                   direction="Up", expiration=self._expiry())
        if o:
            o["filled_acct"] = 0.0
            self.ask = o

    def _check_fills(self):
        """Apply incremental fills on both resting orders (handles partials across ticks)."""
        for which in ("bid", "ask"):
            o = getattr(self, which)
            if not o:
                continue
            filled, px, status = self.broker.order_fill(o)
            new = filled - o.get("filled_acct", 0.0)
            if new > 1e-6:
                o["filled_acct"] = filled
                if which == "bid":
                    self.avg_cost = ((self.avg_cost * self.inv + px * new) / (self.inv + new)
                                     if self.inv + new > 0 else px)
                    self.inv += new
                    self.cash -= new * px
                    self.log(f"🟢 bid +{new:.1f}@{px:.2f} inv={self.inv:.1f} avg={self.avg_cost:.3f}")
                    self._journal("BUY", px, round(new, 2))
                else:
                    rt = (px - self.avg_cost) * new        # realized round-trip on these shares
                    self.realized += rt
                    self.inv -= new
                    self.cash += new * px
                    self.round_trips += 1
                    if self.inv < 1e-6:
                        self.avg_cost = 0.0
                    self.log(f"🔴 ask -{new:.1f}@{px:.2f} inv={self.inv:.1f} rt={rt:+.2f} "
                             f"realized={self.realized:+.2f}")
                    self._journal("SELL", px, round(new, 2), f"rt={rt:+.2f}")
            if status in ("filled", "gone"):
                setattr(self, which, None)

    def _flatten(self, bb: float, ba: float):
        """Near settle: FORCE FLAT via a taker sell (cross the bids) so the MM never carries a
        directional residual into settlement — a cent or two of slippage is cheap insurance vs
        holding a cratering favorite to $0. (The residual is adverse: flatten fails exactly when
        the favorite is collapsing, so we don't wait for a maker ask here — we cross.)"""
        self._check_fills()                      # account pending fills BEFORE cancelling
        self._drop("bid")
        self._drop("ask")
        if self.inv >= 1:
            fill = self.broker.sell_market(self.cur_token, self.inv, self.cur_slug,
                                           direction="Up", floor=max(round(bb - 0.02, 2), 0.01))
            if fill and fill.get("shares", 0) >= 1:
                f, px = fill["shares"], fill["price"]
                rt = (px - self.avg_cost) * f
                self.realized += rt
                self.inv -= f
                self.cash += f * px
                if self.inv < 1e-6:
                    self.avg_cost = 0.0
                self.log(f"⏹ flatten -{f:.1f}@{px:.3f} inv={self.inv:.1f} rt={rt:+.2f} "
                         f"realized={self.realized:+.2f}")
                self._journal("FLATSELL", px, round(f, 2), f"rt={rt:+.2f}")

    # ------------------------------------------------------- orders ---
    def _drop(self, which: str):
        o = getattr(self, which)
        if o:
            try:
                self.broker.cancel(o)
            except Exception as e:
                self.log(f"cancel {which} err: {type(e).__name__}: {e}")
            setattr(self, which, None)

    def _cancel_all(self):
        self._drop("bid")
        self._drop("ask")

    def status(self) -> str:
        return (f"MM {self.underlying} {self.iv} | inv={self.inv:.1f}@{self.avg_cost:.3f} "
                f"realized={self.realized:+.2f} cash={self.cash:+.2f} rts={self.round_trips}"
                f"{' | 🛑KILLED' if self.killed else ''}")
