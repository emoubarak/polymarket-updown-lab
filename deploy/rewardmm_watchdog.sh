#!/usr/bin/env bash
# Watchdog for the std0 rewards-MM PILOT (eth-5m on the Safe). Restarts it ONLY if the process died
# (box reboot / crash). Safe because the hardened bot re-syncs inventory + P&L from on-chain on every
# start (no desync). It never overrides the stop-loss: a self-KILL (mark<=-kill_loss) leaves the
# process ALIVE-but-inert (not dead), so this won't relaunch over it. Does NOT reset state.
# cron: */2 * * * * bash ~/rewardmm_watchdog.sh >> ~/rewardmm_watchdog.log 2>&1   (+ @reboot)
if pgrep -f "[r]un_rewardmm.py.*rewardmm_safe" >/dev/null; then
  exit 0
fi
echo "$(date -u +%H:%M:%S)UTC pilot dead → relaunch"
bash "$HOME/launch_rewardmm_safe.sh"
