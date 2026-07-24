#!/usr/bin/env bash
# Launch the std0-competitive WS HFT maker LIVE on the Gnosis Safe. WebSocket book+spot+fills →
# sub-second re-quote (the speed the poll-1s bot lacked). Aggressive neutrality: tight max-inv + strong
# inventory skew to offload the side we get long. Kill -$8 caps downside. Isolated state-dir.
set -euo pipefail
cd ~/pmlab

if pgrep -af "[r]un_hft.py" >/dev/null; then echo "ALREADY RUNNING"; pgrep -af "[r]un_hft.py"; exit 1; fi

export MINT_JS="$HOME/mint/safeops.js"
set -a; . ~/.poly_env_bbe3; set +a
export POLY_LIVE=1 POLY_CONFIRM=I_UNDERSTAND_REAL_MONEY

LOG=hft_eth_5m.log
echo "=== launch $(date -u +%H:%M:%S)UTC | funder=$POLY_FUNDER sig=$POLY_SIG_TYPE ===" >> "$LOG"
# FILL-RATE PROBE: wide band 0.20-0.80 to measure realistic fills (std0-comparable); kill -$8 caps the
# downside (the $100 block carries ~10% imbalance — fine, this run is to COUNT FILLS, not to be neutral).
setsid nohup .venv-live/bin/python -u run_hft.py \
  --interval 5m --underlying eth --mint-usd 50 --clip 5 \
  --max-inv 6 --skew-k 2.0 --quote-dist 0.01 --beta 0.7 --min-requote-s 0.12 \
  --kill-loss 8 --min-quote 0.20 --max-quote 0.80 --flatten-buf 30 --sync-every 3 \
  --state-dir hft_eth_5m --secs 600 \
  >> "$LOG" 2>&1 < /dev/null &
echo "launched pid $! → $LOG"
