#!/usr/bin/env bash
# Nightly Alfred backup, run from the Pi 5's crontab (see infra/README-backup.md):
#   1. verified SQLite snapshot (inside the container)
#   2. snapshot + recordings + .env -> Jetson NVMe over SSH (dedicated key, rrsync -no-del:
#      this key can only write into the backup folder and can never delete there)
#   3. result reported to Alfred (Systems page; Telegram alert on failure)
#   4. the same snapshot + recordings + .env -> NAS (orchestrator/backup_nas.py, health row backup_nas)
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
KEY="$HOME/.ssh/alfred_backup"
# Backup host (user@host of the Jetson/NAS box): from the environment or infra/backup.env (not in git).
[ -f infra/backup.env ] && . infra/backup.env
TARGET="${BACKUP_TARGET:?set BACKUP_TARGET=user@backup-host in infra/backup.env}"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=15"
report() { docker compose exec -T alfred python -m orchestrator.backup report "$@" </dev/null; }
fail() { echo "$(date -Is) FAIL: $1"; report fail "$1"; exit 1; }

echo "$(date -Is) backup start"
SNAP=$(docker compose exec -T alfred python -m orchestrator.backup snapshot </dev/null | tail -1) \
  || fail "snapshot: $SNAP"
NAME=$(basename "$SNAP")
[ -f "data/backups/nightly/$NAME" ] || fail "snapshot file missing ($NAME)"

rsync -a -e "$SSH" "data/backups/nightly/$NAME" "$TARGET:db/" </dev/null || fail "copy of database to $TARGET failed"
rsync -a -e "$SSH" data/storage/ "$TARGET:storage/" </dev/null || fail "copy of recordings to $TARGET failed"
rsync -a --chmod=F600 -e "$SSH" .env "$TARGET:config/" </dev/null || fail "copy of .env to $TARGET failed"

SIZE=$(du -h "data/backups/nightly/$NAME" | cut -f1)
FILES=$(find data/storage -type f | wc -l)
report ok "db $SIZE + $FILES recording files -> Jetson"

# 4. second copy on the NAS (separate health row; a failure here doesn't hide the Jetson result)
NAS=$(docker compose exec -T alfred python -m orchestrator.backup_nas copy "$NAME" < .env | tail -1)
echo "$(date -Is) NAS copy: $NAS"
echo "$(date -Is) backup done: $NAME ($SIZE), $FILES storage files"
