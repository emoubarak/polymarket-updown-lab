#!/bin/bash
# Morocco→AWS SSH tunnel keeper. Forwards Morocco 127.0.0.1:8430 → AWS 127.0.0.1:8420
# (the pilote-engine control API), so the Morocco display dashboard
# (webdash --pilot-remote http://127.0.0.1:8430) proxies the pilot tab to AWS over a
# PRIVATE, restricted channel (the AWS side permitopen-restricts this key to :8420 only,
# no shell). cron */2 + @reboot.  NOTE: AWS_IP is the box's public IP — if it ever changes
# (stop/start without an Elastic IP), update it here (or move to Tailscale for a stable addr).
AWS_IP="<AWS_IP>"   # renseigner l'IP de l'hôte d'exécution
[ "$(pgrep -fc '[m]orocco_tunnel.sh')" -gt 1 ] && exit 0
if ! pgrep -f "ssh.*8430:127.0.0.1:8420.*${AWS_IP}" >/dev/null; then
  # setsid (not bare -f): detach into a new session so the tunnel survives the parent
  # shell closing — a bare `ssh -f` launched from an interactive session takes SIGHUP and
  # dies on session close (it only survives under cron). setsid makes it robust either way.
  setsid ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 \
      -i "$HOME/.ssh/aws_tunnel" -L 127.0.0.1:8430:127.0.0.1:8420 ubuntu@"$AWS_IP" \
      >/dev/null 2>&1 < /dev/null &
  echo "$(date -u +%FT%TZ) (re)started AWS pilote-API tunnel" >> "$HOME/pmlab/morocco_tunnel.log"
fi