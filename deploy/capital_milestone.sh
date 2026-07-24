#!/bin/bash
# Hourly cron wrapper for the $500 capital-milestone reminder. Sources .live_env for the
# TELEGRAM_* creds (cron doesn't), then runs the one-shot check. Runs on the AWS engine host.
cd "$HOME/pmlab" || exit 1
set -a; . ./.live_env 2>/dev/null; set +a
python3 deploy/capital_milestone.py >> capital_milestone.log 2>&1