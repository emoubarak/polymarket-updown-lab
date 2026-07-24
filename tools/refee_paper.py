#!/usr/bin/env python3
"""Re-apply the 0.07 taker fee to accumulated paper ledgers — the inverse of defee_paper.

The 0.07 crypto taker fee IS charged on-chain — re-verified 2026-06-27 from raw tx receipts
(wallet 0xd630…, NEW pUSD relayer 0xe111…): the wallet debits exactly usdcSize = size×price +
fee at ENTRY, and the fee = 0.07×p×(1−p)×shares is forwarded in pUSD to collector 0x115f48dc
on BOTH 5m AND 15m (never at settlement). The 2026-06-26 defee_paper run (which zeroed the fee
as a "phantom") was the error — it tracked USDC.e transfers and MISSED the pUSD fee leg. This
rebuilds each ledger WITH the fee, reproducing EXACTLY what paper.MultiBroker records:
  entry  → fee = 0.07×p×(1−p)×shares ; cash -= shares×price + fee ; row pnl=0, row fee=fee
  settle → pnl = payout − notional − window_fee ; cash += payout ; row fee=0 (settle never pays)

SAFE BY DESIGN (mirrors defee_paper):
  - ALWAYS reads the CURRENT journal.csv (never a frozen backup) -> never loses trades appended
    since. Keeps every recorded share/price (sizing UNCHANGED — only the fee is re-applied).
  - one-time *.nofee backup for reversibility (never read back for the rebuild).
  - idempotent: fee is recomputed from shares×price each run and cash from `initial`, so
    re-running reproduces the same ledger.
  - VERIFIABLE: for trades predating the defee, the rebuilt rows must match the frozen
    journal.csv.prefee (the original WITH-fee ledger) — a built-in correctness gate (--check).

    python3 tools/refee_paper.py state_zlead_btc_15m state_zleadmk_eth_15m ...
    python3 tools/refee_paper.py --dry-run state_*
    python3 tools/refee_paper.py --check state_*      # dry-run + diff vs *.prefee
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path

FEE_RATE = 0.07
ENTRY = ("BUY", "BUY+", "FILL_BUY")        # all reduce cash + count in window notional
TAKER_ENTRY = ("BUY", "BUY+")              # ONLY taker crossings pay the fee
# FILL_BUY = a resting MAKER order got filled — makers never pay (maker_fee_rate=0), so it
# enters the position (cash/notional) at fee 0. Applying the taker fee to it would be wrong
# (it inflated zleadmk ledgers in the first refee pass; the .prefee backups show fee=0 there).
CLOSE = ("SETTLE_WIN", "SETTLE_LOSS")
NON_CRYPTO = ("state_events", "state_copy", "live_state")


def base(k: str) -> str:
    return k.split("[", 1)[0]


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def fee_for(shares: float, price: float) -> float:
    return FEE_RATE * price * (1.0 - price) * shares


def refee_dir(d: Path, dry: bool) -> dict | None:
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

    cash = initial
    realized = 0.0
    n = nw = 0
    equity: list[tuple[int, float]] = []
    over: dict[int, dict] = {}
    rowfee: dict[int, float] = {}
    fee_applied = 0.0
    acc: dict[str, dict] = {}          # per-window notional+fee, accumulated as entries arrive
    # CHRONOLOGICAL single pass (journal.csv is append-only → already time-sorted): reproduces
    # the engine's EXACT cash trajectory. A group-by-window pass would re-order the intermediate
    # cash column whenever windows interleave (the ~$10-15 artifact defee left vs the engine).
    for i, r in enumerate(rows):
        bk = base(r["kind"])
        a = acc.setdefault(r["slug"], {"notional": 0.0, "fee": 0.0})
        npnl = 0.0
        if bk in ENTRY:
            sh, px = _f(r["shares"]), _f(r["price"])
            fee = fee_for(sh, px) if bk in TAKER_ENTRY else 0.0   # makers (FILL_BUY) pay 0
            cash -= sh * px + fee                          # entry debits price + fee
            a["notional"] += sh * px
            a["fee"] += fee
            fee_applied += fee
            rowfee[i] = fee
        elif bk in CLOSE or bk == "SELL":                  # MultiBroker._close: settle OR taker bail
            sh = _f(r["shares"])
            if bk == "SELL":                               # bail crosses to sell → proceeds net a taker fee
                px = _f(r["price"])
                sell_fee = fee_for(sh, px)
                cashflow = sh * px - sell_fee
                fee_applied += sell_fee
                rowfee[i] = sell_fee
            else:                                          # settlement: payout = sh×$1 if won, never a fee
                cashflow = sh * (1.0 if bk.endswith("WIN") else 0.0)
                rowfee[i] = 0.0
            npnl = cashflow - a["notional"] - a["fee"]     # pnl vs cost basis (entry notional + entry fee)
            cash += cashflow
            realized += npnl
            n += 1
            if npnl > 0:                                   # engine counts a win by pnl>0 (post-fee), not "won"
                nw += 1
            equity.append((int(_f(r["ts"])), round(cash, 2)))
        else:
            rowfee[i] = 0.0                                # REST_BUY/CANCEL/EXPIRE
        over[i] = {"pnl": npnl, "cash": round(cash, 2)}

    out_rows = []
    for i, r in enumerate(rows):
        o = over[i]
        row = dict(r)
        row["pnl"] = f"{o['pnl']:+.4f}"
        if "fee" in row:
            row["fee"] = f"{rowfee[i]:.4f}"
        row["cash"] = f"{o['cash']:.2f}"
        row["shares"] = f"{_f(r['shares']):.4f}"
        row["price"] = f"{_f(r['price']):.4f}"
        out_rows.append(row)

    new_state = _refeed_state(state, cash, realized, n, nw, fee_applied)
    print(f"  {d.name:30s} n={n} win={(nw/n if n else 0):.1%}  fee applied ${fee_applied:.4f}  "
          f"realized -> {realized:+.2f}  cash ${cash:.2f}{'  [dry]' if dry else ''}")
    if dry:
        return {"n": n, "fee_applied": fee_applied, "realized": realized, "cash": cash,
                "out_rows": out_rows, "fields": fields, "equity": equity}

    # one-time pristine (fee-free) backup, then write IN PLACE (current journal is the source)
    if not (d / "journal.csv.nofee").exists():
        (d / "journal.csv").replace(d / "journal.csv.nofee")
    _write_csv(d / "journal.csv", fields, out_rows)
    if (d / "equity.csv").exists() and not (d / "equity.csv.nofee").exists():
        (d / "equity.csv").replace(d / "equity.csv.nofee")
    (d / "equity.csv").write_text(
        "\n".join(f"{t},{v:.2f}" for t, v in equity) + ("\n" if equity else ""))
    if new_state is not None:
        if not (d / "state.json.nofee").exists():
            (d / "state.json").replace(d / "state.json.nofee")
        (d / "state.json").write_text(json.dumps(new_state, indent=2))
    return {"n": n, "fee_applied": fee_applied, "realized": realized, "cash": cash}


def _load_state(d: Path) -> dict | None:
    sp = d / "state.json.nofee"
    sp = sp if sp.exists() else d / "state.json"
    if not sp.exists():
        return None
    try:
        return json.loads(sp.read_text())
    except (OSError, ValueError):
        return None


def _refeed_state(state, cash, realized, n, nw, fee_applied):
    if state is None:
        return None
    s = dict(state)
    s["cash"] = round(cash, 2)
    s["realized_pnl"] = round(realized, 2)
    s["fees_paid"] = round(fee_applied, 4)
    s["n_trades"] = n
    s["n_wins"] = nw
    for p in s.get("positions", []):
        p["entry_fee"] = round(fee_for(_f(p.get("shares")), _f(p.get("avg_price"))), 4)
    if s.get("position"):
        p = s["position"]
        p["entry_fee"] = round(fee_for(_f(p.get("shares")), _f(p.get("avg_price"))), 4)
    return s


def _write_csv(path: Path, fields, rows):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def _check_vs_prefee(d: Path, out_rows, fields) -> None:
    """Correctness gate: the rebuilt rows must match the frozen WITH-fee journal.csv.prefee
    for every trade that predates the defee (the prefee backup is a prefix of the current
    journal). Structural cols (kind/slug/shares/price) must match EXACTLY; numeric cols
    (pnl/fee/cash) may drift sub-cent (we recompute the fee from the journal's 4-dp rounded
    shares×price, vs the engine's full-precision fill) — report the MAX |drift| so it can be
    bounded, and flag any structural mismatch hard."""
    pf = d / "journal.csv.prefee"
    if not pf.exists():
        print(f"      (no .prefee backup — fresh fee applied, no cross-check) {d.name}")
        return
    with pf.open() as f:
        pre = list(csv.DictReader(f))
    nrows = 0
    maxnum = {"pnl": 0.0, "fee": 0.0, "cash": 0.0}
    for a, b in zip(pre, out_rows):
        for k in ("kind", "slug", "shares", "price"):
            if k in a and k in b and a.get(k) != b.get(k):
                print(f"      ✗ STRUCTURAL mismatch at row {nrows} col '{k}': "
                      f"prefee={a.get(k)!r} refee={b.get(k)!r}")
                return
        for k in maxnum:
            if k in a and k in b:
                maxnum[k] = max(maxnum[k], abs(_f(a.get(k)) - _f(b.get(k))))
        nrows += 1
    drift = ", ".join(f"{k}≤${maxnum[k]:.4f}" for k in ("pnl", "fee", "cash"))
    flag = "✓" if all(v <= 0.01 for v in maxnum.values()) else "⚠"
    print(f"      {flag} {nrows} pre-defee rows: structure EXACT, max drift {drift} "
          f"(+{len(out_rows)-nrows} new rows since defee)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dirs", nargs="+", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="dry-run + diff the rebuild against *.prefee (correctness gate)")
    a = ap.parse_args()
    dry = a.dry_run or a.check
    done = 0
    for d in a.dirs:
        if not d.is_dir() or any(d.name.startswith(p) for p in NON_CRYPTO):
            continue
        try:
            res = refee_dir(d, dry)
            if res:
                done += 1
                if a.check:
                    _check_vs_prefee(d, res["out_rows"], res["fields"])
        except Exception as e:
            print(f"  {d.name}: ERROR {type(e).__name__}: {e} — skip")
    print(f"\n{done} ledger(s) re-feed{' (dry-run)' if dry else ''}.")


if __name__ == "__main__":
    main()
