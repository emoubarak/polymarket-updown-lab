#!/bin/bash
cd ~/rebate; source ~/.poly_env_bbe3
now=$(date +%s); wait=$(( 300 - now % 300 - 8 )); [ $wait -lt 0 ] && wait=$(( wait + 300 ))
sleep $wait
STRATEGY=neutral MAX_POSITION=6 QUOTE_SIZE=5 MAX_OPEN_NOTIONAL=40   HALF_SPREAD=0.005 LAMBDA=0.05 REQUOTE_INTERVAL_MS=200 FILL_COOLDOWN_MS=0   MERGE_THRESHOLD=6 PNL_CSV=live_dn.csv REBATE_LIVE=yes   timeout --signal=TERM 2100 ./rebate --live 2>&1 | tee live_dn.log
