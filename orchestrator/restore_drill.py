"""Monthly restore test, run by infra/restore-drill.sh in a throwaway container:

    docker run --rm --network none -v <drill dir>:/drill -e DB_PATH=/drill/alfred.db \\
        -e STORAGE_ROOT=/drill/storage -e LIVE_COUNTS='{...}' alfred:latest python -m orchestrator.restore_drill

The drill directory holds the newest database snapshot and a few recordings, all fetched back
from the Jetson (the real off-device copy). Checks that the copy is intact, that today's code can
open and migrate it, that Alfred's web app answers on top of it, that the sample recordings play,
and that it is not missing much compared with the live database. No network, no secrets: nothing
in here can message or call anyone. Prints one JSON line: {"ok": bool, "detail": str}.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import wave
from pathlib import Path


def run() -> tuple[bool, str]:
    from .config import settings
    dbfile = settings.db_path
    if not dbfile.exists():
        return False, "no database in the drill folder"

    conn = sqlite3.connect(dbfile)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            return False, "integrity check failed"
        from .backup import counts
        got = counts(conn)
    finally:
        conn.close()

    from . import db, sessions
    from .identity import registry
    db.init_db()                                     # today's schema + migrations on the copy
    admins = db.query("SELECT u.id FROM users u JOIN user_identifiers i ON i.user_id = u.id "
                      "WHERE u.role = 'admin' AND u.active = 1 AND i.type = 'dashboard'")
    if not admins:
        return False, "no active admin with a dashboard login in the backup"
    person = registry.get_person(admins[0]["id"])
    sessions.history(person.id, "telegram")
    db.query("SELECT rowid FROM transcripts_fts WHERE transcripts_fts MATCH 'alfred' LIMIT 1")

    from fastapi.testclient import TestClient
    from .main import app
    client = TestClient(app)                         # no lifespan: no bots, no background loops
    if client.get("/healthz").status_code != 200 or client.get("/api/session").status_code != 200:
        return False, "the web app didn't answer on the restored data"

    played = 0
    for f in sorted(Path(settings.storage_root).rglob("audio.wav")):
        with wave.open(str(f)) as w:
            if w.getnframes() <= 0:
                return False, f"empty recording {f.name}"
        played += 1

    live = json.loads(os.environ.get("LIVE_COUNTS") or "{}")
    lt, bt = live.get("transcripts", 0), got["transcripts"]
    if lt and bt < 0.9 * lt - 50:
        return False, f"backup has only {bt} of {lt} live transcript lines"
    return True, (f"{got['users']} people, {got['sessions']} conversations, {bt} lines"
                  + (f" (live {lt})" if lt else "") + f", {played} recordings played")


if __name__ == "__main__":
    try:
        ok, detail = run()
    except Exception as e:  # noqa: BLE001 — any crash is a failed drill, reported as such
        ok, detail = False, f"{type(e).__name__}: {e}"[:300]
    print(json.dumps({"ok": ok, "detail": detail}))
    sys.exit(0 if ok else 1)
