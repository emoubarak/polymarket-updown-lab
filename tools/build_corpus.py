#!/usr/bin/env python3
"""Build a shared per-window CORPUS for the untested-signal backtests (one fetch, many analyses).

Usage: python3 tools/build_corpus.py <coin> <frame> <n_windows> [out_dir]

Each record holds everything the 6 signal studies need, plus an ENTRY snapshot (frac remaining
0.45 = 55% elapsed = zlead's early entry edge) so every signal can be tested with ONLY info
available AT DECISION TIME (the lesson from the crowd test). Fields:
  ws, coin, frame, up_won (settlement), fav_outcome
  coin_open/coin_entry/coin_close, btc_open/btc_entry/btc_close  (Binance 1m; *_entry = at 55% elapsed)
  vol (EWMA 1m vol over the 90min before ws), prev_high/prev_low (prior window range), hour, dow
  fav_trades  = in-band [0.85,0.95] favourite BUYs: [[t_rel_s, price, wallet], ...]
  ls_trades   = longshot-side BUYs (the other outcome, any price): [[t_rel_s, price, size], ...]
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pmlab import feeds

LO, HI = 0.85, 0.95
ENTRY_ELAPSED = 0.55                # frac of window elapsed at the entry checkpoint
PAGE, MAX_PAGES = 500, 8

coin = sys.argv[1]
frame = sys.argv[2]
n = int(sys.argv[3])
out_dir = sys.argv[4] if len(sys.argv) > 4 else "data/corpus"
sec = {"5m": 300, "15m": 900}[frame]
sym = feeds.SYMBOL[coin]


def raw_klines(symbol, start_s, end_s):
    """{minute_ts: (open,high,low,close)} over [start,end], paginated."""
    out, s = {}, start_s
    while s < end_s:
        try:
            raw = feeds._get(f"{feeds.BINANCE}/api/v3/klines", symbol=symbol, interval="1m",
                             startTime=s * 1000, endTime=end_s * 1000, limit=1000)
        except Exception:
            break
        if not raw:
            break
        for k in raw:
            out[k[0] // 1000] = (float(k[1]), float(k[2]), float(k[3]), float(k[4]))
        last = raw[-1][0] // 1000
        if last <= s or len(raw) < 1000:
            break
        s = last + 60
    return out


def all_trades(cond):
    out = []
    for pg in range(MAX_PAGES):
        try:
            ch = feeds._get(f"{feeds.DATA_API}/trades", market=cond, limit=PAGE, offset=pg * PAGE)
        except Exception:
            break
        if not isinstance(ch, list) or not ch:
            break
        out.extend(ch)
        if len(ch) < PAGE:
            break
    return out


now = time.time()
latest = int((now - sec - 90) // sec) * sec
wins = [latest - i * sec for i in range(n)]
span_lo, span_hi = min(wins) - 90 * 60, max(wins) + sec + 60
ck = raw_klines(sym, span_lo, span_hi)
bk = ck if coin == "btc" else raw_klines("BTCUSDT", span_lo, span_hi)


def at(kl, t):  # close of the 1m candle at minute t (floor to 60), or None
    t = (t // 60) * 60
    return kl.get(t, (None, None, None, None))


records = []
for ws in wins:
    try:
        ev = feeds._get(f"{feeds.GAMMA}/events", slug=f"{coin}-updown-{frame}-{ws}")
    except Exception:
        continue
    if not ev:
        continue
    m = ev[0]["markets"][0]
    cond = m.get("conditionId")
    outs = m.get("outcomes"); prs = m.get("outcomePrices")
    outs = json.loads(outs) if isinstance(outs, str) else outs
    prs = json.loads(prs) if isinstance(prs, str) else prs
    try:
        up_px = float(dict(zip(outs, prs))["Up"])
    except Exception:
        continue
    if not (m.get("closed") or up_px >= 0.99 or up_px <= 0.01):
        continue
    up_won = up_px > 0.5
    entry_t = ws + int(ENTRY_ELAPSED * sec)
    raw_tr = all_trades(cond)
    up_ib = dn_ib = 0.0
    for t in raw_tr:
        try:
            if t.get("side") != "BUY":
                continue
            p, s = float(t["price"]), float(t["size"])
            if LO <= p <= HI:
                if t.get("outcome") == "Up":
                    up_ib += p * s
                else:
                    dn_ib += p * s
        except (TypeError, ValueError, KeyError):
            continue
    fav_out = "Up" if up_ib >= dn_ib else "Down"
    fav_won = up_won if fav_out == "Up" else (not up_won)
    fav_trades, ls_trades = [], []
    for t in raw_tr:
        try:
            if t.get("side") != "BUY":
                continue
            p, s = float(t["price"]), float(t["size"])
            tr_rel = int(t["timestamp"]) - ws
            if t.get("outcome") == fav_out:
                if LO <= p <= HI:
                    fav_trades.append([tr_rel, round(p, 4), t.get("proxyWallet")])
            else:
                ls_trades.append([tr_rel, round(p, 4), round(s, 2)])
        except (TypeError, ValueError, KeyError):
            continue
    # vol over the 90 min before ws (EWMA of 1m log returns)
    pre = [{"close": ck[t][3]} for t in range(ws - 90 * 60, ws, 60) if t in ck]
    vol = feeds.realized_vol_per_min(pre) if len(pre) >= 11 else None
    # prior window range
    prevs = [ck[t] for t in range(ws - sec, ws, 60) if t in ck]
    prev_high = max((c[1] for c in prevs), default=None)
    prev_low = min((c[2] for c in prevs), default=None)
    co, ce, cc = at(ck, ws)[0], at(ck, entry_t)[3], at(ck, ws + sec - 60)[3]
    bo, be, bc = at(bk, ws)[0], at(bk, entry_t)[3], at(bk, ws + sec - 60)[3]
    records.append({
        "ws": ws, "coin": coin, "frame": frame, "up_won": up_won, "fav_outcome": fav_out,
        "fav_won": fav_won, "coin_open": co, "coin_entry": ce, "coin_close": cc,
        "btc_open": bo, "btc_entry": be, "btc_close": bc, "vol": vol,
        "prev_high": prev_high, "prev_low": prev_low,
        "hour": (ws // 3600) % 24, "dow": (ws // 86400 + 4) % 7,
        "entry_elapsed": ENTRY_ELAPSED, "win_sec": sec,
        "fav_trades": fav_trades, "ls_trades": ls_trades,
    })

os.makedirs(out_dir, exist_ok=True)
path = f"{out_dir}/corpus_{coin}_{frame}.json"
with open(path, "w") as f:
    json.dump(records, f)
losses = sum(1 for r in records if not r["fav_won"])
print(f"{path}: {len(records)} windows, {losses} fav-losses")
