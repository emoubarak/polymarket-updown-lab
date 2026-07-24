#!/usr/bin/env python3
"""One-shot Telegram reminder when the REAL wallet first crosses a capital milestone.

Honors the operator's request: "remind me at $500 to test the maker entry (levier 1)".
Reads the engine's mark-to-market wallet capital (the same /pilot-data the dashboard shows),
fires ONE Telegram on the first crossing (state in capital_milestone_state.json so it never
spams), then stays quiet. Stdlib only; reuses pmlab.notify (never raises).
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # repo root on path (run from anywhere)
from pmlab.notify import notify

STATE = Path("capital_milestone_state.json")
TARGET = 500.0


def run() -> None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8420/pilot-data", timeout=8) as r:
            cap = float(json.load(r).get("wallet", {}).get("capital", 0.0) or 0.0)
    except Exception:
        return                                   # engine briefly unreachable — try next hour
    try:
        state = json.loads(STATE.read_text()) if STATE.exists() else {}
    except (json.JSONDecodeError, OSError):
        state = {}
    if cap >= TARGET and not state.get("alerted_500"):
        notify(f"🎯 Capital réel ${cap:.0f} ≥ ${TARGET:.0f} — RAPPEL : c'est le moment de tester "
               f"l'ENTRÉE MAKER co-localisée (levier 1) sur AWS : armer zleadmk-btc-15m / zleadmk-eth-15m "
               f"en A/B contre leur jumeau taker, et comparer l'edge net (on récupère le demi-spread, "
               f"le seul coût réel du 15m). Cf mémoire two-host-aws-morocco-split.")
        state["alerted_500"] = True
        STATE.write_text(json.dumps(state))
        print(f"alerted at ${cap:.0f}")
    else:
        print(f"capital ${cap:.0f} (target ${TARGET:.0f}, alerted={state.get('alerted_500', False)})")


if __name__ == "__main__":
    run()
