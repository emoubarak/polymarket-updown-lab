#!/bin/bash
# Neutral-named one-shot engine launcher — run by `bash aws_engine_up.sh` so the
# invocation line never contains "webdash.py --pilot-api" (avoids the pgrep self-match
# that fools the in-script up-check / the watchdog dedup). Idempotent: launches the
# AWS pilote engine only if it isn't already up. Sources .live_env (last-wins key).
cd "$HOME/pmlab" || exit 1
if pgrep -f "[w]ebdash.py.*--pilot-api" >/dev/null; then
  echo "engine already up (pid $(pgrep -f '[w]ebdash.py.*--pilot-api' | head -1))"
else
  set -a; . ./.live_env 2>/dev/null; set +a
  setsid nohup python3 -u webdash.py --pilot-api --host 127.0.0.1 --port 8420 \
    >> engine.log 2>&1 < /dev/null &
  echo "launched engine (sig_type=${POLY_SIG_TYPE:-unset})"
fi
sleep 5
if curl -s --max-time 8 http://127.0.0.1:8420/pilot-data >/dev/null; then
  echo "API OK"
else
  echo "API DOWN"
fi
