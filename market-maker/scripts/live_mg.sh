#!/bin/bash
cd ~/rebate; source ~/.poly_env_bbe3
now=$(date +%s); wait=$(( 300 - now % 300 - 8 )); [ $wait -lt 0 ] && wait=$(( wait + 300 ))
sleep $wait
STRATEGY=sellonly SPLIT_PAIRS=10 QUOTE_SIZE=5 MAX_POSITION=5 MAX_OPEN_NOTIONAL=15   HALF_SPREAD=0.02 GUARD_EDGE=0.015 REQUOTE_INTERVAL_MS=100 REQUOTE_MIN_MOVE=0.001   MARKOUT_GATE=0.3 PNL_CSV=live_mg.csv REBATE_LIVE=yes   timeout --signal=TERM 2700 ./rebate --live 2>&1 | tee live_mg.log
