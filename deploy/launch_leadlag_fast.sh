#!/usr/bin/env bash
# Launch the high-frequency btc->doge lead-lag probe (pure logging, ZERO money). Guard matches the
# PYTHON module pattern (not this script's name, which contains "leadlag_fast" → self-match).
cd ~/pmlab || exit 1
if pgrep -af "[p]olygurdjieff.leadlag_fast" >/dev/null; then exit 0; fi
echo "=== launch $(date -u +%H:%M:%S)UTC ===" >> leadlag_fast.log
setsid nohup python3 -u -m pmlab.leadlag_fast >> leadlag_fast.log 2>&1 < /dev/null &
echo "launched pid $!"
