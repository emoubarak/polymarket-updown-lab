#!/usr/bin/env python3
"""Retroactively re-size paper crypto runs under the weighted (10 %-of-capital,
book-depth-capped) model from a $100 start — so accumulated paper data reflects the
2026-06-26 sizing change instead of the old flat-$25/$1000 model.

WHY THIS IS EXACT (no historical order books needed): every settled window's net P&L
is LINEAR in stake (payout, cost and fee all scale with the number of shares — see
paper.fee_for / MultiBroker.settle). So re-sizing a window = multiplying its recorded
shares, fee and P&L by a single factor `scale = new_stake / old_notional`, keeping the
original blended fill PRICE and WIN/LOSS outcome. The per-coin bet_max is, by
construction, the depth absorbable at +2c, and the new stakes start tiny (~$10 at
$100×10 %), so the original fill price still holds at the new size.

Windows never overlap (zlead holds <=1 position to settlement), so we replay them
chronologically, compounding a bankroll from START_CAPITAL: at each window the new
stake is staking.weighted_clip(bankroll, WEIGHT_PCT, COIN_BET_MAX[coin]) — the SAME
helper the real pilot and the live paper engine use.

Size-invariant metrics (win-rate, avg price, EV/$) are UNCHANGED; only the dollars
move. Rewrites journal.csv, equity.csv and state.json in place; keeps a one-time
*.orig backup and re-runs from it (idempotent).

    python3 tools/recompute_sizing.py state_zlead_btc_15m state_zlead_eth_15m ...
    python3 tools/recompute_sizing.py --dry-run state_*          # print, write nothing
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pmlab.presets import COIN_BET_MAX, WEIGHT_PCT, START_CAPITAL
from pmlab.staking import weighted_clip

ENTRY_KINDS = ("BUY", "BUY+", "FILL_BUY")          # taker / top-up / maker fill
CLOSE_KINDS = ("SETTLE_WIN", "SETTLE_LOSS")        # window resolution (zlead never sells)
NON_CRYPTO = ("state_events", "state_copy", "live_state")


def base_kind(kind: str) -> str:
    """'BUY[favorite]' -> 'BUY', 'SETTLE_WIN' -> 'SETTLE_WIN'."""
    return kind.split("[", 1)[0]


def coin_of(dirname: str) -> str | None:
    for seg in dirname.split("_"):
        if seg in COIN_BET_MAX:
            return seg
    return None


def group_windows(rows: list[dict]) -> list[dict]:
    """Group rows into windows by slug, ordered by first appearance (= chronological,
    since windows don't overlap). Each window: ordered row indices, entry notional/shares,
    a close (won) or None (still open)."""
    order: list[str] = []
    win: dict[str, dict] = {}
    for i, r in enumerate(rows):
        slug = r["slug"]
        bk = base_kind(r["kind"])
        if slug not in win:
            win[slug] = {"slug": slug, "idxs": [], "notional": 0.0, "shares": 0.0,
                         "won": None, "closed": False}
            order.append(slug)
        w = win[slug]
        w["idxs"].append(i)
        if bk in ENTRY_KINDS:
            w["notional"] += float(r["shares"]) * float(r["price"])
            w["shares"] += float(r["shares"])
        elif bk in CLOSE_KINDS:
            w["won"] = bk.endswith("WIN")
            w["closed"] = True
        elif bk in ("SELL", "FILL_SELL"):
            raise ValueError(f"non-settle close ({r['kind']}) — not a zlead run, refusing")
    return [win[s] for s in order]


def recompute_dir(d: Path, cap: float, dry: bool) -> dict | None:
    jpath = d / "journal.csv"
    if not jpath.exists():
        print(f"  {d.name}: no journal.csv — skip")
        return None
    # idempotent: always recompute from the pristine ORIGINAL
    orig = d / "journal.csv.orig"
    src = orig if orig.exists() else jpath
    with src.open() as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    if not rows:
        # runner ran but never traded (header-only journal) — still REBASE the state from
        # $1000 to $100 (no trades = bankroll stays at the start), else it shows old basis.
        state = _load_state(d)
        if state is not None and not dry:
            _backup_write_json(d / "state.json",
                               _rescaled_state(state, START_CAPITAL, 0.0, 0.0, 0, 0, None))
            _backup_write(d / "equity.csv", "")
        print(f"  {d.name:30s} coin={coin_of(d.name)}  n=0 (no trades) -> rebased ${START_CAPITAL:.0f}"
              f"{'  [dry]' if dry else ''}")
        return {"n": 0, "win_rate": 0.0, "realized": 0.0, "cash": START_CAPITAL}

    windows = group_windows(rows)
    overrides: dict[int, dict] = {}     # row idx -> {shares, fee, pnl, cash}
    equity: list[tuple[int, float]] = []
    cash = START_CAPITAL
    realized = fees = 0.0
    n_trades = n_wins = 0
    min_clip = min(5.0, cap)
    open_scale = None

    for w in windows:
        entered = w["notional"] > 1e-9
        if not entered:                 # maker posted+cancelled, never filled — leave as-is
            for i in w["idxs"]:
                overrides[i] = {"shares": float(rows[i]["shares"]),
                                "fee": _f(rows[i].get("fee")), "pnl": _f(rows[i].get("pnl")),
                                "cash": round(cash, 2)}
            continue
        capital = cash
        new_stake = weighted_clip(capital, WEIGHT_PCT, cap, min_clip) if capital > 0.5 else 0.0
        scale = new_stake / w["notional"] if new_stake > 0 else 0.0
        for i in w["idxs"]:
            r = rows[i]
            bk = base_kind(r["kind"])
            nsh = float(r["shares"]) * scale
            # FEE = 0: the 0.07 taker fee is a phantom — flagged in metadata but NEVER
            # charged on-chain (verified 2026-06-26 from raw tx receipts; feeds.taker_rate
            # now 0). The original journals deducted it, so rebuilding fee-free removes that
            # phantom drag and makes paper P&L match the real (no-fee) economics.
            nfee = 0.0
            npnl = _f(r.get("pnl"))
            if bk in ENTRY_KINDS:
                cash -= nsh * float(r["price"]) + nfee
                fees += nfee
                npnl = 0.0
            elif bk in CLOSE_KINDS:
                # fee-free window P&L = payout − scaled entry notional (NOT the original
                # recorded pnl, which baked in the phantom entry fee — keeping cash and
                # realized consistent now that nfee=0).
                npnl = nsh * (1.0 if w["won"] else 0.0) - scale * w["notional"]
                cash += nsh * (1.0 if w["won"] else 0.0)   # payout
                realized += npnl
                n_trades += 1
                n_wins += 1 if w["won"] else 0
                equity.append((int(r["ts"]), round(cash, 2)))
            else:                       # REST_BUY / CANCEL / EXPIRE — cosmetic, no cash
                npnl = 0.0
            overrides[i] = {"shares": nsh, "fee": nfee, "pnl": npnl, "cash": round(cash, 2)}
        if not w["closed"]:
            open_scale = scale          # the trailing open position (rescale state below)

    # ---- write journal ----
    out_rows = []
    for i, r in enumerate(rows):
        o = overrides[i]
        out_rows.append({
            "ts": r["ts"], "kind": r["kind"], "slug": r["slug"], "direction": r["direction"],
            "shares": f"{o['shares']:.4f}", "price": f"{float(r['price']):.4f}",
            "pnl": f"{o['pnl']:+.4f}", "fee": f"{o['fee']:.4f}", "cash": f"{o['cash']:.2f}"})

    state = _load_state(d)
    old_real = state.get("realized_pnl") if state else None
    new_state = _rescaled_state(state, cash, realized, fees, n_trades, n_wins, open_scale)
    win_rate = (n_wins / n_trades) if n_trades else 0.0

    if not dry:
        if not orig.exists():
            jpath.replace(orig)                          # keep pristine original once
        _write_csv(jpath, fields, out_rows)
        _backup_write(d / "equity.csv", "\n".join(f"{t},{v:.2f}" for t, v in equity) + ("\n" if equity else ""))
        if new_state is not None:
            _backup_write_json(d / "state.json", new_state)

    print(f"  {d.name:30s} coin={coin_of(d.name)} cap=${cap:.0f}  "
          f"n={n_trades} win={win_rate:.1%}  realized {old_real if old_real is not None else '—'} "
          f"-> {realized:+.2f}  cash ${cash:.2f}{'  [dry]' if dry else ''}")
    return {"n": n_trades, "win_rate": win_rate, "realized": realized, "cash": cash}


# ----------------------------------------------------------------- helpers ---
def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _load_state(d: Path) -> dict | None:
    sorig = d / "state.json.orig"
    sp = sorig if sorig.exists() else d / "state.json"
    if not sp.exists():
        return None
    try:
        return json.loads(sp.read_text())
    except (OSError, ValueError):
        return None


def _rescaled_state(state, cash, realized, fees, n_trades, n_wins, open_scale):
    """Rebuild state.json: $100 base, recomputed cash/realized, open positions/orders
    rescaled by the trailing open window's factor (their token_id/window_end kept)."""
    if state is None:
        return None
    s = dict(state)
    s["cash"] = round(cash, 2)
    s["initial"] = START_CAPITAL
    s["realized_pnl"] = round(realized, 2)
    s["fees_paid"] = round(fees, 4)
    s["n_trades"] = n_trades
    s["n_wins"] = n_wins
    sc = open_scale if open_scale is not None else 0.0
    for p in s.get("positions", []):           # MultiBroker schema
        p["shares"] = round(p.get("shares", 0.0) * sc, 4)
        p["entry_fee"] = round(p.get("entry_fee", 0.0) * sc, 4)
    for o in s.get("orders", []):
        o["shares"] = round(o.get("shares", 0.0) * sc, 4)
    if "position" in s and s["position"]:      # legacy single-broker schema
        s["position"]["shares"] = round(s["position"].get("shares", 0.0) * sc, 4)
        s["position"]["entry_fee"] = round(s["position"].get("entry_fee", 0.0) * sc, 4)
    return s


def _write_csv(path: Path, fields, rows):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def _backup_write(path: Path, text: str):
    orig = path.with_suffix(path.suffix + ".orig")
    if path.exists() and not orig.exists():
        path.replace(orig)
    path.write_text(text)


def _backup_write_json(path: Path, obj):
    orig = path.with_suffix(path.suffix + ".orig")
    if path.exists() and not orig.exists():
        path.replace(orig)
    path.write_text(json.dumps(obj, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", type=Path, help="crypto state_*/ directories")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    a = ap.parse_args()
    done = 0
    for d in a.dirs:
        if not d.is_dir():
            continue
        if any(d.name.startswith(p) for p in NON_CRYPTO):
            print(f"  {d.name}: not a crypto run — skip")
            continue
        coin = coin_of(d.name)
        if coin is None:
            print(f"  {d.name}: no coin in name — skip")
            continue
        cap = COIN_BET_MAX[coin]
        try:
            if recompute_dir(d, cap, a.dry_run):
                done += 1
        except Exception as e:
            print(f"  {d.name}: ERROR {type(e).__name__}: {e} — skip")
    print(f"\n{done} run(s) recomputed{' (dry-run)' if a.dry_run else ''}.")


if __name__ == "__main__":
    main()
