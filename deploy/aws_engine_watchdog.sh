#!/bin/bash
# AWS pilote-engine keeper — ensures the real-money engine (webdash --pilot-api) is up.
# The engine OWNS the pilots: it holds the key, supervises run_live (revives crashes),
# and serves the control API the Morocco dashboard proxies to. This script only keeps the
# ENGINE alive (the engine keeps the pilots alive); cron */2 + @reboot. Sources .live_env
# so the engine arms with the right sig_type/funder (last-wins — the file has duplicates).
cd "$HOME/pmlab" || exit 1
[ "$(pgrep -fc '[a]ws_engine_watchdog.sh')" -gt 1 ] && exit 0   # dedup overlapping crons
if ! pgrep -f "[w]ebdash.py.*--pilot-api" >/dev/null; then
  set -a; . ./.live_env 2>/dev/null; set +a
  setsid nohup python3 -u webdash.py --pilot-api --host 127.0.0.1 --port 8420 \
    >> engine.log 2>&1 < /dev/null &
  echo "$(date -u +%FT%TZ) (re)launched pilote engine (sig_type=$POLY_SIG_TYPE)" >> aws_engine_watchdog.log
fi