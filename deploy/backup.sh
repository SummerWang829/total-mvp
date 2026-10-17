#!/usr/bin/env bash
# Daily backup of the test environment (cron runs it as root at 03:30 UTC; see deploy/README.md).
#  1. a consistent database snapshot inside the app container (app/backup.py), kept 14 days
#  2. a copy of the snapshots and a mirror of the recordings on the host disk (/opt/backups)
# The mirror uses --delete on purpose: when retention deletes a participant's recording, the backup copy goes the next night.
# Off-site copy (Tencent COS) needs a bucket and a key from the owner: set COS_BUCKET / COS_PREFIX and coscli below when they exist.
set -euo pipefail
cd "$(dirname "$0")"
DEST=${BACKUP_HOST_DIR:-/opt/backups}
VOL=$(docker volume inspect deploy_appdata --format '{{.Mountpoint}}')
mkdir -p "$DEST/db" "$DEST/media"
docker compose exec -T app python -m app.backup
rsync -a --delete "$VOL/backups/" "$DEST/db/"
if [ -d "$VOL/media" ]; then rsync -a --delete "$VOL/media/" "$DEST/media/"; fi
echo "$(date -u +%FT%TZ) backup ok: $(ls "$DEST/db" | tail -1), media $(du -sh "$DEST/media" | cut -f1)"
if [ -n "${COS_BUCKET:-}" ] && command -v coscli >/dev/null; then
  coscli sync "$DEST/db" "cos://${COS_BUCKET}/${COS_PREFIX:-total}/db" && echo "off-site copy ok"
fi
