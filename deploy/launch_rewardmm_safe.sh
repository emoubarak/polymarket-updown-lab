#!/usr/bin/env bash
# Replicate std0 LITERALLY: the maker-REBATE harvester on btc-5m (std0's #1 market = 64% of its
# volume). Income = 0.014·p(1−p) per FILLED maker share (NO min-size — rewardsMinSize=50 is vestigial
# for crypto, NOT the Qmin pool). So: mint a churnable block, post tight SMALL two-sided clips near
# 0.5, refresh fast, get FILLED a lot, redeem/merge net-zero at settle. Self-submit mint from the
# Gnosis Safe (safeops.js, zero quota). Isolated state-dir. Real money — monitored manually.
set -euo pipefail
cd ~/pmlab

# refuse to double-run
if pgrep -af "[r]un_rewardmm.py.*rewardmm_safe" >/dev/null; then
  echo "ALREADY RUNNING:"; pgrep -af "[r]un_rewardmm.py.*rewardmm_safe"; exit 1
fi

export MINT_JS="$HOME/mint/safeops.js"      # mint/merge via the Safe (self-submit), not the relayer
set -a; . ~/.poly_env_bbe3; set +a          # POLY_PRIVATE_KEY (owner 0xE8dc) + POLY_FUNDER=Safe + sig_type=2
export POLY_LIVE=1 POLY_CONFIRM=I_UNDERSTAND_REAL_MONEY

# CLEAN-MARKET PROOF run (eth-5m, genuinely neutral per the reverse-engineering) with the std0 execution
# upgrades the naive btc run lacked: SPOT-ANCHOR (re-price on Binance fair-p that leads the CLOB → cut
# pickoff), FAST poll (1s), FINE neutrality (clip 4 vs block 100 = ~4%/fill, max-inv 8 = std0's ~5%),
# TIGHT kill (−$6 caps the downside). Goal: show mark ≈ breakeven over many windows + a MAKER_REBATE lands.
LOG=rewardmm_safe_eth_5m.log
echo "=== launch $(date -u +%H:%M:%S)UTC | MINT_JS=$MINT_JS funder=$POLY_FUNDER sig=$POLY_SIG_TYPE ===" >> "$LOG"
setsid nohup .venv-live/bin/python -u run_rewardmm.py \
  --interval 5m --underlying eth --mint-usd 100 --clip 5 \
  --quote-dist 0.01 --uptime-bids --max-inv 10 --kill-loss 8 \
  --spot-anchor --beta 0.5 \
  --min-quote 0.40 --max-quote 0.60 --recenter-eps 0.01 --flatten-buf 30 \
  --state-dir rewardmm_safe_eth_5m --poll 1 \
  >> "$LOG" 2>&1 < /dev/null &
echo "launched pid $! → $LOG"
