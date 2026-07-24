"""Generic strategy runner — the market plumbing shared by every paper brain.

Per tick it builds a feature context (spot, vol, model probability, both books,
short history), lets the strategy act through the MultiBroker, matches resting
limit orders against the fresh books, and settles every finished window. It also
resolves *every observed window* (traded or not) and feeds the outcome back to
the strategy via on_resolution.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import feeds
from .feeds import INTERVALS, UpDownMarket
from .journal import window_ts
from .paper import MultiBroker
from .model import model_p_up
from .coins import bet_max_for


@dataclass
class Tick:
    ts: float
    spot: float
    mid_up: float


@dataclass
class Ctx:
    market: UpDownMarket
    ts: float
    spot: float
    window_open: float
    sigma: float
    tau_min: float
    window_min: float
    mid_up: float
    p_up: float                    # diffusion model, shrunk & floored
    book_up: dict
    book_down: dict
    ret_1m: float
    ret_5m: float
    history: deque                 # of Tick, oldest first
    sigma_history: deque
    kill_switch: bool
    btc_lead: float | None = None       # BTC's signed move-to-now (alts, for the btc_align veto); None on btc
    ls_flow_ratio: float | None = None  # pre-entry longshot $vol / depth-cap (alts, for the ls_flow veto); None on btc

    @property
    def best_ask_up(self) -> float | None:
        return self.book_up["asks"][0][0] if self.book_up["asks"] else None

    @property
    def best_ask_down(self) -> float | None:
        return self.book_down["asks"][0][0] if self.book_down["asks"] else None

    @property
    def best_bid_up(self) -> float | None:
        return self.book_up["bids"][0][0] if self.book_up["bids"] else None

    @property
    def best_bid_down(self) -> float | None:
        return self.book_down["bids"][0][0] if self.book_down["bids"] else None

    @property
    def taker_rate(self) -> float:
        return self.market.taker_fee_rate

    @property
    def maker_rate(self) -> float:
        return self.market.maker_fee_rate          # 0: makers never pay

    def taker_fee_ps(self, price: float) -> float:
        """Taker fee per share at a given price."""
        from .paper import fee_for
        return fee_for(1, price, self.taker_rate)

    def exec_book(self, direction: str) -> dict:
        """Book after order-travel latency — fills must use this, not the
        snapshot the decision was made on."""
        return feeds.exec_book(self.token_for(direction))

    def mid_for(self, direction: str) -> float:
        return self.mid_up if direction == "Up" else 1 - self.mid_up

    def p_for(self, direction: str) -> float:
        return self.p_up if direction == "Up" else 1 - self.p_up

    def book_for(self, direction: str) -> dict:
        return self.book_up if direction == "Up" else self.book_down

    def token_for(self, direction: str) -> str:
        return self.market.token_up if direction == "Up" else self.market.token_down


@dataclass
class RunnerConfig:
    interval: str = "5m"
    bankroll: float = 100.0          # rebased 2026-06-26 ($1000 -> $100, weighted sizing)
    poll_s: float = 10.0
    state_dir: Path = Path("paper_state")
    kill_switch_dd: float = 0.15
    stale_s: float = 180.0
    underlying: str = "btc"        # btc | eth | sol | xrp (Binance proxy symbol)


class Runner:
    def __init__(self, strategy, cfg: RunnerConfig):
        self.strategy = strategy
        self.cfg = cfg
        self._symbol = feeds.SYMBOL.get(cfg.underlying, "BTCUSDT")
        cfg.state_dir.mkdir(exist_ok=True)
        self.broker = MultiBroker(cfg.bankroll,
                                  cfg.state_dir / "state.json",
                                  cfg.state_dir / "journal.csv")
        self._market: UpDownMarket | None = None
        self._window_open_px: float | None = None
        self._history: deque[Tick] = deque(maxlen=60)
        self._sigma_hist: deque[float] = deque(maxlen=100)
        self._pending_windows: list[tuple[str, int, int]] = []  # slug, start, end
        self._oracle_res: dict[str, bool] = {}  # slug -> decisive oracle outcome (cache)

    # ------------------------------------------------------------- cycle ---

    def tick(self) -> None:
        self._settle_due()
        self._resolve_observed()
        market = self._current_market()
        if market is None:
            self._log("no active market for this window yet")
            return
        ctx = self._build_ctx(market)
        if ctx is None:
            return
        # resting orders meet the fresh books before the brain acts
        books = {market.token_up: ctx.book_up, market.token_down: ctx.book_down}
        for order, pnl in self.broker.match_orders(books, ctx.ts,
                                                   maker_fee_rate=ctx.maker_rate):
            kind = "filled" if pnl is None else f"closed pnl {pnl:+.2f}"
            self._log(f"⚡ resting {order.side} [{order.tag}] {order.direction} "
                      f"@ {order.price:.3f} {kind} | {self.broker.summary()}")
        # convention: strategies check ctx.kill_switch before opening new risk;
        # on_tick still runs so exits and position management keep working
        pre_ids = {id(p) for p in self.broker.positions}
        self.strategy.on_tick(ctx, self.broker, self._log)
        self._instrument(ctx, pre_ids)

    # ---------------------------------------------------- instrumentation ---

    def _instrument(self, ctx: Ctx, pre_ids: set) -> None:
        """Best-effort live telemetry, additive and never fatal to the tick:
          tick.json    a snapshot of what the brain is looking at RIGHT NOW
                       (current window, favorite, time left, BTC lead in bps,
                       vol) — powers the dashboard's real-time panel + heartbeat.
          decisions.csv  one row per NEW fill with the entry CONTEXT the journal
                       omits (lead_bps, sigma, realized slippage vs the mid) — so
                       losses can later be sliced by soft/strong favorite, and so
                       live slippage can be checked against the backtest's 2c."""
        try:
            fav_side = "Up" if ctx.mid_up >= 0.5 else "Down"
            lead_bps = ((ctx.spot - ctx.window_open) / ctx.window_open * 1e4
                        if ctx.window_open else 0.0)
            conv_bps = lead_bps if fav_side == "Up" else -lead_bps   # toward favorite
            open_here = any(p.slug == ctx.market.slug for p in self.broker.positions)
            snap = {
                "ts": int(ctx.ts), "slug": ctx.market.slug,
                "window_end": ctx.market.window_end,
                "fav_side": fav_side, "fav_price": round(ctx.mid_for(fav_side), 4),
                "tau_min": round(ctx.tau_min, 2), "spot": round(ctx.spot, 2),
                "window_open": round(ctx.window_open, 2),
                "lead_bps": round(conv_bps, 1), "sigma": round(ctx.sigma, 6),
                "open_here": open_here, "kill": ctx.kill_switch,
            }
            (self.cfg.state_dir / "tick.json").write_text(json.dumps(snap))
            new = [p for p in self.broker.positions
                   if id(p) not in pre_ids and p.slug == ctx.market.slug]
            if new:
                dpath = self.cfg.state_dir / "decisions.csv"
                write_head = not dpath.exists()
                with dpath.open("a") as f:
                    if write_head:
                        f.write("ts,slug,direction,fill_price,decision_mid,"
                                "slip_c,lead_bps,sigma,fav_price\n")
                    for p in new:
                        mid = ctx.mid_for(p.direction)
                        slip_c = (p.avg_price - mid) * 100   # cents, + = paid up
                        f.write(f"{int(ctx.ts)},{p.slug},{p.direction},"
                                f"{p.avg_price:.4f},{mid:.4f},{slip_c:.2f},"
                                f"{conv_bps:.1f},{ctx.sigma:.6f},"
                                f"{ctx.mid_for(fav_side):.4f}\n")
        except Exception as e:                     # noqa: BLE001 — telemetry is not critical
            self._log(f"instrument error: {type(e).__name__}: {e}")

    # -------------------------------------------------------- settlement ---

    # Grace after a window closes before we give up on the oracle and settle on
    # the Binance proxy: the oracle is normally decisive by window_end+65 (see
    # feeds.resolve_market), so this only fires on a genuine multi-minute oracle
    # outage — a stalled window must never strand the loop or double-count.
    _ORACLE_GRACE_S = 600

    def _resolution(self, start: int, end: int, slug: str | None = None) -> bool | None:
        """Resolve a finished window: True=Up won, False=Down, None=not decided.

        Settle on the REAL Chainlink oracle (what real money and the backtest
        label both use) so the paper race isn't flattered by the ~3.5% Binance-
        proxy divergence (research/FINDINGS.md §1): the oracle's coarse update
        granularity produces real ties (-> Up) that fine-grained Binance candles
        read as tiny moves (-> Down). The close>=open proxy (tie -> Up, matching
        the oracle rule) is only a FALLBACK when the oracle can't be read — a
        network failure must never stall the tick loop or strand a position."""
        if slug is not None:
            cached = self._oracle_res.get(slug)
            if cached is not None:
                return cached
            try:
                up = feeds.resolve_market(slug)
            except Exception:                   # network/parse failure: fall through
                up = None
            if up is not None:
                self._oracle_res[slug] = up     # decisive — cache it
                return up
            if time.time() < end + self._ORACLE_GRACE_S:
                return None                      # not decisive yet: wait, retry next tick
            # oracle unreadable well past close -> proxy fallback so nothing strands
        px_open = feeds.btc_price_at(start, self._symbol)
        px_close = feeds.btc_price_at(end, self._symbol)
        if px_open is None or px_close is None:
            return None
        return px_close >= px_open               # tie resolves Up (oracle rule)

    def _settle_due(self) -> None:
        now = time.time()
        for pos in list(self.broker.positions):
            if now < pos.window_end + 65:
                continue
            start = window_ts(pos.slug)          # the SINGLE slug→window-start parser (journal.py)
            if start is None:
                continue
            went_up = self._resolution(start, pos.window_end, pos.slug)
            if went_up is None:
                continue
            won = (pos.direction == "Up") == went_up
            pnl = self.broker.settle(pos, won)
            self._log(f"⚖️  settled {pos.slug} [{pos.tag}] held {pos.direction}: "
                      f"{'WIN' if won else 'LOSS'} {pnl:+.2f} | {self.broker.summary()}")

    def _resolve_observed(self) -> None:
        """Feed every finished window's outcome to the strategy (learning)."""
        if not hasattr(self.strategy, "on_resolution"):
            return
        now = time.time()
        for slug, start, end in list(self._pending_windows):
            if now < end + 65:
                continue
            went_up = self._resolution(start, end, slug)
            if went_up is None:
                continue
            self._pending_windows.remove((slug, start, end))
            self.strategy.on_resolution(slug, went_up, self._log)

    # ------------------------------------------------------------ market ---

    def _current_market(self) -> UpDownMarket | None:
        start, _ = feeds.window_bounds(self.cfg.interval)
        if self._market is None or self._market.window_start != start:
            m = feeds.fetch_updown_market(self.cfg.interval, start, self.cfg.underlying)
            if m is None:
                return None
            self._market = m
            self._window_open_px = feeds.btc_price_at(start, self._symbol)
            self._pending_windows.append((m.slug, m.window_start, m.window_end))
            if hasattr(self.strategy, "on_new_window"):
                self.strategy.on_new_window(m)
            self._log(f"🪟 new window: {m.question} "
                      f"(open px {self._window_open_px or '?'})")
        return self._market

    def _build_ctx(self, market: UpDownMarket) -> Ctx | None:
        if self._window_open_px is None:
            self._window_open_px = feeds.btc_price_at(market.window_start, self._symbol)
            if self._window_open_px is None:
                return None
        klines = feeds.btc_klines_1m(90, self._symbol)
        now = time.time()
        if now - klines[-1]["t"] > self.cfg.stale_s:
            self._log("stale Binance data, skipping tick")
            return None
        spot = klines[-1]["close"]
        sigma = feeds.realized_vol_per_min(klines)
        feeds.hydrate(market)
        if market.mid_up is None or not market.book_up.get("asks"):
            self._log("book empty / no midpoint, waiting")
            return None
        self._history.append(Tick(now, spot, market.mid_up))
        self._sigma_hist.append(sigma)
        closes = [k["close"] for k in klines]
        dd = -self.broker.realized_pnl / self.broker.initial
        # ALT-only veto inputs, fetched ONLY when the brain's gate uses them (and never on btc).
        gate = getattr(self.strategy, "gate", None)
        is_alt = self._symbol != "BTCUSDT"
        btc_lead = (feeds.btc_lead_fraction(self._symbol, market.window_start)
                    if (is_alt and getattr(gate, "btc_align", False)) else None)
        ls_flow_ratio = None
        if is_alt and getattr(gate, "ls_flow_cap", 0):
            ls = feeds.longshot_flow(market.condition_id,
                                     "Up" if market.mid_up >= 0.5 else "Down")
            cap = bet_max_for(self.cfg.underlying, self.cfg.interval)
            if ls is not None and cap > 0:
                ls_flow_ratio = ls / cap
        ctx = Ctx(
            market=market, ts=now, spot=spot,
            window_open=self._window_open_px, sigma=sigma,
            tau_min=market.tau_min,
            window_min=INTERVALS[self.cfg.interval] / 60,
            mid_up=market.mid_up,
            p_up=model_p_up(spot, self._window_open_px, sigma, market.tau_min),
            book_up=market.book_up, book_down=market.book_down,
            ret_1m=closes[-1] / closes[-2] - 1,
            ret_5m=closes[-1] / closes[-6] - 1,
            history=self._history, sigma_history=self._sigma_hist,
            kill_switch=dd >= self.cfg.kill_switch_dd,
            btc_lead=btc_lead, ls_flow_ratio=ls_flow_ratio,
        )
        status = self.strategy.status(ctx) if hasattr(self.strategy, "status") else ""
        self._log(f"p_up {ctx.p_up:.3f} / mid {ctx.mid_up:.3f} | tau {ctx.tau_min:.1f}m "
                  f"| {status}{' | ' if status else ''}{self._equity_line(ctx)}")
        return ctx

    def _equity_line(self, ctx: Ctx) -> str:
        equity = self.broker.cash
        for p in self.broker.positions:
            if p.slug == ctx.market.slug:
                equity += p.shares * ctx.mid_for(p.direction)
            else:
                equity += p.shares * p.avg_price          # awaiting settlement
        # resting buy orders are unspent cash (already counted) — nothing to add
        total = equity - self.broker.initial
        with (self.cfg.state_dir / "equity.csv").open("a") as f:
            f.write(f"{int(ctx.ts)},{equity:.2f}\n")
        return f"💰 PnL {total:+.2f} (realized {self.broker.realized_pnl:+.2f})"

    # ------------------------------------------------------------- misc ----

    @staticmethod
    def _log(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def run(self, max_ticks: int | None = None) -> None:
        self._log(f"runner up — strategy '{self.strategy.name}', "
                  f"{self.cfg.interval} windows | {self.broker.summary()}")
        n = 0
        while max_ticks is None or n < max_ticks:
            try:
                self.tick()
            except Exception as e:               # noqa: BLE001 — keep the loop alive
                self._log(f"tick error: {type(e).__name__}: {e}")
            n += 1
            if max_ticks is None or n < max_ticks:
                time.sleep(self.cfg.poll_s)
