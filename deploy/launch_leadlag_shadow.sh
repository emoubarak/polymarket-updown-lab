#!/usr/bin/env bash
# Launch the cross-coin lead-lag REAL-TIME book shadow (pure logging, ZERO money). Guard matches
# the PYTHON module pattern (not this script's name, which contains "leadlag_shadow" → self-match).
cd ~/pmlab || exit 1
if pgrep -af "[p]olygurdjieff.leadlag_shadow" >/dev/null; then exit 0; fi
echo "=== launch $(date -u +%H:%M:%S)UTC ===" >> leadlag_shadow.log
setsid nohup python3 -u -m pmlab.leadlag_shadow >> leadlag_shadow.log 2>&1 < /dev/null &
echo "launched pid $!"
