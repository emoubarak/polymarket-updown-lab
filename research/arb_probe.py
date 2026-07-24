"""Live negative-risk arb probe: does Up_ask + Down_ask ever drop below 1?

Buying N shares of Up and N of Down guarantees N*$1 at settlement (exactly one
side wins). As a taker on both legs the cost is N*(ask_up+ask_down) plus two
taker fees (FEE_RATE*p*(1-p) each). So a *takeable* riskless arb needs

    ask_up + ask_down + fee_up + fee_down < 1.

NOTE the taker fee IS real: the 0.07 crypto fee is charged on-chain (re-verified
2026-06-27 from raw tx receipts — pUSD relayer 0xe111… forwards it to collector
0x115f48dc at entry, both 5m & 15m), so FEE_RATE = 0.07 and the takeable threshold is
ask_up + ask_down + fee_up + fee_down < 1. We share explore2's FEE_RATE so this metric
tracks the real cost automatically (the 2026-06-26 "phantom fee=0" read was wrong —
it missed the pUSD fee leg).

We sample both live CLOB books for the current 5m and 15m windows as fast as
courtesy allows, and report how often (if ever) the asks sum below 1, below
0.99, and below the fee-clearing threshold. Read-only; no orders.

Run: python3 research/arb_probe.py [seconds]
"""
from __future__ import annotations
import os
import sys
import time

# repo root (parent of research/), absolute so the import works from any cwd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pmlab import feeds
from explore2 import FEE_RATE


def fee(p: float) -> float:
    return FEE_RATE * p * (1.0 - p)


def probe_once(mkt) -> tuple[float, float, float] | None:
    bu = feeds.fetch_book(mkt.token_up)
    bd = feeds.fetch_book(mkt.token_down)
    if not bu["asks"] or not bd["asks"]:
        return None
    au = bu["asks"][0][0]
    ad = bd["asks"][0][0]
    net = au + ad + fee(au) + fee(ad)   # all-in taker cost of a matched pair
    return au, ad, net


def main():
    dur = int(sys.argv[1]) if len(sys.argv) > 1 else 180
    end = time.time() + dur
    stats = {iv: {"n": 0, "sub1": 0, "sub99": 0, "takeable": 0,
                  "min_raw": 9.0, "min_net": 9.0} for iv in ("5m", "15m")}
    last_disc = 0.0
    mkts: dict[str, object] = {}
    while time.time() < end:
        if time.time() - last_disc > 20:        # refresh window slugs
            for iv in ("5m", "15m"):
                ws, _ = feeds.window_bounds(iv)
                m = feeds.fetch_updown_market(iv, ws)
                if m:
                    mkts[iv] = m
            last_disc = time.time()
        for iv, m in list(mkts.items()):
            try:
                r = probe_once(m)
            except Exception:
                r = None
            if not r:
                continue
            au, ad, net = r
            s = stats[iv]
            s["n"] += 1
            raw = au + ad
            s["min_raw"] = min(s["min_raw"], raw)
            s["min_net"] = min(s["min_net"], net)
            if raw < 1.0:
                s["sub1"] += 1
            if raw < 0.99:
                s["sub99"] += 1
            if net < 1.0:
                s["takeable"] += 1
                print(f"  [{iv}] TAKEABLE ARB: up_ask={au:.3f} dn_ask={ad:.3f} "
                      f"raw={raw:.4f} all-in={net:.4f}")
        time.sleep(1.0)
    print("\n=== arb probe summary ===")
    for iv, s in stats.items():
        if not s["n"]:
            print(f"{iv}: no samples")
            continue
        print(f"{iv}: n={s['n']}  raw_sum<1: {s['sub1']} ({100*s['sub1']/s['n']:.1f}%)  "
              f"<0.99: {s['sub99']}  TAKEABLE(net<1): {s['takeable']}  "
              f"min_raw={s['min_raw']:.4f}  min_net={s['min_net']:.4f}")


if __name__ == "__main__":
    main()
