#!/usr/bin/env bash
# Monthly restore test, run from the Pi 5's crontab on the 1st (see infra/README-backup.md):
#   1. fetch the newest database snapshot and the 3 newest recordings BACK from the Jetson
#      (same restricted key as the backup; rrsync allows reading, never deleting)
#   2. open them in a throwaway container (no network, no secrets): orchestrator/restore_drill.py
#   3. report the result to Alfred (Systems "Restore test"; Telegram alert to admins on failure)
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
KEY="$HOME/.ssh/alfred_backup"
TARGET="${BACKUP_TARGET:-user@100.64.0.6}"
SSH="ssh -i $KEY -o BatchMode=yes -o ConnectTimeout=15"
DRILL="$PWD/data/restore-drill"
report() { docker compose exec -T alfred python -m orchestrator.backup drill "$@" </dev/null; }
fail() { echo "$(date -Is) FAIL: $1"; report fail "$1"; rm -rf "$DRILL"; exit 1; }

echo "$(date -Is) restore test start"
rm -rf "$DRILL" && mkdir -p "$DRILL/storage" || fail "can't create $DRILL"

NEWEST=$(rsync --list-only -e "$SSH" "$TARGET:db/" </dev/null | awk '{print $5}' | grep '^alfred-.*\.db$' | sort | tail -1)
[ -n "$NEWEST" ] || fail "no database snapshot found on the Jetson"
rsync -a -e "$SSH" "$TARGET:db/$NEWEST" "$DRILL/alfred.db" </dev/null || fail "couldn't fetch $NEWEST back"

# The 3 newest recordings, by the date folder in their path.
rsync --list-only -r -e "$SSH" "$TARGET:storage/" </dev/null | awk '{print $5}' | grep '/audio\.wav$' \
  | awk -F/ '{print $2 "\t" $0}' | sort | tail -3 | cut -f2 > "$DRILL/sample.txt"
if [ -s "$DRILL/sample.txt" ]; then
  rsync -a --files-from="$DRILL/sample.txt" -e "$SSH" "$TARGET:storage/" "$DRILL/storage/" </dev/null \
    || fail "couldn't fetch sample recordings back"
  while read -r f; do   # the copy must match the original byte for byte (if the Pi still has it)
    if [ -f "data/storage/$f" ] && ! cmp -s "data/storage/$f" "$DRILL/storage/$f"; then
      fail "backup copy of $f differs from the original"
    fi
  done < "$DRILL/sample.txt"
fi

LIVE=$(docker compose exec -T alfred python -m orchestrator.backup live-counts </dev/null | tail -1)
OUT=$(docker run --rm --network none --user "$(id -u):$(id -g)" -v "$DRILL:/drill" \
  -e DB_PATH=/drill/alfred.db -e STORAGE_ROOT=/drill/storage -e LIVE_COUNTS="$LIVE" \
  -e TELEGRAM_BOT_TOKEN= -e TWILIO_AUTH_TOKEN= -e ANTHROPIC_API_KEY= \
  alfred:latest python -m orchestrator.restore_drill 2>/dev/null | tail -1)
DETAIL=$(printf '%s' "$OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["detail"])' 2>/dev/null) \
  || DETAIL="the drill container crashed: ${OUT:0:200}"
if printf '%s' "$OUT" | grep -q '"ok": true'; then
  report ok "$NEWEST: $DETAIL"
  echo "$(date -Is) restore test passed: $DETAIL"
else
  fail "$NEWEST: $DETAIL"
fi
rm -rf "$DRILL"
