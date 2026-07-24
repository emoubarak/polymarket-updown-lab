#!/bin/bash
# MODEL-CHANGE deploy (fee 0 -> 0.07): re-apply the real taker fee to the live paper ledgers
# IN PLACE, then restart crypto runners + dashboard with the new code. NO reset; backups -> *.nofee.
# Targets ONLY the canonical 48-runner lineup (the brace glob needs the underscores, so the
# old-name zombies state_zlead_doge15m / esoteric brains are excluded).
set -u
cd "$HOME/pmlab" || exit 1

CANON=$(ls -d state_{zlead,zleadmk,zleadx,zleadn}_{btc,eth,sol,xrp,doge,bnb}_{5m,15m} 2>/dev/null)
echo "lineup dirs: $(echo "$CANON" | wc -w)"

# 1) pause cron so the */5 watchdog can't restart runners mid-refee; restore on ANY exit
crontab -l > "$HOME/cron_backup.$$" 2>/dev/null || true
restore_cron(){ crontab "$HOME/cron_backup.$$" 2>/dev/null && echo "cron restored"; }
trap restore_cron EXIT
crontab -r 2>/dev/null || true
echo "cron paused"

# 2) stop ONLY crypto runners (main.py) — events/copy/dashboard left running
pkill -f "[m]ain.py"; sleep 3
echo "crypto runners after pkill: $(pgrep -f '[m]ain.py' | wc -l) (expect 0)"

# 3) re-apply the fee IN PLACE (reads the CURRENT journal, backups -> *.nofee, idempotent)
echo "===== refee ====="
python3 tools/refee_paper.py $CANON

# 4) restart the dashboard with the new webdash.py (fee column back, no add-back)
pkill -f "[w]ebdash.py"; sleep 2

# 5) relaunch everything with the new code (dedup by --state-dir, states PRESERVED)
echo "===== watchdog restart ====="
bash watchdog.sh
sleep 6
echo "runners up: $(pgrep -f '[m]ain.py' | wc -l) (expect 48)   dashboard: $(pgrep -f '[w]ebdash.py' | wc -l) (expect 1)"
# trap restores cron on exit
