"""Put the strategies on trial against the retroactive dataset.

A — oracle divergence: does Binance close>open match Polymarket resolution?
B — market calibration: is the Up price well-calibrated? (efficiency)
C — longshot bias (the model-confirmed-favorite premise, a falsified early strategy — see FINDINGS): is buying the favorite +EV?
D — tao maker sim on the REAL trade tape: does the passive-bid edge survive
    real adverse selection, with zero fees?

Run:  python3 research/analyze.py
"""
from __future__ import annotations
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dataset import fetch_klines, ewma_vol            # noqa: E402
from explore2 import FEE_RATE                          # noqa: E402
from pmlab.model import model_p_up            # noqa: E402

# Taker-fee coefficient, sourced from explore2 (FEE_RATE) so every EV here follows
# the one convention. It is 0.07: the crypto taker fee IS charged on-chain (re-verified
# 2026-06-27 from raw tx receipts — pUSD relayer 0xe111… forwards it to collector
# 0x115f48dc at entry, both 5m & 15m). The 2026-06-26 "phantom fee=0" read missed the
# pUSD fee leg; tracking FEE_RATE keeps these studies honest.

CACHE = os.path.join(os.path.dirname(__file__), "data")
SEC = 300


def load(interval: str = "5m") -> list[dict]:
    recs = []
    for f in glob.glob(os.path.join(CACHE, f"{interval}_*.json")):
        try:
            recs.append(json.load(open(f)))
        except (json.JSONDecodeError, OSError):
            continue                      # skip a file mid-write by a builder
    recs = [r for r in recs if r.get("price_track") and r.get("binance_open")]
    recs.sort(key=lambda r: r["window_start"])
    return recs


def price_at(track: list, t: int) -> float | None:
    """Last observed Up price at/before t."""
    best = None
    for ts, p in track:
        if ts <= t:
            best = p
        else:
            break
    return best


def spot_at(klines: dict, t: int) -> float | None:
    """Close of the latest completed 1m candle at/before t."""
    m = ((t - 60) // 60) * 60
    for _ in range(5):
        if m in klines:
            return klines[m]["close"]
        m -= 60
    return None


# ---------------------------------------------------------------- A ---
def exp_a(recs):
    both = [r for r in recs if r["binance_up"] is not None]
    agree = sum(1 for r in both if r["binance_up"] == r["up_won"])
    print(f"\n[A] Divergence oracle  (Binance close>open  vs  résolution Polymarket)")
    print(f"    n={len(both)}  accord={agree}  divergence={len(both)-agree} "
          f"({100*(len(both)-agree)/max(len(both),1):.1f}%)")
    up_rate = sum(r['up_won'] for r in recs) / len(recs)
    print(f"    base rate Up = {up_rate:.3f} (équilibre de la pièce)")


# ---------------------------------------------------------------- B ---
def brier(pairs):  # list of (p, outcome01)
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def exp_b(recs, t_off=120):
    pairs = []
    for r in recs:
        p = price_at(r["price_track"], r["window_start"] + t_off)
        if p is None:
            continue
        pairs.append((p, 1 if r["up_won"] else 0))
    print(f"\n[B] Calibration du marché  (prix Up à t=ws+{t_off}s, n={len(pairs)})")
    # reliability deciles
    bins = {}
    for p, o in pairs:
        b = min(9, int(p * 10))
        bins.setdefault(b, []).append(o)
    print("    bande prix   n   P(Up) réalisé   (écart)")
    for b in sorted(bins):
        outs = bins[b]
        lo, hi = b / 10, b / 10 + 0.1
        mid = (lo + hi) / 2
        realized = sum(outs) / len(outs)
        flag = "  <-- " if abs(realized - mid) > 0.12 and len(outs) >= 8 else ""
        print(f"    {lo:.1f}-{hi:.1f}   {len(outs):3d}     {realized:.3f}        "
              f"({realized-mid:+.3f}){flag}")
    bm = brier(pairs)
    b05 = brier([(0.5, o) for _, o in pairs])
    print(f"    Brier marché={bm:.4f}   Brier(0.5 constant)={b05:.4f}   "
          f"-> marché {'AJOUTE' if bm < b05 - 0.003 else 'N AJOUTE PAS'} d'info vs pièce")


# ---------------------------------------------------------------- C ---
def exp_c(recs, klines, t_off=120):
    """model-confirmed-favorite premise (falsified 'kabbalah' strategy): buy the favorite (price in 0.55-0.88) at market, hold."""
    fee = lambda p: FEE_RATE * p * (1 - p)        # taker, crypto
    res_mkt, res_model = [], []
    for r in recs:
        t = r["window_start"] + t_off
        p_mkt = price_at(r["price_track"], t)
        if p_mkt is None:
            continue
        spot = spot_at(klines, t)
        if spot is None:
            continue
        sig = ewma_vol(klines, t)
        pmod = model_p_up(spot, r["binance_open"], sig, (r["window_end"] - t) / 60)
        won_up = 1 if r["up_won"] else 0
        # favorite by market price
        fav_up = p_mkt >= 0.5
        fav_price = p_mkt if fav_up else 1 - p_mkt
        fav_win = won_up if fav_up else 1 - won_up
        if 0.55 <= fav_price <= 0.88:
            res_mkt.append((fav_price, fav_win))
        # favorite confirmed by model underpricing (the model-confirmed-favorite gate: p_model>price)
        pmod_fav = pmod if fav_up else 1 - pmod
        if 0.55 <= fav_price <= 0.88 and pmod_fav - fav_price >= 0.10:
            res_model.append((fav_price, fav_win))
    def report(name, rows):
        if not rows:
            print(f"    {name}: aucun trade")
            return
        ev = sum((w - p - fee(p)) for p, w in rows) / len(rows)
        wr = sum(w for _, w in rows) / len(rows)
        gross = sum((w - p) for p, w in rows) / len(rows)
        print(f"    {name}: n={len(rows)}  win={wr:.3f}  EV brut/part={gross:+.4f}  "
              f"EV net frais/part={ev:+.4f}")
    print(f"\n[C] Biais longshot — acheter le favori 0.55-0.88, tenir au règlement (taker)")
    report("favori (marché seul)      ", res_mkt)
    report("favori + modèle>prix+0.10 ", res_model)


# ---------------------------------------------------------------- D ---
def exp_d(recs, klines, margin=0.24, min_p=0.62,
          place_off=90, expire_off=225, fill_haircut=0.7, through=0.0, spot_lag=0):
    """tao maker sim on the real tape. Rest a bid at p_model-margin on the
    model-favored side; fill when a SELL of that outcome prints <= bid; hold
    to settlement; zero fees."""
    recs = [r for r in recs if r.get("tape")]      # non-empty tape only
    placed = filled = wins = 0
    pnl_total = 0.0
    fills = []
    from collections import defaultdict
    per_day = defaultdict(lambda: [0, 0.0])        # day -> [filled, pnl]
    for r in recs:
        t = r["window_start"] + place_off
        spot = spot_at(klines, t - spot_lag)
        if spot is None:
            continue
        sig = ewma_vol(klines, t - spot_lag)
        pmod = model_p_up(spot, r["binance_open"], sig, (r["window_end"] - t) / 60)
        direction = "Up" if pmod >= 0.5 else "Down"
        p_dir = pmod if direction == "Up" else 1 - pmod
        if p_dir < min_p:
            continue
        bid = round(p_dir - margin, 2)
        if bid <= 0.04 or bid >= 0.90:
            continue
        placed += 1
        size = round(30.0 / bid, 2)
        # fills from the real tape: SELL of `direction` printing through the bid
        remaining = size
        got = 0.0
        for tr in sorted(r["tape"], key=lambda x: x["t"]):
            if tr["t"] < t or tr["t"] > r["window_start"] + expire_off:
                continue
            if tr["outcome"] != direction or tr["side"] != "SELL":
                continue
            if tr["price"] <= bid - through:
                take = min(remaining, tr["size"])
                got += take
                remaining -= take
                if remaining <= 0:
                    break
        got *= fill_haircut
        if got <= 0:
            continue
        filled += 1
        won = (direction == "Up") == r["up_won"]
        wins += won
        pnl = got * ((1.0 if won else 0.0) - bid)     # maker, zero fees
        pnl_total += pnl
        fills.append((bid, won, pnl))
        d = per_day[r["window_start"] // 86400]
        d[0] += 1
        d[1] += pnl
    print(f"\n[D] tao maker sim sur la VRAIE tape  (margin={margin}, min_p={min_p}, "
          f"haircut={fill_haircut})")
    print(f"    placés={placed}  remplis={filled} ({100*filled/max(placed,1):.0f}% des placés)  "
          f"wins={wins}/{filled} ({100*wins/max(filled,1):.0f}%)")
    if fills:
        avg_bid = sum(b for b, _, _ in fills) / len(fills)
        print(f"    P(win|rempli)={wins/max(filled,1):.3f}  bid moyen={avg_bid:.3f}  "
              f"(rentable si P(win|rempli) > bid)")
        print(f"    PnL total={pnl_total:+.2f}  par rempli={pnl_total/max(filled,1):+.3f}  "
              f"par placé={pnl_total/max(placed,1):+.3f}")
        if len(per_day) > 1:
            import datetime
            days = " | ".join(
                f"{datetime.datetime.utcfromtimestamp(d*86400).strftime('%m-%d')}:"
                f"{v[1]:+.0f}({v[0]})" for d, v in sorted(per_day.items()))
            print(f"    par jour: {days}")
    return pnl_total, placed, filled


def fav_at(track, t):
    p = price_at(track, t)
    if p is None:
        return None, None
    return (p, True) if p >= 0.5 else (1 - p, False)


# ---------------------------------------------------------------- E ---
def exp_e(recs, klines=None, t_off=120, stop=0.20, tp=0.18, require_model=0.0):
    """Cost of churn: buy favorite, then HOLD vs kabbalah-style exits
    (stop -0.20 / take-profit +0.18) simulated on the price track. With
    require_model>0, gate on the model-confirmed favorite (the model-confirmed-favorite signal)."""
    fee = lambda p: FEE_RATE * p * (1 - p)
    hold, churn = [], []
    for r in recs:
        t = r["window_start"] + t_off
        p0, fav_up = fav_at(r["price_track"], t)
        if p0 is None or not (0.55 <= p0 <= 0.88):
            continue
        if require_model > 0 and klines is not None:
            spot = spot_at(klines, t)
            if spot is None:
                continue
            pmod = model_p_up(spot, r["binance_open"], ewma_vol(klines, t),
                              (r["window_end"] - t) / 60)
            pmod_fav = pmod if fav_up else 1 - pmod
            if pmod_fav - p0 < require_model:
                continue
        won = (1 if r["up_won"] else 0) if fav_up else (0 if r["up_won"] else 1)
        hold.append((won - p0) - fee(p0))             # entry fee, settle free
        # walk forward on the favorite's mid
        exited = None
        for ts, pu in r["price_track"]:
            if ts <= t:
                continue
            mid = pu if fav_up else 1 - pu
            if mid <= p0 - stop:
                exited = (mid - p0) - fee(p0) - fee(mid); break      # stop + exit fee
            if mid >= p0 + tp:
                exited = (mid - p0) - fee(p0) - fee(mid); break      # TP + exit fee
        churn.append(exited if exited is not None else (won - p0) - fee(p0))
    eh = sum(hold) / len(hold)
    ec = sum(churn) / len(churn)
    tag = f"confirmé-modèle>+{require_model}" if require_model > 0 else "naïf"
    print(f"\n[E] Coût du churn — favori 0.55-0.88 [{tag}] (n={len(hold)})")
    print(f"    HOLD jusqu'au règlement : EV/part = {eh:+.4f}")
    print(f"    avec sorties (stop/TP)  : EV/part = {ec:+.4f}   -> le churn coûte "
          f"{eh-ec:+.4f}/part")


def regime_split(recs, klines, spread_cost=0.015):
    """Favorite edge per calendar day — does it survive across regimes?
    Buys the favorite at mid + a conservative spread cost (you pay the ask)."""
    fee = lambda p: FEE_RATE * p * (1 - p)
    from collections import defaultdict
    days = defaultdict(list)
    for r in recs:
        days[r["window_start"] // 86400].append(r)
    print(f"\n[Régimes] favori 0.55-0.88 tenu, par JOUR (achat au mid +{spread_cost} spread)")
    print(f"    jour        n_fen  favori_n  win    EV_net   Brier_marché  baseRateUp")
    for d in sorted(days):
        chunk = days[d]
        rows, pairs, ups = [], [], 0
        for r in chunk:
            t = r["window_start"] + 120
            p0, fav_up = fav_at(r["price_track"], t)
            pu = price_at(r["price_track"], t)
            ups += r["up_won"]
            if pu is not None:
                pairs.append((pu, 1 if r["up_won"] else 0))
            if p0 is None or not (0.55 <= p0 <= 0.88):
                continue
            won = (1 if r["up_won"] else 0) if fav_up else (0 if r["up_won"] else 1)
            rows.append((p0, won))
        if not rows:
            continue
        ev = sum((w - p - spread_cost - fee(p)) for p, w in rows) / len(rows)
        wr = sum(w for _, w in rows) / len(rows)
        bm = brier(pairs) if pairs else float("nan")
        import datetime
        day = datetime.datetime.utcfromtimestamp(d * 86400).strftime("%m-%d")
        print(f"    {day}      {len(chunk):4d}    {len(rows):4d}    {wr:.3f}  {ev:+.4f}   "
              f"{bm:.3f}        {ups/len(chunk):.3f}")


def exp_f(recs, klines, spread_cost=0.015, t_off=120, spot_lag=0):
    """Pure directional model edge (the premise of the falsified directional-model strategies — see FINDINGS): buy the
    MODEL-favored side at market when |model-market| > thresh, hold.

    spot_lag ages the model's spot by N seconds. The market quote used is the
    last print <= t; if the edge only exists when the model's spot is FRESHER
    than that quote (spot_lag=0) and dies once aged, it was a timestamp
    lookahead artifact, not tradeable alpha."""
    fee = lambda p: FEE_RATE * p * (1 - p)
    tag = f"spot_lag={spot_lag}s" if spot_lag else "spot frais (lag=0)"
    print(f"\n[F] Alpha directionnelle du modèle [{tag}] (parier le côté modèle, tenir)")
    print(f"    seuil |p_model-mid|   n   win   EV_net/part")
    rows_by_thr = {0.05: [], 0.10: [], 0.15: [], 0.20: []}
    for r in recs:
        t = r["window_start"] + t_off
        mid_up = price_at(r["price_track"], t)
        spot = spot_at(klines, t - spot_lag)
        if mid_up is None or spot is None:
            continue
        pmod = model_p_up(spot, r["binance_open"], ewma_vol(klines, t - spot_lag),
                          (r["window_end"] - t) / 60)
        direction_up = pmod >= mid_up           # model says Up underpriced
        gap = abs(pmod - mid_up)
        buy_price = mid_up if direction_up else 1 - mid_up
        if not (0.05 <= buy_price <= 0.95):
            continue
        won = (1 if r["up_won"] else 0) if direction_up else (0 if r["up_won"] else 1)
        for thr in rows_by_thr:
            if gap >= thr:
                rows_by_thr[thr].append((buy_price, won))
    for thr in sorted(rows_by_thr):
        rows = rows_by_thr[thr]
        if not rows:
            continue
        ev = sum((w - p - spread_cost - fee(p)) for p, w in rows) / len(rows)
        wr = sum(w for _, w in rows) / len(rows)
        print(f"    >{thr:.2f}              {len(rows):4d}  {wr:.3f}  {ev:+.4f}")


def exp_model_regime(recs, klines, edge_min=0.10, spread_cost=0.015, spot_lag=0):
    """Per-day robustness of the MODEL-CONFIRMED favorite (model > price+edge)."""
    fee = lambda p: FEE_RATE * p * (1 - p)
    from collections import defaultdict
    import datetime
    days = defaultdict(list)
    for r in recs:
        days[r["window_start"] // 86400].append(r)
    print(f"\n[Régimes-modèle] favori 0.55-0.88 ET modèle>prix+{edge_min}, tenu, par JOUR")
    print(f"    jour        n_trades  win    EV_net")
    allrows = []
    for d in sorted(days):
        rows = []
        for r in days[d]:
            t = r["window_start"] + 120
            p0, fav_up = fav_at(r["price_track"], t)
            if p0 is None or not (0.55 <= p0 <= 0.88):
                continue
            spot = spot_at(klines, t - spot_lag)
            if spot is None:
                continue
            sig = ewma_vol(klines, t - spot_lag)
            pmod = model_p_up(spot, r["binance_open"], sig, (r["window_end"] - t) / 60)
            pmod_fav = pmod if fav_up else 1 - pmod
            if pmod_fav - p0 < edge_min:
                continue
            won = (1 if r["up_won"] else 0) if fav_up else (0 if r["up_won"] else 1)
            rows.append((p0, won))
        allrows += rows
        if not rows:
            print(f"    {datetime.datetime.utcfromtimestamp(d*86400).strftime('%m-%d')}      "
                  f"  0")
            continue
        ev = sum((w - p - spread_cost - fee(p)) for p, w in rows) / len(rows)
        wr = sum(w for _, w in rows) / len(rows)
        day = datetime.datetime.utcfromtimestamp(d * 86400).strftime("%m-%d")
        print(f"    {day}      {len(rows):4d}     {wr:.3f}  {ev:+.4f}")
    if allrows:
        ev = sum((w - p - spread_cost - fee(p)) for p, w in allrows) / len(allrows)
        print(f"    TOTAL         {len(allrows):4d}     "
              f"{sum(w for _,w in allrows)/len(allrows):.3f}  {ev:+.4f}")


def exp_i(recs, min_into=60, max_before_end=20, side=None):
    """Transaction-level calibration on the REAL tape (lookahead-free,
    execution-true). For every actual trade at price p, did that outcome win?
    If realized win-rate > p + taker fee in a band, BUYING that band at the
    real traded price is +EV. side='BUY' uses only ask-hits = the real cost to
    a taker buyer. No model, no stale 1/min quote."""
    fee = lambda p: FEE_RATE * p * (1 - p)
    recs = [r for r in recs if r.get("tape")]
    from collections import defaultdict
    buckets = defaultdict(lambda: [0, 0, 0.0])
    for r in recs:
        ws, we = r["window_start"], r["window_end"]
        for tr in r["tape"]:
            if tr["t"] < ws + min_into or tr["t"] > we - max_before_end:
                continue
            if side and tr["side"] != side:
                continue
            p = tr["price"]
            if not (0.03 <= p <= 0.97):
                continue
            won = (tr["outcome"] == "Up") == r["up_won"]
            b = buckets[min(18, int(p * 20))]
            b[0] += 1; b[1] += won; b[2] += p
    print(f"\n[I] Calibration trades réels (tape, {min_into}s<t<fin-{max_before_end}s"
          f"{', side='+side if side else ''})")
    print(f"    bande    n_trades  prix_moy  win_réel   EV_achat_net")
    for b in sorted(buckets):
        n, wins, sp = buckets[b]
        if n < 50:
            continue
        ap = sp / n
        wr = wins / n
        ev = wr - ap - fee(ap)
        flag = "  <== +EV" if ev > 0.01 else (" <== -EV" if ev < -0.01 else "")
        print(f"    {b/20:.2f}-{(b/20+0.05):.2f}  {n:6d}   {ap:.3f}    {wr:.3f}    {ev:+.4f}{flag}")


def exp_j(recs, t_off=150, lo=0.55, hi=0.72, hold=True):
    """HONEST favorite test: ONE trade per window (no volume-weighting), at a
    real executable ask from the tape, held to settlement. Removes the
    surge-over-sampling bias of exp_i. Lookahead-free, execution-true."""
    fee = lambda p: FEE_RATE * p * (1 - p)
    recs = [r for r in recs if r.get("tape")]
    import datetime
    from collections import defaultdict
    byd = defaultdict(lambda: [0, 0, 0.0])      # n, wins, pnl
    for r in recs:
        ws, we = r["window_start"], r["window_end"]
        t = ws + t_off
        # current level = last trade price <= t
        prev = [tr for tr in r["tape"] if tr["t"] <= t]
        if not prev:
            continue
        cur = max(prev, key=lambda x: x["t"])
        cur_up = cur["price"] if cur["outcome"] == "Up" else 1 - cur["price"]
        fav_up = cur_up >= 0.5
        fav_out = "Up" if fav_up else "Down"
        # real executable ask = earliest BUY of the favorite in [t, t+15]
        asks = [tr["price"] for tr in r["tape"]
                if t <= tr["t"] <= t + 15 and tr["side"] == "BUY" and tr["outcome"] == fav_out]
        if not asks:
            continue
        ask = sorted(asks)[len(asks) // 2]           # median executable ask
        if not (lo <= ask <= hi):
            continue
        won = (fav_out == "Up") == r["up_won"]
        pnl = (1.0 if won else 0.0) - ask - fee(ask)
        d = byd[ws // 86400]
        d[0] += 1; d[1] += won; d[2] += pnl
    print(f"\n[J] Favori HONNÊTE — 1 trade/fenêtre à t=ws+{t_off}s, ask réel, bande {lo}-{hi}")
    print(f"    jour     n    win    EV/part   total")
    tn = tw = tp = 0
    for d in sorted(byd):
        n, w, p = byd[d]
        tn += n; tw += w; tp += p
        day = datetime.datetime.utcfromtimestamp(d * 86400).strftime("%m-%d")
        print(f"    {day}  {n:4d}  {w/max(n,1):.3f}  {p/max(n,1):+.4f}  {p:+.2f}")
    print(f"    TOTAL {tn:5d}  {tw/max(tn,1):.3f}  {tp/max(tn,1):+.4f}  {tp:+.2f}")


def exp_i_perday(recs, lo=0.55, hi=0.90, min_into=60, max_before_end=20, side="BUY"):
    """Per-day robustness of buying favorites in [lo,hi] at the real ask."""
    fee = lambda p: FEE_RATE * p * (1 - p)
    recs = [r for r in recs if r.get("tape")]
    import datetime
    from collections import defaultdict
    byd = defaultdict(lambda: [0, 0.0])
    for r in recs:
        ws, we = r["window_start"], r["window_end"]
        d = ws // 86400
        for tr in r["tape"]:
            if tr["t"] < ws + min_into or tr["t"] > we - max_before_end:
                continue
            if side and tr["side"] != side:
                continue
            p = tr["price"]
            if not (lo <= p <= hi):
                continue
            won = (tr["outcome"] == "Up") == r["up_won"]
            byd[d][0] += 1
            byd[d][1] += won - p - fee(p)
    print(f"\n[I-jour] acheter favori {lo}-{hi} au vrai ask (side={side}), EV net par jour")
    for d in sorted(byd):
        n, ev = byd[d]
        day = datetime.datetime.utcfromtimestamp(d * 86400).strftime("%m-%d")
        print(f"    {day}  n={n:6d}  EV/part={ev/max(n,1):+.4f}  total={ev:+.1f}")


def exp_h(recs, before_end=(90, 60, 45, 30), spread_cost=0.015):
    """Late-window favorite — LOOKAHEAD-FREE (market price only, no model/spot).
    Near expiry the outcome is mostly decided; do strong favorites stay
    UNDERpriced (longshot bias)? Buy the market favorite at its price (taker +
    spread + fee), hold to settlement. Bucket by price, report calibration+EV."""
    fee = lambda p: FEE_RATE * p * (1 - p)
    import datetime
    from collections import defaultdict
    for be in before_end:
        buckets = defaultdict(list)        # price-band -> [(p, won, day)]
        for r in recs:
            t = r["window_end"] - be
            p = price_at(r["price_track"], t)
            if p is None:
                continue
            fav_up = p >= 0.5
            fp = p if fav_up else 1 - p
            won = (1 if r["up_won"] else 0) if fav_up else (0 if r["up_won"] else 1)
            b = min(9, int(fp * 10))
            buckets[b].append((fp, won, r["window_start"] // 86400))
        print(f"\n[H] Favori en fin de fenêtre, T-{be}s (prix-only, sans lookahead)")
        print(f"    bande   n    prix_moy  win    EV_net/part")
        for b in sorted(buckets):
            rows = buckets[b]
            if len(rows) < 10:
                continue
            ap = sum(p for p, _, _ in rows) / len(rows)
            wr = sum(w for _, w, _ in rows) / len(rows)
            ev = sum((w - p - spread_cost - fee(p)) for p, w, _ in rows) / len(rows)
            flag = "  <== +EV" if ev > 0.01 else ""
            print(f"    {b/10:.1f}-{(b/10+0.1):.1f}  {len(rows):4d}  {ap:.3f}    {wr:.3f}  {ev:+.4f}{flag}")
        # robustness per day for the strong-favorite band (>=0.85)
        strong = [(p, w, d) for bb in buckets.values() for p, w, d in bb if p >= 0.85]
        if strong:
            byd = defaultdict(list)
            for p, w, d in strong:
                byd[d].append((p, w))
            days = " ".join(
                f"{datetime.datetime.utcfromtimestamp(d*86400).strftime('%m-%d')}:"
                f"{sum((w-p-spread_cost-fee(p)) for p,w in rs)/len(rs):+.3f}"
                for d, rs in sorted(byd.items()))
            tot = sum((w-p-spread_cost-fee(p)) for p, w in [(p, w) for p, w, _ in strong]) / len(strong)
            print(f"    favori≥0.85 EV/jour: {days}  | TOTAL {tot:+.4f} (n={len(strong)})")


def exp_g(recs, delta=0.04, place_off=90, expire_off=240, haircut=0.7, n_shares=50.0):
    """Delta-neutral maker spread capture (the only un-falsified structural idea).
    Rest BUY Up @ mid-δ and BUY Down @ (1-mid)-δ — together they cost 1-2δ, so
    every matched share locks 2δ at settlement, zero fees. Risk: only one side
    fills, leaving directional exposure. Fills come from the REAL tape."""
    recs = [r for r in recs if r.get("tape")]
    from collections import defaultdict
    per_day = defaultdict(lambda: [0.0, 0, 0])    # pnl, both-fill, one-fill
    pnl_total = matched_tot = exposed_tot = 0.0
    for r in recs:
        t = r["window_start"] + place_off
        mid = price_at(r["price_track"], t)
        if mid is None:
            continue
        bu, bd = round(mid - delta, 2), round((1 - mid) - delta, 2)
        if not (0.04 < bu < 0.96 and 0.04 < bd < 0.96):
            continue
        fu = fd = 0.0
        for tr in sorted(r["tape"], key=lambda x: x["t"]):
            if tr["t"] < t or tr["t"] > r["window_start"] + expire_off or tr["side"] != "SELL":
                continue
            if tr["outcome"] == "Up" and tr["price"] <= bu and fu < n_shares:
                fu += min(n_shares - fu, tr["size"])
            elif tr["outcome"] == "Down" and tr["price"] <= bd and fd < n_shares:
                fd += min(n_shares - fd, tr["size"])
        fu *= haircut; fd *= haircut
        matched = min(fu, fd)
        locked = matched * (1 - bu - bd)              # risk-free, both sides held
        # leftover one-sided exposure -> directional, held to settlement
        exp_up = fu - matched; exp_dn = fd - matched
        up_won = r["up_won"]
        directional = (exp_up * ((1.0 if up_won else 0.0) - bu)
                       + exp_dn * ((0.0 if up_won else 1.0) - bd))
        pnl = locked + directional
        pnl_total += pnl; matched_tot += matched; exposed_tot += exp_up + exp_dn
        d = per_day[r["window_start"] // 86400]
        d[0] += pnl
        d[1] += 1 if (fu > 0 and fd > 0) else 0
        d[2] += 1 if ((fu > 0) ^ (fd > 0)) else 0
    import datetime
    print(f"\n[G] Maker delta-neutre (δ={delta}, spread bloqué={2*delta:.2f}/part appariée)")
    print(f"    PnL total={pnl_total:+.1f}  parts appariées={matched_tot:.0f}  "
          f"exposition résiduelle={exposed_tot:.0f} parts")
    days = " | ".join(f"{datetime.datetime.utcfromtimestamp(d*86400).strftime('%m-%d')}:"
                      f"{v[0]:+.0f}(2c{v[1]}/1c{v[2]})" for d, v in sorted(per_day.items()))
    print(f"    par jour [2c=2côtés remplis,1c=1côté]: {days}")


def main():
    recs = load()
    span_h = (recs[-1]["window_start"] - recs[0]["window_start"]) / 3600
    print(f"Dataset: {len(recs)} fenêtres 5m sur ~{span_h:.1f} h")
    lo = min(r["window_start"] for r in recs) - 95 * 60
    hi = max(r["window_end"] for r in recs) + 120
    print("(refetch klines pour spot/vol intra-fenêtre...)")
    klines = fetch_klines(lo, hi)
    exp_a(recs)
    exp_b(recs)
    exp_c(recs, klines)
    exp_e(recs)
    exp_e(recs, klines, require_model=0.10)   # the model-confirmed-favorite signal: hold vs churn
    regime_split(recs, klines)
    # staleness of the prices-history quote we compare the model against
    gaps = []
    for r in recs:
        t = r["window_start"] + 120
        tq = max((ts for ts, _ in r["price_track"] if ts <= t), default=None)
        if tq is not None:
            gaps.append(t - tq)
    gaps.sort()
    print(f"\n[Diag] retard du dernier prix marché vs t=ws+120 : "
          f"médian={gaps[len(gaps)//2]:.0f}s  moyen={sum(gaps)/len(gaps):.0f}s "
          f"(track ~1/min -> le modèle 'spot frais' peut voir 30-60s d'avance)")
    for lag in (0, 30, 60, 120):
        exp_f(recs, klines, spot_lag=lag)
    print("\n===== TEST D'ÉQUITÉ : spot frais (lag=0, lookahead) vs spot retardé 60s "
          "(tradeable) =====")
    print("\n-- favori confirmé-modèle --")
    exp_model_regime(recs, klines, spot_lag=0)
    exp_model_regime(recs, klines, spot_lag=60)
    # tao is NOT quote-staleness-exposed (it fills on the real forward tape and
    # holds to settlement), so lag=0 is already legitimate. Its honest test is
    # the per-day regime breakdown below.
    print("\n-- tao maker (lag=0, légitime) — voir le détail par jour --")
    for m in (0.12, 0.18, 0.24):
        exp_d(recs, klines, margin=m)
    print("\n===== IDÉE NON-FALSIFIÉE : market-making delta-neutre =====")
    for dlt in (0.02, 0.04, 0.06):
        exp_g(recs, delta=dlt)


if __name__ == "__main__":
    main()
