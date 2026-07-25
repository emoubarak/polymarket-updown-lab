"""Adaptive staking for the favorite LIVE ladder — $100 → $300/trade.

The edge is real but UNCONFIRMED at small n: the live win-rate's 95% CI still
includes the break-even price. So aggression is tied to statistical confidence,
not hope:

  - while n < confirm_n      → f_learning (small): harvest the proof cheaply.
  - after the tripwire clears → f_confirmed (bigger): size the proven edge.
  - if realized win-rate ≤ avg entry price over the window → STOP (edge dead,
    same death as coagula).

Two anti-ruin reflexes on top, because the payoff is 1:8 asymmetric:
  - drawdown brake: bankroll below (1-dd_brake)×peak → halve the fraction.
  - regime pause: rolling net EV over the last `regime_window` trades ≤ 0 →
    take NO new risk (open positions still ride to settlement).

Pure stdlib, no I/O. The live runner owns persistence and calls record() after
each settlement, update_bankroll() each tick, and clip() to size the next entry.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


def weighted_clip(capital: float, frac: float, bet_max: float,
                  min_clip: float = 5.0) -> float:
    """USD to stake = `frac` of capital, floored at min_clip, capped at the book-depth
    bet_max, and never more than the capital on hand. The SINGLE definition of the
    pilot's weighted sizing — reused by the paper engine (favorite), the retroactive
    recompute and the backtest so they all size identically (cf. always-modularize)."""
    return round(min(max(min_clip, frac * capital), bet_max, capital), 2)


@dataclass
class AdaptiveStake:
    bankroll: float
    f_learning: float = 0.04        # fraction while the edge is unconfirmed
    f_confirmed: float = 0.08       # fraction once the tripwire has cleared
    confirm_n: int = 150            # trades of proof required before sizing up
    min_clip: float = 5.0           # exchange-floor-respecting minimum
    max_clip: float = 300.0         # measured 15m book depth at 2c — hard ceiling
    weighted: bool = True           # False = FLAT stake (= max_clip) every bet,
                                    # no f-scaling/compounding ("weighting off")
    dd_brake: float = 0.20          # halve f if bankroll < (1-dd_brake)*peak
    regime_window: int = 40         # pause new risk if rolling EV<0 over this many
    regime_pause: bool = True        # set False to DISABLE the rolling-EV pause.
    # Why opt out: on a strategy whose losses aren't autocorrelated (favorite:
    # P(loss|loss)=0) and whose payoff is asymmetric, this pause fires on ordinary
    # variance, not edge death — and it SELF-PERPETUATES (clip=0 → no new settled
    # trade → the rolling window never refreshes → frozen forever). The live ladder
    # disables it and lets the operator arbitrate pauses; the n>=confirm_n
    # win<=price tripwire below (true edge death) still applies.

    n_trades: int = 0
    peak: float | None = None
    _wins: deque = field(default_factory=lambda: deque(maxlen=150))     # 1/0
    _prices: deque = field(default_factory=lambda: deque(maxlen=150))   # entry px
    _pnl: deque = field(default_factory=lambda: deque(maxlen=40))       # net/$1

    def __post_init__(self) -> None:
        if self.peak is None:
            self.peak = self.bankroll

    # -- called by the runner after each settlement --
    def record(self, entry_price: float, won: bool, pnl_per_dollar: float) -> None:
        self.n_trades += 1
        self._wins.append(1 if won else 0)
        self._prices.append(entry_price)
        self._pnl.append(pnl_per_dollar)

    def update_bankroll(self, bankroll: float) -> None:
        self.bankroll = bankroll
        self.peak = max(self.peak, bankroll)

    # -- gates --
    @property
    def tripwire_breached(self) -> bool:
        """Realized win-rate at/below avg entry price over a full window = no edge."""
        if len(self._wins) < self.confirm_n:
            return False
        wr = sum(self._wins) / len(self._wins)
        avg_px = sum(self._prices) / len(self._prices)
        return wr <= avg_px

    @property
    def confirmed(self) -> bool:
        return self.n_trades >= self.confirm_n and not self.tripwire_breached

    @property
    def regime_paused(self) -> bool:
        if not self.regime_pause:
            return False
        if len(self._pnl) < self.regime_window:
            return False
        return sum(self._pnl) / len(self._pnl) <= 0.0

    def fraction(self) -> float:
        f = self.f_confirmed if self.confirmed else self.f_learning
        # dd_brake=0 DISABLES the brake (else `bankroll < peak` halves f on any
        # dip below the high-water mark — not what 0 should mean).
        if self.dd_brake > 0 and self.bankroll < (1.0 - self.dd_brake) * self.peak:
            f *= 0.5                 # de-risk into a drawdown
        return f

    # -- the number the runner actually uses --
    def clip(self) -> float:
        """USD to stake on the next qualifying window, or 0.0 to stand down.
        Flat (weighted=False) bets max_clip every time; the tripwire and regime
        kill-switches still apply — those are safety, not weighting."""
        if self.tripwire_breached or self.regime_paused:
            return 0.0
        if not self.weighted:
            return round(min(self.max_clip, self.bankroll), 2)
        # weighted: the shared formula (also drives paper + recompute + backtest)
        return weighted_clip(self.bankroll, self.fraction(), self.max_clip, self.min_clip)

    def status(self) -> str:
        wr = (sum(self._wins) / len(self._wins)) if self._wins else 0.0
        px = (sum(self._prices) / len(self._prices)) if self._prices else 0.0
        phase = ("STOP" if self.tripwire_breached else
                 "PAUSE" if self.regime_paused else
                 "confirmed" if self.confirmed else "learning")
        size = "flat" if not self.weighted else f"f={self.fraction():.0%}"
        return (f"{phase} | n={self.n_trades} {size} "
                f"clip=${self.clip():.0f} | win {wr:.3f} vs px {px:.3f} "
                f"| bank ${self.bankroll:.2f} (peak ${self.peak:.2f})")

    # -- persistence helpers (the runner serializes these) --
    def to_dict(self) -> dict:
        return {"bankroll": self.bankroll, "n_trades": self.n_trades,
                "peak": self.peak, "wins": list(self._wins),
                "prices": list(self._prices), "pnl": list(self._pnl)}

    @classmethod
    def from_dict(cls, d: dict, **kw) -> "AdaptiveStake":
        s = cls(bankroll=d.get("bankroll", 100.0), **kw)
        s.n_trades = d.get("n_trades", 0)
        s.peak = d.get("peak", s.bankroll)
        s._wins = deque(d.get("wins", []), maxlen=150)
        s._prices = deque(d.get("prices", []), maxlen=150)
        s._pnl = deque(d.get("pnl", []), maxlen=40)
        return s
