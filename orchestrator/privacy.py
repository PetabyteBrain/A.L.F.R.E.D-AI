"""Data export and deletion for a person (FADP right of access and erasure).

Export: a ZIP with data.json (everything stored about the person, except other people's
private data and secrets such as password hashes or OAuth tokens) + their call recordings.

Deletion is asked for by the person on Profile and carried out by an admin (People tab):
- scope "history": conversations (transcripts, tool calls, recordings + audio files), personal
  email (company mail they were part of stays, without the link to them), memories, reminders, tasks they own, meetings they organised, briefing, quiet hours, voice
  mode, Google link (revoked at Google), activity lines and blocked-access lines with their
  numbers. Account and connections stay, so Alfred still knows them.
- scope "account": all of that, then the person, their connections, 2FA and sessions.
Usage/cost rows are kept without the name (bookkeeping). Backups: database snapshots age out
of the Jetson within 14 days; recordings already in the backup within the retention period.
"""
from __future__ import annotations

import io
import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone

from . import db
from .config import settings
from .identity import registry
from .identity.registry import Person, RegistryError

log = logging.getLogger(__name__)

SCOPES = ("history", "account")


class PrivacyError(RegistryError):
    pass


# --- export ------------------------------------------------------------------------------

def _rows(sql: str, params: tuple) -> list[dict]:
    return db.query(sql, params)


def collect(user_id: int) -> dict:
    user = db.query_one("SELECT id, display_name, role, active, created_at FROM users WHERE id = ?", (user_id,))
    if not user:
        raise PrivacyError("Unknown person.")
    sessions = _rows("SELECT id, channel, direction, started_at, ended_at, status FROM sessions "
                     "WHERE user_id = ? ORDER BY started_at", (user_id,))
    for s in sessions:
        s["transcript"] = _rows("SELECT speaker, text, ts FROM transcripts WHERE session_id = ? ORDER BY turn_index",
                                (s["id"],))
        s["recordings"] = _rows("SELECT id, duration, created_at, deleted_at FROM recordings WHERE session_id = ?",
                                (s["id"],))
    google = db.query_one("SELECT email, scopes, share_availability, linked_at FROM google_accounts WHERE user_id = ?",
                          (user_id,))
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "service": settings.assistant_name,
        "person": user,
        "connections": _rows("SELECT type, value, verified_at FROM user_identifiers WHERE user_id = ?",
                             (user_id,)),
        "conversations": sessions,
        "memories": _rows("SELECT text, created_at, updated_at FROM memories WHERE user_id = ?", (user_id,)),
        "reminders": _rows("SELECT * FROM reminders WHERE user_id = ?", (user_id,)),
        "tasks": _rows("SELECT id, title, notes, due_at, status, follow_up, result, created_at, completed_at "
                       "FROM tasks WHERE owner_user_id = ? OR requested_by = ?", (user_id, user_id)),
        "meetings_organised": _rows("SELECT title, notes, duration_min, status, booked_start, created_at "
                                    "FROM meetings WHERE organizer_id = ?", (user_id,)),
        "meeting_invitations": _rows("SELECT m.title, i.status, i.created_at FROM meeting_invites i "
                                     "JOIN meetings m ON m.id = i.meeting_id WHERE i.user_id = ?", (user_id,)),
        "scheduled_calls": _rows("SELECT run_at, purpose, status, created_at FROM scheduled_calls "
                                 "WHERE target_user_id = ? OR requested_by = ?", (user_id, user_id)),
        "outside_calls": _rows("SELECT contact, number, task, share, status, result, run_at, created_at "
                               "FROM external_calls WHERE requested_by = ?", (user_id,)),
        "emails": _rows("SELECT direction, kind, scope, from_addr, to_addrs, cc_addrs, subject, body_text, "
                        "attachments, summary, created_at FROM emails WHERE user_id = ? ORDER BY id", (user_id,)),
        "escalations": _rows("SELECT source, topic, summary, urgency, status, note, created_at, handled_at "
                             "FROM escalations WHERE requester_id = ?", (user_id,)),
        "settings": {
            "briefing": db.query_one("SELECT * FROM briefings WHERE user_id = ?", (user_id,)),
            "quiet_hours": db.query_one("SELECT * FROM quiet_hours WHERE user_id = ?", (user_id,)),
            "voice_mode": db.query_one("SELECT until, updated_at FROM voice_mode WHERE user_id = ?", (user_id,)),
            "google_calendar": google,
        },
        "activity": _rows("SELECT ts, kind, summary FROM activity WHERE user_id = ? ORDER BY id", (user_id,)),
        "usage": _rows("SELECT provider, kind, COUNT(*) AS requests, SUM(units) AS units, MAX(unit) AS unit, "
                       "SUM(cost_usd) AS cost_usd FROM api_usage WHERE user_id = ? GROUP BY provider, kind", (user_id,)),
    }


def export_zip(user_id: int) -> bytes:
    from .storage import recordings
    data = collect(user_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("data.json", json.dumps(data, indent=2, ensure_ascii=False, default=str))
        z.writestr("README.txt", f"Everything {settings.assistant_name} stores about {data['person']['display_name']}, "
                   f"exported {data['exported_at']}.\ndata.json: profile, connections, conversations with "
                   "transcripts, notes, reminders, tasks, meetings, settings, activity and usage.\n"
                   "recordings/: call audio that is still kept (see the retention period in the privacy policy).\n")
        try:                                         # their personal NAS folder (notes, lists, files)
            from . import nas
            person = registry.get_person(user_id)
            if person:
                nas.export_personal(person, z)
        except Exception:  # noqa: BLE001 — the export still works without the NAS
            log.warning("NAS part of the export failed", exc_info=True)
        for r in db.query("SELECT r.id, r.file_path, s.started_at FROM recordings r JOIN sessions s ON s.id = r.session_id "
                          "WHERE s.user_id = ? AND r.deleted_at IS NULL", (user_id,)):
            from . import recording_archive
            try:
                data = recording_archive.read_bytes(r["file_path"])      # Pi or NAS
            except Exception:  # noqa: BLE001 — missing file: skip it
                continue
            z.writestr(f"recordings/{r['started_at'][:10]}-call-{r['id']}.wav", data)
    db.log_activity("access", "Data export downloaded", user_id=user_id)
    return buf.getvalue()


# --- deletion requests ---------------------------------------------------------------------

def request_deletion(person: Person, scope: str, note: str = "") -> dict:
    if scope not in SCOPES:
        raise PrivacyError("Unknown scope.")
    if scope == "account" and person.is_admin and registry.active_admin_count(excluding=person.id) == 0:
        raise PrivacyError("You're the last admin, so the account itself can't be deleted. "
                           "You can delete your history.")
    if db.query_one("SELECT id FROM data_requests WHERE user_id = ? AND status = 'pending'", (person.id,)):
        raise PrivacyError("There is already a pending request. An admin will handle it.")
    db.execute("INSERT INTO data_requests(user_id, scope, note, created_at) VALUES (?,?,?,?)",
                     (person.id, scope, note[:300], db.now_iso()))
    db.log_activity("access", f"Data deletion requested ({scope})", user_id=person.id)
    return pending_for(person.id)


def pending_for(user_id: int) -> dict | None:
    return db.query_one("SELECT id, scope, note, status, created_at FROM data_requests WHERE user_id = ? "
                        "AND status = 'pending'", (user_id,))


def cancel(person: Person) -> None:
    db.execute("DELETE FROM data_requests WHERE user_id = ? AND status = 'pending'", (person.id,))


def pending_all() -> list[dict]:
    return db.query("SELECT r.id, r.user_id, u.display_name, u.role, r.scope, r.note, r.created_at "
                    "FROM data_requests r JOIN users u ON u.id = r.user_id WHERE r.status = 'pending' ORDER BY r.id")


async def decide(request_id: int, admin: Person, approve: bool) -> dict:
    req = db.query_one("SELECT * FROM data_requests WHERE id = ? AND status = 'pending'", (request_id,))
    if not req:
        raise PrivacyError("No such pending request.")
    if req["user_id"] == admin.id and registry.active_admin_count(excluding=admin.id) > 0:
        raise PrivacyError("Another admin has to confirm your own request.")
    if not approve:
        db.execute("UPDATE data_requests SET status = 'rejected', decided_by = ?, decided_at = ? WHERE id = ?",
                   (admin.id, db.now_iso(), request_id))
        db.log_activity("access", f"Data deletion request #{request_id} rejected by {admin.display_name}",
                        user_id=req["user_id"])
        return {"ok": True, "deleted": False}
    summary = await erase(req["user_id"], req["scope"], by=admin)
    db.execute("DELETE FROM data_requests WHERE id = ?", (request_id,))
    return {"ok": True, "deleted": True, "summary": summary}


# --- erasure -----------------------------------------------------------------------------

async def erase(user_id: int, scope: str, *, by: Person) -> dict:
    if scope not in SCOPES:
        raise PrivacyError("Unknown scope.")
    row = db.query_one("SELECT role, active, display_name FROM users WHERE id = ?", (user_id,))
    if not row:
        raise PrivacyError("Unknown person.")
    if scope == "account" and row["role"] == "admin" and row["active"] and \
            registry.active_admin_count(excluding=user_id) == 0:
        raise PrivacyError("This is the last active admin; only the history can be deleted.")

    from . import google_calendar
    if db.query_one("SELECT 1 FROM google_accounts WHERE user_id = ?", (user_id,)):
        try:
            await google_calendar.disconnect(user_id)   # also revokes the token at Google
        except Exception:
            log.warning("Google disconnect during erasure failed; removing the link locally", exc_info=True)

    idents = []
    for r in db.query("SELECT type, value FROM user_identifiers WHERE user_id = ? AND type != 'dashboard'", (user_id,)):
        idents += [r["value"], f"{r['type']}:{r['value']}"]     # access_attempts stores "type:value"
    sids = [r["id"] for r in db.query("SELECT id FROM sessions WHERE user_id = ?", (user_id,))]
    n = {"conversations": len(sids)}
    with db.tx() as conn:
        for sid in sids:
            conn.execute("DELETE FROM transcripts WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM tool_calls WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM recordings WHERE session_id = ?", (sid,))
            conn.execute("DELETE FROM activity WHERE session_id = ?", (sid,))
            conn.execute("UPDATE api_usage SET session_id = NULL WHERE session_id = ?", (sid,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE tool_calls SET user_id = NULL WHERE user_id = ?", (user_id,))
        n["memories"] = conn.execute("DELETE FROM memories WHERE user_id = ?", (user_id,)).rowcount
        n["reminders"] = conn.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,)).rowcount
        n["tasks"] = conn.execute("DELETE FROM tasks WHERE owner_user_id = ?", (user_id,)).rowcount
        conn.execute("UPDATE tasks SET requested_by = NULL WHERE requested_by = ?", (user_id,))
        n["meetings"] = conn.execute("DELETE FROM meetings WHERE organizer_id = ?", (user_id,)).rowcount
        conn.execute("DELETE FROM meeting_invites WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM scheduled_calls WHERE target_user_id = ? OR requested_by = ?", (user_id, user_id))
        conn.execute("DELETE FROM external_calls WHERE requested_by = ?", (user_id,))
        for table in ("briefings", "quiet_hours", "voice_mode", "context_resets", "google_accounts", "nas_recent",
                      "event_categories"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM activity WHERE user_id = ?", (user_id,))
        for value in idents:
            conn.execute("DELETE FROM access_attempts WHERE raw_identifier = ?", (value,))
        conn.execute("UPDATE api_usage SET user_id = NULL WHERE user_id = ?", (user_id,))
    folder = settings.storage_root / str(user_id)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    try:                                             # their recordings already moved to the NAS
        from . import recording_archive
        recording_archive.erase_user(user_id)
    except Exception:  # noqa: BLE001
        log.warning("Couldn't delete the archived NAS recordings of #%s", user_id, exc_info=True)
    try:                                             # their personal mail (the NAS copies go with the folder)
        from . import mail
        n["emails"] = mail.erase_user(user_id)
    except Exception:  # noqa: BLE001
        log.warning("Couldn't delete the mail of #%s", user_id, exc_info=True)
    try:                                             # their personal NAS folder
        from . import nas
        n["nas_folder"] = nas.erase_personal(user_id)
    except Exception:  # noqa: BLE001
        log.warning("Couldn't delete the personal NAS folder of #%s", user_id, exc_info=True)
        n["nas_folder"] = False

    if scope == "account":
        from dashboard.api.security import revoke_user_sessions
        revoke_user_sessions(user_id)
        registry.delete_user(user_id)
    what = "account and all data" if scope == "account" else "history"
    db.log_activity("access", f"Privacy: {by.display_name} deleted the {what} of #{user_id} "
                    f"({n['conversations']} conversations, {n['memories']} notes, {n['reminders']} reminders)")
    return n
