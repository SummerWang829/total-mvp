#!/usr/bin/env bash
# Daily backup of the test environment (cron runs it as root at 03:30 UTC; see deploy/README.md).
#  1. a consistent database snapshot inside the app container (app/backup.py), kept 14 days
#  2. a copy of the snapshots and a mirror of the recordings on the host disk (/opt/backups)
# The mirror uses --delete on purpose: when retention deletes a participant's recording, the backup copy goes the next night.
# The off-site copy of the database backup (Tencent COS) is made by app.backup itself when COS_* is set in deploy/.env (bash deploy/set-cos-keys.sh).
set -euo pipefail
cd "$(dirname "$0")"
DEST=${BACKUP_HOST_DIR:-/opt/backups}
VOL=$(docker volume inspect deploy_appdata --format '{{.Mountpoint}}')
mkdir -p "$DEST/db" "$DEST/media"
rc=0
docker compose exec -T app python -m app.backup || rc=$?   # 3 = only the off-site copy failed: still mirror locally, then report it below
rsync -a --delete "$VOL/backups/" "$DEST/db/"
if [ -d "$VOL/media" ]; then rsync -a --delete "$VOL/media/" "$DEST/media/"; fi
if [ "$rc" -ne 0 ]; then echo "$(date -u +%FT%TZ) backup finished with exit code $rc (1 = failed, 3 = off-site copy failed); local copy: $(ls "$DEST/db" | tail -1)"; exit "$rc"; fi
echo "$(date -u +%FT%TZ) backup ok: $(ls "$DEST/db" | tail -1), media $(du -sh "$DEST/media" | cut -f1)"
