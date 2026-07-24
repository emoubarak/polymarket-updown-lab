"""Event-market favourite harvester — paper trading of Polymarket EVENT markets.

The crypto up/down edge (favourite-longshot) is nearly arbitraged away (favorite
≈ +0.001 EV/$). Research + our own backtests say the SAME bias is FATTER where
the participants are emotional humans and correction is slow: sports outsiders,
niche politics/entertainment, long-dated events (favourites 0.85-0.95 are
under-priced, longshots over-priced; documented 2-5% edge per contract).

This module harvests that on EVENT markets: scan Gamma for binary markets whose
favourite sits in a target band with tradeable-but-not-saturated liquidity, "buy"
the favourite (paper), hold to resolution, settle on the real oracle outcome.

Cadence is slow (days/weeks), so it is a separate, minimal engine — NOT the
5m/15m windowed Runner. It writes the SAME state format the dashboard already
reads (state.json / journal.csv / equity.csv), so event strategies show up in the
existing UI as extra runners. Stdlib only (urllib) — paper stays dependency-free.
"""
from __future__ import annotations

import datetime
import json
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import journal   # shared journal.csv schema + writer (single source, stdlib-only)

GAMMA = "https://gamma-api.polymarket.com"
_UA = {"User-Agent": "pmlab/0.1 (paper-event-harvester)"}


def _get(path: str, **params) -> object:
    q = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    url = f"{GAMMA}/{path}" + (f"?{q}" if q else "")
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def _jl(v):
    return json.loads(v) if isinstance(v, str) else (v or [])


def _days_to(m) -> float | None:
    e = m.get("endDateIso") or m.get("endDate")
    if not e:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(e).replace("Z", "+00:00"))
        return (dt.timestamp() - time.time()) / 86400.0
    except (ValueError, TypeError):
        return None


@dataclass
class ScanCfg:
    fav_lo: float = 0.85
    fav_hi: float = 0.95
    liq_min: float = 50_000.0      # tradeable
    liq_max: float = 2_000_000.0   # above this = whale-driven / efficient
    days_min: float = 0.0
    days_max: float = 400.0
    match: str = ""                # substring required in event ticker/title/question
    exclude: str = ""              # substring that disqualifies
    pool: int = 500                # how many active markets to pull and filter


def _market_view(m) -> dict | None:
    """Normalise a Gamma market to what the harvester needs, or None if unusable."""
    if not m.get("enableOrderBook") or m.get("closed"):
        return None
    outs = _jl(m.get("outcomes"))
    prices = _jl(m.get("outcomePrices"))
    toks = _jl(m.get("clobTokenIds"))
    if len(outs) != 2 or len(prices) != 2 or len(toks) != 2:
        return None
    try:
        p = [float(x) for x in prices]
    except (TypeError, ValueError):
        return None
    fav_i = 0 if p[0] >= p[1] else 1
    ev = (m.get("events") or [{}])[0]
    return {
        "condition_id": m.get("conditionId"),
        "slug": m.get("slug"),
        "question": m.get("question") or "",
        "fav_index": fav_i,
        "fav_outcome": outs[fav_i],
        "fav_price": p[fav_i],
        "fav_token": toks[fav_i],
        "liquidity": float(m.get("liquidityNum") or m.get("liquidity") or 0),
        "spread": float(m.get("spread") or 0.0),
        "days": _days_to(m),
        "event": ev.get("title") or "",
        "ticker": ev.get("ticker") or "",
    }


def scan(cfg: ScanCfg) -> list[dict]:
    """Active binary markets whose favourite fits the band/liquidity/horizon."""
    raw = []
    for off in range(0, cfg.pool, 100):
        try:
            raw += _get("markets", closed="false", active="true", limit=100,
                        offset=off, order="volume24hr", ascending="false")
        except Exception:
            break
    out = []
    ml, xl = cfg.match.lower(), cfg.exclude.lower()
    for m in raw:
        v = _market_view(m)
        if v is None:
            continue
        if not (cfg.fav_lo <= v["fav_price"] <= cfg.fav_hi):
            continue
        if not (cfg.liq_min <= v["liquidity"] <= cfg.liq_max):
            continue
        d = v["days"]
        if d is None or not (cfg.days_min <= d <= cfg.days_max):
            continue
        hay = f"{v['ticker']} {v['event']} {v['question']}".lower()
        if ml and ml not in hay:
            continue
        if xl and xl in hay:
            continue
        out.append(v)
    return out


def resolve(condition_id: str, slug: str) -> tuple[bool, int] | None:
    """(closed, winning_index) for a market, or None if not yet resolved.
    Short-market `closed` flag can lag, so a decisive outcomePrice also counts."""
    try:                            # Gamma defaults to closed=false → ask explicitly
        ms = (_get("markets", slug=slug, closed="true") if slug
              else _get("markets", condition_ids=condition_id, closed="true"))
    except Exception:
        return None
    if not ms:
        return None
    m = ms[0]
    prices = _jl(m.get("outcomePrices"))
    try:
        p = [float(x) for x in prices]
    except (TypeError, ValueError):
        return None
    if len(p) != 2:
        return None
    if m.get("closed") or max(p) >= 0.99:
        return True, (0 if p[0] >= p[1] else 1)
    return None


@dataclass
class EventHarvester:
    """Scan → buy favourites → hold → settle on resolution. Dashboard-compatible
    state (state.json / journal.csv / equity.csv)."""
    state_dir: str
    scan_cfg: ScanCfg
    bankroll: float = 1000.0
    stake: float = 25.0
    max_positions: int = 40
    haircut: float = 0.01          # paid over the favourite price on entry
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
                "fees_paid": 0.0, "n_trades": 0, "n_wins": 0, "orders": [], "positions": {}}

    def _persist(self):
        (self.sd / "state.json").write_text(json.dumps(self.st))

    def _journal(self, row: dict):
        journal.append_row(self.sd / "journal.csv", row)   # shared schema/writer

    def _equity_point(self, mark: float):
        with (self.sd / "equity.csv").open("a") as fh:
            fh.write(f"{int(time.time())},{self.st['cash'] + mark:.2f}\n")

    # ---- engine ----
    def _settle(self):
        for cid, pos in list(self.st["positions"].items()):
            r = resolve(cid, pos.get("slug", ""))
            if r is None:
                continue
            _closed, win_i = r
            won = (win_i == pos["fav_index"])
            usd = pos["shares"] * pos["price"]
            pnl = (pos["shares"] * 1.0 - usd) if won else -usd
            if won:
                self.st["cash"] += pos["shares"] * 1.0      # redeem at $1
            self.st["realized_pnl"] += pnl
            self.st["n_trades"] += 1
            self.st["n_wins"] += 1 if won else 0
            self._journal({"ts": int(time.time()),
                           "kind": "SETTLE_WIN" if won else "SETTLE_LOSS",
                           "slug": pos["slug"], "direction": pos["fav_outcome"],
                           "shares": round(pos["shares"], 4), "price": round(pos["price"], 4),
                           "pnl": round(pnl, 4), "fee": 0.0, "cash": round(self.st["cash"], 2)})
            self.log(f"⚖️  settled {pos['slug'][:40]} {pos['fav_outcome'][:14]}: "
                     f"{'WIN' if won else 'LOSS'} {pnl:+.2f}")
            del self.st["positions"][cid]

    def _open(self):
        if len(self.st["positions"]) >= self.max_positions:
            return
        cands = scan(self.scan_cfg)
        for v in cands:
            if len(self.st["positions"]) >= self.max_positions:
                break
            cid = v["condition_id"]
            if not cid or cid in self.st["positions"]:
                continue
            if self.st["cash"] < self.stake:
                break
            price = min(round(v["fav_price"] + self.haircut, 3), 0.99)
            shares = round(self.stake / price, 4)
            self.st["cash"] -= self.stake
            self.st["positions"][cid] = {
                "slug": v["slug"], "fav_index": v["fav_index"],
                "fav_outcome": v["fav_outcome"], "price": price, "shares": shares,
                "opened": int(time.time())}
            self._journal({"ts": int(time.time()), "kind": "BUY", "slug": v["slug"],
                           "direction": v["fav_outcome"], "shares": shares,
                           "price": price, "pnl": "", "fee": 0.0,
                           "cash": round(self.st["cash"], 2)})
            self.log(f"🟢 buy {v['fav_outcome'][:18]} @ {price:.3f} "
                     f"(liq ${v['liquidity']:,.0f}, {v['days'] and round(v['days'],1)}j) "
                     f"— {v['question'][:40]}")

    def _mark(self) -> float:
        """Mark open positions at their entry price (cheap, no per-market re-fetch
        every tick — settlement re-prices to 0/1 anyway)."""
        return sum(p["shares"] * p["price"] for p in self.st["positions"].values())

    def tick(self):
        self._settle()
        self._open()
        self._equity_point(self._mark())
        self._persist()

    def status(self) -> str:
        n = self.st["n_trades"]
        wr = (self.st["n_wins"] / n) if n else 0.0
        return (f"open {len(self.st['positions'])} | settled {n} win {wr:.3f} | "
                f"realized {self.st['realized_pnl']:+.2f} | cash ${self.st['cash']:.0f}")
