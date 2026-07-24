"""std0-competitive HFT maker — WebSocket-driven, co-located, sub-second re-quote.

The poll-1s rewardmm bot is a bike in an F1 race: it sees the book ~1s stale and gets adversely
selected. std0's edge is SPEED — it re-prices the instant the (leading) Binance spot moves, before the
(lagging) CLOB taker can hit its stale quote. We already have the chassis (Dublin 22ms RTT). This is
the engine: real-time market data over WebSocket, not REST polls. Measured: WS delivers book updates in
~ms vs the 1s poll = ~1000× faster reaction. Language is NOT the bottleneck (network RTT 22ms dominates;
Python parse+sign ~5ms is noise) — the levers are WS + async order ops + pre-signed orders + co-loc.

This module = the live market-data layer first (LiveBook from the CLOB /ws/market feed). The quote/skew
engine + async order ops build on top. Validate every layer in DRY (real data, no money) before live.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time

import websocket  # websocket-client (pip)

from . import mm_guard  # safety invariants (stdlib) — HALT on the bugs that cost real money

CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CLOB_USER_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
BINANCE_WS = "wss://stream.binance.com:9443/ws"
# on-chain set ops (mint/merge) backend — Safe self-submit via MINT_JS=~/mint/safeops.js
SETOPS_JS = os.path.expanduser(os.environ.get("MINT_JS", "~/mint/setops.js"))


class LiveBook:
    """Best bid/ask per token, maintained in real time from the CLOB /ws/market feed. The initial
    `initial_dump` is a full book per asset; subsequent `price_changes` carry `best_bid`/`best_ask`
    directly (no book reconstruction needed). Thread-safe; fires `on_update(asset_id)` on every change."""

    def __init__(self, token_ids, on_update=None, log=print):
        self.tokens = list(token_ids)
        self.best = {t: (None, None, 0) for t in self.tokens}   # asset_id -> (best_bid, best_ask, ts_ms)
        self.on_update = on_update
        self.log = log
        self._lock = threading.Lock()
        self.msgs = 0
        self.last_msg_ts = 0.0
        self._ws = None
        self._t = None

    def _touch_from_levels(self, bids, asks):
        bb = max((float(b["price"]) for b in bids), default=None) if bids else None
        ba = min((float(a["price"]) for a in asks), default=None) if asks else None
        return bb, ba

    def _on_message(self, ws, raw):
        now = time.time()
        self.msgs += 1
        self.last_msg_ts = now
        try:
            data = json.loads(raw)
        except Exception:
            return
        changed = []
        if isinstance(data, list):                       # initial full-book dump (one entry per asset)
            for e in data:
                aid = e.get("asset_id")
                if aid in self.best:
                    bb, ba = self._touch_from_levels(e.get("bids"), e.get("asks"))
                    with self._lock:
                        self.best[aid] = (bb, ba, int(e.get("timestamp", 0)))
                    changed.append(aid)
        elif isinstance(data, dict) and "price_changes" in data:
            for c in data["price_changes"]:
                aid = c.get("asset_id")
                if aid in self.best and c.get("best_bid") is not None:
                    with self._lock:
                        self.best[aid] = (float(c["best_bid"]), float(c["best_ask"]), int(time.time() * 1000))
                    changed.append(aid)
        for aid in changed:
            if self.on_update:
                try:
                    self.on_update(aid)
                except Exception as e:
                    self.log(f"on_update err: {e}")

    def touch(self, token):
        with self._lock:
            return self.best.get(token, (None, None, 0))

    def mid(self, token):
        bb, ba, _ = self.touch(token)
        return round((bb + ba) / 2, 4) if (bb is not None and ba is not None) else None

    def start(self):
        def _open(ws):
            ws.send(json.dumps({"type": "market", "assets_ids": self.tokens, "initial_dump": True}))

            def _ping():
                while True:
                    time.sleep(30)
                    try: ws.send("PING")
                    except Exception: break
            threading.Thread(target=_ping, daemon=True).start()

        self._ws = websocket.WebSocketApp(CLOB_WS, on_open=_open, on_message=self._on_message,
                                          on_error=lambda w, e: self.log(f"WS err: {e}"),
                                          on_close=lambda w, c, r: self.log(f"WS closed {c}"))
        self._t = threading.Thread(target=self._ws.run_forever, kwargs={"ping_interval": 0}, daemon=True)
        self._t.start()

    def stop(self):
        if self._ws:
            try: self._ws.close()
            except Exception: pass


class SpotFeed:
    """Live Binance spot (bookTicker mid) over WebSocket — leads the lagging CLOB; the re-quote trigger."""

    def __init__(self, symbol="ethusdt", on_update=None, log=print):
        self.symbol = symbol.lower()
        self.spot = None
        self.on_update = on_update
        self.log = log
        self.msgs = 0
        self._ws = None

    def _on_message(self, ws, raw):
        try:
            d = json.loads(raw)
            self.spot = (float(d["b"]) + float(d["a"])) / 2     # bookTicker: b=best bid, a=best ask
            self.msgs += 1
            if self.on_update:
                self.on_update(self.spot)
        except Exception:
            pass

    def start(self):
        url = f"{BINANCE_WS}/{self.symbol}@bookTicker"
        self._ws = websocket.WebSocketApp(url, on_message=self._on_message,
                                          on_error=lambda w, e: self.log(f"spot WS err: {e}"))
        threading.Thread(target=self._ws.run_forever, daemon=True).start()

    def stop(self):
        if self._ws:
            try: self._ws.close()
            except Exception: pass


class HftMM:
    """Event-driven quote engine. On every WS spot/book tick: fair_p (spot leads the CLOB) → target
    two-sided quotes skewed by inventory → cancel/replace only the orders whose target moved (rate-limit
    aware). Aggressive skew offloads the side we're getting long → keeps us delta-neutral, which is what
    our slow poll-bot couldn't do. DRY logs targets + measures reaction latency; live wires order ops."""

    def __init__(self, args, broker, log=print):
        import math
        self._math = math
        self.iv = args.interval
        self.coin = args.underlying
        self.sym = None
        self.clip = args.clip
        self.d = args.quote_dist                      # half-spread off the skewed center (>= 0.01 tick)
        # beta blends the QUOTE PRICE toward spot-fair. Default 0 = price at the CLOB mid so post-only
        # orders REST (beta 0.7 pushed the center across the tight btc book → 9000 rejects → 0 fills).
        # The spot-lead is used DEFENSIVELY in _gates (pull the stale side), not to reprice across the spread.
        self.beta = getattr(args, "beta", 0.0)
        self.skew_k = getattr(args, "skew_k", 0.5)    # ¢ of center shift per clip of net inventory
        self.max_inv = args.max_inv
        self.min_quote = args.min_quote
        self.max_quote = args.max_quote
        self.sell_only = getattr(args, "sell_only", True)   # 26-05 std0 = ASK-ONLY (sell minted block, no bids)
        self.min_requote_s = getattr(args, "min_requote_s", 0.12)   # rate-limit guard (60/s ÷ ~8 ops)
        self.broker = broker
        self.log = log
        self.dry = getattr(broker, "dry_run", True)
        # window state
        self.tok_up = self.tok_dn = self.cid = self.slug = None
        self.window_end = 0
        self.open_px = None
        self.sigma = 0.0
        self.inv_up = self.inv_dn = 0.0
        self.minted = 0.0
        self.mint_usd = getattr(args, "mint_usd", 100.0)
        self.kill_loss = getattr(args, "kill_loss", 8.0)
        self.flatten_buf = getattr(args, "flatten_buf", 30.0)
        self.orders = {}                              # key -> order dict (live: incl order_id)
        # accounting (lag-free, like the hardened rewardmm): realized = received − spent; mark adds inv
        self.spent = self.received = 0.0
        self.realized = self.mark = 0.0
        self.fills = 0
        self.fill_pq = 0.0                            # Σ p(1−p)·filled → maker rebate ≈ 0.014·this
        self.killed = False
        # safety harness: on any invariant violation → cancel all + EXIT (the watchdog restarts fresh with a
        # clean on-chain re-sync). Exit, not just killed=True: a killed-but-alive process stays inert and the
        # watchdog (which checks pgrep) never relaunches it → "no bets for 20min". A persistent bug loops
        # visibly (wd_restarts climbs) instead of dying silently.
        self.guard = mm_guard.Guard(log=self.log, on_halt=self._on_guard_halt)
        self._flattened = False                        # reclaimed (merged) this window? (once per window)
        self._pusd = 0.0
        self.baseline_pusd = None                     # P&L zero (set at first on-chain sync, live)
        self._roll_ts = 0.0
        self._last_mu = self._last_md = 0.5
        self.spot = None
        self._last_requote = 0.0
        self._lat = []                                # reaction latencies (ms): event → decision
        self._imb = []                                # |inv_up−inv_dn| samples while quoting (the goal metric)
        self._requotes = 0
        self._lock = threading.Lock()
        self.book = None
        self.spotfeed = None

    # ---- spot-derived fair P(Up) — it LEADS the lagging CLOB, the whole point of speed ----
    def fair_p(self):
        if not self.open_px or self.sigma <= 0 or self.spot is None:
            return None
        math = self._math
        tau = max(1.0 / 60, (self.window_end - time.time()) / 60.0)
        z = (self.spot - self.open_px) / (self.open_px * self.sigma * math.sqrt(tau))
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    def _targets(self):
        """4 target prices (bid/ask × Up/Down) around the inventory-skewed, spot-led center."""
        from . import feeds  # noqa
        mu = self.book.mid(self.tok_up) if self.book else None
        fp = self.fair_p()
        if mu is None and fp is None:
            return None
        center = mu if fp is None else (mu * (1 - self.beta) + fp * self.beta if mu is not None else fp)
        # aggressive inventory skew: long Up ⇒ push center DOWN ⇒ Up-ask cheaper (sell), Up-bid lower
        # (buy less), Down-bid higher (buy Down = re-balance). 1 clip of imbalance = skew_k cents.
        imb = (self.inv_up - self.inv_dn) / max(self.clip, 1)
        center = center - self.skew_k * 0.01 * imb
        center = min(0.97, max(0.03, center))
        d = self.d
        cu, cd = center, 1 - center
        ask_up, ask_dn = round(cu + d, 2), round(cd + d, 2)
        # OFFLOAD THE LOSER — std0's actual small-scale mechanism (46% of its 26-05 sells were at <0.25). When
        # the inventory skew pushes the HELD (losing) side's ask below the best bid, post-only REJECTS it → the
        # gate turns that ask OFF → we KEEP the loser → 100% directional (the ±$10 swings). Instead, REST it
        # just above the best bid: an aggressive MAKER sell that chases the falling side down each requote and
        # gets lifted by dip-buyers. Earns the rebate, never a taker dump. Strictly ≥ old behavior: if no dip-
        # buyer shows, it's the same as before (kept, flatten backstops at settle) — it can only help.
        bbu = self.book.touch(self.tok_up)[0] if self.book else None
        bbd = self.book.touch(self.tok_dn)[0] if self.book else None
        if bbu is not None: ask_up = max(ask_up, round(bbu + 0.01, 2))
        if bbd is not None: ask_dn = max(ask_dn, round(bbd + 0.01, 2))
        return {
            "ask_up": ask_up, "bid_up": round(cu - d, 2),
            "ask_dn": ask_dn, "bid_dn": round(cd - d, 2),
            "center": round(center, 4),
        }

    def _gates(self, t):
        """Which of the 4 orders are ON: inventory band + post-only (never cross) + spot-lead DEFENSIVE
        pull. We price at the CLOB mid so post-only orders rest; fair_p is used HERE to PULL the side
        about to be picked off — NOT to reprice across the spread (that crossed the tight book → 0 fills).
        lean = fair_p − CLOB_mid: >0 ⇒ spot says Up rising ⇒ our Up-ask (sell Up) & Down-bid (buy Down)
        are stale → pull them; <0 ⇒ pull the Up-bid & Down-ask. Threshold = quote_dist (our half-spread):
        pull the ask only once fair value exceeds the ask price (true mispricing), not on tick noise."""
        imb = self.inv_up - self.inv_dn
        bbu, bau, _ = self.book.touch(self.tok_up)
        bbd, bad, _ = self.book.touch(self.tok_dn)
        fp, mu = self.fair_p(), self.book.mid(self.tok_up)
        lean = (fp - mu) if (fp is not None and mu is not None) else 0.0
        g = self.d                                    # spot must lead by more than our half-spread to pull
        on = {}
        # asks need inventory to sell; bids need neutrality room. post-only: ask must be > best_bid,
        # bid must be < best_ask, else it crosses → skip. Last term = the spot-lead defensive pull.
        on["ask_up"] = self.inv_up >= self.clip and imb > -self.max_inv and (bbu is None or t["ask_up"] > bbu) and lean <= g
        on["ask_dn"] = self.inv_dn >= self.clip and imb < self.max_inv and (bbd is None or t["ask_dn"] > bbd) and lean >= -g
        on["bid_up"] = imb < self.max_inv and self.inv_up < self.minted + self.max_inv and (bau is None or t["bid_up"] < bau) and lean >= -g
        on["bid_dn"] = imb > -self.max_inv and self.inv_dn < self.minted + self.max_inv and (bad is None or t["bid_dn"] < bad) and lean <= g
        if self.sell_only:                            # 26-05 std0 = ASK-ONLY: sell the minted block two-sided
            on["bid_up"] = on["bid_dn"] = False       # across the book, NEVER buy the crashing side (= no
        #                                               directional bleed; the ask gates + skew rebalance)
        return on

    def requote(self, ev_ts=None):
        """The hot path: fired on every WS tick (throttled). Re-prices only what moved."""
        now = time.time()
        if self.killed:
            return
        if now - self._last_requote < self.min_requote_s:
            return
        if not self.tok_up or self.book is None:
            return
        mu = self.book.mid(self.tok_up)
        if mu is None:
            return
        md = self.book.mid(self.tok_dn) or round(1 - mu, 4)
        self._last_mu, self._last_md = mu, md
        if self.dry:                                  # PAPER: fill crossed quotes against the real book, mark to mid
            self._check_paper_fills()
            matched = min(self.inv_up, self.inv_dn)
            self.mark = (self.received - self.spent) + matched \
                + (self.inv_up - matched) * mu + (self.inv_dn - matched) * md
        # mark is the on-chain truth set by _sync_onchain (pUSD + inventory − baseline) in live; paper marks
        # above. Suppress the kill for 35s after a roll: the settled block's auto-redeem lags pUSD (live).
        # settle zone: from ~flatten_buf before window_end through the redeem lag (until the roll resyncs).
        # At settle the held block AUTO-REDEEMS — its tokens go to 0 BEFORE the pUSD credit lands, so an
        # on-chain sync reads (pUSD_low + inv_0) → a phantom −$block mark. Orders are already cancelled by
        # flatten_buf, so no new risk is taken here; suppress the kill (it self-corrects after the credit).
        settle_zone = (self.window_end - now) < (self.flatten_buf + 15)
        holding = (self.inv_up > 1 or self.inv_dn > 1)     # no position ⇒ a −mark is only a redeem-credit
        # guard.check_mark: a −mark deeper than the block is a phantom (redeem lag), not a loss → don't kill
        if self.mark <= -self.kill_loss and holding and (now - self._roll_ts) > 35 and not settle_zone \
                and self.guard.check_mark(self.mark, self.minted):
            # lag (the matched block always redeems ~whole), never a real loss. Real bleed = while holding.
            self.log(f"🛑 KILL mark {self.mark:+.2f} (on-chain)"); self.killed = True; self._cancel_all(); return
        if self.window_end - now <= self.flatten_buf:
            self._cancel_all()
            self._reclaim()                           # MERGE matched block → instant pUSD (no redeem lag →
            return                                    # capital ready for next window) + flatten the residual
        if not (self.min_quote <= mu <= self.max_quote):
            self._cancel_all(); return                # only quote near 0.5 (balanced)
        if self.minted < 1:                           # mint ask ammunition once per window
            self._mint()
            if self.minted < 1:
                return
        # two-sided mode only: if we've leaned past max_inv, STOP quoting (no dump). In SELL-ONLY (26-05
        # std0) we don't stop — the ask gates already stop selling the LIGHT side and keep selling the HEAVY
        # side (skew), which rebalances; and we never buy, so there's no accumulation to run away.
        if not self.sell_only and abs(self.inv_up - self.inv_dn) > self.max_inv:
            self._cancel_all(); return
        with self._lock:
            t = self._targets()
            if not t:
                return
            on = self._gates(t)
            for key in ("ask_up", "ask_dn", "bid_up", "bid_dn"):
                cur = self.orders.get(key)
                if not on[key]:
                    if cur: self._cancel(key)
                    continue
                if cur and abs(cur["price"] - t[key]) < 0.005:
                    continue                          # still good — leave it (saves a rate-limit slot)
                self._place(key, t[key])
            self._last_requote = now
            self._requotes += 1
            self._imb.append(abs(self.inv_up - self.inv_dn))   # neutrality metric while actively quoting
            if ev_ts:
                self._lat.append((now - ev_ts) * 1000)

    # ---- order ops ----
    def _place(self, key, price):
        tok = self.tok_up if key.endswith("up") else self.tok_dn
        direction = "Up" if key.endswith("up") else "Down"
        if self.dry:
            # PAPER: rest a simulated maker order; _check_paper_fills() fills it against the REAL WS book
            # when the market crosses it (= the same adverse selection a live order eats). Zero money.
            self.orders[key] = {"price": price, "side": "SELL" if key.startswith("ask") else "BUY",
                                "token": tok, "shares": self.clip, "dry": True}
            return
        exp = self.window_end if self.window_end - time.time() > 65 else 0
        try:
            if key.startswith("ask"):
                o = self.broker.place_sell(tok, price, self.clip, self.slug, direction=direction, expiration=exp)
            else:
                o = self.broker.place_limit(tok, price, round(self.clip * price, 2), direction, self.slug, expiration=exp)
        except Exception as e:
            self.log(f"place {key} err: {type(e).__name__}: {e}"); return
        if o:
            o["filled_acct"] = 0.0
            self.orders[key] = o

    def _cancel(self, key):
        o = self.orders.pop(key, None)
        if o and not self.dry:
            try: self.broker.cancel(o)
            except Exception as e: self.log(f"cancel {key}: {e}")

    def _cancel_all(self):
        for k in list(self.orders.keys()):
            self._cancel(k)

    def _on_guard_halt(self):
        """A guard invariant tripped → cancel everything and EXIT the process so the watchdog restarts it
        fresh (a killed-but-alive process would sit inert and never get relaunched → no bets)."""
        self.killed = True
        try: self._cancel_all()
        except Exception as e: self.log(f"halt cancel err: {e}")
        self.log("🛑🛡 guard halt → exiting for a clean watchdog restart")
        os._exit(1)

    def _reclaim(self):
        """End of window — MERGE the matched block to reclaim capital IMMEDIATELY (no auto-redeem lag that
        locks pUSD past the next window's open → missed windows). 1 Up+1 Down = $1 pUSD, guaranteed by the
        CTF contract PRE-resolution (mergePositions, self-submit via the Safe = zero quota, instant, no
        oracle wait). We do NOT flatten/dump the small residual — std0 doesn't (it holds ~8% to auto-redeem);
        our residual is ≤ max_inv (stop-when-leaning) so it just redeems at settle for ±a few $, cheaper than
        a taker dump (the dump cost −$26). Dry: in-memory. The tracker reads MERGE + REDEEM on-chain."""
        if self._flattened:
            return
        self._flattened = True
        # STEP 1 — merge the matched block for instant capital (only if there IS a matched block).
        matched = int(min(self.inv_up, self.inv_dn))
        if matched >= 1:
            if self.dry:
                self.inv_up -= matched; self.inv_dn -= matched; self.received += matched
            else:
                try:
                    r = subprocess.run(["node", SETOPS_JS, "merge", self.cid, f"{matched:.2f}"],
                                       capture_output=True, text=True, timeout=80)
                    if r.returncode == 0 and "OK" in (r.stdout + r.stderr):
                        self.log(f"♻ merged {matched} sets → ${matched} pUSD reclaimed")
                        self._sync_onchain()          # re-read TRUE inv+pUSD post-merge (avoid the sync race)
                    else:
                        self.log(f"merge failed: {(r.stdout + r.stderr)[:120]}")
                except Exception as e:
                    self.log(f"merge err: {e}")
        # STEP 2 — flatten the residual ALWAYS (even when matched==0). The 100%-one-sided residual (0/15) is
        # the WHOLE directional problem — the old `if matched<1: return` skipped this exact case → imbalance
        # stayed 100%. Realize it near the mid (mid≈fair at settle → ~no cost; NOT the 0.02 dump that cost −$26).
        excess = round(self.inv_up - self.inv_dn, 2)
        if abs(excess) >= 1:
            up = excess > 0
            if self.dry:
                if up: self.inv_up -= abs(excess)
                else:  self.inv_dn -= abs(excess)
            else:
                mid = (self._last_mu if up else self._last_md) or 0.5
                floor = self.guard.check_sell_price(round(mid - 0.04, 2), mid)   # anti-dump clamp
                try:
                    self.broker.sell_market(self.tok_up if up else self.tok_dn, abs(excess), self.slug,
                                            direction=("Up" if up else "Down"), floor=max(0.02, floor))
                    if up: self.inv_up -= abs(excess)     # decrement inv — the flatten is a TAKER sell (not
                    else:  self.inv_dn -= abs(excess)     # via on_fill), so track it or engine diverges from
                    self.log(f"⚖ flattened residual {excess:+.0f} @ floor {floor}")   # chain → false reconcile HALT
                    self._sync_onchain()                  # and re-read the true post-flatten inv
                except Exception as e:
                    self.log(f"flatten err: {e}")

    # ---- on-chain set ops + truth sync (Safe self-submit) ----
    def _mint(self):
        if self.dry:
            self.minted = self.mint_usd; self.inv_up += self.mint_usd; self.inv_dn += self.mint_usd
            self.spent += self.mint_usd; return
        pusd = self._pusd_now()
        amt = round(min(self.mint_usd, pusd - 2.0), 2)    # mint what we can AFFORD (leave $2 gas) — a fixed
        if amt < 10:                                      # mint_usd > pUSD used to abort SILENTLY → no quotes
            self.log(f"mint skipped: pUSD ${pusd:.2f} too low for a block"); return
        try:
            r = subprocess.run(["node", SETOPS_JS, "mint", self.cid, f"{amt:.2f}"],
                               capture_output=True, text=True, timeout=80)
        except Exception as e:
            self.log(f"mint err: {e}"); return
        if r.returncode == 0 and "OK" in (r.stdout + r.stderr):
            self.minted = amt
            self.inv_up += amt; self.inv_dn += amt; self.spent += amt
            self.log(f"⚒ minted {amt:.0f} on {self.slug} (pUSD ${pusd:.2f})")
        else:
            self.log(f"mint failed: {(r.stdout + r.stderr)[:120]}")

    def _pusd_now(self):
        if self.dry: return 1e9
        try: return float(self.broker.usdc_balance())
        except Exception: return self._pusd

    def _sync_onchain(self):
        """The ONLY P&L source: read real pUSD + held tokens, mark = (pUSD + inventory value) − baseline.
        This is measured on-chain so it CANNOT phantom (the cash-flow model kept doing so). inv_value =
        matched sets ($1 each) + residual imbalance at the live mid. Re-quote-time kill is suppressed for
        35s after a roll because the just-settled block's auto-redeem LAGS pUSD (else a roll-time dip)."""
        if self.dry or not self.tok_up:
            return
        try:
            r = subprocess.run(["node", SETOPS_JS, "baltoks", str(self.tok_up), str(self.tok_dn)],
                               capture_output=True, text=True, timeout=40)
            pu = re.search(r"pUSD:\s*([\d.]+)", r.stdout or "")
            ud = re.search(r"Up:\s*([\d.]+)\s+Down:\s*([\d.]+)", r.stdout or "")
            if pu: self._pusd = float(pu.group(1))
            if ud:
                chain_up, chain_dn = float(ud.group(1)), float(ud.group(2))
                # RECONCILE engine-tracked inv (from fills) vs on-chain truth → HALT if the fill tracking
                # lied (the −60% desync). Skip the roll/reclaim transient (inv legitimately in flux there).
                if (time.time() - self._roll_ts) > 40 and not self._flattened and self.minted > 1:
                    # tol must exceed a couple of in-flight clips (a fill matched on-chain before its WS msg
                    # is a NORMAL 5-share gap, not a bug). A real parse desync (114 vs 5) blows way past this.
                    self.guard.reconcile_inv(self.inv_up, self.inv_dn, chain_up, chain_dn,
                                             tol=max(2 * self.clip + 2, 0.25 * self.minted))
                self.inv_up, self.inv_dn = chain_up, chain_dn
                self.minted = max(self.minted, min(self.inv_up, self.inv_dn))
            matched = min(self.inv_up, self.inv_dn)
            inv_val = matched + (self.inv_up - matched) * self._last_mu + (self.inv_dn - matched) * self._last_md
            wealth = self._pusd + inv_val                 # TOTAL wealth = cash + held inventory value
            if self.baseline_pusd is None:
                # PERSIST the P&L-zero across RESTARTS — else each restart re-bases to current wealth, so the
                # kill measures per-session drawdown from an arbitrary peak → repeated FALSE kills (paid for).
                bf = f".hft_baseline_{self.coin}_{self.iv}.json"
                try:
                    if os.path.exists(bf):
                        self.baseline_pusd = json.load(open(bf)).get("baseline", wealth)
                    else:
                        self.baseline_pusd = wealth
                        json.dump({"baseline": wealth}, open(bf, "w"))
                except Exception:
                    self.baseline_pusd = wealth
            self.mark = wealth - self.baseline_pusd        # real cumulative P&L vs the true (persisted) start
            self.realized = self.mark
        except Exception as e:
            self.log(f"sync err: {e}")

    def on_fill(self, token, side, size, price):
        """User-WS push on a maker fill. Inventory truth comes from _sync_onchain (not here, to avoid the
        double-source desync that kept breaking the P&L); a fill just (a) counts rebate base and (b)
        nudges inv for a FAST re-skew between syncs, then re-quotes. The next sync corrects any drift."""
        if not self.guard.check_fill(size, self.clip):    # impossible fill (>clip) = parse bug → HALT, drop it
            return
        up = (token == self.tok_up)
        self.fills += 1
        self.fill_pq += price * (1 - price) * size
        # structured per-fill log → eps_reader computes per-fill edge = (mid-px) BUY / (px-mid) SELL.
        # mid at fill-time is the drift-free benchmark (std0 captures +0.22c; we measure ours).
        fmid = self.book.mid(token) if self.book else None
        self.log(f"FILL {'Up' if up else 'Down'} {side} px={price} mid={fmid} sz={size} slug={self.slug}")
        if side == "SELL":
            if up: self.inv_up = max(0.0, self.inv_up - size)
            else:  self.inv_dn = max(0.0, self.inv_dn - size)
        else:
            if up: self.inv_up += size
            else:  self.inv_dn += size
        # HARD imbalance cap — the reason std0 has NO slip tail: it holds a MATCHED block and never sells one
        # side to 0. Our max_inv gate leaked because requote is throttled (0.12s) → a burst kept selling the
        # winner past the cap → 15/0 one-sided → directional slip. So the INSTANT a fill breaches max_inv,
        # cancel the OVER-sold side's ask NOW (bypass the throttle). We then hold a matched block → merge/redeem
        # → neutral by construction. Cancelling an ask only REDUCES selling → can't make things worse.
        imb = self.inv_up - self.inv_dn
        if imb > self.max_inv:                         # too long Up ⇒ we're overselling Down (the winner)
            self._cancel("ask_dn")
        elif imb < -self.max_inv:                      # too long Down ⇒ overselling Up
            self._cancel("ask_up")
        self.requote(time.time())                     # re-skew immediately on a fill

    # ---- PAPER fill simulation (zero money): fill our resting quotes when the REAL book crosses them ----
    def _check_paper_fills(self):
        bbu, bau, _ = self.book.touch(self.tok_up)
        bbd, bad, _ = self.book.touch(self.tok_dn)
        for key in list(self.orders.keys()):
            o = self.orders.get(key)
            if not o:
                continue
            bb, ba = (bbu, bau) if o["token"] == self.tok_up else (bbd, bad)
            p = o["price"]
            crossed = (o["side"] == "SELL" and bb is not None and bb >= p) or \
                      (o["side"] == "BUY" and ba is not None and ba <= p)
            if crossed:
                self._on_paper_fill(o["token"], o["side"], o["shares"], p)
                self.orders.pop(key, None)

    def _on_paper_fill(self, token, side, size, price):
        up = (token == self.tok_up)
        self.fills += 1
        self.fill_pq += price * (1 - price) * size
        if side == "SELL":
            self.received += size * price
            if up: self.inv_up -= size
            else:  self.inv_dn -= size
        else:
            self.spent += size * price
            if up: self.inv_up += size
            else:  self.inv_dn += size

    # ---- window lifecycle ----
    def _roll(self, m):
        from . import feeds
        if self.dry and self.slug and (self.inv_up > 1e-6 or self.inv_dn > 1e-6):
            matched = min(self.inv_up, self.inv_dn)    # PAPER auto-redeem credit (instant, no lag in-memory)
            self.received += matched + (self.inv_up - matched) * self._last_mu + (self.inv_dn - matched) * self._last_md
        self._cancel_all()
        self.slug, self.cid = m.slug, m.condition_id
        self.tok_up, self.tok_dn = m.token_up, m.token_down
        self.window_end = m.window_end
        self.minted = self.inv_up = self.inv_dn = 0.0
        self._flattened = False                        # new window → residual not yet flattened
        self._roll_ts = time.time()                   # kill-suppress window (redeem of prior block lags pUSD)
        self._sync_onchain()                          # inv = on-chain truth (fresh window 0/0); refresh mark
        try:
            self.open_px = feeds.btc_price_at(m.window_start, self.sym)
            self.sigma = feeds.realized_vol_per_min(feeds.btc_klines_1m(symbol=self.sym))
        except Exception:
            self.open_px, self.sigma = None, 0.0
        self.log(f"▶ HFT {self.slug} open={self.open_px} sig={self.sigma:.5f}")

    def status(self):
        lat = sorted(self._lat)
        p50 = lat[len(lat) // 2] if lat else 0
        return (f"HFT {self.coin} {self.iv} | inv={self.inv_up:.0f}/{self.inv_dn:.0f} "
                f"requotes={self._requotes} react_p50={p50:.0f}ms resting={len(self.orders)} "
                f"spot={self.spot} fair_p={self.fair_p() and round(self.fair_p(),3)}")


class UserFeed:
    """CLOB /ws/user — pushes our order/trade events (instant fill detection, no rate-limit cost).
    Schema is learned live (logs the first few raw messages); best-effort parses trades into on_fill.
    A periodic on-chain inventory sync in the runner is the safety net if a message shape is missed."""

    def __init__(self, creds, condition_id, on_fill=None, log=print):
        self.creds = creds
        self.cid = condition_id
        self.on_fill = on_fill
        self.log = log
        self.seen = 0
        self._ws = None
        self._matched = {}                            # our order_id -> cumulative size_matched (for fill deltas)

    def _handle(self, raw):
        self.seen += 1
        try:
            data = json.loads(raw)
        except Exception:
            return
        for e in (data if isinstance(data, list) else [data]):
            if not isinstance(e, dict):
                continue
            # FILLS come from OUR "order" events (size_matched), NOT "trade" events — a trade event carries
            # the TAKER's size/side + the counterparty maker (0xE357, not us) → that was the 114-vs-5 misparse.
            # Our order event: {event_type:"order", id, asset_id, side, size_matched, price}. Count the DELTA.
            if (e.get("event_type") or "").lower() != "order":
                continue
            oid = e.get("id"); aid = e.get("asset_id"); side = (e.get("side") or "").upper()
            try:
                sm = float(e.get("size_matched") or 0); px = float(e.get("price") or 0)
            except Exception:
                continue
            if not (oid and aid and side):
                continue
            delta = sm - self._matched.get(oid, 0.0)
            if delta > 1e-9:
                self._matched[oid] = sm
                if self.on_fill:
                    try:
                        self.on_fill(aid, side, delta, px)
                    except Exception as ex:
                        self.log(f"on_fill err: {ex}")

    def start(self):
        sub = {"auth": {"apiKey": self.creds.api_key, "secret": self.creds.api_secret,
                        "passphrase": self.creds.api_passphrase},
               "type": "user", "markets": [self.cid], "assets_ids": [], "initial_dump": False}

        def _open(ws):
            ws.send(json.dumps(sub))
            def _ping():
                while True:
                    time.sleep(30)
                    try: ws.send("PING")
                    except Exception: break
            threading.Thread(target=_ping, daemon=True).start()
        self._ws = websocket.WebSocketApp(CLOB_USER_WS, on_open=_open,
                                          on_message=lambda w, m: self._handle(m),
                                          on_error=lambda w, e: self.log(f"user-ws err: {e}"))
        threading.Thread(target=self._ws.run_forever, daemon=True).start()

    def resubscribe(self, condition_id):
        self.cid = condition_id  # on roll; simplest = restart the socket
        self.stop(); time.sleep(0.2); self.start()

    def stop(self):
        if self._ws:
            try: self._ws.close()
            except Exception: pass


def _test_feed(coin="eth", interval="5m", secs=12):
    """DRY: run both feeds for `secs`, print live touch + spot + the message rate (= the speed win)."""
    from . import feeds
    sec = {"5m": 300, "15m": 900}[interval]
    ws = (int(time.time()) // sec) * sec
    m = feeds.fetch_updown_market(interval, ws, coin)
    if not m:
        print("no market"); return
    sym = feeds.SYMBOL.get(coin, "BTCUSDT").lower()
    upd = {"book": 0, "spot": 0}
    book = LiveBook([m.token_up, m.token_down], on_update=lambda a: upd.__setitem__("book", upd["book"] + 1))
    spot = SpotFeed(sym, on_update=lambda s: upd.__setitem__("spot", upd["spot"] + 1))
    book.start(); spot.start()
    t0 = time.time()
    while time.time() - t0 < secs:
        time.sleep(2)
        mu, md = book.mid(m.token_up), book.mid(m.token_down)
        print(f"[+{int(time.time()-t0)}s] CLOB mid Up={mu} Dn={md} | Binance spot={spot.spot} "
              f"| book_upd={upd['book']} spot_upd={upd['spot']}")
    book.stop(); spot.stop()
    print(f"--- {secs}s: {upd['book']} book updates ({upd['book']/secs:.1f}/s), "
          f"{upd['spot']} spot updates ({upd['spot']/secs:.1f}/s) — vs the OLD 1 poll/s ---")


def _test_engine(coin="eth", interval="5m", secs=30, wide="1"):
    """Jalon 2 DRY: mint (paper), wire the WS feeds to the quote engine, run, and report reaction
    latency + how the targets track the spot-led fair-p. wide=1 widens the band so we see it quote even
    on a lopsided test window (the real config keeps 0.40-0.60)."""
    from . import feeds
    secs = int(secs)
    sec = {"5m": 300, "15m": 900}[interval]
    ws = (int(time.time()) // sec) * sec
    m = feeds.fetch_updown_market(interval, ws, coin)
    if not m:
        print("no market"); return

    class A: pass
    a = A()
    a.interval, a.underlying, a.clip, a.quote_dist = interval, coin, 5.0, 0.01
    a.max_inv, a.beta, a.skew_k, a.min_requote_s = 10.0, 0.0, 0.5, 0.12
    a.min_quote, a.max_quote = (0.05, 0.95) if wide == "1" else (0.40, 0.60)

    class DryBroker:  # minimal dry broker
        dry_run = True
    mm = HftMM(a, DryBroker(), log=print)
    mm.sym = feeds.SYMBOL.get(coin, "BTCUSDT")          # UPPERCASE for feeds REST; SpotFeed lowercases for the WS
    mm._roll(m)
    mm.minted, mm.inv_up, mm.inv_dn = 100.0, 100.0, 100.0    # DRY mint

    mm.book = LiveBook([m.token_up, m.token_down], on_update=lambda aid: mm.requote(time.time()), log=lambda *a: None)
    def _spot(s):
        mm.spot = s; mm.requote(time.time())
    mm.spotfeed = SpotFeed(mm.sym, on_update=_spot, log=lambda *a: None)
    mm.book.start(); mm.spotfeed.start()

    t0 = time.time()
    while time.time() - t0 < secs:
        time.sleep(5)
        t = mm._targets()
        tg = (f"center={t['center']} | bid/ask Up={t['bid_up']}/{t['ask_up']} Dn={t['bid_dn']}/{t['ask_dn']}"
              if t else "no targets yet")
        print(f"[+{int(time.time()-t0)}s] {mm.status()}\n        targets: {tg}")
    mm.book.stop(); mm.spotfeed.stop()
    lat = sorted(mm._lat)
    if lat:
        p = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))]
        print(f"\n=== {mm._requotes} requotes in {secs}s ({mm._requotes/secs:.1f}/s) | "
              f"reaction latency p50={p(.5):.2f}ms p95={p(.95):.2f}ms max={lat[-1]:.1f}ms ===")
    else:
        print("\n(no requotes — window out of band? try wide=1)")


def _paper_test(coin="eth", interval="5m", secs="240", band="0.40,0.60", maxinv="6", skewk="2.0", mint="100"):
    """THE validation harness (zero money): run the FULL engine in paper — mint, quote, fills simulated
    against the REAL WS book, skew, roll — over many windows, and report the IMBALANCE distribution (the
    goal: does the fast engine + skew hold ~5% like std0 vs the old poll-bot's ~10%?) + paper P&L."""
    from . import feeds
    secs = int(secs)
    sec = {"5m": 300, "15m": 900}[interval]

    class A: pass
    a = A()
    a.interval, a.underlying, a.clip, a.quote_dist = interval, coin, 5.0, 0.01
    a.beta, a.skew_k, a.min_requote_s = 0.0, float(skewk), 0.12
    a.max_inv = float(maxinv)
    a.mint_usd, a.kill_loss, a.flatten_buf = float(mint), 8.0, 30.0
    a.min_quote, a.max_quote = (float(x) for x in band.split(","))

    class DryBroker: dry_run = True
    mm = HftMM(a, DryBroker(), log=lambda *x: None)
    mm.sym = feeds.SYMBOL.get(coin, "BTCUSDT")
    spot = SpotFeed(mm.sym, on_update=lambda s: (setattr(mm, "spot", s), mm.requote(time.time())), log=lambda *x: None)
    spot.start()
    book = [None]

    def roll_to(m):
        if book[0]: book[0].stop()
        mm.book = None; mm._roll(m)
        book[0] = LiveBook([mm.tok_up, mm.tok_dn], on_update=lambda aid: mm.requote(time.time()), log=lambda *x: None)
        mm.book = book[0]; book[0].start()

    print(f"PAPER: {coin}-{interval} band={a.min_quote}-{a.max_quote} max_inv={a.max_inv} skew_k={a.skew_k} clip={a.clip}")
    t0 = time.time(); last = 0.0; windows = 0
    while time.time() - t0 < secs:
        now = int(time.time()); ws = (now // sec) * sec
        m = feeds.fetch_updown_market(interval, ws, coin)
        if m and m.slug != mm.slug:
            roll_to(m); windows += 1
        if time.time() - last > 12:
            imb = abs(mm.inv_up - mm.inv_dn)
            print(f"[+{int(time.time()-t0)}s] win={windows} inv={mm.inv_up:.0f}/{mm.inv_dn:.0f} "
                  f"imb={imb:.0f} ({100*imb/max(mm.minted,1):.0f}%) mark={mm.mark:+.2f} fills={mm.fills} "
                  f"requotes={mm._requotes} resting={len(mm.orders)}")
            last = time.time()
        time.sleep(1)
    if book[0]: book[0].stop()
    spot.stop()
    imb = sorted(mm._imb)
    print(f"\n=== PAPER {secs}s · {windows} windows · {mm.fills} fills · {mm._requotes} requotes ===")
    if imb:
        p = lambda q: imb[min(len(imb) - 1, int(q * len(imb)))]
        mref = max(mm.minted, 100.0)
        print(f"IMBALANCE |inv_up−inv_dn| (the goal): p50={p(.5):.1f}sh ({p(.5)/mref*100:.0f}%) "
              f"p90={p(.9):.1f}sh ({p(.9)/mref*100:.0f}%) max={imb[-1]:.1f}sh — std0≈5%, old poll-bot≈10%")
    print(f"paper P&L (mark-to-mid): {mm.mark:+.2f} | maker-rebate≈${0.014*mm.fill_pq:.3f} | "
          f"killed={mm.killed}")


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "paper"
    if mode == "feed":
        _test_feed(*sys.argv[2:])
    elif mode == "engine":
        _test_engine(*sys.argv[2:])
    else:
        _paper_test(*sys.argv[1:])
