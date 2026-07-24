#!/usr/bin/env python3
"""Cadence + size analysis of std0 inventory ops from the cached activity stream.

Answers task Q1 (cadence / ratio / inter-op histogram), Q2 (size distributions),
and the per-minute throughput envelope for Q5.
"""
import json, os, statistics as st
from collections import Counter, defaultdict

DATA = os.path.join(os.path.dirname(__file__), "data", "std0_activity.json")
OPS = ("SPLIT", "MERGE", "REDEEM")


def load():
    with open(DATA) as f:
        return json.load(f)


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return 0.0
    i = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[i]


def hist(xs, edges):
    c = [0] * (len(edges) + 1)
    for x in xs:
        placed = False
        for i, e in enumerate(edges):
            if x <= e:
                c[i] += 1
                placed = True
                break
        if not placed:
            c[-1] += 1
    return c


def main():
    rows = load()
    span_h = (rows[-1]["timestamp"] - rows[0]["timestamp"]) / 3600
    span_min = span_h * 60
    typ = Counter(r["type"] for r in rows)
    print(f"=== STREAM: {len(rows)} rows over {span_h:.2f} h "
          f"({rows[0]['timestamp']}..{rows[-1]['timestamp']}) ===")
    print("type counts:", dict(typ))

    # --- Q1 ratio & per-minute cadence ---
    n_split = typ.get("SPLIT", 0)
    n_merge = typ.get("MERGE", 0)
    n_redeem = typ.get("REDEEM", 0)
    base = max(1, n_merge)
    print(f"\n--- Q1 CADENCE ---")
    print(f"SPLIT={n_split}  MERGE={n_merge}  REDEEM={n_redeem}")
    print(f"ratio SPLIT:MERGE:REDEEM = "
          f"{n_split/base:.2f} : 1 : {n_redeem/base:.2f}  (norm to MERGE)")
    if n_redeem:
        print(f"ratio SPLIT:REDEEM = {n_split/n_redeem:.2f} : 1")
    for t in OPS:
        n = typ.get(t, 0)
        print(f"  {t:7s}: {n:4d}  = {n/span_min:.2f}/min  ({n/span_h:.0f}/h)")
    all_ops = n_split + n_merge + n_redeem
    print(f"  ALL OPS: {all_ops}  = {all_ops/span_min:.2f}/min  ({all_ops/span_h:.0f}/h)")
    n_trade = typ.get("TRADE", 0)
    print(f"  TRADE  : {n_trade}  = {n_trade/span_min:.2f}/min  ({n_trade/span_h:.0f}/h)")

    # --- per-minute throughput envelope (Q5) ---
    by_min = defaultdict(lambda: Counter())
    for r in rows:
        m = r["timestamp"] // 60
        by_min[m][r["type"]] += 1
    op_counts_per_min = [sum(c[t] for t in OPS) for c in by_min.values()]
    trade_per_min = [c["TRADE"] for c in by_min.values()]
    allact_per_min = [sum(c.values()) for c in by_min.values()]
    print(f"\n--- Q5 THROUGHPUT (per active minute, {len(by_min)} minutes seen) ---")
    print(f"ops/min   (SPLIT+MERGE+REDEEM): "
          f"max={max(op_counts_per_min)} p95={pct(op_counts_per_min,95)} "
          f"median={int(st.median(op_counts_per_min))} mean={st.mean(op_counts_per_min):.1f}")
    print(f"trades/min: max={max(trade_per_min)} p95={pct(trade_per_min,95)} "
          f"median={int(st.median(trade_per_min))} mean={st.mean(trade_per_min):.1f}")
    print(f"ALL activity/min: max={max(allact_per_min)} p95={pct(allact_per_min,95)} "
          f"median={int(st.median(allact_per_min))} mean={st.mean(allact_per_min):.1f}")
    # busiest minute breakdown
    busiest = max(by_min.items(), key=lambda kv: sum(kv[1].values()))
    print(f"busiest minute {busiest[0]*60}: {dict(busiest[1])}")

    # --- inter-op gaps (Q1 histogram) ---
    # gaps between consecutive INVENTORY ops (any of SPLIT/MERGE/REDEEM)
    op_ts = sorted(r["timestamp"] for r in rows if r["type"] in OPS)
    gaps = [b - a for a, b in zip(op_ts, op_ts[1:])]
    print(f"\n--- Q1 INTER-OP GAPS (consecutive SPLIT/MERGE/REDEEM, n={len(gaps)}) ---")
    print(f"median={st.median(gaps):.1f}s mean={st.mean(gaps):.1f}s "
          f"p10={pct(gaps,10)}s p90={pct(gaps,90)}s max={max(gaps)}s")
    edges = [0, 1, 2, 3, 5, 10, 20, 30, 60, 120]
    h = hist(gaps, edges)
    labels = (["=0s"] + [f"{edges[i-1]+1}-{edges[i]}s" for i in range(1, len(edges))]
              + [f">{edges[-1]}s"])
    for lab, c in zip(labels, h):
        bar = "#" * int(60 * c / max(h))
        print(f"  {lab:10s} {c:4d} {bar}")
    back_to_back = sum(1 for g in gaps if g <= 1)
    print(f"back-to-back (<=1s apart): {back_to_back}/{len(gaps)} = "
          f"{100*back_to_back/len(gaps):.0f}%")

    # also: gaps within each op type
    for t in OPS:
        ts = sorted(r["timestamp"] for r in rows if r["type"] == t)
        g = [b - a for a, b in zip(ts, ts[1:])]
        if g:
            print(f"  {t:7s} inter-gap median={st.median(g):.0f}s "
                  f"p10={pct(g,10)}s p90={pct(g,90)}s")

    # --- Q2 sizes ---
    print(f"\n--- Q2 SIZES ($ usdcSize) ---")
    for t in OPS:
        sz = [r["usdcSize"] for r in rows if r["type"] == t and r.get("usdcSize")]
        if not sz:
            continue
        print(f"{t:7s} n={len(sz):4d}  median=${st.median(sz):8.1f}  "
              f"mean=${st.mean(sz):8.1f}  p10=${pct(sz,10):7.1f}  p90=${pct(sz,90):8.1f}  "
              f"max=${max(sz):9.1f}  total=${sum(sz):,.0f}")
    # size histogram for SPLIT
    sp = [r["usdcSize"] for r in rows if r["type"] == "SPLIT"]
    if sp:
        sedges = [50, 100, 250, 500, 1000, 2000, 5000]
        sh = hist(sp, sedges)
        slabels = [f"<=${sedges[0]}"] + [f"${sedges[i-1]}-{sedges[i]}" for i in range(1, len(sedges))] + [f">${sedges[-1]}"]
        print("SPLIT size histogram:")
        for lab, c in zip(slabels, sh):
            print(f"  {lab:14s} {c:4d} {'#'*int(50*c/max(sh)) if max(sh) else ''}")

    # per-coin split of ops
    print(f"\n--- ops by coin ---")
    coin_op = defaultdict(Counter)
    for r in rows:
        if r["type"] in OPS:
            coin = r.get("slug", "?").split("-updown")[0]
            coin_op[coin][r["type"]] += 1
    for coin, c in sorted(coin_op.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {coin:6s} {dict(c)}  total={sum(c.values())}")

    # frame split (5m vs 15m)
    fr = Counter()
    for r in rows:
        if r["type"] in OPS:
            s = r.get("slug", "")
            fr["5m" if "-5m-" in s else "15m" if "-15m-" in s else "?"] += 1
    print(f"frame split of ops: {dict(fr)}")


if __name__ == "__main__":
    main()
