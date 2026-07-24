"""Polymarket liquidity-REWARDS harvester — the real std0 edge (research/std0_strategy.md).

Earns MAKER_REBATE/TAKER_REBATE by posting LARGE two-sided resting maker quotes pegged TIGHT to the
mid (inside the reward max-spread band), with high uptime, on crypto Up/Down windows. This is NOT a
spread play — the realised spread ≈ breakeven; the income is the rewards program, whose score is
~ size × closeness-to-mid × two-sidedness × uptime. std0 nets ~$1,778/day this way (~75% of profit).

Mechanics, per window (btc-5m/15m where the program runs; gamma rewardsMinSize=50 shares,
rewardsMaxSpread=4.5¢):
  - MINT a block of complete sets early = ASK ammunition (recovered at redeem, net-zero round-trip).
  - Post on BOTH Up and Down a resting BID at mid−d and ASK at mid+d, each >= min_size shares,
    with d < max_band/2 so every order sits inside the reward band. Re-peg every tick to track the
    moving mid (cancel/replace when it drifts > recenter_eps).
  - Skew against inventory (lean the side you're getting long) to stay delta-neutral; only ~15% of
    quotes fill, the balanced minted block is held and REDEEMED at settle (never merged).
  - Stop quoting flatten_buf_s before settle; redeem the held winning shares after close.

On-chain mint/redeem via the builder relayer (node ~/mint/setops.js). CLOB bids/asks/cancels via
live.LiveBroker (place_limit/place_sell/cancel/order_fill). Honest cash-flow accounting:
spent (mint + bid fills) vs received (ask fills + redeems); P&L reconciled to wallet value. The
rewards land as separate MAKER_REBATE rows in the data-api feed (not modelled here — measured live).
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
from pathlib import Path

from . import feeds
from .feeds import INTERVALS

# On-chain set ops backend. Default = the relayer (setops.js); override MINT_JS=~/mint/safeops.js to
# self-submit from a Gnosis Safe (zero relayer / zero quota) — same `<cmd> <cid> <usd>` CLI + "OK" on
# success, so it's a drop-in (see research/std0/safeops.js, the std0 Safe unblock 2026-06-29).
SETOPS_JS = os.path.expanduser(os.environ.get("MINT_JS", "~/mint/setops.js"))


def book_mid(book: dict):
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None
    bb = max(b[0] for b in bids)
    ba = min(a[0] for a in asks)
    return bb, ba, round((bb + ba) / 2, 4)


class RewardMM:
    def __init__(self, args, broker, log=print):
        self.iv = args.interval
        self.underlying = args.underlying
        self.mint_usd = args.mint_usd          # $ of sets to mint as ask ammunition (recovered @ redeem)
        self.clip = args.clip                  # shares per resting order (must be >= reward min_size)
        self.quote_dist = args.quote_dist      # peg bids/asks this far off mid (< max_band/2)
        self.back_off = getattr(args, "back_off", 0.0)  # sit this far behind the touch (avoid crosses, rest)
        self.max_band = args.max_band          # reward max-spread band (4.5¢ on btc) — stay inside it
        self.max_inv = args.max_inv            # max directional shares (skew quotes to defend this)
        self.min_quote = args.min_quote        # only operate when mid in this band (near 0.5 = two-sided)
        self.max_quote = args.max_quote
        self.recenter_eps = args.recenter_eps  # re-peg when mid drifts more than this
        self.flatten_buf_s = args.flatten_buf  # stop quoting this long before settle
        self.kill_loss = args.kill_loss
        self.spot_anchor = getattr(args, "spot_anchor", False)  # center quotes on Binance spot fair-p
        self.beta = getattr(args, "beta", 0.5)                  # blend: 0=CLOB mid, 1=pure spot-fair
        # liquidity-REWARDS mode: rest bid AND ask simultaneously on each token for two-sided UPTIME
        # (the Qmin score = min(bid,ask)·proximity·uptime needs both sides present), vs the default
        # maker-rebate mode where bids only replenish sold inventory (one side often empty → Qmin=0).
        self.uptime_bids = getattr(args, "uptime_bids", False)
        self._last_mu = self._last_md = 0.5    # last seen Up/Down mids — value held inventory at auto-redeem
        self._pusd = 0.0                       # last-read on-chain Safe pUSD (mint gate + P&L anchor)
        self.baseline_pusd = None              # pUSD at first sync — the P&L zero (real money, not cash-flow)
        self.sym = feeds.SYMBOL.get(args.underlying, "BTCUSDT")
        self.open_px = None                    # cached window-open spot (set in _roll)
        self.sigma = 0.0                       # cached EWMA vol/min (set in _roll)
        self._last_op = 0.0                    # relayer ops are QUOTA-limited (429) — never spam them
        self.op_cooldown = getattr(args, "op_cooldown", 25.0)
        self._recovered = False                # merge the block at most ONCE per window (quota)
        self.broker = broker
        self.log = log
        self.sd = Path(args.state_dir)
        self.sd.mkdir(exist_ok=True)
        st = self._load()
        self.minted = st.get("minted", 0.0)
        self.inv_up = st.get("inv_up", 0.0)    # held Up  = minted + bought_up − sold_up
        self.inv_dn = st.get("inv_dn", 0.0)    # held Down
        self.spent = st.get("spent", 0.0)      # cumulative $ out (mint + bid fills)
        self.received = st.get("received", 0.0)  # cumulative $ in (ask fills + redeems)
        self.cur_slug = st.get("cur_slug")
        self.cid = st.get("cid")
        self.tok_up = st.get("tok_up")
        self.tok_dn = st.get("tok_dn")
        self.window_end = st.get("window_end", 0)
        self.orders = st.get("orders", {})     # key -> resting order dict (bid_up/ask_up/bid_dn/ask_dn)
        self.killed = st.get("killed", False)
        self.realized = self.received - self.spent
        # marked P&L incl. held inventory (set each tick). Persisted so a read-only reader
        # (the dashboard) shows the TRUE P&L, not just the misleading cash-flow `realized`
        # (mint spends cash up-front; the held block is only worth $ once merged/redeemed).
        self.mark = st.get("mark", self.realized)
        self.fills = st.get("fills", 0)            # count of maker fills (the rebate driver)
        self.fill_pq = st.get("fill_pq", 0.0)      # cumulative Σ p(1−p)·shares → rebate ≈ 0.014·this

    # ----------------------------------------------------------- state ---
    def _load(self):
        p = self.sd / "rewardmm_state.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _persist(self):
        (self.sd / "rewardmm_state.json").write_text(json.dumps({
            "minted": self.minted, "inv_up": self.inv_up, "inv_dn": self.inv_dn,
            "spent": self.spent, "received": self.received, "cur_slug": self.cur_slug,
            "cid": self.cid, "tok_up": self.tok_up, "tok_dn": self.tok_dn,
            "window_end": self.window_end, "orders": self.orders, "killed": self.killed,
            "fills": self.fills, "fill_pq": self.fill_pq, "mark": round(self.mark, 4),
            "rebate": round(0.014 * self.fill_pq, 4), "ts": int(time.time())}))

    def _journal(self, kind, key, px, sh, note=""):
        f = self.sd / "rewardmm_journal.csv"
        new = not f.exists()
        with f.open("a") as fh:
            if new:
                fh.write("ts,kind,slug,order,price,shares,inv_up,inv_dn,realized,note\n")
            fh.write(f"{int(time.time())},{kind},{self.cur_slug},{key},{px},{sh},"
                     f"{round(self.inv_up,1)},{round(self.inv_dn,1)},{round(self.realized,2)},{note}\n")

    # ----------------------------------------------------------- relayer ops ---
    def _setop(self, cmd, amt) -> bool:
        if getattr(self.broker, "dry_run", True):
            self.log(f"DRY-RUN {cmd} {amt:.2f} — NOT sent")
            return True
        if time.time() - self._last_op < self.op_cooldown:
            return False                        # relayer quota guard — never burst ops
        self._last_op = time.time()
        try:
            r = subprocess.run(["node", SETOPS_JS, cmd, self.cid, f"{amt:.2f}"],
                               capture_output=True, text=True, timeout=80)
        except Exception as e:
            self.log(f"{cmd} err: {type(e).__name__}: {e}")
            return False
        out = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0 and "OK" in out:
            return True
        self.log(f"{cmd} {'busy' if 'busy' in out else 'failed'}: {out[:100]}")
        return False

    # ----------------------------------------------------------- on-chain truth ---
    def _sync_onchain(self) -> None:
        """The ON-CHAIN balance is the truth — auto-redeem at settle, GTD orders that fill AFTER the
        process is killed, and restarts all make the in-memory cash-flow model DRIFT (observed: thought
        0/10, really held 110/100 → tried to double-mint → 'balance 0'). Read the Safe's actual pUSD +
        held tokens for the CURRENT window and overwrite the tracked inventory. Called every roll so
        desync can NEVER accumulate. Also re-anchors the P&L zero to real pUSD (not the drifting
        received−spent). DRY-RUN: no-op (no real balances)."""
        if getattr(self.broker, "dry_run", True) or not self.tok_up:
            return
        try:
            r = subprocess.run(["node", SETOPS_JS, "baltoks", str(self.tok_up), str(self.tok_dn)],
                               capture_output=True, text=True, timeout=40)
            out = r.stdout or ""
            pu = re.search(r"pUSD:\s*([\d.]+)", out)
            ud = re.search(r"Up:\s*([\d.]+)\s+Down:\s*([\d.]+)", out)
            if not (pu and ud):
                self.log(f"sync: unparseable balOut {out[:80]!r}"); return
            self._pusd = float(pu.group(1))
            self.inv_up, self.inv_dn = float(ud.group(1)), float(ud.group(2))
            self.minted = min(self.inv_up, self.inv_dn)          # gates reflect REAL holdings
            # NB: do NOT re-anchor realized to live pUSD here — at a roll the just-settled block's
            # auto-redeem LAGS (seconds–minutes), so pUSD momentarily reads low (mint spent, redeem
            # pending) → a phantom −$100 realized → false KILL. The running P&L uses the lag-free
            # auto-redeem ESTIMATE (see _roll); _pusd is only for the mint gate + telemetry.
            self.log(f"⟲ sync: pUSD={self._pusd:.2f} inv={self.inv_up:.0f}/{self.inv_dn:.0f}")
        except Exception as e:
            self.log(f"sync err: {type(e).__name__}: {e}")

    def _pusd_now(self) -> float:
        """Live pUSD for the mint gate (fresh — the per-roll sync can be stale vs a just-landed redeem)."""
        if getattr(self.broker, "dry_run", True):
            return 1e9
        try:
            return float(self.broker.usdc_balance())
        except Exception:
            return self._pusd

    # ----------------------------------------------------------- tick ---
    def on_tick(self):
        if self.killed:
            self._cancel_all(); return
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
        self._last_mu, self._last_md = mu[2], md[2]   # for valuing held inventory at the auto-redeem roll
        self._check_fills()
        mark = (self.received - self.spent) + min(self.inv_up, self.inv_dn) * 1.0 \
            + (self.inv_up - min(self.inv_up, self.inv_dn)) * mu[2] \
            + (self.inv_dn - min(self.inv_up, self.inv_dn)) * md[2]
        self.mark = mark                            # true P&L ex-rebate (realized=cash-flow is misleading)
        if mark <= -self.kill_loss:
            self.log(f"🛑 KILL marked P&L {mark:+.2f}")
            self.killed = True; self._cancel_all(); self._persist(); return
        remaining = self.window_end - now
        if remaining <= self.flatten_buf_s:
            self._cancel_all()                      # stop quoting; this account AUTO-REDEEMS the held
            self._persist(); return                 # block at settle (no merge needed — see _roll credit)
        if not (self.min_quote <= mu[2] <= self.max_quote):
            self._cancel_all(); self._persist(); return
        # 1. mint ask ammunition once — ONLY if live pUSD covers it (a stale model once tried to mint a
        #    second block while the first was still held → 'balance 0'; the live check makes that impossible).
        if self.minted < 1 and self.mint_usd >= 1 and self._pusd_now() >= self.mint_usd \
                and self._setop("mint", self.mint_usd):
            self.minted = self.mint_usd
            self.inv_up += self.mint_usd
            self.inv_dn += self.mint_usd
            self.spent += self.mint_usd
            self.log(f"⚒ minted {self.minted:.0f} sets on {self.cur_slug}")
            self._journal("MINT", "", "", self.minted)
        # 2. JOIN the touch (best bid / best ask) — post-only safe (never crosses the book), rests
        #    top-of-book to get filled by takers. ASKS spend inventory; BIDS rebuild it. Inventory
        #    skew: stop quoting the side we're already too long, to stay delta-neutral.
        long_up = self.inv_up - self.inv_dn        # >0 ⇒ long Up
        # ASKS sell from the minted block. BIDS only REPLENISH what the asks sold (inv < minted) —
        # never grow inventory directionally — and shut off on the side we're already too long. This
        # keeps held ≈ minted on both sides ⇒ delta-neutral, churning the block for rebate fills.
        # quote centres: default = join the touch (best ask / best bid); with spot-anchor = blend the
        # CLOB mid toward the spot-derived fair p (which leads the CLOB) and quote ±quote_dist around it.
        bo = self.back_off          # sit this far BEHIND the touch: post-only never crosses + the order
        au, bu = round(mu[1] + bo, 2), round(mu[0] - bo, 2)   # rests long enough for a taker to reach it
        ad, bd = round(md[1] + bo, 2), round(md[0] - bo, 2)
        fp = self._fair_p()
        if fp is not None:
            d = self.quote_dist
            cu = (1 - self.beta) * mu[2] + self.beta * fp
            cd = (1 - self.beta) * md[2] + self.beta * (1 - fp)
            au, bu = round(cu + d, 2), round(cu - d, 2)
            ad, bd = round(cd + d, 2), round(cd - d, 2)
        # cap BOTH asks AND bids by the inventory band, else the asks alone drain one side -> directional.
        # selling Down lengthens Up (long_up↑) -> gate ask_dn at +max_inv; selling Up lengthens Down -> gate
        # ask_up at −max_inv; bids symmetric.
        ask_up_on = self.inv_up >= self.clip and long_up > -self.max_inv
        ask_dn_on = self.inv_dn >= self.clip and long_up < self.max_inv
        if self.uptime_bids:
            # rewards mode: keep a bid resting for two-sided uptime, gated only by net-neutrality
            # (max_inv) and a gross cap (don't accumulate more than ~minted+max_inv per side).
            bid_up_on = long_up < self.max_inv and self.inv_up < self.minted + self.max_inv
            bid_dn_on = long_up > -self.max_inv and self.inv_dn < self.minted + self.max_inv
        else:
            bid_up_on = self.inv_up < self.minted and long_up < self.max_inv
            bid_dn_on = self.inv_dn < self.minted and long_up > -self.max_inv
        self._quote("ask_up", self.tok_up, "Up", au, self.clip if ask_up_on else 0)
        self._quote("ask_dn", self.tok_dn, "Down", ad, self.clip if ask_dn_on else 0)
        self._quote("bid_up", self.tok_up, "Up", bu, self.clip if bid_up_on else 0, bid=True)
        self._quote("bid_dn", self.tok_dn, "Down", bd, self.clip if bid_dn_on else 0, bid=True)
        self._persist()

    def _roll(self, m):
        # The account AUTO-REDEEMS the held block at settle. Credit that redeem to `received` with a
        # LAG-FREE ESTIMATE (matched sets → $1 each, certain; residual imbalance → EV = last mid) BEFORE
        # switching window — else the up-front mint spend reads as a phantom loss → false KILL. Then
        # _sync_onchain() overwrites the inventory with the ON-CHAIN truth (fixes desync from GTD fills
        # after a kill / restarts). pUSD is NOT used for P&L (its redeem LAGS the roll → would phantom-
        # kill); it gates the mint only.
        if self.cur_slug and (self.inv_up > 1e-6 or self.inv_dn > 1e-6):
            matched = min(self.inv_up, self.inv_dn)
            redeem = matched + (self.inv_up - matched) * self._last_mu \
                + (self.inv_dn - matched) * self._last_md
            self.received += redeem
            self.realized = self.received - self.spent
            self.log(f"↩ auto-redeem {self.inv_up:.1f}/{self.inv_dn:.1f} → +${redeem:.2f}  "
                     f"realized={self.realized:+.2f}")
        self._cancel_all()
        self.cur_slug, self.cid = m.slug, m.condition_id
        self.tok_up, self.tok_dn = m.token_up, m.token_down
        self.window_end = m.window_end
        self.minted = self.inv_up = self.inv_dn = 0.0
        self._recovered = False
        self._sync_onchain()                   # overwrite inv with the on-chain truth (desync guard)
        if self.spot_anchor:                   # cache once/window (avoid 2 slow API calls per tick)
            try:
                self.open_px = feeds.btc_price_at(m.window_start, self.sym)
                self.sigma = feeds.realized_vol_per_min(feeds.btc_klines_1m(symbol=self.sym))
            except Exception:
                self.open_px, self.sigma = None, 0.0
        self.realized = self.received - self.spent
        self.log(f"▶ rewardMM {self.cur_slug}  realized={self.realized:+.2f}")

    def _fair_p(self):
        """Spot-derived P(Up)=P(close>open) from Binance spot+vol+time-remaining. It LEADS the lagging
        CLOB mid, so centering quotes on it cuts adverse selection (we re-price before takers can hit a
        stale CLOB quote). Returns None when disabled or data missing → caller falls back to the touch."""
        if not self.spot_anchor or not self.open_px or self.sigma <= 0:
            return None
        try:
            spot = feeds.btc_spot(self.sym)
            tau = max(1.0 / 60, (self.window_end - time.time()) / 60.0)
            z = (spot - self.open_px) / (self.open_px * self.sigma * math.sqrt(tau))
            return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        except Exception:
            return None

    def _recover(self):
        """Near settle: MERGE matched held sets (1 Up + 1 Down → $1 pUSD) INSTANTLY — no resolution
        wait, robust vs redeem. ONE attempt per window (quota): the merge is a relayer op and the
        relayer rate-limits (429). The small unmatched remainder settles and is swept later."""
        if self._recovered:
            return
        matched = min(self.inv_up, self.inv_dn)
        if matched < 1 or not self.cid:
            return
        self._recovered = True                  # spend the window's one recovery attempt
        if self._setop("merge", matched):
            self.received += matched
            self.inv_up -= matched
            self.inv_dn -= matched
            self.realized = self.received - self.spent
            self.log(f"⚗ recovered {matched:.0f} matched sets → +${matched:.0f}  realized={self.realized:+.2f}")
            self._journal("MERGE", "", "", round(matched, 1))

    # ----------------------------------------------------------- quoting ---
    def _quote(self, key, token, direction, px, shares, bid=False):
        cur = self.orders.get(key)
        px = max(0.02, min(0.98, px))
        if shares < 1:
            self._cancel(key); return
        if cur and abs(cur["price"] - px) < self.recenter_eps:
            return                                  # still near the mid — leave it resting (uptime)
        self._cancel(key)
        if bid:
            o = self.broker.place_limit(token, px, round(shares * px, 2), direction, self.cur_slug,
                                        expiration=self._expiry())
        else:
            o = self.broker.place_sell(token, px, shares, self.cur_slug, direction=direction,
                                       expiration=self._expiry())
        if o:
            o["filled_acct"] = 0.0
            self.orders[key] = o
            self._journal("BID" if bid else "ASK", key, px, round(shares, 1))

    def _expiry(self):
        return self.window_end if self.window_end - time.time() > 65 else 0

    def _check_fills(self):
        for key in ("ask_up", "ask_dn", "bid_up", "bid_dn"):
            o = self.orders.get(key)
            if not o:
                continue
            filled, px, status = self.broker.order_fill(o)
            new = filled - o.get("filled_acct", 0.0)
            if new > 1e-6:
                o["filled_acct"] = filled
                self.fills += 1
                self.fill_pq += px * (1 - px) * new   # maker rebate ≈ 0.014 · Σ p(1−p)·filled shares
                up = key.endswith("up")
                if key.startswith("ask"):              # we SOLD -> inventory down, cash in
                    self.received += new * px
                    if up: self.inv_up -= new
                    else:  self.inv_dn -= new
                else:                                   # we BOUGHT -> inventory up, cash out
                    self.spent += new * px
                    if up: self.inv_up += new
                    else:  self.inv_dn += new
                self.realized = self.received - self.spent
                self.log(f"{'🔴 sold' if key.startswith('ask') else '🟢 bought'} {key} {new:.1f}@{px:.2f}"
                         f"  inv {self.inv_up:.0f}/{self.inv_dn:.0f} realized={self.realized:+.2f}")
                self._journal("FILL", key, px, round(new, 1))
            if status in ("filled", "gone"):
                self.orders.pop(key, None)

    def _cancel(self, key):
        o = self.orders.get(key)
        if o:
            try:
                self.broker.cancel(o)
            except Exception as e:
                self.log(f"cancel {key}: {e}")
            self.orders.pop(key, None)

    def _cancel_all(self):
        for key in list(self.orders.keys()):
            self._cancel(key)

    def status(self):
        return (f"rewardMM {self.underlying} {self.iv} | inv={self.inv_up:.0f}/{self.inv_dn:.0f} "
                f"mark={self.mark:+.2f} (cash={self.realized:+.2f}) | fills={self.fills} "
                f"rebate≈${0.014 * self.fill_pq:.3f} | resting={len(self.orders)}"
                f"{' 🛑KILLED' if self.killed else ''}")
