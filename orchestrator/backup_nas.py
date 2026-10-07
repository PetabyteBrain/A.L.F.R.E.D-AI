"""Second off-device backup copy on the NAS (the first goes to the Jetson, infra/backup.sh).

    python -m orchestrator.backup_nas copy <snapshot file name> < .env
    python -m orchestrator.backup_nas fetch           -> newest copy back into /data/restore-drill-nas
                                                         (monthly restore test, infra/restore-drill.sh)

Run by infra/backup.sh right after the Jetson copy, inside the container (SMB via smbprotocol).
Target: NAS_BACKUP_PATH in the personal share (default `.alfred-backups`), reachable only by
alfred-ai and the owner, never through Alfred's own file tools (hidden folder).
  db/       the verified SQLite snapshot          (kept 30 days)
  storage/  recordings + transcripts, only new or changed files are sent  (audio kept 97 days,
            like the Jetson: Alfred deletes his own copy after RECORDING_RETENTION_DAYS)
  config/   .env (from stdin), so a restore needs nothing else
Deletion protection comes from the TrueNAS snapshots of the dataset (SMB users can't touch them),
the counterpart of the Jetson's no-delete key. Result recorded as health row "backup_nas".
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db
from .config import settings

KEEP_DB_DAYS = 30
KEEP_AUDIO_DAYS = 97
MAX_AGE = timedelta(hours=26)
_STATE_KEY = "backup_nas_state"


def _root() -> str:
    rel = settings.nas_backup_path.replace("/", "\\").strip("\\")
    return rf"\\{settings.nas_host}\{settings.nas_share_personal}\{rel}"


def copy(snapshot_name: str, env_bytes: bytes | None) -> str:
    from . import nas
    smb = nas._smb()
    root = _root()
    snap = settings.db_path.parent / "backups" / "nightly" / Path(snapshot_name).name
    if not snap.exists():
        raise RuntimeError(f"snapshot {snap.name} not found")

    def go():
        for sub in ("", "\\db", "\\storage", "\\config"):
            nas._ensure_dir(smb, root + sub)
        with smb.open_file(root + "\\db\\" + snap.name, mode="wb") as f:
            f.write(snap.read_bytes())
        if env_bytes:
            with smb.open_file(root + "\\config\\.env", mode="wb") as f:
                f.write(env_bytes)
        sent = skipped = 0
        base = settings.storage_root
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            rel = str(path.relative_to(base)).replace("/", "\\")
            target = root + "\\storage\\" + rel
            try:
                if smb.stat(target).st_size == path.stat().st_size:
                    skipped += 1
                    continue
            except (FileNotFoundError, OSError):
                pass
            nas._ensure_dir(smb, target.rsplit("\\", 1)[0])
            with smb.open_file(target, mode="wb") as f:
                f.write(path.read_bytes())
            sent += 1
        pruned = _prune(smb, root)
        return f"db {snap.stat().st_size // 1024} KB, {sent} new + {skipped} unchanged files, {pruned} old pruned"
    return nas._call(go)


def _prune(smb, root: str) -> int:
    now = time.time()
    n = 0
    for e in smb.scandir(root + "\\db"):
        if e.name.startswith("alfred-") and now - e.stat().st_mtime > KEEP_DB_DAYS * 86400:
            smb.remove(root + "\\db\\" + e.name)
            n += 1
    for dirpath, _dirs, files in smb.walk(root + "\\storage"):
        for f in files:
            if f == "audio.wav":
                full = dirpath + "\\" + f
                if now - smb.stat(full).st_mtime > KEEP_AUDIO_DAYS * 86400:
                    smb.remove(full)
                    n += 1
    return n


def drill_dir() -> Path:
    return settings.db_path.parent / "restore-drill-nas"


def fetch(samples: int = 3) -> str:
    """Restore test: bring the newest database copy and the newest recordings back from the NAS
    into drill_dir(), and check the recordings match the originals still on the Pi. The drill
    container then opens them exactly like the Jetson copy. Returns the snapshot's name."""
    import shutil
    from . import nas
    smb = nas._smb()
    root = _root()
    dest = drill_dir()
    shutil.rmtree(dest, ignore_errors=True)
    (dest / "storage").mkdir(parents=True)

    def unc(*parts: str) -> str:
        return "\\".join((root, *parts))

    def go():
        snaps = sorted(e.name for e in smb.scandir(unc("db"))
                       if e.name.startswith("alfred-") and e.name.endswith(".db"))
        if not snaps:
            raise RuntimeError("no database copy on the NAS")
        with smb.open_file(unc("db", snaps[-1]), mode="rb") as f:
            (dest / "alfred.db").write_bytes(f.read())
        wavs = []
        prefix = unc("storage") + "\\"
        for dirpath, _dirs, files in smb.walk(unc("storage")):
            if "audio.wav" in files and dirpath.startswith(prefix):
                rel = dirpath[len(prefix):].split("\\")
                wavs.append((rel[1] if len(rel) > 1 else "", rel))      # {user}/{date}/{session}
        for _date, rel in sorted(wavs)[-samples:]:
            with smb.open_file(unc("storage", *rel, "audio.wav"), mode="rb") as f:
                data = f.read()
            local = dest / "storage" / Path(*rel) / "audio.wav"
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
            original = settings.storage_root / Path(*rel) / "audio.wav"
            if original.exists() and original.read_bytes() != data:
                raise RuntimeError(f"NAS copy of {'/'.join(rel)}/audio.wav differs from the original")
        return snaps[-1]
    return nas._call(go)


def report(ok: bool, detail: str) -> None:
    st = db.get_setting(_STATE_KEY) or {}
    if ok:
        st.update(last_ok_at=db.now_iso(), last_detail=detail[:300], last_error=None)
    else:
        st.update(last_error=detail[:300], last_error_at=db.now_iso())
    db.set_setting(_STATE_KEY, st)
    db.log_activity("system" if ok else "error", f"NAS backup {'done' if ok else 'FAILED'}: {detail[:200]}")
    db.set_health("backup_nas", *health_now())
    if not ok:
        from .backup import _alert_admins
        _alert_admins(f"⚠️ Alfred's backup copy to the NAS failed: {detail[:300]} (the Jetson copy is separate)")


def health_now() -> tuple[bool, str]:
    st = db.get_setting(_STATE_KEY) or {}
    if not st.get("last_ok_at"):
        return False, st.get("last_error") or "no NAS backup yet"
    age = datetime.now(timezone.utc) - datetime.fromisoformat(st["last_ok_at"])
    detail = f"last {age.total_seconds() / 3600:.0f} h ago · {st.get('last_detail', '')}"
    if st.get("last_error") and st.get("last_error_at", "") >= st["last_ok_at"]:
        return False, f"failing: {st['last_error']}"
    return age <= MAX_AGE, detail if age <= MAX_AGE else f"overdue: {detail}"


def main(argv: list[str]) -> int:
    if argv[:1] == ["copy"] and len(argv) == 2:
        env = sys.stdin.buffer.read() if not sys.stdin.isatty() else None
        try:
            detail = copy(argv[1], env or None)
        except Exception as e:  # noqa: BLE001 — reported, never crashes the nightly script
            report(False, f"{type(e).__name__}: {e}")
            print(f"FAIL {e}")
            return 1
        report(True, detail)
        print(f"ok {detail}")
        return 0
    if argv[:1] == ["fetch"]:
        try:
            print(f"ok {fetch()}")
            return 0
        except Exception as e:  # noqa: BLE001 — the restore test reports it
            print(f"FAIL {type(e).__name__}: {e}"[:300])
            return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
