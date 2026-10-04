"""Call recordings move to the NAS after their first days on the Pi (#39).

New recordings are written locally (a NAS hiccup must never break a call). Once a day, every
recording older than RECORDINGS_TO_NAS_AFTER_DAYS (default 14; 0 = off) has its session folder
(audio.wav + transcript.json) copied to NAS_RECORDINGS_PATH in the personal share (default
`.alfred-recordings`, hidden: only reachable through Alfred), checked by size, and removed from
the Pi. `recordings.file_path` then starts with "nas:". The Jetson backup keeps its copy until its
own 97-day prune (it never deletes), so a recording sits on two devices for the whole retention.

Everything that reads or deletes recordings goes through `read_bytes` / `delete` here.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone

from . import db
from .config import settings
from .storage import recordings

log = logging.getLogger(__name__)

PREFIX = "nas:"
_LAST_RUN_KEY = "recording_archive_last_run"


def is_nas(file_path: str) -> bool:
    return file_path.startswith(PREFIX)


def _unc(rel: str) -> str:
    base = settings.nas_recordings_path.replace("/", "\\").strip("\\")
    rel = rel.replace("/", "\\")
    if ".." in rel.split("\\"):
        raise PermissionError("path escapes the recordings folder")
    return rf"\\{settings.nas_host}\{settings.nas_share_personal}\{base}\{rel}"


def read_bytes(file_path: str) -> bytes:
    if is_nas(file_path):
        from . import nas
        smb = nas._smb()

        def go():
            with smb.open_file(_unc(file_path[len(PREFIX):]), mode="rb") as f:
                return f.read()
        return nas._call(go)
    return recordings.resolve_safe(file_path).read_bytes()


def delete(file_path: str) -> None:
    if is_nas(file_path):
        from . import nas
        smb = nas._smb()

        def go():
            try:
                smb.remove(_unc(file_path[len(PREFIX):]))
            except FileNotFoundError:
                pass
        nas._call(go)
        return
    recordings.resolve_safe(file_path).unlink(missing_ok=True)


def archive_old(now: datetime | None = None) -> int:
    """Move recordings older than the threshold to the NAS. Returns how many moved."""
    from . import nas
    days = settings.recordings_to_nas_after_days
    if days <= 0 or not nas.enabled():
        return 0
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    rows = db.query("SELECT id, file_path FROM recordings WHERE deleted_at IS NULL AND file_path NOT LIKE 'nas:%' "
                    "AND created_at < ?", (cutoff,))
    if not rows:
        return 0
    smb = nas._smb()
    moved = 0
    for r in rows:
        try:
            audio = recordings.resolve_safe(r["file_path"])
        except PermissionError:
            continue
        folder = audio.parent
        if not folder.is_dir():
            continue
        rel_folder = str(folder.relative_to(recordings.root())).replace("\\", "/")

        def go(folder=folder, rel_folder=rel_folder):
            nas._ensure_dir(smb, _unc(rel_folder))
            for f in folder.iterdir():
                if f.is_file():
                    target = _unc(f"{rel_folder}/{f.name}")
                    with smb.open_file(target, mode="wb") as out:
                        out.write(f.read_bytes())
                    if smb.stat(target).st_size != f.stat().st_size:
                        raise OSError(f"size check failed for {f.name}")
        try:
            nas._call(go)
        except Exception:  # noqa: BLE001 — stays local, tried again tomorrow
            log.exception("Archiving recording %s to the NAS failed", r["id"])
            continue
        db.execute("UPDATE recordings SET file_path = ? WHERE id = ?",
                   (PREFIX + rel_folder + "/" + audio.name, r["id"]))
        shutil.rmtree(folder, ignore_errors=True)
        moved += 1
    if moved:
        db.log_activity("system", f"Recordings: moved {moved} call recording(s) older than {days} days to the NAS")
    return moved


def erase_user(user_id: int) -> None:
    """Data deletion: remove the person's archived recordings folder on the NAS."""
    from . import nas
    if not nas.enabled():
        return
    smb = nas._smb()

    def go():
        from smbclient import shutil as smb_shutil
        try:
            smb_shutil.rmtree(_unc(str(int(user_id))))
        except FileNotFoundError:
            pass
    nas._call(go)


def daily() -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if db.get_setting(_LAST_RUN_KEY) == today:
        return
    archive_old()
    db.set_setting(_LAST_RUN_KEY, today)
