#!/bin/bash
# One-window live test, hard-capped at ~$5 exposure.
# Usage: ./run_live_window.sh
set -e
cd ~/rebate
source ~/.poly_env_bbe3
sudo systemctl stop rebate 2>/dev/null || true

now=$(date +%s)
wait=$(( 300 - now % 300 - 10 ))
[ $wait -lt 0 ] && wait=$(( wait + 300 ))
echo "waiting ${wait}s to start 10s before the next window boundary..."
sleep $wait

QUOTE_SIZE=5 MAX_POSITION=6 MAX_OPEN_NOTIONAL=8 SPLIT_PAIRS=5 PNL_CSV=pnl-live.csv REBATE_LIVE=yes \
  timeout --signal=TERM 308 ./rebate --live 2>&1 | tee live_window.log

echo "=== fills ==="; grep -E "fill|LIQUID|REVERS" live_window.log || echo "(none)"
echo "=== window summary ==="; grep -E "closed|final inventory" live_window.log
