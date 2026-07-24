"""The favorite-family ENTRY GATE — the single source of truth for "should we act
on this window, and which side?".

Used by BOTH the paper runner (favorite.on_tick) and the real pilot (run_live), so
the two can NEVER drift. They HAD drifted: a guard the paper had and the pilot
lacked cost the live pilot 2 losses the paper avoided. The lesson — mutualise the
decision, don't duplicate it — lives here.

Pure and broker-agnostic (stdlib only): it takes the market scalars and returns a
(direction, favorite-mid) decision or None. The EXECUTION guards that depend on the
live book (the executable ask must still be in [min_fav, max_fav]; abandon if the
book moved off the decision mid) are applied at fill time by each broker, with the
SAME rule on both sides — see pmlab.paper (MultiBroker / favorite._taker_enter)
and pmlab.live (LiveBroker.buy).
"""
from __future__ import annotations

from dataclasses import dataclass


def favorite_of(mid_up):
    """(direction, favorite-mid) from the Up-token midpoint — the canonical favorite
    identification. Shared by the gate AND the maker taker-fallbacks (paper + real),
    so a favorite that FLIPPED mid-rest is handled identically on both sides (the
    fallback used to keep the stale direction on the real path — a drift)."""
    direction = "Up" if mid_up >= 0.5 else "Down"
    return direction, (mid_up if direction == "Up" else 1.0 - mid_up)


# top-up: the validated stake is a TARGET, not a single shot. A first taker fill can
# be depth-limited on a thin book (the live btc-15m pilot once got $5 of an intended
# $30 — the touch had ~6 shares inside the +2c band and FAK killed the rest), so the
# engine keeps buying toward the target across the entry slot instead of accepting the
# partial. Shared by paper (favorite._taker_enter) and real (run_live._maybe_enter) so the
# rule can't drift. The fills are MERGED into one position (one settlement, honest count).
FILL_TARGET_TOL = 0.95     # a window is "filled" at >= 95% of target (stop chasing the
#                            last few % through a thin book; book/rounding noise)


def topup_remaining(target_usd, filled_usd, min_clip, tol=FILL_TARGET_TOL):
    """$ still to buy this window to reach `target_usd`, or 0.0 if the position is
    already >= tol of target OR the remainder is below `min_clip` (not worth a second
    order / below the venue minimum). 0 target (stood down) returns 0."""
    if target_usd <= 0 or filled_usd >= target_usd * tol:
        return 0.0
    remaining = target_usd - filled_usd
    return remaining if remaining >= min_clip else 0.0


def lead_z(spot, window_open, sigma, tau_min, direction):
    """Vol-normalized lead z = the favorite's directional displacement / (sigma·√tau), in
    remaining-window sigmas — THE zlead signal. The SINGLE definition, shared by the
    EntryGate z floor AND the conviction-sizing tilt (favorite), so they can't drift. Returns
    None when inputs are degenerate (window_open/sigma/tau ≤ 0) — every caller then SKIPS its
    z logic (the gate doesn't reject, the tilt doesn't fire), preserving the old behaviour."""
    if window_open <= 0 or sigma <= 0 or tau_min <= 0:
        return None
    conv = (spot - window_open) if direction == "Up" else (window_open - spot)
    return (conv / window_open) / (sigma * tau_min ** 0.5)


def ask_acceptable(ask, mid, min_fav, max_fav, band=0.02):
    """The shared EXECUTION guard for crossing the ask, used by BOTH paper
    (favorite._taker_enter) and real (live.buy): the executable ask must still be an
    extreme favorite (in [min_fav, max_fav]) AND within `band` of the decision mid
    (the book hasn't run off it, in EITHER direction). THIS is the rule that drifted
    once and cost 2 real losses — it lives here, once. False if ask is None."""
    return ask is not None and min_fav <= ask <= max_fav and abs(ask - mid) <= band


# BTC's move must oppose the favorite by MORE than this (fraction) to trigger the btc_align veto
# — a small deadband so a flat BTC tape never vetoes (the deep backtest's "misaligned" was strict-
# sign; 2bps guards against vetoing on noise).
BTC_ALIGN_DEADBAND = 0.0002

# Longshot-flow stake TILT (the PnL-additive use of the #5 signal — better than vetoing, which throws
# away the +EV of high-flow windows). Bet MORE on clean (low-flow) windows, LESS on dirty (high-flow),
# at ~constant average exposure (≈2/3 of windows are clean → 0.67*UP + 0.33*DOWN ≈ 1.0). The deep 90d
# backtest: tilting toward clean beats flat by ~+6% at equal exposure/ruin. This is the VALIDATED
# INVERSE of the cut "greed" tilt (favorite._conviction_mult docstring): the favorite's edge FALLS as
# longshot demand rises, so size DOWN into it.
FLOW_TILT_UP, FLOW_TILT_DOWN = 1.2, 0.6


# PER-FRAME longshot-flow threshold (the ratio above which a window is "dirty"). Calibrated
# separately per frame (deep corpus): 15m p67≈2.4, 5m p67≈2.7 — close but routed per-frame so the
# ONE strat zleadp adapts to each window length (resolved at runtime by the caller, like bet_max_for).
LS_FLOW_CAP_BY_FRAME = {"5m": 2.7, "15m": 2.4}


def ls_flow_cap_for(frame, base: float = 2.5) -> float:
    """The per-frame longshot-flow threshold for zleadp/zleadf. `base` (the preset's ls_flow_cap, used
    only as the enable-flag/fallback) is returned for an unknown frame so behaviour never silently
    breaks. SHARED by paper + real so the per-frame routing can't drift."""
    return LS_FLOW_CAP_BY_FRAME.get(frame, base)


def flow_tilt_mult(ls_flow_ratio, cap, enabled) -> float:
    """Stake multiplier for the longshot-flow tilt (1.0 = neutral). UP when longshot flow is below the
    cap (clean window), DOWN when at/above it (informed-fade-heavy window). Shared by paper + real."""
    if not enabled or not cap or ls_flow_ratio is None:
        return 1.0
    return FLOW_TILT_UP if ls_flow_ratio < cap else FLOW_TILT_DOWN


@dataclass(frozen=True)
class EntryGate:
    """Engine-family entry gate. One instance per strategy, built from its params.

    The favorite is identified by PRICE (the edge is in the price, not a model):
    buy the already-extreme favorite mid-window and hold to settlement. min_fav is
    the floor; max_fav is enforced on the executable ASK at fill time (NOT here),
    exactly as the validated paper engine does, so a favorite that has run too far
    is still skipped while a high mid that the book won't fill above max_fav is not
    pre-rejected. Optional gates: a volatility cap (skip storm-favorites) and a
    spot-lead floor in bps or vol-normalized z (skip soft, not-yet-established
    favorites). 0 / None disables a gate.
    """
    min_fav: float = 0.85
    max_fav: float = 0.95
    vol_cap: float | None = None
    min_lead_bps: float = 0.0
    min_lead_z: float = 0.0
    btc_align: bool = False         # BTC-align veto (alts): skip if BTC's move opposes the favorite
    ls_flow_cap: float = 0.0        # longshot-flow threshold (alts): pre-entry longshot $vol / depth-cap.
    #                                 With ls_flow_tilt=False it VETOES at/above this; True → size-TILT only
    ls_flow_tilt: bool = False      # use the flow signal as a stake TILT (no veto) — the PnL-additive use
    enter_lo: float = 0.27          # fraction of the window still REMAINING …
    enter_hi: float = 0.45          # … (zlead flagship 0.27-0.45 == 4-6.75 min left in 15m;
    #                                 widened 2026-06-25 — presets.py drives the real value)

    @property
    def needs_spot(self) -> bool:
        """Whether decide() actually uses spot/sigma/window_open — lets a caller skip
        the klines fetch for a price-only strategy (e.g. bare favorite)."""
        return self.vol_cap is not None or bool(self.min_lead_bps) or bool(self.min_lead_z)

    def decide(self, *, mid_up, sigma, spot, window_open, tau_min, window_min,
               btc_lead=None, ls_flow_ratio=None, ls_flow_cap=None):
        """Return (direction, mid_fav) if the window qualifies for entry, else None.

        mid_up = midpoint price of the Up token; the favorite mid is mid_up or
        1-mid_up (matches Ctx.mid_for and run_live identically). sigma = EWMA 1m vol,
        window_open = price at the window's open, tau_min = minutes left, window_min =
        window length. Only the scalars the active gates need must be real; pass 0 for
        the rest (needs_spot tells you whether they matter). btc_lead = BTC's signed
        move-to-now (fraction); only used when btc_align is on (None disables the veto —
        e.g. on BTC itself, no self-veto)."""
        if mid_up is None or window_min <= 0:
            return None
        frac_remaining = tau_min / window_min
        if not (self.enter_lo <= frac_remaining <= self.enter_hi):
            return None
        direction, mid_fav = favorite_of(mid_up)
        if mid_fav < self.min_fav:                 # not extreme enough — no premium
            return None
        if self.vol_cap is not None and sigma > self.vol_cap:   # storm-favorite gate
            return None
        if self.min_lead_bps and window_open > 0:               # bps lead floor (favorite_lead)
            lead = (spot - window_open) if direction == "Up" else (window_open - spot)
            if lead / window_open * 1e4 < self.min_lead_bps:
                return None
        if self.min_lead_z:                                     # vol-normalized z floor (zlead)
            z = lead_z(spot, window_open, sigma, tau_min, direction)
            if z is not None and z < self.min_lead_z:           # degenerate (None) → don't reject
                return None
        if self.btc_align and btc_lead is not None:             # BTC-align veto (alts only; 90d OOS)
            opposed = ((direction == "Up" and btc_lead < -BTC_ALIGN_DEADBAND) or
                       (direction == "Down" and btc_lead > BTC_ALIGN_DEADBAND))
            if opposed:                                         # BTC pushing AGAINST the favorite
                return None
        if self.ls_flow_cap and not self.ls_flow_tilt and ls_flow_ratio is not None:
            cap = ls_flow_cap if ls_flow_cap is not None else self.ls_flow_cap   # per-frame override
            if ls_flow_ratio >= cap:                            # longshot-flow VETO (tilt-mode skips this)
                return None
        return direction, mid_fav
