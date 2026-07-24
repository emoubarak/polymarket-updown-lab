#!/usr/bin/env python3
"""Remove the PHANTOM 0.07 taker fee from accumulated paper ledgers.

The fee is flagged in market metadata (feesEnabled=True, rate 0.07) but is NEVER
actually charged on-chain — verified 2026-06-26 by pulling raw CTF Exchange tx
receipts (only the full-set collateral transfers; zero fee transfer; the data-api
`usdcSize` adds it for DISPLAY only). The paper engine deducted it, depressing P&L
by ~0.6%/trade. This rebuilds each ledger FEE-FREE.

vs tools/recompute_sizing.py — DIFFERENT jobs, don't confuse them: recompute_sizing
RE-SIZES a run from the pristine `.orig` (a one-time replay for a sizing-MODEL change, e.g.
flat→weighted); defee_paper REMOVES the phantom fee IN PLACE on the CURRENT journal
(preserving every trade appended since). They share the by-window grouping shape but serve
opposite directions (rebuild-from-origin vs correct-in-place).

SAFE BY DESIGN for a live, append-only journal:
  - ALWAYS reads the CURRENT journal.csv (never a frozen backup) -> never loses
    trades appended since a prior run. Keeps every recorded share/price (sizing
    UNCHANGED — only the fee is removed).
  - one-time *.prefee backup for reversibility (never read back for the rebuild).
  - idempotent: rebuilding a fee-free journal reproduces it (fees already 0).

    python3 tools/defee_paper.py state_zlead_btc_15m state_zleadmk_eth_15m ...
    python3 tools/defee_paper.py --dry-run state_*
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path

ENTRY = ("BUY", "BUY+", "FILL_BUY")
CLOSE = ("SETTLE_WIN", "SETTLE_LOSS")
NON_CRYPTO = ("state_events", "state_copy", "live_state")


def base(k: str) -> str:
    return k.split("[", 1)[0]


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def group(rows: list[dict]) -> list[dict]:
    order: list[str] = []
    win: dict[str, dict] = {}
    for i, r in enumerate(rows):
        s = r["slug"]
        if s not in win:
            win[s] = {"idxs": [], "notional": 0.0, "shares": 0.0, "won": None}
            order.append(s)
        w = win[s]
        w["idxs"].append(i)
        bk = base(r["kind"])
        if bk in ENTRY:
            w["notional"] += _f(r["shares"]) * _f(r["price"])
            w["shares"] += _f(r["shares"])
        elif bk in CLOSE:
            w["won"] = bk.endswith("WIN")
    return [win[s] for s in order]


def defee_dir(d: Path, dry: bool) -> dict | None:
    jp = d / "journal.csv"
    if not jp.exists():
        print(f"  {d.name}: no journal.csv — skip")
        return None
    with jp.open() as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    if not rows:
        print(f"  {d.name}: empty journal — skip")
        return None
    state = _load_state(d)
    initial = (state or {}).get("initial", 100.0)

    wins = group(rows)
    cash = initial
    realized = 0.0
    n = nw = 0
    equity: list[tuple[int, float]] = []
    over: dict[int, dict] = {}
    fee_removed = 0.0
    for w in wins:
        for i in w["idxs"]:
            r = rows[i]
            bk = base(r["kind"])
            fee_removed += _f(r.get("fee"))
            npnl = _f(r.get("pnl"))
            if bk in ENTRY:
                cash -= _f(r["shares"]) * _f(r["price"])      # no fee
                npnl = 0.0
            elif bk in CLOSE:
                payout = _f(r["shares"]) * (1.0 if w["won"] else 0.0)
                npnl = payout - w["notional"]                 # fee-free window P&L
                cash += payout
                realized += npnl
                n += 1
                nw += 1 if w["won"] else 0
                equity.append((int(_f(r["ts"])), round(cash, 2)))
            else:
                npnl = 0.0                                     # REST_BUY/CANCEL/EXPIRE
            over[i] = {"pnl": npnl, "cash": round(cash, 2)}

    out_rows = []
    for i, r in enumerate(rows):
        o = over[i]
        row = dict(r)
        row["pnl"] = f"{o['pnl']:+.4f}"
        if "fee" in row:
            row["fee"] = "0.0000"
        row["cash"] = f"{o['cash']:.2f}"
        row["shares"] = f"{_f(r['shares']):.4f}"
        row["price"] = f"{_f(r['price']):.4f}"
        out_rows.append(row)

    new_state = _defeed_state(state, cash, realized, n, nw)
    print(f"  {d.name:30s} n={n} win={(nw/n if n else 0):.1%}  fee removed ${fee_removed:.4f}  "
          f"realized -> {realized:+.2f}  cash ${cash:.2f}{'  [dry]' if dry else ''}")
    if dry:
        return {"n": n, "fee_removed": fee_removed, "realized": realized, "cash": cash}

    # one-time pristine backup, then write IN PLACE (current journal is the source)
    if not (d / "journal.csv.prefee").exists():
        (d / "journal.csv").replace(d / "journal.csv.prefee")
    _write_csv(d / "journal.csv", fields, out_rows)
    if (d / "equity.csv").exists() and not (d / "equity.csv.prefee").exists():
        (d / "equity.csv").replace(d / "equity.csv.prefee")
    (d / "equity.csv").write_text(
        "\n".join(f"{t},{v:.2f}" for t, v in equity) + ("\n" if equity else ""))
    if new_state is not None:
        if not (d / "state.json.prefee").exists():
            (d / "state.json").replace(d / "state.json.prefee")
        (d / "state.json").write_text(json.dumps(new_state, indent=2))
    return {"n": n, "fee_removed": fee_removed, "realized": realized, "cash": cash}


def _load_state(d: Path) -> dict | None:
    sp = d / "state.json.prefee"
    sp = sp if sp.exists() else d / "state.json"
    if not sp.exists():
        return None
    try:
        return json.loads(sp.read_text())
    except (OSError, ValueError):
        return None


def _defeed_state(state, cash, realized, n, nw):
    if state is None:
        return None
    s = dict(state)
    s["cash"] = round(cash, 2)
    s["realized_pnl"] = round(realized, 2)
    s["fees_paid"] = 0.0
    s["n_trades"] = n
    s["n_wins"] = nw
    for p in s.get("positions", []):
        p["entry_fee"] = 0.0
    if s.get("position"):
        s["position"]["entry_fee"] = 0.0
    return s


def _write_csv(path: Path, fields, rows):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    done = 0
    for d in a.dirs:
        if not d.is_dir() or any(d.name.startswith(p) for p in NON_CRYPTO):
            continue
        try:
            if defee_dir(d, a.dry_run):
                done += 1
        except Exception as e:
            print(f"  {d.name}: ERROR {type(e).__name__}: {e} — skip")
    print(f"\n{done} ledger(s) de-feed{' (dry-run)' if a.dry_run else ''}.")


if __name__ == "__main__":
    main()
