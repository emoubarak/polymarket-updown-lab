"""Delta-neutral COMPLETE-SET ARB via the CLOB (no on-chain minting needed).

std0 mints complete sets at $0.50/side and sells the overround. We can't mint (Polymarket's
pUSD/proxy collateral system is closed), but we can BUY complete sets cheaply on the order book:
post maker bids on BOTH Up and Down such that their fill prices sum to < $1, and every matched
pair is a complete set that settles for EXACTLY $1 (one side wins, one loses) — guaranteed,
market-NEUTRAL, no directional trend-bleed (the failure mode of the one-sided quoter). Profit =
$1 − (cost of the pair). The only risk is UNMATCHED inventory (more Up than Down or vice-versa);
we cap it hard and let it settle.

Mechanics per window:
  • bid Up at mid_up − edge, bid Down at mid_down − edge (sum ≈ 1 − 2·edge < 1).
  • as fills land, track inv_up / inv_down; matched = min(inv_up, inv_down).
  • STOP bidding the heavier side once unmatched >= max_unmatched (cap directional risk).
  • hold everything to settlement: matched sets → +$1 each (vs ~$0.97 paid); unmatched → the
    real oracle outcome (small, bounded). run_live-style _settle/redeem realizes it.
P&L is HONEST: realized only at settlement (no mark-to-mid). Stdlib; broker = live.LiveBroker.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from . import feeds
from .feeds import INTERVALS

SETOPS_JS = os.path.expanduser("~/mint/setops.js")   # node helper: mint/merge/redeem via relayer


def book_mid(book: dict):
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None
    bb = max(b[0] for b in bids)
    ba = min(a[0] for a in asks)
    return bb, ba, round((bb + ba) / 2, 4)


class SetArb:
    def __init__(self, args, broker, log=print):
        self.iv = args.interval
        self.underlying = args.underlying
        self.edge = args.edge                  # bid this far below each side's mid
        self.set_usd = args.set_usd            # $ of complete-sets to target acquiring per window
        self.max_unmatched = args.max_unmatched  # cap on |inv_up - inv_down| shares (directional)
        self.recenter_eps = args.recenter_eps
        self.flatten_buf_s = args.flatten_buf  # stop quoting this long before settle (hold to redeem)
        self.merge_min = getattr(args, "merge_min", 3.0)   # merge once this many sets are matched
        self._last_merge = 0.0                             # cooldown (relayer serializes actions/wallet)
        self.broker = broker
        self.log = log
        self.sd = Path(args.state_dir)
        self.sd.mkdir(exist_ok=True)
        st = self._load()
        self.inv_up = st.get("inv_up", 0.0)
        self.inv_dn = st.get("inv_dn", 0.0)
        self.spent = st.get("spent", 0.0)      # CUMULATIVE $ spent on buys (never reset)
        self.received = st.get("received", 0.0)  # CUMULATIVE $ from merges + settles (never reset)
        self.cost = st.get("cost", 0.0)        # $ paid for CURRENT open inventory (per-window, info only)
        # restart boundary: if accounting is fresh but inventory persisted, set its cost-basis to mark
        # value (held inventory = recovered capital, NOT profit) so realized never phantoms a gain.
        if self.spent == 0 and self.received == 0 and (self.inv_up + self.inv_dn) > 0:
            self.spent = min(self.inv_up, self.inv_dn) * 1.0 + abs(self.inv_up - self.inv_dn) * 0.5
        self.realized = self.received - self.spent  # net cash flow = the TRUE locked P&L
        self.cur_slug = st.get("cur_slug")
        self.tok_up = st.get("tok_up")
        self.tok_dn = st.get("tok_dn")
        self.window_end = st.get("window_end", 0)
        self.cid = st.get("cid")
        self.bid_up = st.get("bid_up")
        self.bid_dn = st.get("bid_dn")

    def _load(self):
        p = self.sd / "setarb_state.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _persist(self):
        (self.sd / "setarb_state.json").write_text(json.dumps({
            "inv_up": self.inv_up, "inv_dn": self.inv_dn, "spent": self.spent,
            "received": self.received, "cost": self.cost,
            "realized": self.realized, "cur_slug": self.cur_slug, "tok_up": self.tok_up,
            "tok_dn": self.tok_dn, "window_end": self.window_end, "cid": self.cid,
            "bid_up": self.bid_up, "bid_dn": self.bid_dn}))

    def _journal(self, kind, side, px, sh, note=""):
        f = self.sd / "setarb_journal.csv"
        new = not f.exists()
        with f.open("a") as fh:
            if new:
                fh.write("ts,kind,slug,side,price,shares,inv_up,inv_dn,cost,realized,note\n")
            fh.write(f"{int(time.time())},{kind},{self.cur_slug},{side},{px},{sh},"
                     f"{round(self.inv_up,2)},{round(self.inv_dn,2)},{round(self.cost,2)},"
                     f"{round(self.realized,2)},{note}\n")

    def on_tick(self):
        sec = INTERVALS[self.iv]
        now = int(time.time())
        ws = (now // sec) * sec
        m = feeds.fetch_updown_market(self.iv, ws, self.underlying)
        if m is None:
            return
        if m.slug != self.cur_slug:
            self._roll(m)
        bu = feeds.fetch_book(self.tok_up) or {}
        bd = feeds.fetch_book(self.tok_dn) or {}
        mu = book_mid(bu)
        md = book_mid(bd)
        if mu is None or md is None:
            return
        self._check_fills()
        self._merge_matched()                  # instantly realize matched sets -> pUSD (+overround)
        remaining = self.window_end - now
        if remaining <= self.flatten_buf_s:
            self._cancel_all()                 # stop acquiring; hold inventory to settle+redeem
            self._persist()
            return
        # only quote where BOTH sides are meaningfully priced (cheap balanced sets exist) — NOT on an
        # established favorite/longshot (there a bid is just a directional longshot bet, not a set).
        if not (0.30 <= mu[2] <= 0.70):
            self._cancel_all()
            self._persist()
            return
        unmatched = self.inv_up - self.inv_dn  # >0 = long Up excess, <0 = long Down excess
        room = max(0.0, self.set_usd - min(self.inv_up, self.inv_dn))  # set_usd budget remaining
        up_px = round(mu[2] - self.edge, 2)
        dn_px = round(md[2] - self.edge, 2)
        cap = self.max_unmatched               # a single bid must never exceed the unmatched cap (shares)
        up_room = min(room, cap * up_px) if unmatched < self.max_unmatched else 0.0
        dn_room = min(room, cap * dn_px) if -unmatched < self.max_unmatched else 0.0
        self._ensure("up", self.tok_up, up_px, up_room)
        self._ensure("dn", self.tok_dn, dn_px, dn_room)
        self._persist()

    def _roll(self, m):
        # settle the OLD window: matched sets are worth $1 each; account realized at the oracle.
        if self.inv_up >= 1 or self.inv_dn >= 1:
            self._settle_old()
        self._cancel_all()
        self.cur_slug = m.slug
        self.tok_up, self.tok_dn = m.token_up, m.token_down
        self.window_end = m.window_end
        self.cid = m.condition_id
        self.inv_up = self.inv_dn = self.cost = 0.0   # spent/received are CUMULATIVE — never reset
        self.log(f"▶ set-arb {self.cur_slug}")

    def _settle_old(self):
        """Realize the old window: total payout = matched_sets*$1 + winning unmatched side*$1.
        Uses the real oracle (feeds.resolve_market); falls back to marking the set value only."""
        matched = min(self.inv_up, self.inv_dn)
        payout = matched * 1.0
        up_won = None
        try:
            up_won = feeds.resolve_market(self.cur_slug)
        except Exception:
            up_won = None
        excess_up = self.inv_up - matched
        excess_dn = self.inv_dn - matched
        if up_won is True:
            payout += excess_up * 1.0
        elif up_won is False:
            payout += excess_dn * 1.0
        else:
            payout += (excess_up + excess_dn) * 0.5    # unknown → mark unmatched at 0.5
        self.received += payout
        self.realized = self.received - self.spent
        self.log(f"⚖ settle {self.cur_slug}: matched={matched:.1f} payout={payout:.2f} "
                 f"net realized={self.realized:+.2f}")
        self._journal("SETTLE", "", "", round(matched, 2), f"net={self.realized:+.2f}")
        try:                                            # redeem winning tokens -> pUSD (best-effort)
            if self.cid:
                self.broker.redeem(self.cid)
        except Exception as e:
            self.log(f"redeem {self.cur_slug}: {type(e).__name__}: {e}")

    def _merge_matched(self):
        """Merge matched complete sets (1 Up + 1 Down) -> $1 pUSD INSTANTLY via the relayer, locking
        the arb (paid <$1/set, no settle wait, no directional residual). The relayer SERIALIZES actions
        per wallet ('wallet busy'), so cooldown + tolerate busy (the inventory just waits a later tick)."""
        matched = min(self.inv_up, self.inv_dn)
        if matched < self.merge_min or not self.cid:
            return
        if time.time() - self._last_merge < 20:         # relayer is serial — don't spam it
            return
        self._last_merge = time.time()
        m = round(matched, 2)
        try:
            r = subprocess.run(["node", SETOPS_JS, "merge", self.cid, f"{m:.2f}"],
                               capture_output=True, text=True, timeout=75)
        except Exception as e:
            self.log(f"merge err: {type(e).__name__}: {e}")
            return
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0 and "OK" in out:
            self.received += m                          # m sets -> $m pUSD (honest cash in)
            self.inv_up -= m
            self.inv_dn -= m
            self.realized = self.received - self.spent  # net cash flow (true, no avg-cost phantom)
            self.log(f"⚗ merged {m:.1f} sets → +${m:.2f} pUSD  net realized={self.realized:+.2f}")
            self._journal("MERGE", "", "", m, f"net={self.realized:+.2f}")
        elif "wallet busy" in out:
            self.log("merge deferred: wallet busy (relayer serializing) — retry later")
        else:
            self.log(f"merge failed: {out[:100]}")

    def _ensure(self, side, token, px, room_usd):
        cur = self.bid_up if side == "up" else self.bid_dn
        if room_usd < 1 or px < 0.02:
            if cur:
                self._cancel(side)
            return
        if cur and abs(cur["price"] - px) < self.recenter_eps:
            return
        if cur:
            self._cancel(side)
        usd = round(min(room_usd, self.set_usd) , 2)
        o = self.broker.place_limit(token, px, usd, "Up" if side == "up" else "Down",
                                    self.cur_slug, expiration=self._expiry())
        if o:
            o["filled_acct"] = 0.0
            if side == "up":
                self.bid_up = o
            else:
                self.bid_dn = o
            self._journal("REST", side, px, o["shares"])

    def _expiry(self):
        return self.window_end if self.window_end - time.time() > 65 else 0

    def _check_fills(self):
        for side in ("up", "dn"):
            o = self.bid_up if side == "up" else self.bid_dn
            if not o:
                continue
            filled, px, status = self.broker.order_fill(o)
            new = filled - o.get("filled_acct", 0.0)
            if new > 1e-6:
                o["filled_acct"] = filled
                self.cost += new * px
                self.spent += new * px            # cumulative cash out (honest accounting)
                if side == "up":
                    self.inv_up += new
                else:
                    self.inv_dn += new
                self.log(f"🟢 {side} +{new:.1f}@{px:.2f}  inv_up={self.inv_up:.1f} inv_dn={self.inv_dn:.1f}")
                self._journal("BUY", side, px, round(new, 2))
            if status in ("filled", "gone"):
                if side == "up":
                    self.bid_up = None
                else:
                    self.bid_dn = None

    def _cancel(self, side):
        o = self.bid_up if side == "up" else self.bid_dn
        if o:
            try:
                self.broker.cancel(o)
            except Exception as e:
                self.log(f"cancel {side}: {e}")
            if side == "up":
                self.bid_up = None
            else:
                self.bid_dn = None

    def _cancel_all(self):
        self._cancel("up")
        self._cancel("dn")

    def status(self):
        return (f"set-arb {self.underlying} {self.iv} | up={self.inv_up:.1f} dn={self.inv_dn:.1f} "
                f"matched={min(self.inv_up,self.inv_dn):.1f} cost={self.cost:.2f} "
                f"realized={self.realized:+.2f}")
