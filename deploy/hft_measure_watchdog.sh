#!/usr/bin/env bash
# Watchdog for the btc-5m MM measurement run (run_hft.py on the Safe). Restarts it ONLY if the process
# truly DIED (Cloudflare 5xx / OOM / crash) — a self-KILL leaves the process ALIVE-but-inert (killed=True),
# so pgrep sees it and this won't relaunch over a real stop-loss. Never resets state; on-chain P&L is the
# truth (research/std0/pnl_tracker.py). cron: */2 * * * * bash ~/pmlab/deploy/hft_measure_watchdog.sh   (+ @reboot)
cd "$HOME/pmlab" || exit 0
if pgrep -f "[r]un_hft.py" >/dev/null; then exit 0; fi     # alive (or inert-killed) → nothing to do
echo "$(date -u +%H:%M:%S)UTC watchdog: run_hft DOWN → relaunch" >> hft_measure_wd.log
bash deploy/launch_hft_measure.sh >> hft_measure_wd.log 2>&1
