#!/bin/bash
cd ~/rebate; source ~/.poly_env_bbe3
now=$(date +%s); wait=$(( 300 - now % 300 - 8 )); [ $wait -lt 0 ] && wait=$(( wait + 300 ))
sleep $wait
STRATEGY=sellonly SPLIT_PAIRS=30 QUOTE_SIZE=5 MAX_POSITION=4 MAX_OPEN_NOTIONAL=30   HALF_SPREAD=0.02 FLATTEN_T2=25 PNL_CSV=live_fix.csv REBATE_LIVE=yes   timeout --signal=TERM 2500 ./rebate --live 2>&1 | tee live_fix.log
