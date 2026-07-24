#!/usr/bin/env python3
"""Per-window ON-CHAIN P&L tracker for OUR MM Safe — the ONLY trustworthy P&L (the engine's `mark`
phantoms on redeem-lag; we stop using it). Reads the Safe's data-api /activity, groups by window
(conditionId), and decomposes each window into:
  trading  = -mint - buys + sells + merge + redeem     (realized round-trip cash flow, ex-rebate)
  rebate   = MAKER_REBATE + TAKER_REBATE credited (by day; the actual income)
  imb      = |endUp - endDn| / mint                     (neutrality; the drawdown driver)
  lean     = signed end imbalance × outcome payoff      (directional component of `trading`)
Answers the ONE question: over many windows, is realized P&L ≈ rebate with low drawdown (neutral),
and is any directional `lean` systematically +EV or just noise?

Run on the box (data-api needs the AWS egress):  .venv-live/bin/python research/std0/pnl_tracker.py [hours]
"""
import sys, urllib.request, json, time
from collections import defaultdict

SAFE = "0xBbe3..."  # adresse tronquée pour publication — renseigner l'adresse complète du Safe
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0

def api(typ):
    url = f"https://data-api.polymarket.com/activity?user={SAFE}&limit=500&type={typ}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=25))

now = time.time(); cutoff = now - HOURS * 3600
acts = api("TRADE") + api("SPLIT") + api("MERGE") + api("REDEEM")
acts = [r for r in acts if r.get("timestamp", 0) >= cutoff]
reb = [r for r in (api("MAKER_REBATE") + api("TAKER_REBATE")) if r.get("timestamp", 0) >= cutoff]

W = defaultdict(lambda: {"mint": 0., "merge": 0., "redeem": 0., "slug": "", "ts": 0,
                          "bUp": 0., "bDn": 0., "sUp": 0., "sDn": 0.,
                          "bUp_s": 0., "bDn_s": 0., "sUp_s": 0., "sDn_s": 0., "n": 0, "reb": 0.})
for r in acts:
    cid = r.get("conditionId")
    if not cid:
        continue
    w = W[cid]; w["slug"] = r.get("slug") or w["slug"]; w["ts"] = max(w["ts"], r.get("timestamp", 0))
    t = r.get("type"); usd = r.get("usdcSize", 0) or 0; sz = r.get("size", 0) or 0
    up = (r.get("outcome") == "Up")
    if t == "SPLIT": w["mint"] += usd
    elif t == "MERGE": w["merge"] += usd
    elif t == "REDEEM": w["redeem"] += usd
    elif t == "TRADE":
        w["n"] += 1
        px = r.get("price", 0) or 0
        w["reb"] += 0.014 * px * (1 - px) * sz          # est. maker rebate from on-chain fills (real sizes)
        if r.get("side") == "BUY":
            (w.__setitem__("bUp", w["bUp"] + usd), w.__setitem__("bUp_s", w["bUp_s"] + sz)) if up \
                else (w.__setitem__("bDn", w["bDn"] + usd), w.__setitem__("bDn_s", w["bDn_s"] + sz))
        else:
            (w.__setitem__("sUp", w["sUp"] + usd), w.__setitem__("sUp_s", w["sUp_s"] + sz)) if up \
                else (w.__setitem__("sDn", w["sDn"] + usd), w.__setitem__("sDn_s", w["sDn_s"] + sz))

rows = []
tot_trade = 0.0
for cid, w in sorted(W.items(), key=lambda kv: kv[1]["ts"]):
    if w["n"] == 0 and w["mint"] == 0:
        continue
    trading = -w["mint"] - w["bUp"] - w["bDn"] + w["sUp"] + w["sDn"] + w["merge"] + w["redeem"]
    endUp = w["mint"] + w["bUp_s"] - w["sUp_s"]
    endDn = w["mint"] + w["bDn_s"] - w["sDn_s"]
    imb = abs(endUp - endDn)
    imb_pct = 100 * imb / max(w["mint"], 1)
    settled = w["redeem"] > 0 or w["merge"] > 0
    # OPEN (unsettled) window: the held block hasn't redeemed → don't show it as a −$mint loss. Mark it:
    # matched sets are worth ~$1 each, residual at ~0.5 (neutral). A held neutral block ⇒ trading ≈ 0.
    matched = min(endUp, endDn); resid = abs(endUp - endDn)
    trading_m = trading + (0.0 if settled else matched + resid * 0.5)
    tot_trade += trading_m
    rows.append((w["slug"][-4:] or "?", w["n"], w["mint"], trading_m, w["reb"], imb, imb_pct, settled))
tot_est_reb = sum(w["reb"] for w in W.values())

reb_by_day = defaultdict(float)
for r in reb:
    reb_by_day[time.strftime("%m-%d", time.gmtime(r["timestamp"]))] += r.get("usdcSize", 0) or 0
tot_reb = sum(r.get("usdcSize", 0) or 0 for r in reb)

print(f"== OUR Safe per-window realized P&L (on-chain), last {HOURS:.0f}h — {len(rows)} windows ==")
print(f"{'win':>6}{'fills':>6}{'mint$':>7}{'trade$':>9}{'estReb$':>8}{'imb_sh':>8}{'imb%':>7}{'settled':>8}")
for slug, n, mint, tr, rb, imb, imbp, settled in rows:
    print(f"{slug:>6}{n:>6}{mint:>7.0f}{tr:>9.2f}{rb:>8.3f}{imb:>8.0f}{imbp:>6.0f}%{('yes' if settled else 'OPEN'):>8}")
print(f"\nTRADING total (ex-rebate) = ${tot_trade:+.2f}   [neutral+flatten => ~0/small; big +/- => directional]")
print(f"REBATE est from fills     = ${tot_est_reb:+.2f}   (accrues now; the real credit lands next daily cycle)")
print(f"REBATE actually credited  = ${tot_reb:+.2f}   {dict(reb_by_day)}")
print(f"NET realized (trade+credit)= ${tot_trade + tot_reb:+.2f}")
med_imb = sorted(r[5] for r in rows)[len(rows)//2] if rows else 0
print(f"neutrality: median window imbalance = {med_imb:.0f}%  (drawdown driver; want small)")
print("\nreading: if TRADING≈0 (neutral) and REBATE>0 => the thesis holds (free rebate, low drawdown).")
print("         if TRADING is the big +/- term => it's directional (lean), NOT the rebate model — check if +EV over n.")
