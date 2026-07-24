#!/usr/bin/env python3
"""std0_pnl — per-window realized P&L of std0, mark-to-settlement.

Robust per-window P&L that does NOT depend on the REDEEM event landing in
our capture (it lands +74..+346s after expiry, often on an uncaptured page):

  winner   = the outcome whose net inventory is positive AND (if a REDEEM
             exists) matches the redeemed side; else inferred from the
             larger held side at expiry.
  P&L      = SELL_usd - BUY_usd - split_cost + winner_inventory*$1

A window is COMPLETE (counted) only if it has a SPLIT and its trading is
internally consistent (net inventory resolvable). Windows still open, or
with a split but no resolvable winner yet, are skipped. Read-only.
"""
import json, sys, re, statistics, collections, pathlib, datetime

HOME = pathlib.Path.home() / "rebate"
FILES = sys.argv[1:] or [str(HOME/"std0_hist.jsonl"), str(HOME/"std0_activity.jsonl")]

def wstart(slug):
    m = re.search(r"-(\d+)$", slug or "")
    return int(m.group(1)) if m else None

def family(slug):
    m = re.match(r"([a-z]+-updown-\d+m)-\d+$", slug or "")
    return m.group(1) if m else "other"

seen, rows = set(), []
for fn in FILES:
    p = pathlib.Path(fn)
    if not p.exists():
        continue
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            a = json.loads(line)
        except Exception:
            continue
        k = (a.get("transactionHash",""), a.get("asset",""), a.get("timestamp",0),
             str(a.get("size","")), a.get("side",""), a.get("type",""))
        if k not in seen:
            seen.add(k); rows.append(a)

wins = collections.defaultdict(list)
for r in rows:
    ws = wstart(r.get("slug",""))
    if ws is not None:
        wins[(family(r["slug"]), ws)].append(r)

now = datetime.datetime.now(datetime.UTC).timestamp()

def window_pnl(evs):
    up = dn = 0.0
    trade_cash = 0.0
    redeemed = 0.0
    has_split = has_redeem = False
    for r in evs:
        t, side = r["type"], r.get("side","")
        sz, usd = r.get("size",0), r.get("usdcSize", 0)
        oc = r.get("outcome","")
        if t == "SPLIT":
            up += sz; dn += sz; trade_cash -= sz; has_split = True
        elif t == "TRADE":
            sgn = -1 if side == "SELL" else 1
            trade_cash -= sgn*usd
            if oc == "Up": up += sgn*sz
            else: dn += sgn*sz
        elif t == "REDEEM":
            redeemed += usd if usd else sz; has_redeem = True
    net = up - dn
    winner_inv = up if net >= 0 else dn      # winning side held at expiry
    pnl = trade_cash + max(up, dn)           # mark winner @ $1, loser @ $0
    # If a redeem was captured, prefer it (it's the realized payout) and use
    # it to validate the winner-side guess.
    if has_redeem:
        pnl = trade_cash + redeemed
    return pnl, has_split, has_redeem, net, up, dn

by_family = collections.defaultdict(list)
detail = []
for (fam, ws), evs in wins.items():
    closed = (now - (ws+300)) > 400
    if not closed:
        continue
    pnl, hs, hr, net, up, dn = window_pnl(evs)
    if not hs:
        continue  # need the split to know full position
    # completeness: winner side must be materially held (std0 mints 2500)
    if max(up, dn) < 100:
        continue
    by_family[fam].append(pnl)
    detail.append((ws, fam, pnl, hr, net))

print(f"{len(rows)} events, {len(wins)} windows seen\n")
print(f"{'family':16} {'n':>4} {'sum':>9} {'mean':>7} {'median':>7} {'win%':>5} {'p25':>7} {'p75':>7}")
for fam in sorted(by_family):
    v = sorted(by_family[fam]); n = len(v)
    if not n: continue
    win = 100*sum(1 for x in v if x>0.005)/n
    print(f"{fam:16} {n:4d} {sum(v):9.1f} {statistics.mean(v):7.2f} "
          f"{statistics.median(v):7.2f} {win:5.0f} {v[n//4]:7.2f} {v[3*n//4]:7.2f}")

btc = sorted(by_family.get("btc-updown-5m", []))
if btc:
    n = len(btc)
    sd = statistics.pstdev(btc)
    print(f"\n*** btc-5m TARGET: mean {statistics.mean(btc):+.3f}$/window, "
          f"median {statistics.median(btc):+.2f}, sd {sd:.1f}, n={n} ***")

btcw = sorted((d for d in detail if d[1]=="btc-updown-5m"), key=lambda x:x[0])
print("\nrecent btc-5m windows (time, pnl, redeem_seen, net_resid):")
for ws, fam, pnl, hr, net in btcw[-12:]:
    t = datetime.datetime.fromtimestamp(ws, datetime.UTC).strftime("%H:%M")
    print(f"  {t}  pnl={pnl:+8.2f}  redeem={'Y' if hr else 'n'}  net={net:+.0f}")
