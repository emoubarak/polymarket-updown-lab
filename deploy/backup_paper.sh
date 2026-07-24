#!/bin/bash
# Rolling backup of the paper-trading data — a safety net against a code
# regression (or a bad reset) silently destroying the accumulated runs.
# Cron: 0 */3 * * *  (every 3h). Restorable RAW state, not a lossy summary:
# archive.py is for redeploy-time dashboard snapshots; THIS is disaster
# recovery (tar the real state_* dirs + history, rotate, keep a week).
set -uo pipefail
cd ~/pmlab || exit 1

BACKUP_DIR=~/pmlab_backups
KEEP=56                        # 56 × 3h ≈ 7 jours de fenêtre roulante
mkdir -p "$BACKUP_DIR"

STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$BACKUP_DIR/paper-$STAMP.tar.gz"

# state_* = chaque runner (state.json/journal.csv/equity.csv) ; history/runs.json
# = les snapshots archivés du dashboard. On embarque les deux, on ignore les .log
# transitoires. Si rien à sauver, on n'écrit pas d'archive vide.
shopt -s nullglob
STATES=(state_*)
shopt -u nullglob
if [ ${#STATES[@]} -eq 0 ]; then
  echo "$(date -Is) ⚠ aucun state_* — rien à sauvegarder"
  exit 0
fi

tar czf "$OUT" "${STATES[@]}" $( [ -f history/runs.json ] && echo history/runs.json )

# Rotation : ne garder que les $KEEP archives les plus récentes.
ls -1t "$BACKUP_DIR"/paper-*.tar.gz 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f

echo "$(date -Is) sauvegardé → $OUT ($(du -h "$OUT" | cut -f1)), $(ls -1 "$BACKUP_DIR"/paper-*.tar.gz | wc -l) archives"

# --- COPIE OFF-HOST (vraie reprise sur sinistre) : le backup local est sur le MÊME disque
# que ce qu'il protège — couvre une régression/reset, PAS la mort de l'hôte. Si $BACKUP_REMOTE
# est défini (ex. "<user>@autre-hote:~/poly_backups/" ou une cible rsync/bucket-via-rclone),
# on y pousse la dernière archive ; absent → no-op (rien à configurer pour l'instant). Mettre
# l'export dans ~/pmlab/.live_env (déjà sourcé par le watchdog) ou la crontab.
if [ -n "${BACKUP_REMOTE:-}" ]; then
  if rsync -az --timeout=60 "$OUT" "$BACKUP_REMOTE" 2>/dev/null; then
    echo "$(date -Is) ↗ off-host OK → $BACKUP_REMOTE"
  else
    echo "$(date -Is) ⚠ push off-host ÉCHOUÉ → $BACKUP_REMOTE (à vérifier)"
  fi
fi
