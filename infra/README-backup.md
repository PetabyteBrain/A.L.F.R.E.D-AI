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

**Retention:** the Jetson prunes database snapshots older than 14 days and call audio older than
97 days (Alfred deletes its own copy after `RECORDING_RETENTION_DAYS`=90; the backup follows a week
later) with its own crontab, so a broken or wiped Pi can't take the history down with it:

```
15 4 * * * find /ssd/backups/alfred/db -name "alfred-*.db" -mtime +14 -delete
20 4 * * * find /ssd/backups/alfred/storage -name "audio.wav" -mtime +97 -delete
```
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

## Second copy on the NAS

Right after the Jetson copy, `backup.sh` sends the same snapshot, the recordings (only new or
changed files) and `.env` to the NAS: `Alfred_Personal/.alfred-backups/{db,storage,config}`
(`orchestrator/backup_nas.py`, `NAS_BACKUP_PATH`). DB copies are kept 30 days, audio 97 days.
Systems shows it as "Backups · NAS". Protection against deletion comes from the TrueNAS
periodic snapshots of the Alfred_Personal dataset. To restore from the NAS, copy those three
folders back the same way as from the Jetson (any SMB client, e.g. the NAS web shell or a laptop).

## Monthly restore test

`infra/restore-drill.sh` runs on the 1st of each month at 04:30 (Pi crontab):

```
30 4 1 * * /home/user/alfred/infra/restore-drill.sh >> /home/user/alfred/data/backups/restore-drill.log 2>&1
```

It fetches the newest snapshot and the 3 newest recordings back from the Jetson, checks the
recordings match the originals, and opens everything in a throwaway container without network or
secrets (`orchestrator/restore_drill.py`). The result shows on Systems as "Restore test
(monthly)"; admins get a Telegram message if it fails. Run it by hand the same way.

Run a backup by hand: `~/alfred/infra/backup.sh` (log: `~/alfred/data/backups/backup.log`).
