#!/usr/bin/env bash
# Launch the trade-flow shadow (pure logging, ZERO money). Guard matches the PYTHON module pattern.
cd ~/pmlab || exit 1
if pgrep -af "[p]olygurdjieff.flow_shadow" >/dev/null; then exit 0; fi
echo "=== launch $(date -u +%H:%M:%S)UTC ===" >> flow_shadow.log
setsid nohup python3 -u -m pmlab.flow_shadow >> flow_shadow.log 2>&1 < /dev/null &
echo "launched pid $!"
