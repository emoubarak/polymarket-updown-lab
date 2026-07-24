#!/usr/bin/env bash
# MEASUREMENT run (not a profit run): WS fast maker on btc-5m (the ONLY market where the rebate
# funds the strategy). Goal = measure our per-fill execution edge `eps` (buy-VWAP vs sell-VWAP) to
# settle the ONE open question: does sub-second WS re-quote stop the pickoff that cost us -2.8c/share
# on the old poll-1s bot? Small block ($80), TIGHT kill (-$6), finer clip (3 sh, > our $1 code floor),
# tight neutrality (max-inv 8 => settle swing <= ~$4 < kill). hftmm.on_fill logs `FILL ...` lines that
# eps_reader.py parses into the verdict. Real money — monitored.
set -euo pipefail
cd ~/pmlab

if pgrep -af "[r]un_hft.py" >/dev/null; then echo "ALREADY RUNNING"; pgrep -af "[r]un_hft.py"; exit 1; fi

export MINT_JS="$HOME/mint/safeops.js"          # Safe self-submit mint (zero quota)
set -a; . ~/.poly_env_bbe3; set +a              # POLY_FUNDER=Safe(0xBbe3) sig_type=2 + owner key
export POLY_LIVE=1 POLY_CONFIRM=I_UNDERSTAND_REAL_MONEY

LOG=hft_measure_btc_5m.log
: > "$LOG"                                       # fresh log each (re)start — the crosses-book spam had bloated it to 442M/6M lines (a run died); on-chain is the P&L truth anyway
echo "=== MEASURE launch $(date -u +%H:%M:%S)UTC | funder=$POLY_FUNDER sig=$POLY_SIG_TYPE ===" >> "$LOG"
setsid nohup .venv-live/bin/python -u run_hft.py \
  --interval 5m --underlying btc --mint-usd 15 --clip 5 \
  --max-inv 6 --skew-k 2.0 --quote-dist 0.01 --beta 0.0 --min-requote-s 0.12 \
  --kill-loss 20 --min-quote 0.05 --max-quote 0.95 --flatten-buf 30 --sync-every 3 \
  --state-dir hft_measure_btc_5m --secs 0 \
  >> "$LOG" 2>&1 < /dev/null &
echo "launched pid $! → $LOG"
