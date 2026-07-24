#!/usr/bin/env python3
"""Archive the current paper-trading run into history/runs.json.

Run this BEFORE resetting state dirs or redeploying a new strategy version:
the snapshot (final equity, trade stats, downsampled curve) survives the
reset and feeds the dashboard's "Historique" tab.

    python3 archive.py --label "v3 frais officiels"
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

DEFAULT_STRATS = {
    "favorite-15m": "state_rubedo",
    "favorite_vol-15m": "state_favorite_vol",
    "favorite_wide-15m": "state_favorite_wide",
    "favorite-5m": "state_rubedo_5m",
    "favorite_vol-5m": "state_favorite_vol_5m",
    "favorite_wide-5m": "state_favorite_wide_5m",
}

HISTORY = Path("history/runs.json")


def snapshot(name: str, state_dir: str, label: str) -> dict | None:
    sd = Path(state_dir)
    sf = sd / "state.json"
    if not sf.exists():
        return None
    state = json.loads(sf.read_text())
    equity, started, ended = [], None, None
    ef = sd / "equity.csv"
    if ef.exists():
        rows = [r.split(",") for r in ef.read_text().strip().splitlines() if r]
        if rows:
            started, ended = int(rows[0][0]), int(rows[-1][0])
            step = max(1, len(rows) // 120)        # keep the curve light
            equity = [[int(a), round(float(b), 2)] for a, b in rows[::step]]
            if equity[-1][0] != ended:
                equity.append([ended, round(float(rows[-1][1]), 2)])
    final = equity[-1][1] if equity else state.get("cash", 0)
    return {
        "strategy": name,
        "label": label,
        "archived_at": int(time.time()),
        "started": started,
        "ended": ended,
        "initial": state.get("initial", 1000),
        "final_equity": final,
        "pnl": round(final - state.get("initial", 1000), 2),
        "realized_pnl": round(state.get("realized_pnl", 0), 2),
        "fees_paid": round(state.get("fees_paid", 0), 2),
        "n_trades": state.get("n_trades", 0),
        "n_wins": state.get("n_wins", 0),
        "equity": equity,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--label", required=True,
                   help="version label for this run (e.g. git short sha + note)")
    p.add_argument("--strat", action="append", default=[],
                   help="name:state_dir (repeatable; default: all five)")
    a = p.parse_args()
    # Default = AUTO-DISCOVER the actual paper states on disk (state_zlead_btc -> zlead-btc).
    # The old hardcoded DEFAULT_STRATS pointed at long-retired dirs, so a bare `archive.py`
    # silently archived NOTHING (and a reset after it lost the real data — a paid lesson).
    if a.strat:
        strats = dict(s.split(":") for s in a.strat)
    else:
        strats = {p.name[6:].replace("_", "-"): p.name
                  for p in sorted(Path(".").glob("state_*")) if (p / "state.json").exists()}
        if not strats:
            print("⚠ no state_* dirs with state.json found — nothing to archive")

    HISTORY.parent.mkdir(exist_ok=True)
    runs = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
    added = 0
    for name, sd in strats.items():
        rec = snapshot(name, sd, a.label)
        if rec is None:
            print(f"  {name}: no state, skipped")
            continue
        runs.append(rec)
        added += 1
        h = (rec["ended"] - rec["started"]) / 3600 if rec["started"] else 0
        print(f"  {name}: pnl {rec['pnl']:+.2f} sur {h:.1f}h "
              f"({rec['n_trades']} trades) archivé")
    HISTORY.write_text(json.dumps(runs))
    print(f"{added} run(s) ajoutés → {HISTORY} ({len(runs)} au total)")


if __name__ == "__main__":
    main()
