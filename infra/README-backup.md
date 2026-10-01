# Alfred backups

**What:** every night at 03:30 (Pi 5 crontab) `infra/backup.sh`:

1. takes an online, integrity-checked SQLite snapshot (`python -m orchestrator.backup snapshot`)
   into `data/backups/nightly/` on the Pi (last 3 kept there);
2. copies the snapshot, all recordings (`data/storage/`) and `.env` to the Jetson:
   `/ssd/backups/alfred/{db,storage,config}` (NVMe);
3. reports the result to Alfred: Systems shows "backup" with the age of the last good run
   (red after 26 h), and admins get a Telegram message if a run fails.

**Access:** the Pi uses a dedicated key `~/.ssh/alfred_backup`. On the Jetson it is pinned in
`~/.ssh/authorized_keys` to `command="rrsync -no-del /ssd/backups/alfred",restrict`, so it can
only write into that folder and can never delete or overwrite history elsewhere.

**Retention:** the Jetson prunes database snapshots older than 14 days itself (its own crontab),
so a broken or wiped Pi can't take the history down with it. Recordings are only ever added.
`config/.env` holds Alfred's secrets (mode 600, readable by `user` only).

## Restore (new SD card / new Pi)

```bash
# on the new Pi, after cloning/deploying ~/alfred
rsync -a user@100.64.0.6:/ssd/backups/alfred/storage/ ~/alfred/data/storage/
scp user@100.64.0.6:/ssd/backups/alfred/config/.env ~/alfred/.env && chmod 600 ~/alfred/.env
ls /ssd/backups/alfred/db/            # (on the Jetson) pick the newest alfred-*.db
scp user@100.64.0.6:/ssd/backups/alfred/db/alfred-<newest>.db ~/alfred/data/alfred.db
cd ~/alfred && docker compose up -d --build
```

Run a backup by hand: `~/alfred/infra/backup.sh` (log: `~/alfred/data/backups/backup.log`).
