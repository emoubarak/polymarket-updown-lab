"""Engine — the reddening, the stage where the work finally holds.

After coagula (the 5m favorite-longshot harvester) was FALSIFIED — its in-sample
edge turned out to be cherry-picked regime momentum that died on the live choppy
days — the same idea was re-tested on the **15-minute** frame with the discipline
coagula lacked: out-of-sample on real tape, a robust neighborhood (not one cell),
and day-by-day sign stability across two independent samples.

  Buy the already-extreme favorite (price 0.85-0.95) at ~6 minutes left in a
  15m window, pay the taker fee, hold to settlement. No exits.

What survived (research/explore2.py + FINDINGS.md autopsy, 2026-06-17):
  - IN-SAMPLE  06-09..14 (price_track): win 0.977 vs buy 0.936 (+4.1pts),
    EV +0.041/$ net of a 2-cent haircut, positive 6 of 6 days.
  - OUT-OF-SAMPLE 06-14..16 (real executed tape, incl. the choppy 06-15/16 that
    KILLED coagula on 5m): win 0.990 vs buy 0.952 (+3.8pts), EV +0.038/$, 3/3 days.
  - Robust over a contiguous region (enter 5.25-6.75 min left, favorite 0.80-0.90)
    and to the haircut up to +3c — not a single fragile cell.

Why 15m and not 5m: on the slower frame the longshot premium the crowd pays for
the near-dead side is larger and less arbitraged; the favorite's lead reflects a
more established move and holds to settlement even on choppy days.

HONEST RISK (this is a forward-test, not a proven rent): n is still ~270 over
9 days; the extreme-favorite payoff is brutally asymmetric (buy 0.95 → win +0.05 /
lose −1.00, break-even win = the price itself), so a cluster of flips hurts; and
15m books are THINNER than 5m, so real fill slippage may exceed the 2-cent haircut
the backtest assumed. The live fills are the judge. Tripwire: if realized win-rate
sits at/below the average entry price over ~150 trades, the edge is not real.

VARIANTS (research/backtest_engine.py, 2026-06-19, IS 06-09..14 / OOS 06-15..18,
real taker fee + 2c ask haircut, hold to settle, day-by-day sign filter):

  favorite      control — fav 0.85-0.95, no vol gate.
              OOS EV/$ +0.049, days+ 75%, win 0.974 vs px 0.926.
  favorite_vol  + a volatility gate (skip windows whose EWMA 1m vol > ~0.00056,
              the sample's 66th pct). The high-vol favorite is a STORM-favorite
              about to flip — the shadow favorite cannot see by price alone.
              Gating it OUT *raised* OOS EV/$ to +0.060 (days+ 75%, win 0.983).
              This is the integration of the longshot harvester's blind spot,
              not a risk-trim: the gate adds edge, it does not just cut variance.
  favorite_wide  vol gate + a looser floor (fav 0.80-0.95): more windows qualify
              (n≈211) at OOS EV/$ +0.037, days+ 75% — trades volume for edge.

  favorite_lead     + a spot-LEAD floor (research/backtest_lead.py, 2026-06-20). The
              live autopsy found favorite's losses concentrate in SOFT favorites:
              priced 0.85-0.91 by the book but with BTC barely moved by entry —
              near-ties the book overprices (the 2-6bps zone won only 0.917 vs
              0.97 beyond). Demand the favorite's move be ESTABLISHED: lead >= 6
              bps of the window open, toward the favored side. Raised OOS EV/$
              +0.032->+0.040 (days+ 75%, win 0.966 vs px 0.926) keeping ~95% of
              volume. bps not $ so it survives BTC's price drift out-of-sample.
  favorite_vollead  favorite_lead + favorite_vol's vol gate — the union of both shadows the
              price-only harvester is blind to (storm-favorite AND soft-favorite).
              Highest OOS EV/$ +0.044 (days+ 75%, n≈64): trades volume for edge.
  favorite_conviction       the Stone — favorite_vollead's validated GATES, but the flat stake is
              replaced by CONVICTION SIZING (research/backtest_lead.py lapis_curve):
                extremity  the cheaper favorite carries more longshot premium
                           (nigredo: EV/$ unchanged, $-curve up) — size up.
                lead       a vol-normalized z-lead beyond the floor is surer — up.
              Per-$ edge is favorite_vollead's; what changes is the $-curve (IS
              +127->+232, OOS +89->+102 on favorite_vollead's entries).
              A third "greed" tilt (size up on longshot-side demand) was tried and
              CUT: its only testable proxy — aggressive longshot BUY flow in the
              trades tape (research/backtest_greed.py) — runs the OTHER way (the
              favorite's edge FALLS as longshot greed rises; the 5m high-greed
              tercile went EV-negative). Aggressive flow into the dying side is
              informed FADE money, not the dumb lottery demand the tilt assumed.
              Deferred (architectural, not a single-runner strategy): maker entry
              to kill the taker fee, and BTC/ETH/SOL correlation-aware exposure.

  REJECTED — albedo (enter earlier / purer 0.87-0.97) looked clean in-sample
  (+0.029, days+ 100%) but went DEAD out-of-sample (−0.003, days+ 25%): the
  extreme tail 0.94+ is fully arbitraged (win 0.941 == px 0.941). The coagula
  lesson held: in-sample days+ is worth nothing; only OOS tranches.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .paper import MultiBroker, LimitOrder
from .runner import Ctx
from .entry import (ask_acceptable, favorite_of, lead_z, topup_remaining,   # shared (= run_live)
                    flow_tilt_mult, ls_flow_cap_for)
from .staking import weighted_clip                                # shared weighted sizing (= pilot)
from .presets import WEIGHT_PCT, DEFAULT_BET_MAX

if TYPE_CHECKING:
    from .presets import Preset


class Engine:
    """The favorite-longshot harvester. A single parametrized engine drives the
    whole family (favorite / favorite_vol / favorite_wide) — same decision skeleton,
    different floor and an optional volatility gate; see the module docstring."""

    def __init__(self, preset: "Preset", stake: float = 25.0,
                 weighted: bool = False, weight_pct: float = WEIGHT_PCT,
                 bet_max: float = DEFAULT_BET_MAX, min_clip: float = 5.0):
        # ALL gate + execution params come from the Preset (presets.py) — the SINGLE
        # source of truth shared with run_live, so paper and pilot decide identically
        # and enter_lo/enter_hi are no longer hardcoded twice (the old latent drift).
        self.preset = preset
        self.name = preset.key
        # SIZING. Flat (weighted=False) bets `stake` every window. Weighted (2026-06-26,
        # main.py default) sizes each bet as weight_pct of current capital, capped at the
        # per-coin book depth bet_max — the EXACT pilot model (staking.weighted_clip), so
        # paper P&L reflects realistic max-capacity throughput, not a token $25 clip.
        self.stake = stake          # $ per qualifying window when flat
        self.weighted = weighted
        self.weight_pct = weight_pct
        self.bet_max = bet_max
        self.min_clip = min_clip
        self.conviction_size = preset.conviction_size   # favorite_conviction: stake by conviction, not flat
        # MAKER entry: instead of crossing the ask (taker: 2c haircut + taker fee), rest a
        # passive bid at the favorite's price; the broker fills it when the market trades
        # through (paper.py match_orders: 0.7 of size, no fee). If still unfilled once
        # < maker_fb_frac of the window remains, cross taker so a favorite that ran
        # straight up (all winners — adverse) is never missed. Forward-test the magnitude.
        self.maker_entry = preset.maker_entry
        self.maker_fb_frac = preset.maker_fb_frac
        self._maker_order: LimitOrder | None = None
        self.min_fav = preset.min_fav      # only extreme favorites carry the premium
        self.max_fav = preset.max_fav      # above this the upside is too thin vs slippage
        self.vol_cap = preset.vol_cap      # skip storm-favorites (EWMA 1m vol gate); None = off
        self.min_lead_bps = preset.min_lead_bps   # bps lead floor (favorite_lead); 0 = off
        self.min_lead_z = preset.min_lead_z       # vol-normalized lead floor (zlead); 0 = off
        self.enter_lo = preset.enter_lo    # entry slot, window fraction REMAINING (zlead 0.27-0.45)
        self.enter_hi = preset.enter_hi
        # the SHARED entry gate — the exact same object run_live builds from the same
        # Preset, so paper and pilot decide identically (no drift).
        self.gate = preset.gate()
        self._traded_window: str | None = None

    # ------------------------------------------------------------- brain ---

    def on_tick(self, ctx: Ctx, broker: MultiBroker, log) -> None:
        slug = ctx.market.slug
        # maker variant: a bid resting from an earlier tick owns this window's
        # decision until it fills, falls back to a taker cross, or expires.
        if self._maker_order is not None:
            self._manage_maker(ctx, broker, log, slug)
            return
        # filled positions ride to settlement — chasing an exit only feeds
        # informed flow and pays a second fee (coagula/tao's lesson).
        if self._traded_window == slug or ctx.kill_switch:
            return
        # THE ENTRY DECISION — the SHARED gate (pmlab.entry.EntryGate), the
        # exact same logic run_live runs in real money: timing band, extreme favorite
        # by price, optional vol gate + spot-lead floor (bps or vol-normalized z). No
        # _traded_window is set on a soft miss, so a window can still qualify on a
        # later in-band tick if vol eases or the lead establishes. max_fav is enforced
        # on the executable ask in _taker_enter, not here (see EntryGate).
        decision = self.gate.decide(mid_up=ctx.mid_up, sigma=ctx.sigma, spot=ctx.spot,
                                    window_open=ctx.window_open, tau_min=ctx.tau_min,
                                    window_min=ctx.window_min, btc_lead=ctx.btc_lead,
                                    ls_flow_ratio=ctx.ls_flow_ratio,
                                    ls_flow_cap=self._flow_cap(ctx))
        if decision is None:
            return
        direction, mid = decision
        if self.maker_entry:
            self._place_maker(ctx, broker, log, slug, direction, mid)
        else:
            self._taker_enter(ctx, broker, log, slug, direction, mid)

    def _taker_enter(self, ctx: Ctx, broker: MultiBroker, log,
                     slug: str, direction: str, mid: float) -> None:
        """Cross the ask now, buying toward the TARGET stake — not a single shot. A
        first fill can be depth-limited on a thin book; subsequent in-slot ticks TOP
        UP the same position (merged via broker.topup, one settlement) until it reaches
        the target. The window is only locked (_traded_window) once the target is met,
        or — if it was never entered — when the ask has run off the validated band.
        Also the maker variant's fallback once the window runs short."""
        # an open position for this window means we are TOPPING UP; derive how much
        # is already filled from it (restart-safe, no separate counter to drift) and
        # never add to the OTHER side if the favorite flipped mid-window.
        existing = next((p for p in broker.positions_for(slug) if p.tag == "favorite"), None)
        if existing is not None and existing.direction != direction:
            return                              # favorite flipped — leave the original alone
        target = (self._base_stake(broker) * self._conviction_mult(ctx, direction, mid)
                  * flow_tilt_mult(ctx.ls_flow_ratio, self._flow_cap(ctx), self.gate.ls_flow_tilt))
        filled = existing.shares * existing.avg_price if existing else 0.0
        remaining = topup_remaining(target, filled, min_clip=self.min_clip)
        if remaining <= 0:
            self._traded_window = slug          # target already reached — done
            return
        # meet the book the order will actually hit after travel time; the
        # backtest's 2-cent haircut IS this abandon band. Reject a gift that ran.
        book = ctx.exec_book(direction)
        if not book["asks"]:
            return
        ask = book["asks"][0][0]
        if not ask_acceptable(ask, mid, self.min_fav, self.max_fav):  # shared guard (entry.py)
            if existing is None:
                self._traded_window = slug      # never entered & ask off-band: skip the window
            return                              # a partial position: keep it, retry next tick
        capped = [(pr, s) for pr, s in book["asks"] if pr <= ask + 0.02]
        usd = min(remaining, broker.cash)
        if usd < self.min_clip:
            return
        if existing is not None:
            pos = broker.topup(existing, ctx.token_for(direction), usd, capped,
                               fee_rate=ctx.taker_rate)
        else:
            pos = broker.buy(slug, ctx.token_for(direction), direction, usd,
                             capped, ctx.market.window_end, tag="favorite",
                             fee_rate=ctx.taker_rate)
        if not pos:
            return
        log(f"🜍 {self.name}: {pos.shares:.1f} {direction} @ {pos.avg_price:.3f} "
            f"(fav {mid:.2f}, {ctx.tau_min:.1f}min left, "
            f"${pos.shares * pos.avg_price:.0f}/${target:.0f}, ride to settlement)")
        if topup_remaining(target, pos.shares * pos.avg_price, min_clip=self.min_clip) <= 0:
            self._traded_window = slug          # target met → lock the window

    # -------------------------------------------------------- maker entry ---

    def _place_maker(self, ctx: Ctx, broker: MultiBroker, log,
                     slug: str, direction: str, mid: float) -> None:
        """Rest a passive bid at the favorite's price instead of crossing. The
        broker fills it (0.7 of size, no fee) only once the market trades through
        — paper.py match_orders. Size UP by the fill ratio so a fill lands ~the
        intended stake. The order expires at window_end; _manage_maker handles the
        fill / taker-fallback transitions."""
        usd = (self._base_stake(broker) * self._conviction_mult(ctx, direction, mid)
               * flow_tilt_mult(ctx.ls_flow_ratio, self._flow_cap(ctx), self.gate.ls_flow_tilt))
        shares = (usd / broker.passive_fill_ratio) / mid
        order = LimitOrder(slug, ctx.token_for(direction), direction, "buy",
                           price=round(mid, 4), shares=shares,
                           window_end=ctx.market.window_end,
                           expire_ts=ctx.market.window_end, tag=self.name)
        if broker.place_limit(order) is not None:
            self._maker_order = order
            log(f"🜍 {self.name}: rest bid {shares:.1f} {direction} @ {mid:.3f} "
                f"(maker; taker fallback < {self.maker_fb_frac:.0%} window left)")

    def _manage_maker(self, ctx: Ctx, broker: MultiBroker, log, slug: str) -> None:
        """Drive a resting maker bid to resolution: fill (position rides to
        settlement), taker fallback once the window runs short (never miss a
        favorite that ran straight up — the miss side is all winners), or cancel
        under the kill switch."""
        o = self._maker_order
        if o.slug != slug:                      # window rolled over
            self._maker_order = None            # a fill rides to settle; else expire
            return
        if o not in broker.orders:              # filled this window (or expired)
            self._traded_window = slug
            self._maker_order = None
            return
        if ctx.kill_switch:                     # no new passive risk under kill
            broker.cancel(o)
            self._maker_order = None
            return
        if ctx.tau_min / ctx.window_min < self.maker_fb_frac:
            broker.cancel(o)                    # give up waiting -> cross now
            self._maker_order = None
            direction, mid = favorite_of(ctx.mid_up)   # re-derive from CURRENT mid (shared)
            self._taker_enter(ctx, broker, log, slug, direction, mid)
            self._traded_window = slug          # one shot, even if the book ran

    def _flow_cap(self, ctx: Ctx) -> float:
        """The PER-FRAME longshot-flow threshold for this runner (0 = signal off). One strat
        zleadp, but 15m runners use ~2.4 and 5m runners ~2.7 — routed by the window length, so the
        veto/tilt adapts to each frame. Mirrors run_live (always-modularize)."""
        if not self.gate.ls_flow_cap:
            return 0.0
        frame = "5m" if ctx.window_min <= 5 else "15m"
        return ls_flow_cap_for(frame, self.gate.ls_flow_cap)

    # ----------------------------------------------------- conviction sizing ---

    def _base_stake(self, broker: MultiBroker) -> float:
        """The window's target stake BEFORE the conviction multiplier. Flat -> self.stake;
        weighted -> weight_pct of current capital capped at the coin's book depth (bet_max),
        the same staking.weighted_clip the real pilot uses. Capital = cash marked-to-cost of
        open positions (zlead holds <=1 at entry time, so this is ~cash anyway)."""
        if not self.weighted:
            return self.stake
        capital = broker.cash + sum(p.shares * p.avg_price for p in broker.positions)
        return weighted_clip(capital, self.weight_pct, self.bet_max, self.min_clip)

    def _conviction_mult(self, ctx: Ctx, direction: str, fav: float) -> float:
        """Stake multiplier for the conviction-sized variant (favorite_conviction); flat 1.0 for
        every other family member (identical to the old behaviour). Two bounded
        tilts, their product clamped to [0.5, 2.0] so no single signal can blow up
        a bet on a brutally asymmetric payoff:

          extremity  the cheaper favorite carries MORE longshot premium (the crowd
                     overpays the fatter near-dead side). nigredo backtest: EV/$ is
                     unchanged, the $-curve improves. Size up toward min_fav.
          lead       a vol-normalized z-lead well beyond the entry floor is a surer
                     bet — scale up gently with the excess z (backtest_lead z mode).

        A third "greed" tilt (size UP on longshot-side demand) was tried and CUT:
        its only testable proxy — aggressive longshot BUY flow in the trades tape
        (research/backtest_greed.py) — runs the OTHER way. The favorite's edge
        FALLS as longshot greed rises (5m high-greed tercile went EV-negative):
        aggressive flow into the dying side is informed FADE money, not the dumb
        lottery demand the tilt assumed. Resting depth (what the tilt would have
        read) has no retroactive history, so it could not be vindicated either.
        """
        if not self.conviction_size:
            return 1.0
        # extremity: 0.85 fav -> 1.20x, 0.95 fav -> 1.00x (band 0.85-0.95)
        m = 1.0 + 2.0 * max(0.0, self.max_fav - fav)
        # established-lead conviction, in remaining-window sigmas (z); reference
        # threshold 1.0 == "the move is established". Uses the SHARED lead_z (entry.py),
        # the same signal the gate floors on — None (degenerate inputs) → no tilt.
        z = lead_z(ctx.spot, ctx.window_open, ctx.sigma, ctx.tau_min, direction)
        if z is not None:
            m *= 1.0 + 0.3 * max(0.0, z - 1.0)
        return max(0.5, min(2.0, m))

    def status(self, ctx: Ctx) -> str:
        if ctx.mid_up is None:
            return "favorite —"
        fav = max(ctx.mid_up, 1 - ctx.mid_up)
        return f"fav {fav:.2f} | {ctx.tau_min:.1f}m"
