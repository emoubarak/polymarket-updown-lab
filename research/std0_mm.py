"""Reverse-engineer std0's market-making strategy from cached on-chain activity.

Reads research/data/std0_activity.json (produced by std0_fetch.py = FULL history
via start/end time windows) and answers, QUANTITATIVELY (medians/distributions
across many windows):

 1. SELL-side vs mid  — recovered without historical books two ways:
      (a) overround: per matched window, VWAP(sell Up) + VWAP(sell Down) − 1
      (b) self-quoted spread: same token, BUY-VWAP (its bid) vs SELL-VWAP (its ask)
          in the same window → mid=(a+b)/2, half-spread=(a−b)/2 = (sell−mid)
 2. Per-window flow reconstruction (representative btc-5m windows)
 3. Two-sidedness / neutrality: net (Up sold − Down sold)
 4. Markets & timing: coin/frame split + seconds-into-window concentration
 5. Effective edge per round-trip / per window net cash
 6. Clip sizes: median $ per SELL / BUY / SPLIT / REDEEM
"""
from __future__ import annotations
import json, os, statistics, time
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "data", "std0_activity.json")


def q(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    i = min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))
    return xs[i]


def market_of(slug):       # 'btc-updown-5m-1782598800' -> 'btc-updown-5m'
    return slug.rsplit("-", 1)[0]


def window_start(slug):
    try:
        return int(slug.rsplit("-", 1)[1])
    except Exception:
        return None


def frame_of(slug):
    return "5m" if "-5m-" in slug else ("15m" if "-15m-" in slug else "?")


def load():
    rows = json.load(open(SRC))
    for r in rows:
        r["mkt"] = market_of(r["slug"])
        r["ws"] = window_start(r["slug"])
        r["frame"] = frame_of(r["slug"])
        r["tiw"] = (r["timestamp"] - r["ws"]) if r["ws"] else None
    rows.sort(key=lambda r: r["timestamp"])
    return rows


def section(t):
    print("\n" + "=" * 78 + f"\n{t}\n" + "=" * 78)


def main():
    rows = load()
    lo, hi = rows[0]["timestamp"], rows[-1]["timestamp"]
    days = (hi - lo) / 86400
    section("0. DATASET")
    print(f"{len(rows)} rows  {time.strftime('%Y-%m-%d', time.gmtime(lo))} → "
          f"{time.strftime('%Y-%m-%d', time.gmtime(hi))}  ({days:.1f} days)")
    print("types:", dict(Counter(r["type"] for r in rows)))

    trades = [r for r in rows if r["type"] == "TRADE"]
    buys = [r for r in trades if r["side"] == "BUY"]
    sells = [r for r in trades if r["side"] == "SELL"]
    splits = [r for r in rows if r["type"] == "SPLIT"]
    merges = [r for r in rows if r["type"] == "MERGE"]
    redeems = [r for r in rows if r["type"] == "REDEEM"]

    # ----------------------------------------------------------------- 6 CLIPS
    section("6. CLIP SIZES ($ per fill / event)")
    for label, rs in (("BUY", buys), ("SELL", sells), ("SPLIT", splits),
                      ("MERGE", merges), ("REDEEM", redeems)):
        if not rs:
            print(f"  {label:7} n=0")
            continue
        usd = [r["usdcSize"] for r in rs]
        sz = [r["size"] for r in rs]
        print(f"  {label:7} n={len(rs):6d}  $ med={statistics.median(usd):7.2f} "
              f"mean={statistics.mean(usd):7.2f} p90={q(usd,.9):8.2f}  "
              f"shares med={statistics.median(sz):8.1f}")

    # ----------------------------------------------------------------- 4 MARKETS
    section("4a. MARKETS (coin-frame split, by trade count and by $ traded)")
    by_mkt_n = Counter()
    by_mkt_usd = Counter()
    for r in trades:
        by_mkt_n[r["mkt"]] += 1
        by_mkt_usd[r["mkt"]] += r["usdcSize"]
    tot_usd = sum(by_mkt_usd.values()) or 1
    for m, _ in by_mkt_usd.most_common():
        print(f"  {m:18} trades={by_mkt_n[m]:7d}  $vol={by_mkt_usd[m]:12.0f} "
              f"({100*by_mkt_usd[m]/tot_usd:4.1f}%)")

    section("4b. TIMING — seconds into window where activity concentrates ($-weighted)")
    for frame, span in (("5m", 300), ("15m", 900)):
        nb = 10
        bw = span / nb
        print(f"  -- {frame} (bucket={bw:.0f}s) --")
        for side_label, rs in (("SELL", [r for r in sells if r["frame"] == frame]),
                               ("BUY", [r for r in buys if r["frame"] == frame]),
                               ("SPLIT", [r for r in splits if r["frame"] == frame])):
            rs = [r for r in rs if r["tiw"] is not None and 0 <= r["tiw"] <= span]
            if not rs:
                continue
            hist = [0.0] * nb
            for r in rs:
                hist[min(nb - 1, int(r["tiw"] / bw))] += r["usdcSize"]
            tot = sum(hist) or 1
            bars = " ".join(f"{100*h/tot:4.0f}" for h in hist)
            med_t = statistics.median([r["tiw"] for r in rs])
            print(f"     {side_label:6} %$/bucket: {bars}   med={med_t:.0f}s n={len(rs)}")

    # ---------------------------------------------------- per-window aggregation
    W = defaultdict(lambda: {"split": 0.0, "merge": 0.0, "redeem": 0.0,
                             "buy": defaultdict(float), "sell": defaultdict(float),
                             "buy_px": defaultdict(list), "sell_px": defaultdict(list),
                             "frame": None, "coin": None, "events": []})
    for r in rows:
        if r["ws"] is None:
            continue
        w = W[(r["mkt"], r["ws"])]
        w["frame"] = r["frame"]
        w["coin"] = r["slug"].split("-")[0]
        w["events"].append(r)
        if r["type"] == "SPLIT":
            w["split"] += r["usdcSize"]
        elif r["type"] == "MERGE":
            w["merge"] += r["usdcSize"]
        elif r["type"] == "REDEEM":
            w["redeem"] += r["usdcSize"]
        elif r["type"] == "TRADE":
            oc = r["outcome"]
            side = "buy" if r["side"] == "BUY" else "sell"
            w[side][oc] += r["usdcSize"]
            w[side + "_px"][oc].append((r["size"], r["price"]))

    # ------------------------------------------------ 5 EDGE per window / total
    section("5. REALIZED CASH FLOW  (SELL + REDEEM + MERGE − BUY − SPLIT)")
    sum_sell = sum(r["usdcSize"] for r in sells)
    sum_buy = sum(r["usdcSize"] for r in buys)
    sum_split = sum(r["usdcSize"] for r in splits)
    sum_merge = sum(r["usdcSize"] for r in merges)
    sum_redeem = sum(r["usdcSize"] for r in redeems)
    net = sum_sell + sum_redeem + sum_merge - sum_buy - sum_split
    print(f"  Σ SELL   +{sum_sell:12.0f}")
    print(f"  Σ REDEEM +{sum_redeem:12.0f}")
    print(f"  Σ MERGE  +{sum_merge:12.0f}")
    print(f"  Σ BUY    -{sum_buy:12.0f}")
    print(f"  Σ SPLIT  -{sum_split:12.0f}")
    print(f"  ------------------------")
    print(f"  NET realized cash = {net:+.0f}   over {days:.1f} days "
          f"→ {net/max(days,1e-9):+.0f}/day")

    section("5b. PER-WINDOW NET CASH + per-set overround edge")
    wnets = []
    set_edges = []
    for (m, ws), w in W.items():
        ncash = (sum(w["sell"].values()) + w["redeem"] + w["merge"]
                 - sum(w["buy"].values()) - w["split"])
        wnets.append(ncash)
        up = w["sell_px"].get("Up")
        dn = w["sell_px"].get("Down")
        if up and dn:
            vw_up = sum(s * p for s, p in up) / sum(s for s, _ in up)
            vw_dn = sum(s * p for s, p in dn) / sum(s for s, _ in dn)
            set_edges.append(vw_up + vw_dn - 1)
    print(f"  windows touched: {len(wnets)}")
    print(f"  per-window net cash: med={statistics.median(wnets):+.2f} "
          f"mean={statistics.mean(wnets):+.2f} p10={q(wnets,.1):+.2f} p90={q(wnets,.9):+.2f}")
    print(f"  win-rate (net>0): {100*sum(1 for x in wnets if x>0)/len(wnets):.0f}%")
    if set_edges:
        print(f"  overround per matched window (VWAP_up+VWAP_dn−1): "
              f"med={statistics.median(set_edges):+.4f} "
              f"mean={statistics.mean(set_edges):+.4f} n={len(set_edges)}")

    # per-frame edge breakdown
    section("5c. NET CASH by coin-frame ($-flow)")
    by_mkt_net = Counter()
    by_mkt_wins = Counter()
    by_mkt_wn = Counter()
    for (m, ws), w in W.items():
        ncash = (sum(w["sell"].values()) + w["redeem"] + w["merge"]
                 - sum(w["buy"].values()) - w["split"])
        by_mkt_net[m] += ncash
        by_mkt_wn[m] += 1
        if ncash > 0:
            by_mkt_wins[m] += 1
    for m, v in by_mkt_net.most_common():
        print(f"  {m:18} net={v:+10.0f}  windows={by_mkt_wn[m]:5d}  "
              f"win%={100*by_mkt_wins[m]/max(by_mkt_wn[m],1):3.0f}  "
              f"$/win={v/max(by_mkt_wn[m],1):+7.2f}")

    # ------------------------------------------------ 1 SELL vs mid (self-quote)
    section("1. SELL vs MID — self-quoted spread (same token: BUY-VWAP=bid vs SELL-VWAP=ask)")
    half_spreads = []
    for (m, ws), w in W.items():
        for oc in ("Up", "Down"):
            b = w["buy_px"].get(oc)
            s = w["sell_px"].get(oc)
            if b and s and sum(x for x, _ in b) > 0 and sum(x for x, _ in s) > 0:
                bid = sum(x * p for x, p in b) / sum(x for x, _ in b)
                ask = sum(x * p for x, p in s) / sum(x for x, _ in s)
                if ask > bid:
                    half_spreads.append((ask - bid) / 2)
    if half_spreads:
        print(f"  windows*sides with both bid&ask: {len(half_spreads)}")
        print(f"  half-spread (=sell−mid): med={statistics.median(half_spreads):.4f} "
              f"mean={statistics.mean(half_spreads):.4f} p25={q(half_spreads,.25):.4f} "
              f"p75={q(half_spreads,.75):.4f}")
        print(f"  → SELL sits ~{100*statistics.median(half_spreads):.2f}¢ ABOVE its own mid")

    section("1b. INVENTORY: minted (SPLIT) vs sold vs bought vs redeemed (shares)")
    tot_split_sh = sum(r["size"] for r in splits)
    tot_sell_sh = sum(r["size"] for r in sells)
    tot_buy_sh = sum(r["size"] for r in buys)
    tot_redeem_sh = sum(r["size"] for r in redeems)
    tot_merge_sh = sum(r["size"] for r in merges)
    print(f"  minted sets (SPLIT shares/side): {tot_split_sh:12.0f}")
    print(f"  sold shares (both sides):        {tot_sell_sh:12.0f}")
    print(f"  bought shares (both sides):      {tot_buy_sh:12.0f}")
    print(f"  merged sets:                     {tot_merge_sh:12.0f}")
    print(f"  redeemed shares:                 {tot_redeem_sh:12.0f}")
    print(f"  sold / minted   = {tot_sell_sh/max(tot_split_sh,1):.2f}")
    print(f"  (sold+merged*2)/minted ≈ disposal of long inventory")
    print(f"  redeemed / minted = {tot_redeem_sh/max(tot_split_sh,1):.2f}")

    # ------------------------------------------------ 3 TWO-SIDEDNESS
    section("3. TWO-SIDEDNESS — net (Up sold $ − Down sold $) per window")
    nets = []
    gross = []
    for (m, ws), w in W.items():
        up = w["sell"].get("Up", 0)
        dn = w["sell"].get("Down", 0)
        if up + dn > 0:
            nets.append(up - dn)
            gross.append(up + dn)
    if nets:
        absratio = [abs(n) / g for n, g in zip(nets, gross) if g > 0]
        print(f"  windows with sells: {len(nets)}")
        print(f"  net Up−Down $ per window: med={statistics.median(nets):+.2f} "
              f"mean={statistics.mean(nets):+.2f}")
        print(f"  |net|/gross (0=balanced, 1=one-sided): "
              f"med={statistics.median(absratio):.2f} mean={statistics.mean(absratio):.2f}")
        for thr in (0.2, 0.34, 0.5):
            bal = sum(1 for r in absratio if r < thr)
            print(f"  fraction balanced (|net|/gross<{thr}): {100*bal/len(absratio):.0f}%")

    # ------------------------------------------------ 2 representative windows
    section("2. REPRESENTATIVE btc-5m WINDOW RECONSTRUCTIONS (timeline)")
    btc5 = [(ws, w) for (m, ws), w in W.items()
            if m == "btc-updown-5m" and w["split"] > 0 and w["sell"] and w["buy"]]
    btc5.sort()
    if len(btc5) >= 4:
        picks = [btc5[len(btc5)//4], btc5[len(btc5)//2], btc5[3*len(btc5)//4]]
    else:
        picks = btc5
    for ws, w in picks:
        print(f"\n  --- btc-updown-5m-{ws}  ({time.strftime('%m-%d %H:%M', time.gmtime(ws))} UTC) ---")
        print(f"    SPLIT minted ${w['split']:.0f}  | REDEEM ${w['redeem']:.0f}  MERGE ${w['merge']:.0f}")
        for oc in ("Up", "Down"):
            bs = w["buy_px"].get(oc, [])
            ss = w["sell_px"].get(oc, [])
            bsh = sum(x for x, _ in bs)
            ssh = sum(x for x, _ in ss)
            bvw = (sum(x*p for x, p in bs)/bsh) if bsh else 0
            svw = (sum(x*p for x, p in ss)/ssh) if ssh else 0
            print(f"    {oc:4}: BUY {bsh:7.0f}sh @{bvw:.3f}  SELL {ssh:7.0f}sh @{svw:.3f}  "
                  f"inv(buy−sell)={bsh-ssh:+.0f}")
        ncash = (sum(w["sell"].values()) + w["redeem"] + w["merge"]
                 - sum(w["buy"].values()) - w["split"])
        print(f"    window net cash = {ncash:+.2f}")
        # mini timeline: first 14 events
        print("    timeline (t+s | type side oc | sh @ px):")
        for e in sorted(w["events"], key=lambda x: x["timestamp"])[:14]:
            print(f"      +{e['tiw']:4}s {e['type']:6} {str(e.get('side') or ''):4} "
                  f"{str(e.get('outcome') or ''):4} {e['size']:7.1f} @ {e['price']:.3f}  ${e['usdcSize']:.1f}")


if __name__ == "__main__":
    main()
