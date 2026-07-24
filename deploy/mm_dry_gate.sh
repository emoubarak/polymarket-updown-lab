#!/usr/bin/env bash
# DRY VALIDATION GATE — run the engine DRY against the LIVE book for a few windows and decide GO / NO-GO
# BEFORE arming real money. Zero money. This is the gate that would have caught (offline) the bugs that
# cost −$62: orders not resting (beta crossed the book), imbalance blowing up, a guard invariant tripping.
#
# Usage: bash deploy/mm_dry_gate.sh [coin] [interval] [secs] [band] [maxinv] [skewk] [mint]
#   default: btc 5m 420 0.05,0.95 6 2.0 15   (~1.5 windows)
# RULE: never run run_hft.py live until this prints ✅ GO for the same config.
set -uo pipefail
cd "$HOME/pmlab"
A="${1:-btc} ${2:-5m} ${3:-420} ${4:-0.05,0.95} ${5:-6} ${6:-2.0} ${7:-15}"
echo "=== DRY GATE (paper vs live book): $A ==="
OUT=$(timeout $(( ${3:-420} + 40 )) .venv-live/bin/python -u -m pmlab.hftmm $A 2>&1)
echo "$OUT" | grep -E "PAPER|IMBALANCE|paper P&L|GUARD HALT|=== PAPER"

req=$(echo "$OUT"   | grep -oE "· [0-9]+ requotes" | grep -oE "[0-9]+" | tail -1)
imbp90=$(echo "$OUT"| grep -oE "p90=[0-9.]+sh \([0-9]+%\)" | grep -oE "\([0-9]+%" | tr -dc '0-9' | head -1)
halt=$(echo "$OUT"  | grep -c "GUARD HALT")

echo "--- verdict (req=${req:-0} imb_p90=${imbp90:-?}% halt=${halt:-0}) ---"
go=1
[ "${halt:-0}" -gt 0 ]          && { echo "  ✗ a guard invariant HALTED (a real bug)"; go=0; }
[ "${req:-0}" -lt 20 ]          && { echo "  ✗ engine barely quoted (req<20) — orders not resting? band/beta/clip?"; go=0; }
[ -n "${imbp90:-}" ] && [ "${imbp90:-99}" -gt 25 ] && { echo "  ✗ imbalance p90 ${imbp90}% > 25% — neutrality won't hold"; go=0; }
[ "$go" = 1 ] && echo "  ✅ GO — dry validation passed; safe to arm THIS config" \
             || echo "  ❌ NO-GO — do NOT arm real money; fix the above first"
