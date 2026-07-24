"""Paper broker — simulated fills against the *real* Polymarket orderbook.

Buys walk the live asks, sells walk the live bids, so slippage is real.
State persists to JSON, every fill and settlement appends to a CSV journal.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .journal import JOURNAL_COLUMNS   # the single journal.csv schema


def fee_for(shares: float, price: float, rate: float) -> float:
    """Taker fee C × rate × p × (1−p). The crypto schedule's rate 0.07 IS charged on-chain
    (re-verified 2026-06-27 from raw tx receipts — the pUSD relayer 0xe111… forwards it to the
    fee collector at entry; see feeds.fetch_updown_market), so the runner passes
    rate=feeds.taker_rate=0.07. Makers never pay; settlement never pays."""
    return rate * price * (1 - price) * shares


@dataclass
class Position:
    slug: str
    token_id: str
    direction: str            # 'Up' | 'Down'
    shares: float
    avg_price: float
    window_end: int
    opened_at: float = field(default_factory=time.time)
    tag: str = ""             # strategy-specific label (e.g. 'arb', 'leadlag')
    entry_fee: float = 0.0    # carried into realized P&L at close


@dataclass
class LimitOrder:
    slug: str
    token_id: str
    direction: str            # 'Up' | 'Down'
    side: str                 # 'buy' | 'sell'
    price: float
    shares: float
    window_end: int
    expire_ts: float
    tag: str = ""


def walk_book(levels: list[tuple[float, float]], usd: float | None = None,
              shares: float | None = None, impact_k: float = 0.0) -> tuple[float, float]:
    """Consume levels (best first). Returns (filled_shares, avg_price).

    Already real: the average price walks UP each level you eat — true
    book-traversal slippage. `impact_k` adds the missing piece, MARKET IMPACT AT
    SIZE: a surcharge on the average proportional to the fraction of shown depth
    you consume (your own footprint + phantom/cancelled liquidity). It is 0 by
    default — and 0 is CORRECT at the live $25 clip, where real fills match the
    zero-impact model because the book is ~10× the clip. It only bites when the
    stake approaches book depth, which is exactly where the engine would
    otherwise lie. Calibrate impact_k against real fills once run_live is armed."""
    filled, cost = 0.0, 0.0
    shown = sum(s for _, s in levels) or 1.0
    for price, size in levels:
        if usd is not None:
            afford = (usd - cost) / price
            take = min(size, afford)
        else:
            take = min(size, shares - filled)
        if take <= 1e-9:
            break
        filled += take
        cost += take * price
    avg = cost / filled if filled else 0.0
    if impact_k and filled:
        avg *= 1.0 + impact_k * (filled / shown)   # ∝ fraction of shown depth eaten
    return filled, avg


class MultiBroker:
    """Paper broker with several concurrent positions and resting limit orders.

    Needed by the non-Gurdjieff brains: polarity arbitrage holds Up and Down
    at once, the taoist strategy only rests passive limit orders. Fills still
    walk the *real* book; resting buys fill when the live ask crosses them.
    """

    def __init__(self, bankroll: float, state_path: Path, journal_path: Path,
                 passive_fill_ratio: float = 0.7, trade_through: float = 0.01,
                 impact_k: float = 0.0):
        self.state_path = state_path
        self.journal_path = journal_path
        self.cash = bankroll
        self.initial = bankroll
        self.positions: list[Position] = []
        self.orders: list[LimitOrder] = []
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self.n_trades = 0
        self.n_wins = 0
        # adverse-selection model for resting orders: the level must trade
        # *through* our price (we are not first in queue), and only part of
        # our size is assumed filled
        self.passive_fill_ratio = passive_fill_ratio
        self.trade_through = trade_through
        # market-impact-at-size on taker fills (0 = off; correct at the $25 clip,
        # turn on to stress-test a larger stake — see walk_book)
        self.impact_k = impact_k
        self._load()
        if not self.state_path.exists():
            self._save()

    # ----------------------------------------------------------- actions ---

    def buy(self, slug: str, token_id: str, direction: str, usd: float,
            asks: list[tuple[float, float]], window_end: int,
            tag: str = "", fee_rate: float = 0.0) -> Position | None:
        usd = min(usd, self.cash)
        if usd <= 0 or not asks:
            return None
        shares, avg = walk_book(asks, usd=usd, impact_k=self.impact_k)
        if shares <= 0:
            return None
        fee = fee_for(shares, avg, fee_rate)
        if shares * avg + fee > self.cash:
            shares *= self.cash / (shares * avg + fee)
            fee = fee_for(shares, avg, fee_rate)
        self.cash -= shares * avg + fee
        self.fees_paid += fee
        pos = Position(slug, token_id, direction, shares, avg, window_end,
                       tag=tag, entry_fee=fee)
        self.positions.append(pos)
        self._journal(f"BUY[{tag}]" if tag else "BUY", slug, direction,
                      shares, avg, 0.0, fee)
        self._save()
        return pos

    def topup(self, pos: Position, token_id: str, usd: float,
              asks: list[tuple[float, float]], fee_rate: float = 0.0) -> Position | None:
        """Add to an EXISTING open position (same window/token) toward the target
        stake — used when the first fill was depth-limited and the book later offers
        more inside the band (favorite._taker_enter top-up). Merges at the blended avg
        price and accumulates the entry fee so the window stays ONE position: one
        settlement, an honest trade count, and the dashboard's BUY→SETTLE-by-slug
        stake sums correctly. Returns the (grown) position or None."""
        if pos not in self.positions:
            return None
        usd = min(usd, self.cash)
        if usd <= 0 or not asks:
            return None
        shares, avg = walk_book(asks, usd=usd, impact_k=self.impact_k)
        if shares <= 0:
            return None
        fee = fee_for(shares, avg, fee_rate)
        if shares * avg + fee > self.cash:
            shares *= self.cash / (shares * avg + fee)
            fee = fee_for(shares, avg, fee_rate)
        self.cash -= shares * avg + fee
        self.fees_paid += fee
        tot = pos.shares + shares
        pos.avg_price = (pos.shares * pos.avg_price + shares * avg) / tot
        pos.shares = tot
        pos.entry_fee += fee
        self._journal(f"BUY+[{pos.tag}]" if pos.tag else "BUY+", pos.slug,
                      pos.direction, shares, avg, 0.0, fee)
        self._save()
        return pos

    def sell(self, pos: Position, bids: list[tuple[float, float]],
             fee_rate: float = 0.0) -> float | None:
        if pos not in self.positions or not bids:
            return None
        shares, avg = walk_book(bids, shares=pos.shares)
        if shares < pos.shares * 0.99:           # book too thin to exit: stay
            return None
        fee = fee_for(shares, avg, fee_rate)
        proceeds = shares * avg - fee
        self.fees_paid += fee
        pnl = proceeds - pos.shares * pos.avg_price - pos.entry_fee
        self.cash += proceeds
        self._close(pnl, "SELL", pos, avg, fee)
        return pnl

    def settle(self, pos: Position, won: bool) -> float:
        payout = pos.shares * (1.0 if won else 0.0)
        pnl = payout - pos.shares * pos.avg_price - pos.entry_fee
        self.cash += payout
        self._close(pnl, "SETTLE_WIN" if won else "SETTLE_LOSS", pos,
                    1.0 if won else 0.0, 0.0)
        return pnl

    def place_limit(self, order: LimitOrder) -> LimitOrder | None:
        cost = order.shares * order.price
        reserved = sum(o.shares * o.price for o in self.orders if o.side == "buy")
        if order.side == "buy" and cost + reserved > self.cash:
            return None
        self.orders.append(order)
        self._journal(f"REST_{order.side.upper()}[{order.tag}]", order.slug,
                      order.direction, order.shares, order.price, 0.0)
        self._save()
        return order

    def cancel(self, order: LimitOrder) -> None:
        """Remove a resting order (a buy reserves no cash until it fills, so there
        is nothing to refund). Used by the maker entry's taker fallback."""
        if order in self.orders:
            self.orders.remove(order)
            self._journal(f"CANCEL[{order.tag}]", order.slug, order.direction,
                          order.shares, order.price, 0.0)
            self._save()

    def match_orders(self, books: dict[str, dict], now: float,
                     maker_fee_rate: float = 0.0) -> list[tuple[LimitOrder, float | None]]:
        """Fill resting orders against fresh books; drop expired ones.

        Adverse-selection model: the market must trade *through* our price
        (best opposite side strictly beyond it by `trade_through`), and only
        `passive_fill_ratio` of our size fills — we are never first in queue
        and the flow that hits passive orders is informed flow.
        """
        events = []
        for o in list(self.orders):
            if now >= o.expire_ts:
                self.orders.remove(o)
                self._journal(f"EXPIRE[{o.tag}]", o.slug, o.direction,
                              o.shares, o.price, 0.0)
                continue
            book = books.get(o.token_id)
            if not book:
                continue
            if (o.side == "buy" and book["asks"]
                    and book["asks"][0][0] <= o.price - self.trade_through):
                self.orders.remove(o)
                shares = o.shares * self.passive_fill_ratio
                fee = fee_for(shares, o.price, maker_fee_rate)
                self.cash -= shares * o.price + fee
                self.fees_paid += fee
                pos = Position(o.slug, o.token_id, o.direction, shares,
                               o.price, o.window_end, tag=o.tag, entry_fee=fee)
                self.positions.append(pos)
                self._journal(f"FILL_BUY[{o.tag}]", o.slug, o.direction,
                              shares, o.price, 0.0, fee)
                events.append((o, None))
            elif (o.side == "sell" and book["bids"]
                    and book["bids"][0][0] >= o.price + self.trade_through):
                self.orders.remove(o)
                pos = next((p for p in self.positions if p.token_id == o.token_id), None)
                if pos:
                    shares = min(pos.shares, o.shares)
                    fee = fee_for(shares, o.price, maker_fee_rate)
                    proceeds = shares * o.price - fee
                    self.fees_paid += fee
                    pnl = proceeds - pos.shares * pos.avg_price - pos.entry_fee
                    self.cash += proceeds
                    self._close(pnl, f"FILL_SELL[{o.tag}]", pos, o.price, fee)
                    events.append((o, pnl))
        if events:
            self._save()
        return events

    def positions_for(self, slug: str) -> list[Position]:
        return [p for p in self.positions if p.slug == slug]

    # --------------------------------------------------------- internals ---

    def _close(self, pnl: float, kind: str, pos: Position, exit_price: float,
               fee: float = 0.0):
        self.realized_pnl += pnl
        self.n_trades += 1
        if pnl > 0:
            self.n_wins += 1
        self._journal(kind, pos.slug, pos.direction, pos.shares, exit_price, pnl, fee)
        self.positions.remove(pos)
        self._save()

    def _journal(self, kind: str, slug: str, direction: str,
                 shares: float, price: float, pnl: float, fee: float = 0.0):
        new = not self.journal_path.exists()
        with self.journal_path.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(JOURNAL_COLUMNS)            # shared schema (journal.py)
            w.writerow([int(time.time()), kind, slug, direction,
                        f"{shares:.4f}", f"{price:.4f}", f"{pnl:+.4f}",
                        f"{fee:.4f}", f"{self.cash:.2f}"])

    def _save(self):
        self.state_path.write_text(json.dumps({
            "cash": self.cash, "initial": self.initial,
            "realized_pnl": self.realized_pnl,
            "fees_paid": self.fees_paid,
            "n_trades": self.n_trades, "n_wins": self.n_wins,
            "positions": [asdict(p) for p in self.positions],
            "orders": [asdict(o) for o in self.orders],
        }, indent=2))

    def _load(self):
        if not self.state_path.exists():
            return
        d = json.loads(self.state_path.read_text())
        self.cash = d["cash"]
        self.initial = d.get("initial", self.initial)
        self.realized_pnl = d.get("realized_pnl", 0.0)
        self.fees_paid = d.get("fees_paid", 0.0)
        self.n_trades = d.get("n_trades", 0)
        self.n_wins = d.get("n_wins", 0)
        self.positions = [Position(**p) for p in d.get("positions", [])]
        self.orders = [LimitOrder(**o) for o in d.get("orders", [])]

    def summary(self) -> str:
        wr = f"{100 * self.n_wins / self.n_trades:.0f}%" if self.n_trades else "—"
        return (f"cash ${self.cash:.2f} | realized P&L {self.realized_pnl:+.2f} "
                f"| fees {self.fees_paid:.2f} | trades {self.n_trades} (win {wr}) "
                f"| open {len(self.positions)} pos, {len(self.orders)} orders")
