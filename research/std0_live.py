"""LIVE capture: directly measure where std0 SELLS relative to the real CLOB mid.

For the currently-open btc-5m (and btc-15m) windows we:
  - resolve the Up/Down clobTokenIds from gamma,
  - poll the CLOB book every ~2.5s, recording (best_bid, best_ask, mid) per token,
  - poll std0's /activity, capturing each new TRADE fill,
  - match every std0 fill to the book snapshot nearest in time and record
    (fill_price − mid) and where in the book the fill sits (at/above ask, etc).

Run for ~`MINUTES`. Writes raw matched fills + a summary to
research/data/std0_live_fills.json. This is the fill-independent ground truth for
Q1 (does it SELL above mid / BUY below mid = classic two-sided MM spread capture).
"""
from __future__ import annotations
import json, os, time, sys
import requests

WALLET = "0xdf7930e89a2c47560165331863c31deca0733dcd"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "std0_live_fills.json")

MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 18.0
COINS_FRAMES = [("btc", "5m", 300), ("btc", "15m", 900),
                ("eth", "15m", 900), ("sol", "5m", 300)]

S = requests.Session()
S.headers["User-Agent"] = "pmlab-research/std0-live"


def jget(url, **p):
    for a in range(3):
        try:
            r = S.get(url, params=p or None, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(0.5)
    return None


def market_tokens(slug):
    ev = jget(f"{GAMMA}/events", slug=slug)
    if not ev:
        return None
    m = ev[0]["markets"][0]
    toks = json.loads(m["clobTokenIds"])
    outs = m["outcomes"]
    outs = json.loads(outs) if isinstance(outs, str) else outs
    d = dict(zip(outs, toks))
    return {"Up": d.get("Up"), "Down": d.get("Down"), "cond": m["conditionId"]}


def book_mid(token_id):
    b = jget(f"{CLOB}/book", token_id=token_id)
    if not b:
        return None
    bids = b.get("bids") or []
    asks = b.get("asks") or []
    # CLOB returns bids ascending? normalize: best bid = max price, best ask = min price
    try:
        bb = max(float(x["price"]) for x in bids) if bids else None
        ba = min(float(x["price"]) for x in asks) if asks else None
    except Exception:
        return None
    if bb is None or ba is None:
        return None
    return {"bid": bb, "ask": ba, "mid": (bb + ba) / 2, "t": time.time()}


def cur_window(frame_sec):
    now = int(time.time())
    return (now // frame_sec) * frame_sec


def main():
    deadline = time.time() + MINUTES * 60
    # token cache per slug
    tok = {}
    snaps = {}          # token_id -> list of book snaps
    seen_fills = set()
    fills = []
    print(f"capturing for {MINUTES} min ...")
    while time.time() < deadline:
        # refresh markets (window may roll over)
        active_tokens = {}
        for coin, frame, sec in COINS_FRAMES:
            ws = cur_window(sec)
            slug = f"{coin}-updown-{frame}-{ws}"
            if slug not in tok:
                tok[slug] = market_tokens(slug)
            t = tok[slug]
            if t:
                for oc in ("Up", "Down"):
                    if t[oc]:
                        active_tokens[t[oc]] = (slug, oc)

        # snapshot books
        for tid, (slug, oc) in active_tokens.items():
            bm = book_mid(tid)
            if bm:
                bm.update(slug=slug, oc=oc)
                snaps.setdefault(tid, []).append(bm)

        # poll std0 fills (recent 200)
        acts = jget(f"{DATA_API}/activity", user=WALLET, limit=200) or []
        for r in acts:
            if r["type"] != "TRADE":
                continue
            key = (r["transactionHash"], r["asset"], r["side"], r["size"], r["timestamp"])
            if key in seen_fills:
                continue
            seen_fills.add(key)
            # find token snapshots for this asset
            ss = snaps.get(r["asset"])
            if not ss:
                continue
            # nearest snapshot in time to the fill timestamp
            near = min(ss, key=lambda s: abs(s["t"] - r["timestamp"]))
            if abs(near["t"] - r["timestamp"]) > 12:   # no fresh book within 12s
                continue
            fills.append({
                "slug": r["slug"], "oc": r["outcome"], "side": r["side"],
                "price": r["price"], "size": r["size"], "usd": r["usdcSize"],
                "ts": r["timestamp"], "tiw": r["timestamp"] - int(r["slug"].rsplit("-", 1)[1]),
                "bid": near["bid"], "ask": near["ask"], "mid": near["mid"],
                "dt": round(near["t"] - r["timestamp"], 1),
            })
        time.sleep(2.5)

    # summarize
    import statistics
    with open(OUT, "w") as f:
        json.dump(fills, f)
    print(f"\ncaptured {len(fills)} matched fills → {OUT}")

    def summ(rows, label):
        if not rows:
            print(f"  {label}: n=0")
            return
        dm = [r["price"] - r["mid"] for r in rows]
        print(f"  {label}: n={len(rows)}  (price−mid) med={statistics.median(dm):+.4f} "
              f"mean={statistics.mean(dm):+.4f}  "
              f"frac>0={100*sum(1 for x in dm if x>0)/len(dm):.0f}%")

    sells = [r for r in fills if r["side"] == "SELL"]
    buys = [r for r in fills if r["side"] == "BUY"]
    print("\n=== SELL vs mid (expect ABOVE mid, +) ===")
    summ(sells, "ALL SELL")
    summ([r for r in sells if r["oc"] == "Up"], "  SELL Up")
    summ([r for r in sells if r["oc"] == "Down"], "  SELL Down")
    print("=== BUY vs mid (expect BELOW mid, −) ===")
    summ(buys, "ALL BUY")
    # at-or-above-ask fraction (taker vs maker signature)
    if sells:
        ge_ask = sum(1 for r in sells if r["price"] >= r["ask"] - 1e-9)
        print(f"\n  SELL at/above best ask: {100*ge_ask/len(sells):.0f}%  "
              f"(high = it's the resting ASK getting hit = MAKER)")
    if buys:
        le_bid = sum(1 for r in buys if r["price"] <= r["bid"] + 1e-9)
        print(f"  BUY at/below best bid:  {100*le_bid/len(buys):.0f}%  "
              f"(high = it's the resting BID getting hit = MAKER)")


if __name__ == "__main__":
    main()
