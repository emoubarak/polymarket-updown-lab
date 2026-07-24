"""Build & cache a retroactive dataset of closed BTC up/down 5m windows.

For each window we store the ground-truth resolution (Gamma outcomePrices),
the market price track (CLOB prices-history), the real executed-trade tape
(data-api /trades), and the Binance open/close + pre-window EWMA vol. Cached
per-window as JSON so analysis iterates without refetching.

Usage:  python3 research/dataset.py --count 300 [--interval 5m]
"""
from __future__ import annotations
import argparse
import json
import math
import os
import time
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pmlab.coins import SYMBOL   # noqa: E402 — coin→Binance symbol, single registry

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"
BINANCE = "https://api.binance.com"
INTERVALS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
CACHE = os.path.join(os.path.dirname(__file__), "data")


def get(url, **params):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "pmlab-bt/0.1"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))


# ------------------------------------------------------------- Binance ---
def fetch_klines(start_ts: int, end_ts: int, symbol: str = "BTCUSDT") -> dict[int, dict]:
    """1m candles keyed by open-second, covering [start_ts, end_ts]."""
    out: dict[int, dict] = {}
    cur = start_ts
    while cur < end_ts:
        raw = get(f"{BINANCE}/api/v3/klines", symbol=symbol, interval="1m",
                  startTime=cur * 1000, limit=1000)
        if not raw:
            break
        for k in raw:
            t = k[0] // 1000
            out[t] = {"open": float(k[1]), "close": float(k[4])}
        cur = raw[-1][0] // 1000 + 60
        if len(raw) < 1000:
            break
    return out


def ewma_vol(klines: dict[int, dict], end_ts: int, lookback_min: int = 90,
             lam: float = 0.94) -> float:
    closes = [klines[t]["close"] for t in range(end_ts - lookback_min * 60, end_ts, 60)
              if t in klines]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0]
    if len(rets) < 10:
        return 0.0
    var = (sum(r * r for r in rets) / len(rets))
    for r in rets:
        var = lam * var + (1 - lam) * r * r
    return math.sqrt(var)


# ----------------------------------------------------------- Polymarket ---
def fetch_trades(condition_id: str, cap: int = 4000) -> list[dict]:
    """Full executed-trade tape for a market (paginated)."""
    out, offset = [], 0
    while len(out) < cap:
        try:
            batch = get(f"{DATA}/trades", market=condition_id, limit=500, offset=offset)
        except Exception:
            break               # a 400 past the last page just ends the tape
        if not isinstance(batch, list) or not batch:
            break
        out.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    return out


def build_window(ws: int, interval: str, klines: dict[int, dict],
                 underlying: str = "btc", with_tape: bool = True) -> dict | None:
    sec = INTERVALS[interval]
    slug = f"{underlying}-updown-{interval}-{ws}"
    events = get(f"{GAMMA}/events", slug=slug)
    if not events:
        return None
    m = events[0]["markets"][0]
    if not m.get("closed"):
        return None
    tokens = json.loads(m["clobTokenIds"])
    outcomes = json.loads(m["outcomes"]) if isinstance(m["outcomes"], str) else m["outcomes"]
    prices = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
    by_tok = dict(zip(outcomes, tokens))
    by_res = dict(zip(outcomes, prices))
    up_tok = by_tok["Up"]
    up_won = float(by_res["Up"]) > 0.5
    ph = get(f"{CLOB}/prices-history", market=up_tok,
             startTs=ws - 180, endTs=ws + sec + 60, fidelity=1)
    track = [(int(p["t"]), float(p["p"])) for p in ph.get("history", [])]
    if with_tape:
        trades = fetch_trades(m["conditionId"])
        tape = [{"t": int(t["timestamp"]), "side": t["side"], "price": float(t["price"]),
                 "size": float(t["size"]), "outcome": t["outcome"]} for t in trades]
    else:
        tape = []
    o = klines.get(ws, {}).get("open")
    c = klines.get(ws + sec - 60, {}).get("close")
    return {
        "slug": slug, "window_start": ws, "window_end": ws + sec,
        "up_won": up_won, "up_token": up_tok, "condition_id": m["conditionId"],
        "binance_open": o, "binance_close": c,
        "binance_up": (c > o) if (o and c) else None,
        "vol_per_min": ewma_vol(klines, ws),
        "price_track": track, "tape": tape, "has_tape": with_tape,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=200)
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--underlying", default="btc", choices=list(SYMBOL),
                    help="btc (default, cache '{interval}_*') or eth/sol/xrp "
                         "(cache '{underlying}_{interval}_*')")
    ap.add_argument("--skip", type=int, default=0, help="skip the N most-recent windows")
    ap.add_argument("--no-tape", action="store_true", help="light: skip trade tape")
    ap.add_argument("--force", action="store_true", help="rebuild even if cached")
    args = ap.parse_args()
    sec = INTERVALS[args.interval]
    # btc keeps the bare '{interval}_{ws}' filename (back-compat with the existing
    # cache + explore2.load_all); other underlyings get a '{u}_' prefix.
    prefix = "" if args.underlying == "btc" else f"{args.underlying}_"
    os.makedirs(CACHE, exist_ok=True)
    now = int(time.time())
    base = (now // sec) * sec
    windows = [base - sec * (i + 1 + args.skip) for i in range(args.count)]
    lo, hi = min(windows) - 95 * 60, max(windows) + sec + 60
    print(f"Binance {SYMBOL[args.underlying]} klines {lo}..{hi} ...")
    klines = fetch_klines(lo, hi, symbol=SYMBOL[args.underlying])
    print(f"  {len(klines)} candles")
    built, skipped = 0, 0
    for i, ws in enumerate(sorted(windows)):
        path = os.path.join(CACHE, f"{prefix}{args.interval}_{ws}.json")
        if os.path.exists(path) and not args.force:
            built += 1
            continue
        try:
            rec = build_window(ws, args.interval, klines,
                               underlying=args.underlying, with_tape=not args.no_tape)
        except Exception as e:
            print(f"  {ws} ERROR {e}")
            skipped += 1
            continue
        if rec is None:
            skipped += 1
            continue
        with open(path, "w") as f:
            json.dump(rec, f)
        built += 1
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(windows)}  built={built} skipped={skipped}")
        time.sleep(0.15)
    print(f"DONE built={built} skipped={skipped} -> {CACHE}")


if __name__ == "__main__":
    main()
