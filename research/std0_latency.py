#!/usr/bin/env python3
"""Q6: mint-then-sell latency + clean split-only gas. From the cached activity stream.

For each SPLIT (mint of a complete set on a given conditionId/window), find the first
SELL TRADE on the SAME conditionId at or after the mint, and measure the delay. Also
the time from mint to the point the whole minted notional is sold (cumulative).
"""
import json, os, statistics as st
from collections import defaultdict

DATA = os.path.join(os.path.dirname(__file__), "data", "std0_activity.json")


def pct(xs, p):
    xs = sorted(xs)
    return xs[max(0, min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1)))))]


def main():
    rows = json.load(open(DATA))
    rows.sort(key=lambda r: r["timestamp"])
    # index sells by conditionId
    sells = defaultdict(list)
    for r in rows:
        if r["type"] == "TRADE" and r["side"] == "SELL":
            sells[r["conditionId"]].append(r)
    for k in sells:
        sells[k].sort(key=lambda r: r["timestamp"])

    splits = [r for r in rows if r["type"] == "SPLIT"]
    first_lat = []
    sold_frac_60 = []
    examples = []
    for sp in splits:
        cid = sp["conditionId"]
        ts = sp["timestamp"]
        mint_shares = sp["size"]  # complete sets minted
        ss = [s for s in sells.get(cid, []) if s["timestamp"] >= ts]
        if not ss:
            continue
        lat = ss[0]["timestamp"] - ts
        first_lat.append(lat)
        # cumulative shares sold within 60s of mint (one side max = mint_shares)
        sold60 = sum(s["size"] for s in ss if s["timestamp"] - ts <= 60)
        sold_frac_60.append(min(1.0, sold60 / mint_shares) if mint_shares else 0)
        if len(examples) < 12:
            examples.append((ts, cid[:10], mint_shares, lat,
                             ss[0]["size"], ss[0]["price"], len(ss)))

    print("=== Q6 MINT -> FIRST-SELL LATENCY ===")
    print(f"splits matched to a later same-condition SELL: {len(first_lat)}/{len(splits)}")
    if first_lat:
        print(f"first-sell latency (s): median={st.median(first_lat):.0f} "
              f"mean={st.mean(first_lat):.1f} p10={pct(first_lat,10)} "
              f"p25={pct(first_lat,25)} p75={pct(first_lat,75)} p90={pct(first_lat,90)} "
              f"max={max(first_lat)}")
        from collections import Counter
        buckets = Counter()
        for l in first_lat:
            b = ("<=2s" if l <= 2 else "3-5s" if l <= 5 else "6-15s" if l <= 15
                 else "16-60s" if l <= 60 else ">60s")
            buckets[b] += 1
        for b in ["<=2s", "3-5s", "6-15s", "16-60s", ">60s"]:
            print(f"   {b:7s}: {buckets.get(b,0)}")
    if sold_frac_60:
        print(f"\nfraction of minted set sold within 60s of mint: "
              f"median={st.median(sold_frac_60):.2f} mean={st.mean(sold_frac_60):.2f}")
    print("\nexamples (mint_ts, cond, mintShares, firstSellLat_s, firstSellSize, px, #sells):")
    for e in examples:
        print(f"   {e}")

    # --- clean split-only gas (uses values cached by std0_tx if present, else reprint) ---
    print("\n(see std0_tx.py for measured split gas: ~384k gasUsed @ ~280 gwei "
          "= ~0.107 POL/split; POL~$0.07 => ~$0.0075/split)")


if __name__ == "__main__":
    main()
