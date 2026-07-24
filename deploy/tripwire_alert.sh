#!/bin/bash
# Cron wrapper for the real-pilot tripwire alert. Sources .live_env so the Telegram
# creds (TELEGRAM_BOT_TOKEN/CHAT_ID) are present under cron (which has no shell profile),
# then runs the stdlib checker. Logs one summary line per real pilot to tripwire.log.
# Cron: */30 * * * * /bin/bash $HOME/pmlab/tripwire_alert.sh >> $HOME/pmlab/tripwire.log 2>&1
cd ~/pmlab || exit 1
[ -f .live_env ] && set -a && . ./.live_env && set +a
echo "=== $(date '+%F %T %Z') ==="
python3 tripwire_alert.py
