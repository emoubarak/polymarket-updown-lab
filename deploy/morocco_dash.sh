#!/bin/bash
# Paper dashboard launcher (single source). Serves the SPA + paper/events/copy.
# The watchdog calls this (passing its
# COINS/VARIANTS/FRAMES matrix as env, so there's ONE source for the strat list); you
# can also run it directly. Idempotent: pgrep-guarded, so a re-run never double-launches.
cd "$HOME/pmlab" || exit 1
COINS="${COINS:-btc eth sol xrp doge bnb}"
VARIANTS="${VARIANTS:-zlead zleadx zleadn zleada zleadp}"
FRAMES="${FRAMES:-5m 15m}"
DSTRATS=""
for v in $VARIANTS; do for c in $COINS; do for f in $FRAMES; do
  DSTRATS="$DSTRATS --strat ${v}-${c}-${f}:state_${v}_${c}_${f}:${v}_${c}_${f}.log"
done; done; done
pgrep -f "[w]ebdash.py" > /dev/null || setsid nohup python3 -u webdash.py --port 8420 \
  $DSTRATS \
  --estrat "events-broad:state_events_broad:events_broad.log" \
  --estrat "events-fast:state_events_fast:events_fast.log" \
  --estrat "events-slow:state_events_slow:events_slow.log" \
  --cstrat "copy-coinman2:state_copy_coinman2:copy_coinman2.log" \
  --cstrat "copy-06dc:state_copy_06dc:copy_06dc.log" \
  --cstrat "copy-king:state_copy_king:copy_king.log" \
  >> webdash.log 2>&1 < /dev/null &