"""Nightly backups: consistent SQLite snapshot here, shipped off-device by infra/backup.sh.

    python -m orchestrator.backup snapshot          -> verified copy in /data/backups/nightly, prints its path
    python -m orchestrator.backup report ok "..."   -> records success (Systems shows its age)
    python -m orchestrator.backup report fail "..." -> records failure + Telegram alert to admins

These run in a one-off process (docker compose exec), where the Telegram bot is not running, so
the alert goes straight to the Bot API instead of through telegram_handler.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import db
from .config import settings

KEEP_LOCAL = 3
MAX_AGE = timedelta(hours=26)         # one nightly run missed -> Systems turns red
_STATE_KEY = "backup_state"


def _nightly_dir() -> Path:
    d = settings.db_path.parent / "backups" / "nightly"
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot() -> Path:
    """Online backup (safe while Alfred runs), checked before it counts."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    target = _nightly_dir() / f"alfred-{stamp}.db"
    tmp = target.with_suffix(".tmp")
    src = sqlite3.connect(settings.db_path, timeout=30)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    check = sqlite3.connect(tmp)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0]
        users = check.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        check.close()
    if ok != "ok" or users < 1:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"snapshot failed verification: integrity={ok!r}, users={users}")
    tmp.replace(target)          # same second twice: the newer snapshot wins
    for old in sorted(_nightly_dir().glob("alfred-*.db"))[:-KEEP_LOCAL]:
        old.unlink()
    return target


def state() -> dict:
    row = db.query_one("SELECT value FROM app_settings WHERE key = ?", (_STATE_KEY,))
    return json.loads(row["value"]) if row else {}


def _save(st: dict) -> None:
    db.execute("INSERT INTO app_settings(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
               "value = excluded.value, updated_at = excluded.updated_at", (_STATE_KEY, json.dumps(st), db.now_iso()))


def report(ok: bool, detail: str) -> None:
    st = state()
    now = db.now_iso()
    if ok:
        st.update(last_ok_at=now, last_detail=detail[:300], last_error=None, last_error_at=None)
    else:
        st.update(last_error=detail[:300], last_error_at=now)
    _save(st)
    db.log_activity("system" if ok else "error", f"Backup {'done' if ok else 'FAILED'}: {detail[:200]}")
    db.set_health("backup", *health_now())
    if not ok:
        _alert_admins(f"⚠️ Alfred backup failed: {detail[:300]}")


def health_now() -> tuple[bool, str]:
    st = state()
    if not st.get("last_ok_at"):
        return False, st.get("last_error") or "no backup yet"
    age = datetime.now(timezone.utc) - datetime.fromisoformat(st["last_ok_at"])
    hours = age.total_seconds() / 3600
    detail = f"last {hours:.0f} h ago · {st.get('last_detail', '')}"
    if st.get("last_error") and st.get("last_error_at") and st["last_error_at"] >= st["last_ok_at"]:
        return False, f"failing since {st['last_error_at'][:16]}: {st.get('last_error')}"
    return age <= MAX_AGE, detail if age <= MAX_AGE else f"overdue: {detail}"


def _alert_admins(text: str) -> None:
    """Direct Bot API message (this runs outside the server process)."""
    if not settings.telegram_enabled:
        return
    from .identity import registry
    for admin in db.query("SELECT id FROM users WHERE role = 'admin' AND active = 1"):
        tg = registry.telegram_for(admin["id"])
        if not tg:
            continue
        try:
            httpx.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                       json={"chat_id": int(tg), "text": text}, timeout=10)
        except httpx.HTTPError:
            pass


def main(argv: list[str]) -> int:
    if argv[:1] == ["snapshot"]:
        print(snapshot())
        return 0
    if argv[:1] == ["report"] and len(argv) >= 2:
        report(argv[1] == "ok", " ".join(argv[2:]) or ("ok" if argv[1] == "ok" else "unknown error"))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
