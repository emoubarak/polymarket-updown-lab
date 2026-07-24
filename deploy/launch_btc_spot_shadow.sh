#!/usr/bin/env bash
# Launch the spot->BTC-Poly latency probe (needs .venv-live for websocket). ZERO money.
cd ~/pmlab || exit 1
if pgrep -af "[p]olygurdjieff.btc_spot_shadow" >/dev/null; then exit 0; fi
echo "=== launch $(date -u +%H:%M:%S)UTC ===" >> btc_spot_shadow.log
setsid nohup .venv-live/bin/python -u -m pmlab.btc_spot_shadow >> btc_spot_shadow.log 2>&1 < /dev/null &
echo "launched pid $!"
