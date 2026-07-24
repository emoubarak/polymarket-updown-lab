#!/bin/bash
set -e
cd ~/rebate; source ~/.poly_env_bbe3
now=$(date +%s); wait=$(( 300 - now % 300 - 8 )); [ $wait -lt 0 ] && wait=$(( wait + 300 ))
echo "waiting ${wait}s..."; sleep $wait
STRATEGY=sellonly SPLIT_PAIRS=40 QUOTE_SIZE=5 MAX_POSITION=8 MAX_OPEN_NOTIONAL=30 \
  PNL_CSV=live_mm.csv REBATE_LIVE=yes \
  timeout --signal=TERM 4300 ./rebate --live 2>&1 | tee live_mm.log
