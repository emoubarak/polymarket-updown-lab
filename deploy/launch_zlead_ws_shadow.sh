#!/usr/bin/env bash
# Launch the zlead WS-vs-REST z-lead shadow (pure logging, ZERO money). Guard matches the PYTHON
# process pattern (not this script's name, which contains "zlead_ws_shadow" → would self-match).
cd ~/pmlab || exit 1
if pgrep -af "[p]olygurdjieff.zlead_ws_shadow" >/dev/null; then exit 0; fi
echo "=== launch $(date -u +%H:%M:%S)UTC ===" >> zlead_ws_shadow.log
setsid nohup .venv-live/bin/python -u -m pmlab.zlead_ws_shadow >> zlead_ws_shadow.log 2>&1 < /dev/null &
echo "launched pid $!"
