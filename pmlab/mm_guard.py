"""MM safety harness — runtime invariants + pre-flight self-checks.

Every check here maps to a REAL incident that cost real money or hours of confusion (2026-07-01 session,
−$62). The rule the session taught: NEVER validate in production. These invariants HALT the bot on a
violation instead of bleeding silently, and the pre-flight refuses to arm on a misconfig. Stdlib only
(paper convention). See research/std0/MM_WORKFLOW.md for how this fits the workflow.

Incident → check:
  * UserFeed counted the TAKER's size (114) for our 5-share order  → check_fill: a fill can't exceed our clip.
  * inventory tracking desynced from on-chain (that misparse)      → reconcile_inv: engine vs chain, HALT.
  * settle-lag phantom mark (−$block) → false kills                → check_mark: mark can't be < −block.
  * cap/flatten dumped at floor 0.02 (−$26)                        → check_sell_price: never sell ≫ below mid.
  * mint_usd > pUSD aborted SILENTLY → no quotes ("no orders")     → preflight: mint ≤ pUSD (+ no silent aborts).
  * clip 3 < CLOB min 5 → every order rejected                     → preflight: clip ≥ CLOB_MIN.
  * beta 0.7 crossed the tight book → 0 fills                      → the DRY-vs-live gate (mm_dry_gate.py), not here.
"""
from __future__ import annotations

CLOB_MIN_SHARES = 5          # Polymarket CLOB minimum order size to POST (fills can be partial/fractional)
MAX_SELL_BELOW_MID = 0.10    # never sell a resting/flatten order more than this below the mid (anti-dump)


class GuardHalt(Exception):
    """Raised when an invariant is violated → the engine must cancel all + stop (never bleed silently)."""


class Guard:
    """Runtime invariants. `halt()` is called on a violation: it logs, flips `tripped`, and (if given)
    invokes on_halt (the engine's cancel-all + kill). Checks return True if OK, False/raise on violation."""

    def __init__(self, log=print, on_halt=None):
        self.log = log
        self.on_halt = on_halt
        self.tripped = False
        self.reason = ""
        self._desync_streak = 0        # consecutive reconcile failures (one-off = timing, persistent = bug)

    def _halt(self, reason):
        if self.tripped:
            return
        self.tripped = True
        self.reason = reason
        self.log(f"🛑🛡 GUARD HALT: {reason}")
        if self.on_halt:
            try:
                self.on_halt()
            except Exception as e:
                self.log(f"on_halt err: {e}")

    def check_fill(self, fill_size, clip):
        """A single fill can NEVER exceed our resting order size (clip). A 114-share 'fill' on a 5-share
        order is impossible → it's a parse bug (we're reading the taker's size). Small tolerance for float."""
        if fill_size > clip * 1.05 + 0.01:
            self._halt(f"fill {fill_size} > clip {clip} — impossible, fill-parse bug (reading taker size?)")
            return False
        return True

    def reconcile_inv(self, eng_up, eng_dn, chain_up, chain_dn, tol=None, max_streak=3):
        """Engine-tracked inventory (from fills) vs on-chain truth (baltoks). A ONE-OFF divergence is a
        timing gap (a fill/redeem in flight) that _sync_onchain corrects anyway — do NOT kill on it (that
        false-halt left the bot inert → no bets). Only HALT on a PERSISTENT desync (max_streak consecutive
        failures) = the tracker is truly lying (the −60% misparse). Call AFTER reading chain, BEFORE overwrite."""
        blk = max(chain_up, chain_dn, eng_up, eng_dn, 1)
        if tol is None:
            tol = max(3.0, 0.15 * blk)
        du, dd = abs(eng_up - chain_up), abs(eng_dn - chain_dn)
        if du > tol or dd > tol:
            self._desync_streak += 1
            self.log(f"⚠🛡 inv desync #{self._desync_streak}: engine {eng_up:.0f}/{eng_dn:.0f} vs chain "
                     f"{chain_up:.0f}/{chain_dn:.0f} (Δ {du:.0f}/{dd:.0f} > tol {tol:.0f}) — sync will correct")
            if self._desync_streak >= max_streak:
                self._halt(f"PERSISTENT inv desync ×{self._desync_streak} — fill tracking is truly lying")
                return False
            return True                # transient: let _sync_onchain overwrite to truth and continue
        self._desync_streak = 0        # matched again → reset the streak
        return True

    def check_mark(self, mark, block, buffer=3.0):
        """A matched block ALWAYS redeems ≥ 0 (1 Up+1 Down = $1). So the mark can't be more negative than
        ~the block — a −$block mark with a held/settling block is a PHANTOM (redeem-credit lag), not a loss.
        Returns True if the mark is trustworthy for the kill; False = phantom (suppress the kill)."""
        if mark < -(block + buffer):
            self.log(f"⚠🛡 mark {mark:+.2f} < −block {block:.0f} → PHANTOM (redeem lag), kill suppressed")
            return False
        return True

    def check_sell_price(self, price, mid):
        """Never place a sell (resting ask or flatten) more than MAX_SELL_BELOW_MID under the mid — the
        0.02 dump that cost −$26. Returns a safe price (clamped) so the caller never dumps."""
        if mid is None:
            return price
        floor = mid - MAX_SELL_BELOW_MID
        if price < floor:
            self.log(f"⚠🛡 sell {price:.2f} ≫ below mid {mid:.2f} → clamped to {floor:.2f} (anti-dump)")
            return round(floor, 2)
        return price


def preflight(cfg, pusd, armed, log=print):
    """Startup self-checks. Returns (ok, failures). The runner REFUSES to arm real money if any fail —
    a misconfig must never reach the live wallet again. cfg = the argparse namespace; pusd = live balance."""
    f = []
    clip = getattr(cfg, "clip", 0)
    mint = getattr(cfg, "mint_usd", 0)
    lo, hi = getattr(cfg, "min_quote", 0), getattr(cfg, "max_quote", 1)
    kill = getattr(cfg, "kill_loss", 0)
    maxinv = getattr(cfg, "max_inv", 0)
    if clip < CLOB_MIN_SHARES:
        f.append(f"clip {clip} < CLOB min {CLOB_MIN_SHARES} → every order rejected")
    if armed and mint > pusd + 0.01:
        f.append(f"mint_usd {mint} > pUSD {pusd:.2f} → mint aborts, no quotes (lower mint or fund)")
    if not (0 < lo < hi < 1):
        f.append(f"band [{lo},{hi}] invalid (need 0 < min < max < 1)")
    if kill <= 0:
        f.append(f"kill_loss {kill} ≤ 0 → no downside stop")
    if maxinv < clip:
        f.append(f"max_inv {maxinv} < clip {clip} → one fill breaches the neutrality cap")
    ok = not f
    log(f"🛡 preflight: {'PASS ✅' if ok else 'FAIL ❌'} (pUSD ${pusd:.2f}, armed={armed})")
    for x in f:
        log(f"   ✗ {x}")
    return ok, f
