#!/usr/bin/env python3
"""Tripwire alert — Telegram ping when a REAL pilot loses its edge.

THE decisive kill signal for the favorite-longshot harvest (docs/ARMING-MULTICOIN §6,
stop-loss-doesnt-apply): a per-coin tripwire `win-rate <= avg entry price` once the
sample is statistically meaningful (n>=100 settled). Below that edge the asymmetric
1:9 payoff means the pilot is in the "->$0" regime and should be CUT (paused), not
stop-lossed per trade. This runs from cron (deploy/tripwire_alert.sh), reads the SAME
settled-journal metric the dashboard shows (single source), and pings Telegram only on
a state CHANGE (fresh breach, or recovery) + a daily re-ping while still breached — so
it never spams. Stdlib only; reuses pmlab.notify (urllib, never raises).

State: tripwire_state.json {id: {"breached": bool, "last_alert": ts}}.
Args: --test sends a single test message and exits (deploy sanity check).
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

from pmlab.notify import notify, enabled

REGISTRY = Path("pilot_registry.json")
STATE = Path("tripwire_state.json")
MIN_N = 100              # statistical floor: below this, win-rate vs price is noise
REALERT_S = 86400        # re-ping at most once/day while a breach persists


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pilot_metrics(entry: dict) -> tuple[int, float, float, float]:
    """(n_settled, win_rate, avg_price, realized_pnl) over the pilot's OWN market
    family — mirrors webdash._collect_one_pilot so the alert matches the dashboard."""
    cfg = entry.get("config") or {}
    sd = Path(entry.get("state_dir", ""))
    prefix = f"{cfg.get('underlying','btc')}-updown-{cfg.get('interval','15m')}-"
    j = sd / "journal.csv"
    if not j.exists():
        return 0, 0.0, 0.0, 0.0
    settled = []
    with j.open() as fh:
        for t in csv.DictReader(fh):
            if not str(t.get("slug", "")).startswith(prefix):
                continue
            if (t.get("kind") or "").upper().endswith(("WIN", "LOSS")):
                settled.append(t)
    n = len(settled)
    if not n:
        return 0, 0.0, 0.0, 0.0
    wins = sum(1 for t in settled if (t.get("kind") or "").upper().endswith("WIN"))
    prices = [p for p in (_num(t.get("price")) for t in settled) if p is not None]
    pnls = [p for p in (_num(t.get("pnl")) for t in settled) if p is not None]
    win_rate = wins / n
    avg_price = (sum(prices) / len(prices)) if prices else 0.0
    return n, win_rate, avg_price, sum(pnls)


def run() -> None:
    if not REGISTRY.exists():
        print("no pilot_registry.json — nothing to check")
        return
    reg = json.loads(REGISTRY.read_text())
    try:
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
    except (json.JSONDecodeError, OSError):
        state = {}
    now = int(time.time())

    # REAL money only: a pilot actively armed live (mode == "live"). Paused/dry are skipped.
    real = {pid: e for pid, e in reg.items() if e.get("mode") == "live"}
    for pid, entry in real.items():
        n, wr, px, pnl = _pilot_metrics(entry)
        edge_pt = (wr - px) * 100.0
        breached = n >= MIN_N and wr <= px
        prev = state.get(pid, {})
        was = bool(prev.get("breached"))
        print(f"{pid:20} n={n:<4} win={wr*100:5.1f}% px={px*100:5.1f}% "
              f"edge={edge_pt:+.1f}pt pnl={pnl:+.1f} "
              f"{'BREACHED' if breached else ('(<n100)' if n < MIN_N else 'ok')}")

        if breached and (not was or now - int(prev.get("last_alert", 0)) > REALERT_S):
            notify(f"🚨 TRIPWIRE — {pid}\n"
                   f"L'edge a disparu : win {wr*100:.1f}% ≤ prix payé {px*100:.1f}% "
                   f"sur n={n} (marge {edge_pt:+.1f}pt).\n"
                   f"PnL réglé ${pnl:+.1f}. → envisage de COUPER (dashboard ▸ Pause). "
                   f"Pas de stop par trade (l'asymétrie le rend contre-productif).")
            state[pid] = {"breached": True, "last_alert": now}
        elif breached:
            state[pid] = {"breached": True, "last_alert": int(prev.get("last_alert", now))}
        elif was:
            notify(f"✅ Tripwire levé — {pid}\n"
                   f"L'edge est repassé positif : win {wr*100:.1f}% > prix {px*100:.1f}% "
                   f"(marge {edge_pt:+.1f}pt, n={n}).")
            state[pid] = {"breached": False, "last_alert": now}
        else:
            state[pid] = {"breached": False, "last_alert": int(prev.get("last_alert", 0))}

    # forget pilots no longer live (avoids stale recovery pings)
    state = {pid: v for pid, v in state.items() if pid in real}
    STATE.write_text(json.dumps(state))


if __name__ == "__main__":
    if "--test" in sys.argv:
        ok = notify("🔔 Tripwire armé — ce canal recevra une alerte si un pilote réel "
                    "franchit win ≤ prix à n≥100 (perte d'edge → couper). Test OK.")
        print(f"test telegram sent={ok} (enabled={enabled()})")
        sys.exit(0 if ok else 1)
    run()
