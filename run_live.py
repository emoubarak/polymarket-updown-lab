#!/usr/bin/env python3
"""Run favorite for REAL on Polymarket — small, auditable, disarmed by default.

This is a deliberately separate, minimal loop (not the paper Runner) so every
line that can move money is in front of you. It:
  1. discovers the current 15m BTC up/down market (read-only feeds),
  2. applies favorite's entry rule (extreme favorite, mid-window),
  3. sizes the stake via the AdaptiveStake ladder (f tied to confidence),
  4. places ONE marketable buy via LiveBroker (DRY-RUN unless armed),
  5. holds to settlement, reads the real oracle outcome, redeems a win,
  6. records the result back into the ladder and persists.

One open position at a time (favorite never overlaps). Arm with:
    POLY_LIVE=1 POLY_CONFIRM=I_UNDERSTAND_REAL_MONEY POLY_PRIVATE_KEY=0x...
Without those it runs forever in DRY-RUN, logging exactly what it would do.

    python3 run_live.py --strategy favorite --interval 15m --start 100
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from pmlab import feeds
from pmlab.feeds import INTERVALS
from pmlab.entry import (favorite_of, topup_remaining, flow_tilt_mult,   # shared (mutualised with favorite)
                                 ls_flow_cap_for)
from pmlab.live import LiveBroker, is_armed
from pmlab.staking import AdaptiveStake
from pmlab.presets import (ALL_PRESETS, add_preset_args,   # single source of truth
                                   preset_from_args, COIN_KEYS,     # (gate params + customization)
                                   bet_max_for)                     # per-(coin,frame) book ceiling


MIN_TOPUP_USD = 2.0     # don't post a top-up order smaller than this (avoid dust fills)
POSMARK_FRESH_S = 90    # count a sibling pilot's position mark only if its sync is this recent
RECENTER_EPS = 0.01     # maker re-centering: re-post the resting bid only if the favorite mid
#                         drifted >= this (one penny-tick) — avoids thrashing on book noise


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class LiveLadder:
    def __init__(self, args):
        self.name = args.strategy
        self.iv = args.interval
        self.win_min = INTERVALS[self.iv] / 60
        self.underlying = args.underlying
        self._symbol = feeds.SYMBOL.get(args.underlying, "BTCUSDT")
        preset = preset_from_args(args.strategy, args)  # same resolver as paper (types + overrides)
        self.preset = preset
        self.min_fav = preset.min_fav
        self.max_fav = preset.max_fav
        self.vol_cap = preset.vol_cap                  # favorite_vol storm gate
        self.min_lead_bps = preset.min_lead_bps        # favorite_lead bps floor
        self.min_lead_z = preset.min_lead_z            # zlead vol-normalized floor
        self.maker_entry = preset.maker_entry          # zleadmk: rest a bid
        self.maker_fb_frac = preset.maker_fb_frac      # cross taker if unfilled this late
        self.maker_recenter = preset.maker_recenter    # COLO: re-post bid at live favorite mid on drift
        self.enter_lo = preset.enter_lo                # entry slot bounds (window frac remaining)
        self.enter_hi = preset.enter_hi
        # the SHARED entry gate — same object favorite (paper) builds from the same
        # Preset, so the pilot decides identically to its paper twin (no drift).
        self.gate = preset.gate()
        self.sd = Path(args.state_dir)
        self.sd.mkdir(exist_ok=True)
        self.broker = LiveBroker(log=log)
        self.stake = self._load_stake(args)
        self.pos = self._load_json("position.json", None)
        self._holdings = self._load_json("holdings.json", [])   # tokens still on-chain
        self.maker_order = self._load_json("maker_order.json", None)  # resting maker bid
        self._traded_window: str | None = None

    # ----------------------------------------------------- persistence ---
    def _load_json(self, name, default):
        p = self.sd / name
        return json.loads(p.read_text()) if p.exists() else default

    def _save_json(self, name, obj):
        (self.sd / name).write_text(json.dumps(obj))

    def _load_stake(self, args) -> AdaptiveStake:
        d = self._load_json("stake.json", None)
        if args.weighted:
            # bet = weight_pct × capital, capped at bet_max. A flat % of capital
            # already de-leverages into a drawdown (smaller capital → smaller bet),
            # so no extra dd brake — the size stays a predictable single %.
            kw = dict(max_clip=args.bet_max, min_clip=min(5.0, args.bet_max),
                      weighted=True, f_learning=args.weight_pct,
                      f_confirmed=args.weight_pct, dd_brake=0.0)
        else:
            kw = dict(max_clip=args.stake, min_clip=min(5.0, args.stake), weighted=False)
        # No AUTOMATIC pause on the live ladder — the operator arbitrates pauses
        # (dashboard Stop). The rolling-EV regime pause fires on ordinary variance
        # for a non-autocorrelated, asymmetric payoff (favorite) AND self-perpetuates
        # (clip=0 freezes it forever). The n>=150 win<=price tripwire still applies.
        kw["regime_pause"] = False
        if d:
            return AdaptiveStake.from_dict(d, **kw)
        return AdaptiveStake(bankroll=args.start, **kw)

    def _persist(self):
        self._save_json("stake.json", self.stake.to_dict())
        self._save_json("position.json", self.pos)
        self._save_json("maker_order.json", self.maker_order)

    def _journal(self, row: dict):
        f = self.sd / "journal.csv"
        new = not f.exists()
        with f.open("a") as fh:
            if new:
                fh.write("ts,kind,slug,direction,shares,price,pnl,bankroll\n")
            fh.write(",".join(str(row.get(k, "")) for k in
                     ("ts", "kind", "slug", "direction", "shares", "price",
                      "pnl", "bankroll")) + "\n")
        self._notify(row)

    def _notify(self, row: dict):
        """One Telegram line per journal row (no-op unless TELEGRAM_* set)."""
        from pmlab.notify import notify
        kind = row.get("kind", "")
        slug = str(row.get("slug", "")).replace("btc-updown-", "")
        tag = "" if is_armed() else " (DRY)"
        if kind in ("BUY", "BUY+", "FILL_BUY"):
            try:
                usd = float(row.get("shares", 0)) * float(row.get("price", 0))
            except (TypeError, ValueError):
                usd = 0.0
            how = ("MAKER" if kind == "FILL_BUY"
                   else "top-up" if kind == "BUY+" else "taker")
            msg = (f"🟢 BUY {how}{tag} {row.get('direction')} @ {row.get('price')} "
                   f"· ${usd:.2f}\n{slug}")
        elif kind in ("WIN", "LOSS"):
            emo = "✅ WON" if kind == "WIN" else "❌ LOST"
            msg = (f"{emo}{tag} {row.get('direction')} · P&L ${row.get('pnl')} "
                   f"· capital ${row.get('bankroll')}\n{slug}")
        else:
            return
        notify(msg)

    # ---------------------------------------------------------- settle ---
    def _settle(self):
        if not self.pos:
            return
        # Only a small margin to be sure the window has CLOSED — resolve_market()
        # already returns None until the outcome is decisive, so this buffer just
        # gates the first check, it doesn't gate the settlement. 10s (was 65) so
        # the win/loss + capital + Telegram land within seconds of resolution.
        if time.time() < self.pos["window_end"] + 10:
            return
        up_won = feeds.resolve_market(self.pos["slug"])
        if up_won is None:
            return                                       # not resolved yet
        won = (self.pos["direction"] == "Up") == up_won
        usd = self.pos["shares"] * self.pos["price"]
        if won:
            self.broker.redeem(self.pos.get("condition_id", ""))
            gross = self.pos["shares"] * 1.0             # each share redeems $1
            pnl = gross - usd
        else:
            pnl = -usd
        self.stake.record(self.pos["price"], won, pnl / usd)
        if is_armed():                                   # real wallet, redeem-anticipated
            self.stake.update_bankroll(self._real_capital())
        else:
            self.stake.update_bankroll(self.stake.bankroll + pnl)    # dry-run model
        log(f"⚖️  settled {self.pos['slug']} {self.pos['direction']}: "
            f"{'WIN' if won else 'LOSS'} {pnl:+.2f} | {self.stake.status()}")
        self._journal({"ts": int(time.time()),
                       "kind": "WIN" if won else "LOSS",
                       "slug": self.pos["slug"], "direction": self.pos["direction"],
                       "shares": self.pos["shares"], "price": self.pos["price"],
                       "pnl": round(pnl, 2), "bankroll": round(self.stake.bankroll, 2)})
        self.pos = None
        self._persist()

    # ----------------------------------------------------------- enter ---
    def _maybe_enter(self):
        if self.maker_order is not None:                 # a resting bid owns this decision
            self._manage_maker()
            return
        start, _ = feeds.window_bounds(self.iv)
        m = feeds.fetch_updown_market(self.iv, start, self.underlying)
        if m is None:
            return
        # a position from a PAST window must settle before any new entry (one window at
        # a time); a position from the CURRENT window may still be TOPPED UP toward its
        # target stake (the first taker fill can be depth-limited on a thin book — the
        # btc-15m pilot once got $5 of an intended $30 and never completed it).
        if self.pos and self.pos["slug"] != m.slug:
            return
        if m.slug == self._traded_window:                # window done: filled / stood down
            return
        # cheap timing short-circuit before any fetch (the gate re-checks it; this
        # just spares the midpoint/klines calls every off-band tick).
        if not (self.enter_lo <= m.tau_min / self.win_min <= self.enter_hi):
            return
        mid_up = feeds.fetch_midpoint(m.token_up)
        spot = sigma = window_open = 0.0
        if self.gate.needs_spot:                         # only fetch klines if a gate uses them
            klines = feeds.btc_klines_1m(90, self._symbol)
            spot = klines[-1]["close"]
            sigma = feeds.realized_vol_per_min(klines)
            window_open = feeds.btc_price_at(start, self._symbol) or 0.0
        # ALT-only veto inputs (None on btc), fetched only when the gate uses them — identical to paper.
        is_alt = self._symbol != "BTCUSDT"
        btc_lead = (feeds.btc_lead_fraction(self._symbol, start)
                    if (is_alt and self.gate.btc_align) else None)
        ls_flow_ratio = None
        # PER-FRAME longshot-flow threshold (one strat zleadp, 2.4 on 15m / 2.7 on 5m — routed by frame)
        flow_cap = ls_flow_cap_for(self.iv, self.gate.ls_flow_cap) if self.gate.ls_flow_cap else 0.0
        if is_alt and self.gate.ls_flow_cap and mid_up is not None:
            ls = feeds.longshot_flow(m.condition_id, "Up" if mid_up >= 0.5 else "Down")
            depth = bet_max_for(self.underlying, self.iv)
            if ls is not None and depth > 0:
                ls_flow_ratio = ls / depth
        # THE ENTRY DECISION — the SHARED gate, identical to favorite (paper).
        decision = self.gate.decide(mid_up=mid_up, sigma=sigma, spot=spot,
                                    window_open=window_open, tau_min=m.tau_min,
                                    window_min=self.win_min, btc_lead=btc_lead,
                                    ls_flow_ratio=ls_flow_ratio, ls_flow_cap=flow_cap)
        if decision is None:
            return
        direction, mid_fav = decision
        if is_armed():                                   # size on the REAL liquid pUSD
            self._sync_bankroll()
        cur = self.pos if (self.pos and self.pos["slug"] == m.slug) else None
        if cur is not None and direction != cur["direction"]:
            return                                       # favorite flipped — leave it alone
        if cur is None:                                  # fresh window: buy the whole clip
            # flagship flow-TILT: bet more on clean (low longshot-flow) windows, less on dirty
            target = self.stake.clip() * flow_tilt_mult(ls_flow_ratio, flow_cap,
                                                        self.gate.ls_flow_tilt)
            if target <= 0:
                log(f"stand down ({self.stake.status()})")
                self._traded_window = m.slug
                return
            remaining = target                           # buy() enforces its own $ minimum
        else:                                            # topping up toward the fixed target
            target = cur.get("target_usd", cur["usd"])
            remaining = topup_remaining(target, cur["usd"], min_clip=MIN_TOPUP_USD)
            if remaining <= 0:
                self._traded_window = m.slug             # target reached — stop topping up
                return
        if self.maker_entry:                             # favorite_leadzmk: rest a passive bid
            self._place_maker(m, direction, mid_fav, remaining)
        else:                                            # everyone else: cross taker now
            self._taker_enter(m, direction, mid_fav, remaining, target)

    # ------------------------------------------------- entry mechanics ---
    def _open_position(self, fill: dict, window_end: int, condition_id: str, kind: str,
                       target: float | None = None):
        """Record a fresh fill (taker BUY or maker FILL_BUY) as the open position +
        on-chain holding + journal row. Shared by both entry paths. `target` (taker
        only) stamps the window's stake target so later ticks can top up toward it."""
        self.pos = {**fill, "window_end": window_end, "condition_id": condition_id}
        if target is not None:
            self.pos["target_usd"] = target
        self._holdings.append({"token": fill["token_id"], "slug": fill["slug"],
                               "direction": fill["direction"],
                               "shares": fill["shares"], "price": fill["price"]})
        self._persist()
        self._save_json("holdings.json", self._holdings)
        self._journal({"ts": int(time.time()), "kind": kind, "slug": fill["slug"],
                       "direction": fill["direction"], "shares": fill["shares"],
                       "price": fill["price"], "pnl": "",
                       "bankroll": round(self.stake.bankroll, 2)})

    def _absorb_fill(self, fill: dict, m, target: float | None):
        """Apply a taker fill: open a fresh position, or MERGE a top-up into the open
        one (same window/token) at the blended price so the window stays ONE position
        — one settlement, an honest trade count, no on-chain double-count. Locks the
        window once the merged stake reaches the target."""
        if self.pos and self.pos["slug"] == fill["slug"]:
            tot_sh = round(self.pos["shares"] + fill["shares"], 2)
            tot_usd = round(self.pos["usd"] + fill["usd"], 2)
            self.pos["shares"] = tot_sh
            self.pos["usd"] = tot_usd
            self.pos["price"] = round(tot_usd / tot_sh, 4) if tot_sh else fill["price"]
            if target is not None and "target_usd" not in self.pos:
                self.pos["target_usd"] = target
            # merge the on-chain holding row for this token (a 2nd row would make
            # _real_capital count the redeem twice — same token, one balance).
            h = next((h for h in self._holdings if h["token"] == fill["token_id"]), None)
            if h:
                h["shares"] = round(h["shares"] + fill["shares"], 2)
                h["price"] = self.pos["price"]
            else:
                self._holdings.append({"token": fill["token_id"], "slug": fill["slug"],
                                       "direction": fill["direction"],
                                       "shares": fill["shares"], "price": fill["price"]})
            self._persist()
            self._save_json("holdings.json", self._holdings)
            self._journal({"ts": int(time.time()), "kind": "BUY+", "slug": fill["slug"],
                           "direction": fill["direction"], "shares": fill["shares"],
                           "price": fill["price"], "pnl": "",
                           "bankroll": round(self.stake.bankroll, 2)})
        else:
            self._open_position(fill, m.window_end, m.condition_id, "BUY", target)
        if target and self.pos["usd"] >= target * 0.95:  # FILL_TARGET_TOL — target met
            self._traded_window = self.pos["slug"]

    def _taker_enter(self, m, direction: str, mid_fav: float, clip: float,
                     target: float | None = None):
        """Cross the ask now, buying `clip` (the remaining-to-target on a top-up). The
        position is opened or grown via _absorb_fill; the window is locked only when the
        stake reaches target — or, if it was NEVER filled, when the buy is declined (ask
        off-band / no liquidity) so a dead window isn't retried forever. Also the maker
        fallback (no target = one shot)."""
        had_pos = self.pos is not None and self.pos["slug"] == m.slug
        token = m.token_up if direction == "Up" else m.token_down
        fill = self.broker.buy(token, mid_fav, clip, direction, m.slug,
                               min_fav=self.min_fav, max_fav=self.max_fav)
        if fill:
            self._absorb_fill(fill, m, target)
        elif not had_pos:
            self._traded_window = m.slug                 # never entered & declined: skip window

    def _place_maker(self, m, direction: str, mid_fav: float, clip: float):
        """Rest a passive bid at the favorite's price (GTD = auto-expires at window
        end). If it can't be rested (no spread / rejected), cross taker now so a
        runaway favorite — all winners — is never missed."""
        if not (self.min_fav <= mid_fav <= self.max_fav):  # the maker must respect the SAME band the
            self._traded_window = m.slug                   # taker enforces at fill (ask_acceptable):
            return                                         # never rest a bid on an off-edge favorite
        token = m.token_up if direction == "Up" else m.token_down
        order = self.broker.place_limit(token, mid_fav, clip, direction, m.slug,
                                        expiration=m.window_end)
        if order is None:
            self._taker_enter(m, direction, mid_fav, clip)
            return
        order["window_end"] = m.window_end
        order["condition_id"] = m.condition_id
        self.maker_order = order
        self._persist()
        self._journal({"ts": int(time.time()), "kind": "REST_BUY", "slug": m.slug,
                       "direction": direction, "shares": order["shares"],
                       "price": order["price"], "pnl": "",
                       "bankroll": round(self.stake.bankroll, 2)})

    def _journal_cancel(self, o: dict):
        self._journal({"ts": int(time.time()), "kind": "CANCEL", "slug": o["slug"],
                       "direction": o["direction"], "shares": o["shares"],
                       "price": o["price"], "pnl": "",
                       "bankroll": round(self.stake.bankroll, 2)})

    def _manage_maker(self):
        """Drive a resting maker bid to resolution each tick: fill (-> position),
        window-close/cancel (-> drop), or late-and-unfilled (-> cancel + cross taker
        so the winner side is never missed). Mirrors favorite._manage_maker (paper)."""
        o = self.maker_order
        filled, px, status = self.broker.order_fill(o)
        if filled > 0:                                   # (partly) filled -> our position
            fill = {"slug": o["slug"], "token_id": o["token_id"],
                    "direction": o["direction"], "shares": round(filled, 2),
                    "price": px, "usd": round(filled * px, 2)}
            self.maker_order = None
            self._traded_window = o["slug"]
            self._open_position(fill, o["window_end"], o.get("condition_id", ""), "FILL_BUY")
            log(f"🜍 maker filled {o['slug']} {o['direction']}: {filled} @ {px:.2f}")
            return
        frac = ((o["window_end"] - time.time()) / 60.0) / self.win_min
        if status == "gone" or frac <= 0:                # cancelled/expired/closed -> drop
            if status != "gone":
                self.broker.cancel(o)
            self._journal_cancel(o)
            self.maker_order = None
            self._traded_window = o["slug"]
            self._persist()
            return
        if frac < self.maker_fb_frac:                    # late & unfilled -> cancel + taker
            self.broker.cancel(o)
            self._journal_cancel(o)
            self.maker_order = None
            self._persist()
            start = int(o["slug"].rsplit("-", 1)[1])
            m = feeds.fetch_updown_market(self.iv, start, self.underlying)
            if m is None or m.slug != o["slug"]:
                self._traded_window = o["slug"]
                return
            mid_up = feeds.fetch_midpoint(m.token_up)
            if mid_up is None:
                self._traded_window = o["slug"]
                return
            d, mid_fav = favorite_of(mid_up)        # re-derive from CURRENT mid (matches paper;
            #                                         the stale o["direction"] was a drift)
            if is_armed():                          # buy()'s shared ask guard enforces the band
                self._sync_bankroll()
            clip = self.stake.clip()
            if clip <= 0:
                self._traded_window = o["slug"]
                return
            self._taker_enter(m, d, mid_fav, clip)       # no target = one shot (late cross)
            self._traded_window = o["slug"]              # maker fallback never tops up
            return
        if self.maker_recenter:                          # not late & still resting -> track the
            self._recenter_maker(o)                      # favorite mid (colo); else leave bid as-is

    def _recenter_maker(self, o: dict):
        """Co-located maker: keep the resting bid AT the current favorite mid. A bid left
        stale fills only when the favorite WEAKENS to it (adverse selection); re-posting at
        the live mid on drift turns those into spread-capture fills on the bid-ask bounce.
        If the favorite FLIPPED side, abandon the window rather than chase. Gated on
        self.maker_recenter — the static favorite_leadzmk path never reaches here."""
        start = int(o["slug"].rsplit("-", 1)[1])
        m = feeds.fetch_updown_market(self.iv, start, self.underlying)
        if m is None or m.slug != o["slug"]:
            return                                       # market rolled/gone — retry next tick
        mid_up = feeds.fetch_midpoint(m.token_up)
        if mid_up is None:
            return
        d, mid_fav = favorite_of(mid_up)
        if d != o["direction"]:                          # favorite flipped — abandon, don't chase
            self.broker.cancel(o)
            self._journal_cancel(o)
            self.maker_order = None
            self._traded_window = o["slug"]
            self._persist()
            return
        if abs(mid_fav - o["price"]) < RECENTER_EPS:
            return                                       # negligible drift — keep the resting bid
        self.broker.cancel(o)                            # drift >= eps: re-post at the live mid
        self._journal_cancel(o)
        self.maker_order = None
        self._place_maker(m, d, mid_fav, round(o["shares"] * o["price"], 2))

    # ------------------------------------------------------------ loop ---
    def _real_capital(self) -> float:
        """The TRUE shared-wallet value = liquid pUSD + mark-to-market of EVERY open
        outcome position the wallet holds — this pilot's AND its siblings'. So the
        displayed capital ANTICIPATES the redeem (a just-won position counts at ~$1
        before pUSD lands — no phantom dip) AND no longer DIPS when two pilots hold at
        once. That dip was an accounting artifact: each pilot saw the SHARED cash (drawn
        down by every pilot's buys) but added back only ITS OWN positions, so Telegram /
        dashboard showed less than cash+positions during simultaneous trades. Siblings
        are valued from their own fresh posmark.json (no extra chain calls), matching the
        documented intent that every pilot reads the whole wallet as its capital."""
        cash = self.broker.usdc_balance()
        own = self._mark_holdings()
        self._save_json("posmark.json", {"mark": round(own, 2), "ts": int(time.time())})
        return round(cash + own + self._foreign_marks(), 2)

    def _mark_holdings(self) -> float:
        """Mark THIS pilot's still-held tokens: open at mid, won at the on-chain ~$1
        (until redeem lands), lost at 0. Drops resolved/redeemed from tracking."""
        extra, keep = 0.0, []
        for h in self._holdings:
            won = feeds.resolve_market(h["slug"])
            if won is None:                              # still open → mark at mid
                mid = feeds.fetch_midpoint(h["token"]) or h["price"]
                extra += h["shares"] * mid
                keep.append(h)
            elif (h["direction"] == "Up") == won:        # won → ~$1 until redeem lands
                sh = self.broker.conditional_balance(h["token"])
                if sh > 1e-4:
                    extra += sh
                    keep.append(h)                       # else: redeemed → now in cash
            # lost → worth 0, drop from tracking
        self._holdings = keep
        self._save_json("holdings.json", self._holdings)
        return extra

    def _foreign_marks(self) -> float:
        """Sum the position marks of the OTHER armed pilots sharing this wallet, read
        from each sibling's posmark.json (the owner's own fresh mark — so it inherits the
        redeem-anticipation with zero extra chain/API calls here). A stale file (a stopped
        or stalled pilot) is skipped, so a dead pilot's ghost position is never counted."""
        total, now = 0.0, time.time()
        for p in self.sd.parent.glob("live_state_*/posmark.json"):
            if p.parent.resolve() == self.sd.resolve():   # skip self (own mark already in)
                continue
            try:
                d = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if now - d.get("ts", 0) <= POSMARK_FRESH_S:
                total += float(d.get("mark", 0.0))
        return total

    def _sync_bankroll(self):
        """Sync the bankroll to the redeem-anticipated real capital + persist, so the
        dashboard tracks the actual wallet instead of dipping while a win redeems."""
        if is_armed():
            self.stake.update_bankroll(self._real_capital())
            self._persist()

    def run(self, poll, ticks=None):
        mode = "🔴 LIVE" if is_armed() else "DRY-RUN (disarmed)"
        log(f"live ladder up — {mode} | {self.name} {self.underlying.upper()} "
            f"{self.iv} | {self.stake.status()}")
        if is_armed():
            self.broker.ensure_allowances()
            self._sync_bankroll()
            log(f"capital synced on-chain: ${self.stake.bankroll:.2f} pUSD")
            from pmlab.notify import notify, enabled
            if enabled():
                notify(f"🤖 REAL pilot armed — {self.name} {self.underlying.upper()} "
                       f"{self.iv}\nCapital ${self.stake.bankroll:.2f} · notifications active")
                log("Telegram notifications active")
        n = 0
        last_sync = time.time()
        while ticks is None or n < ticks:
            try:
                self._settle()
                self._maybe_enter()
                if is_armed() and time.time() - last_sync > 25:   # keep display live
                    self._sync_bankroll()
                    last_sync = time.time()
            except Exception as e:                       # keep the loop alive
                log(f"tick error: {type(e).__name__}: {e}")
            n += 1
            if ticks is None or n < ticks:
                time.sleep(poll)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--strategy", choices=list(ALL_PRESETS), default="zlead")
    add_preset_args(p)      # --type / --enter-lo / --maker … (per-pilot zlead customization)
    p.add_argument("--interval", choices=["15m", "5m"], default="15m",
                   help="market window (default 15m — the live-PROVEN frame; 5m is "
                        "live-negative). Armed pilots pass this explicitly via the dashboard.")
    p.add_argument("--underlying", choices=COIN_KEYS, default="btc",
                   help="up/down underlying (must match a validated strategy/frame). The coin "
                        "list is the single registry pmlab/coins.py. NOTE bnb's "
                        "favourite book is ~$9 deep, it will barely fill (size it tiny)")
    p.add_argument("--start", type=float, default=20.0, help="starting bankroll $")
    p.add_argument("--stake", type=float, default=5.0,
                   help="$ per bet (flat amount, or the per-bet ceiling if --weighted)")
    p.add_argument("--weighted", action="store_true",
                   help="size each bet as --weight-pct of capital (capped at --bet-max); "
                        "default = flat --stake every bet")
    p.add_argument("--weight-pct", type=float, default=0.10,
                   help="fraction of capital per bet when --weighted (default 0.10)")
    p.add_argument("--bet-max", type=float, default=None,
                   help="per-bet ceiling $ when --weighted (default = per-(coin,frame) book depth)")
    p.add_argument("--state-dir", default="live_state")
    p.add_argument("--poll", type=float, default=10.0)
    p.add_argument("--ticks", type=int, default=None)
    a = p.parse_args()
    if a.bet_max is None:                      # default to the measured per-(coin,frame) ceiling
        a.bet_max = bet_max_for(a.underlying, a.interval)
    LiveLadder(a).run(a.poll, a.ticks)


if __name__ == "__main__":
    main()
