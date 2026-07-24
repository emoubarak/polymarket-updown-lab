"""Copy-mirror — paper-trade by FOLLOWING a skilled wallet's fills on Polymarket
crypto THRESHOLD markets ("Will the price of BTC be above $X on <date>?").

Rationale (2026-06-25 wallet hunt, see memory btc5m-profitable-wallets-hunt): the
ONLY crypto-Polymarket arena where a *skill* edge survives is the daily BTC/ETH
threshold ("-above-") markets, farmed by a cohort of forecasters — coinman2
(+$1.15M all-time) and 0x06dc (+$516k). Their edge is a superior multi-day price
view we CANNOT reverse-engineer into a mechanical rule. But the markets are SLOW
(median entry 48h before resolution, 87% >12h out), so unlike the 5m/15m up/down
windows we CAN mirror their entries at leisure and OUTSOURCE the model — which is
exactly when copy-trading beats cloning. king (0xe9c6) runs alongside as a NEGATIVE
control: same surface strategy, anti-calibrated (−4%/20d) — a positive copy-PnL on
coinman2 only counts if king stays flat/negative in parallel.

This is the honest forward-test of "would copy-trading this guy make money": watch
the wallet's /activity, replicate each NEW threshold BUY in paper (proportional to
their size, scaled + capped to our bankroll), hold to settlement, settle on the real
oracle. NOT real money — pure simulation.

Forward-test discipline: on the first poll we set a timestamp watermark to NOW and
mirror only fills that happen AFTER we start watching. We NEVER replay the backlog —
that would be a retroactive, circular "+24%" (mirror coinman2 == his own PnL by
construction). Only forward fills answer "does the skill persist?".

Slow cadence and a wallet-driven (not market-driven) signal, so this is a minimal
engine — NOT the windowed Runner / Ctx. It writes the SAME dashboard state format
(state.json / journal.csv / equity.csv) so each follow shows up in the UI as a
runner. Reuses events.resolve for Gamma settlement (no duplicated resolve path).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from pmlab import feeds, journal
from pmlab.events import resolve as gamma_resolve


# The wallets we follow. coinman2 & 0x06dc are the threshold-market forecasters the
# hunt surfaced; kingofcoinflips is the NEGATIVE control. FULL 42-char addresses —
# a truncated address makes the data-api return the global feed (garbage).
COPY_TARGETS = {
    "coinman2": "0x55be7aa03ecfbe37aa5460db791205f7ac9ddca3",
    "06dc":     "0x06dc51826bc524d9a83770e7de9dd7e005b04524",
    "king":     "0xe9c6312464b52aa3eff13d822b003282075995c9",   # negative control
}

# A threshold market is the daily "<asset> above $X on <date>" BINARY (Yes/No). We
# skip up/down (a different, efficient game) and the multi-outcome "<asset> price on
# <date>" range buckets (negRisk, >2 outcomes — the binary settle below can't score
# them; 0x06dc trades a few, accepted as a small blind spot).
THRESHOLD_TAG = "-above-"


def is_threshold(slug: str) -> bool:
    s = (slug or "").lower()
    return THRESHOLD_TAG in s and "up-or-down" not in s


@dataclass
class CopyMirror:
    """Watch a wallet's /activity, replicate each new threshold BUY in paper
    (proportional to their notional, scaled + capped), hold to settlement, settle on
    the real oracle. Dashboard-compatible state."""
    state_dir: str
    target: str                    # full 42-char proxy wallet address
    name: str = "copy"
    target_name: str = ""
    bankroll: float = 2000.0
    scale: float = 0.1             # our stake = their_notional * scale ...
    stake_cap: float = 50.0        # ... clamped to [stake_min, stake_cap] per fill
    stake_min: float = 1.0
    haircut: float = 0.005         # taker slippage we eat crossing to mirror their fill
    lookback: int = 200            # activity rows pulled per poll
    log: object = print

    def __post_init__(self):
        self.sd = Path(self.state_dir)
        self.sd.mkdir(exist_ok=True)
        self.st = self._load()

    # ---- persistence (dashboard format) ----
    def _load(self) -> dict:
        f = self.sd / "state.json"
        if f.exists():
            return json.loads(f.read_text())
        return {"cash": self.bankroll, "initial": self.bankroll, "realized_pnl": 0.0,
                "settled_cost": 0.0, "fees_paid": 0.0, "n_trades": 0, "n_wins": 0,
                "orders": [], "positions": {}, "cursor_ts": 0, "seen_tx": [],
                "n_mirrored": 0, "n_skipped_cash": 0, "started": 0,
                "target": self.target, "target_name": self.target_name}

    def _persist(self):
        (self.sd / "state.json").write_text(json.dumps(self.st))

    def _journal(self, row: dict):
        journal.append_row(self.sd / "journal.csv", row)   # shared schema/writer

    def _equity_point(self, mark: float):
        with (self.sd / "equity.csv").open("a") as fh:
            fh.write(f"{int(time.time())},{self.st['cash'] + mark:.2f}\n")

    # ---- engine ----
    def _settle(self):
        for key, pos in list(self.st["positions"].items()):
            r = gamma_resolve(pos.get("condition_id", ""), pos.get("slug", ""))
            if r is None:
                continue
            _closed, win_i = r
            won = (win_i == pos["outcome_index"])
            pnl = (pos["shares"] * 1.0 - pos["cost"]) if won else -pos["cost"]
            if won:
                self.st["cash"] += pos["shares"] * 1.0      # redeem at $1
            self.st["realized_pnl"] += pnl
            self.st["settled_cost"] = self.st.get("settled_cost", 0.0) + pos["cost"]
            self.st["n_trades"] += 1
            self.st["n_wins"] += 1 if won else 0
            self._journal({"ts": int(time.time()),
                           "kind": "SETTLE_WIN" if won else "SETTLE_LOSS",
                           "slug": pos["slug"], "direction": pos["outcome"],
                           "shares": round(pos["shares"], 4),
                           "price": round(pos["cost"] / pos["shares"], 4) if pos["shares"] else 0,
                           "pnl": round(pnl, 4), "fee": 0.0,
                           "cash": round(self.st["cash"], 2)})
            self.log(f"⚖️  settled {pos['slug'][:42]} {str(pos['outcome'])[:4]}: "
                     f"{'WIN' if won else 'LOSS'} {pnl:+.2f}")
            del self.st["positions"][key]

    def _mirror(self):
        acts = feeds.wallet_activity(self.target, limit=self.lookback)
        if not acts:
            return
        # Forward-test discipline: the FIRST poll only sets the watermark to the newest
        # fill and returns — never replay the backlog (that would be a circular copy).
        if not self.st["cursor_ts"]:
            newest = max((int(a.get("timestamp", 0)) for a in acts), default=int(time.time()))
            self.st["cursor_ts"] = newest
            self.st["started"] = int(time.time())
            self.log(f"watching {self.target_name or self.target[:10]} from ts {newest} "
                     f"({len(acts)} backlog rows skipped)")
            return
        seen = self.st["seen_tx"]
        seen_set = set(seen)
        fresh = [a for a in acts
                 if a.get("type") == "TRADE" and a.get("side") == "BUY"
                 and is_threshold(a.get("slug", ""))
                 and int(a.get("timestamp", 0)) > self.st["cursor_ts"]
                 and a.get("transactionHash") not in seen_set]
        fresh.sort(key=lambda a: int(a.get("timestamp", 0)))    # oldest first → build order
        for a in fresh:
            self._mirror_fill(a)
            seen.append(a.get("transactionHash"))
        # advance the cursor past the whole batch (incl. skipped sells/up-down/range) so
        # next poll only sees genuinely new fills; per-tx dedup guards the boundary.
        newest = max((int(a.get("timestamp", 0)) for a in acts), default=self.st["cursor_ts"])
        self.st["cursor_ts"] = max(self.st["cursor_ts"], newest)
        self.st["seen_tx"] = seen[-2000:]

    def _mirror_fill(self, a: dict):
        notional = a.get("usdcSize")
        if notional is None:
            try:
                notional = float(a.get("size", 0)) * float(a.get("price", 0))
            except (TypeError, ValueError):
                return
        stake = min(max(float(notional) * self.scale, self.stake_min), self.stake_cap)
        if self.st["cash"] < stake:
            self.st["n_skipped_cash"] += 1
            return
        try:
            their_px = float(a["price"])
        except (KeyError, TypeError, ValueError):
            return
        # We cross the spread as a taker within ~a poll of their fill; on a 48h-out daily
        # market the price barely moves, so their fill price + a small slippage haircut is
        # an honest proxy for what we'd actually pay.
        price = min(max(round(their_px + self.haircut, 3), 0.01), 0.99)
        shares = stake / price
        cid = a.get("conditionId") or ""
        oi = int(a.get("outcomeIndex", 0))
        key = f"{cid}:{oi}"
        self.st["cash"] -= stake
        pos = self.st["positions"].get(key)
        if pos:                                  # they added to this (market, side) — average in
            pos["shares"] = round(pos["shares"] + shares, 4)
            pos["cost"] = round(pos["cost"] + stake, 4)
            pos["n_fills"] += 1
        else:
            self.st["positions"][key] = {
                "condition_id": cid, "slug": a.get("slug", ""), "title": a.get("title", ""),
                "outcome": a.get("outcome", ""), "outcome_index": oi, "asset": a.get("asset", ""),
                "shares": round(shares, 4), "cost": round(stake, 4),
                "opened": int(time.time()), "n_fills": 1}
        self.st["n_mirrored"] += 1
        self._journal({"ts": int(time.time()), "kind": "BUY", "slug": a.get("slug", ""),
                       "direction": a.get("outcome", ""), "shares": round(shares, 4),
                       "price": round(price, 4), "pnl": "", "fee": 0.0,
                       "cash": round(self.st["cash"], 2)})
        self.log(f"🪞 mirror {str(a.get('outcome',''))[:4]} @ {price:.3f} ${stake:.2f} "
                 f"(their ${float(notional):.0f}@{their_px:.3f}) — {str(a.get('title',''))[:40]}")

    def _mark(self) -> float:
        """Mark open positions at cost (no per-tick re-pricing — settlement re-prices to
        0/1 anyway, and these markets are days from resolution)."""
        return sum(p["cost"] for p in self.st["positions"].values())

    def tick(self):
        self._settle()
        self._mirror()
        self._equity_point(self._mark())
        self._persist()

    def status(self) -> str:
        n = self.st["n_trades"]
        wr = (self.st["n_wins"] / n) if n else 0.0
        return (f"{self.target_name or self.target[:8]} | open {len(self.st['positions'])} "
                f"mirrored {self.st['n_mirrored']} | settled {n} win {wr:.3f} | "
                f"realized {self.st['realized_pnl']:+.2f} | cash ${self.st['cash']:.0f}")
