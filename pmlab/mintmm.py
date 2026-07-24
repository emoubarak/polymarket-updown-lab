"""std0-style complete-set MARKET-MAKER (the real sell-side edge).

Replicates the reference wallet 0xdf79… (see research/std0_ops.md + std0_strategy.md):
  1. MINT a batch of complete sets EARLY in a window (split $X pUSD -> X Up + X Down) via the
     Builder Relayer (node setops.js — gasless after the one POL-funded EOA approval).
  2. Post RESTING two-sided maker SELLS on BOTH Up and Down at a premium (mid + edge) — the
     overround/favorite-longshot skew. Off-chain CLOB orders (gasless, NOT relayer-serialized).
  3. Let them fill GRADUALLY over the window (std0 sells 0% in the first 60s — it does NOT dump);
     refresh/re-centre the asks as the mid drifts.
  4. Near settle: MERGE matched unsold sets back to pUSD (instant, breakeven recovery) and let the
     rest REDEEM at settlement. Stay roughly delta-neutral (sell both sides ~equally).

Profit per set fully sold ≈ (ask_up + ask_dn) − $1 = (mid_up+mid_dn − 1) + 2·edge, i.e. the spread/
overround we add on top of the mids. Unsold sets recover $1 (breakeven). HONEST accounting: cost =
$ minted, proceeds = $ from sells + merges + redeems; P&L = proceeds − cost, reconciled to the wallet.

On-chain ops (mint/merge/redeem) go through node `~/mint/setops.js` (relayer). CLOB sells/cancels go
through live.LiveBroker (place_sell/cancel/order_fill). Stdlib + subprocess.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from . import feeds
from .feeds import INTERVALS

SETOPS_JS = os.path.expanduser("~/mint/setops.js")


def book_tob(book: dict):
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None
    bb = max(b[0] for b in bids)
    ba = min(a[0] for a in asks)
    return bb, ba, round((bb + ba) / 2, 4)


class MintMM:
    def __init__(self, args, broker, log=print):
        self.iv = args.interval
        self.underlying = args.underlying
        self.mint_usd = args.mint_usd          # $ of complete sets to mint per window
        self.sell_edge = args.sell_edge        # post each ask this far ABOVE its mid (the premium)
        self.max_imbalance = getattr(args, "max_imbalance", 2.0)  # cap directional (sold_up−sold_dn) shares
        self.sell_floor = getattr(args, "sell_floor", 0.51)       # never sell a side below this (no fire-sale)
        self.min_quote = args.min_quote        # only operate when mid in [min_quote, max_quote]
        self.max_quote = args.max_quote
        self.enter_lo = args.enter_lo          # mint when window-fraction-remaining first drops below this
        self.recenter_eps = args.recenter_eps
        self.flatten_buf_s = args.flatten_buf  # stop selling / start recovering this long before settle
        self.kill_loss = args.kill_loss        # hard stop if marked P&L <= -this
        self.broker = broker
        self.log = log
        self.sd = Path(args.state_dir)
        self.sd.mkdir(exist_ok=True)
        st = self._load()
        self.minted = st.get("minted", 0.0)    # sets minted this window
        self.sold_up = st.get("sold_up", 0.0)
        self.sold_dn = st.get("sold_dn", 0.0)
        self.spent = st.get("spent", 0.0)       # cumulative $ minted (cost)
        self.received = st.get("received", 0.0)  # cumulative $ from sells + merges + redeems
        self.cur_slug = st.get("cur_slug")
        self.cid = st.get("cid")
        self.tok_up = st.get("tok_up")
        self.tok_dn = st.get("tok_dn")
        self.window_end = st.get("window_end", 0)
        self.ask_up = st.get("ask_up")
        self.ask_dn = st.get("ask_dn")
        self.killed = st.get("killed", False)
        self.realized = self.received - self.spent

    # --------------------------------------------------------- state ---
    def _load(self):
        p = self.sd / "mintmm_state.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _persist(self):
        (self.sd / "mintmm_state.json").write_text(json.dumps({
            "minted": self.minted, "sold_up": self.sold_up, "sold_dn": self.sold_dn,
            "spent": self.spent, "received": self.received, "cur_slug": self.cur_slug,
            "cid": self.cid, "tok_up": self.tok_up, "tok_dn": self.tok_dn,
            "window_end": self.window_end, "ask_up": self.ask_up, "ask_dn": self.ask_dn,
            "killed": self.killed}))

    def _journal(self, kind, side, px, sh, note=""):
        f = self.sd / "mintmm_journal.csv"
        new = not f.exists()
        with f.open("a") as fh:
            if new:
                fh.write("ts,kind,slug,side,price,shares,minted,sold_up,sold_dn,realized,note\n")
            fh.write(f"{int(time.time())},{kind},{self.cur_slug},{side},{px},{sh},"
                     f"{round(self.minted,2)},{round(self.sold_up,2)},{round(self.sold_dn,2)},"
                     f"{round(self.realized,2)},{note}\n")

    # --------------------------------------------------------- ops ---
    def _setop(self, cmd, usd_or_sh) -> bool:
        """Run a relayer on-chain op (mint/merge/redeem) via the node helper. Tolerates 'wallet busy'."""
        if getattr(self.broker, "dry_run", True):
            self.log(f"DRY-RUN {cmd} {usd_or_sh:.2f} — NOT sent")
            return True
        try:
            r = subprocess.run(["node", SETOPS_JS, cmd, self.cid, f"{usd_or_sh:.2f}"],
                               capture_output=True, text=True, timeout=80)
        except Exception as e:
            self.log(f"{cmd} err: {type(e).__name__}: {e}")
            return False
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0 and "OK" in out:
            return True
        if "busy" in out:
            self.log(f"{cmd} deferred: wallet busy")
        else:
            self.log(f"{cmd} failed: {out[:120]}")
        return False

    def _redeem(self) -> bool:
        if getattr(self.broker, "dry_run", True):
            self.log("DRY-RUN redeem — NOT sent")
            return True
        try:
            r = subprocess.run(["node", SETOPS_JS, "redeem", self.cid, "0"],
                               capture_output=True, text=True, timeout=80)
            return r.returncode == 0 and "OK" in (r.stdout or "")
        except Exception as e:
            self.log(f"redeem err: {type(e).__name__}: {e}")
            return False

    # --------------------------------------------------------- tick ---
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
        if m.slug != self.cur_slug:
            self._roll(m)
        bu = feeds.fetch_book(self.tok_up) or {}
        bd = feeds.fetch_book(self.tok_dn) or {}
        tu = book_tob(bu)
        td = book_tob(bd)
        if tu is None or td is None:
            return
        self._check_fills()
        # mark-to-market P&L: cash flow + value of held inventory (matched sets ~$1, lone tokens at mid)
        held_up = max(0.0, self.minted - self.sold_up)
        held_dn = max(0.0, self.minted - self.sold_dn)
        matched = min(held_up, held_dn)
        mark = (self.received - self.spent) + matched * 1.0 + (held_up - matched) * tu[2] + (held_dn - matched) * td[2]
        if mark <= -self.kill_loss:
            self.log(f"🛑 KILL: marked P&L {mark:+.2f} <= -{self.kill_loss}")
            self.killed = True
            self._cancel_all()
            self._persist()
            return
        remaining = self.window_end - now
        if remaining <= self.flatten_buf_s:
            self._recover(held_up, held_dn)            # stop selling; merge matched + redeem rest
            self._persist()
            return
        if not (self.min_quote <= tu[2] <= self.max_quote):  # only MM where both sides are live
            self._cancel_all()
            self._persist()
            return
        # 1. mint inventory once, on first entry into the active band (the band gates it to the
        #    near-0.5 working period — std0 mints early, then rests sells over the window)
        if self.minted < 1 and self.mint_usd >= 1:
            if self._setop("mint", self.mint_usd):
                self.minted = self.mint_usd            # 1 set per $1
                self.spent += self.mint_usd
                self.log(f"⚒ minted {self.minted:.0f} sets (${self.mint_usd:.0f}) on {self.cur_slug}")
                self._journal("MINT", "", "", self.minted)
        # 2. post resting two-sided maker SELLS at max(mid+edge, floor). CAP the imbalance so we never
        #    go > max_imbalance shares directional (selling one side while holding the other), and FLOOR
        #    the price so we never fire-sale the falling side. In a trend the falling side simply doesn't
        #    fill (held -> merged/redeemed at breakeven); only the premium on filled sides is the edge.
        imb = self.sold_up - self.sold_dn
        up_sz = held_up if imb < self.max_imbalance else 0.0
        dn_sz = held_dn if -imb < self.max_imbalance else 0.0
        up_px = round(max(tu[2] + self.sell_edge, self.sell_floor), 2)
        dn_px = round(max(td[2] + self.sell_edge, self.sell_floor), 2)
        self._ensure_ask("up", self.tok_up, up_px, up_sz)
        self._ensure_ask("dn", self.tok_dn, dn_px, dn_sz)
        self._persist()

    @property
    def win_min(self):
        return INTERVALS[self.iv] / 60.0

    def _roll(self, m):
        # recover anything left on the OLD window before moving on (best-effort redeem)
        if self.minted >= 1 and self.cid:
            self._redeem()
            # account: assume matched recovered at $1 (settlement pays $1/complete set held)
            held_up = max(0.0, self.minted - self.sold_up)
            held_dn = max(0.0, self.minted - self.sold_dn)
            self.received += min(held_up, held_dn) * 1.0   # matched -> $1 (unmatched is oracle-dependent)
            self._journal("ROLL_REDEEM", "", "", round(min(held_up, held_dn), 2))
        self._cancel_all()
        self.cur_slug, self.cid = m.slug, m.condition_id
        self.tok_up, self.tok_dn = m.token_up, m.token_down
        self.window_end = m.window_end
        self.minted = self.sold_up = self.sold_dn = 0.0
        self.realized = self.received - self.spent
        self.log(f"▶ mintMM {self.cur_slug}  realized={self.realized:+.2f}")

    def _recover(self, held_up, held_dn):
        """Near settle: stop selling. Merge matched unsold sets -> pUSD (instant breakeven recovery);
        the unmatched remainder rides to settlement (redeemed on roll)."""
        self._cancel_all()
        matched = min(held_up, held_dn)
        if matched >= 1 and self.cid:
            if self._setop("merge", matched):
                self.received += matched
                self.sold_up += matched            # account as removed from inventory
                self.sold_dn += matched
                self.realized = self.received - self.spent
                self.log(f"⚗ recovered {matched:.0f} matched sets → +${matched:.0f}  realized={self.realized:+.2f}")
                self._journal("MERGE", "", "", round(matched, 2))

    # --------------------------------------------------------- sells ---
    def _ensure_ask(self, side, token, px, size):
        cur = self.ask_up if side == "up" else self.ask_dn
        px = min(px, 0.99)
        if size < 1 or px <= 0.5:                  # nothing to sell, or a "premium" below 0.5 = no edge
            self._drop(side)
            return
        if cur and abs(cur["price"] - px) < self.recenter_eps:
            return
        self._drop(side)
        o = self.broker.place_sell(token, px, size, self.cur_slug,
                                   direction=("Up" if side == "up" else "Down"),
                                   expiration=self._expiry())
        if o:
            o["filled_acct"] = 0.0
            if side == "up":
                self.ask_up = o
            else:
                self.ask_dn = o
            self._journal("ASK", side, px, round(size, 2))

    def _expiry(self):
        return self.window_end if self.window_end - time.time() > 65 else 0

    def _check_fills(self):
        for side in ("up", "dn"):
            o = self.ask_up if side == "up" else self.ask_dn
            if not o:
                continue
            filled, px, status = self.broker.order_fill(o)
            new = filled - o.get("filled_acct", 0.0)
            if new > 1e-6:
                o["filled_acct"] = filled
                self.received += new * px
                if side == "up":
                    self.sold_up += new
                else:
                    self.sold_dn += new
                self.realized = self.received - self.spent
                self.log(f"🔴 sold {side} {new:.1f}@{px:.2f}  realized={self.realized:+.2f}")
                self._journal("SELL", side, px, round(new, 2))
            if status in ("filled", "gone"):
                if side == "up":
                    self.ask_up = None
                else:
                    self.ask_dn = None

    def _drop(self, side):
        o = self.ask_up if side == "up" else self.ask_dn
        if o:
            try:
                self.broker.cancel(o)
            except Exception as e:
                self.log(f"cancel {side}: {e}")
            if side == "up":
                self.ask_up = None
            else:
                self.ask_dn = None

    def _cancel_all(self):
        self._drop("up")
        self._drop("dn")

    def status(self):
        return (f"mintMM {self.underlying} {self.iv} | minted={self.minted:.0f} "
                f"sold_up={self.sold_up:.1f} sold_dn={self.sold_dn:.1f} realized={self.realized:+.2f}"
                f"{' | 🛑KILLED' if self.killed else ''}")
