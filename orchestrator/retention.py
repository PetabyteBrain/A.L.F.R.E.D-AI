"""Recording retention (FADP data minimisation).

Call audio is deleted RECORDING_RETENTION_DAYS after the call (default 90; 0 = keep forever).
The transcript (DB + transcript.json) stays until someone deletes it; the recording row stays
with `deleted_at` set, so the Archive can say why there is no audio any more.

Backups: the Jetson's own crontab deletes audio.wav files a week after this limit
(infra/README-backup.md), because the Pi's backup key may never delete anything there.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import db
from .config import settings

log = logging.getLogger(__name__)

_LAST_RUN_KEY = "retention_last_run"


def purge(now: datetime | None = None) -> int:
    """Delete audio older than the limit; returns how many recordings were removed."""
    days = settings.recording_retention_days
    if days <= 0:
        return 0
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    rows = db.query("SELECT id, file_path FROM recordings WHERE deleted_at IS NULL AND created_at < ?", (cutoff,))
    from . import recording_archive
    for r in rows:
        try:
            recording_archive.delete(r["file_path"])       # on the Pi or already on the NAS
        except Exception:  # noqa: BLE001
            log.exception("Could not delete recording %s", r["id"])
            continue
        db.execute("UPDATE recordings SET deleted_at = ? WHERE id = ?", (db.now_iso(), r["id"]))
    if rows:
        db.log_activity("system", f"Retention: deleted the audio of {len(rows)} call recording(s) older than {days} days")
    return len(rows)


def daily() -> None:
    """Called from the health loop; runs at most once a day."""
    today = datetime.now(timezone.utc).date().isoformat()
    if db.get_setting(_LAST_RUN_KEY) == today:
        return
    purge()
    db.set_setting(_LAST_RUN_KEY, today)
