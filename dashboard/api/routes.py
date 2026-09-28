"""Dashboard API: auth, live sessions, history, recordings, tasks, activity, health, registry.

Admins see everything; other roles with dashboard access only see their own sessions,
recordings and activity.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from orchestrator import db, health, sessions
from orchestrator.config import settings
from orchestrator.identity import registry
from orchestrator.identity.registry import Person
from orchestrator.storage import recordings

from . import security

router = APIRouter(prefix="/api")

# --- auth --------------------------------------------------------------------------------


def current_person(request: Request) -> Person:
    uid = security.session_user_id(request)
    person = registry.get_person(uid) if uid else None
    if person is None or not registry.role_allows_channel(person.role, "dashboard"):
        raise HTTPException(status_code=401)
    return person


def admin_only(person: Person = Depends(current_person)) -> Person:
    if not person.is_admin:
        raise HTTPException(status_code=403)
    return person


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginBody(Strict):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    ip = security.client_ip(request)
    uname = registry.normalize("dashboard", body.username)
    if security.ip_throttle.blocked(ip) or security.user_throttle.blocked(uname):
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in 15 minutes.")
    person = registry.verify_dashboard_login(body.username, body.password)
    if person is None:
        security.ip_throttle.fail(ip)
        security.user_throttle.fail(uname)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    security.ip_throttle.reset(ip)
    security.user_throttle.reset(uname)
    security.destroy_session(request)  # no session fixation: always a fresh token
    security.set_session_cookie(response, security.create_session(person.id, request))
    db.log_activity("access", f"Dashboard login from {ip}", user_id=person.id)
    return {"id": person.id, "display_name": person.display_name, "role": person.role,
            "assistant_name": settings.assistant_name}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    security.destroy_session(request)
    security.clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
async def me(person: Person = Depends(current_person)) -> dict:
    return {"id": person.id, "display_name": person.display_name, "role": person.role,
            "assistant_name": settings.assistant_name}


# --- helpers -----------------------------------------------------------------------------

def _scope(person: Person, alias: str = "s") -> tuple[str, list]:
    return ("", []) if person.is_admin else (f" AND {alias}.user_id = ?", [person.id])


def _seconds_since(iso: str) -> int:
    return int((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds())


def _own_session(person: Person, session_id: str) -> dict:
    row = db.query_one(
        "SELECT s.*, u.display_name FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?", (session_id,)
    )
    if not row or (not person.is_admin and row["user_id"] != person.id):
        raise HTTPException(status_code=404)
    return row


# --- sessions ----------------------------------------------------------------------------

@router.get("/sessions/active")
async def active_sessions(person: Person = Depends(current_person)) -> list[dict]:
    where, params = _scope(person)
    rows = db.query(
        "SELECT s.id, s.user_id, s.channel, s.direction, s.started_at, s.last_activity_at, u.display_name "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.status = 'active'" + where +
        " ORDER BY s.started_at DESC",
        params,
    )
    for r in rows:
        live = sessions.LIVE.get(r["id"])
        r["duration_s"] = _seconds_since(r["started_at"])
        r["idle_s"] = _seconds_since(r["last_activity_at"])
        r["busy"] = bool(live and live.busy)
        r["speaking"] = bool(live and live.speaking)
        r["partial"] = live.partial if live else ""
        tail = db.query(
            "SELECT speaker, text, ts FROM transcripts WHERE session_id = ? ORDER BY turn_index DESC LIMIT 8",
            (r["id"],),
        )
        r["tail"] = list(reversed(tail))
    return rows


@router.get("/sessions")
async def list_sessions(
    person: Person = Depends(current_person),
    user_id: int | None = Query(default=None, ge=1),
    channel: str | None = Query(default=None, pattern="^(call|telegram)$"),
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> list[dict]:
    where, params = _scope(person)
    sql = ("SELECT s.id, s.user_id, s.channel, s.direction, s.started_at, s.ended_at, s.status, u.display_name, "
           "(SELECT COUNT(*) FROM transcripts t WHERE t.session_id = s.id) AS turns, "
           "(SELECT r.id FROM recordings r WHERE r.session_id = s.id LIMIT 1) AS recording_id, "
           "(SELECT r.duration FROM recordings r WHERE r.session_id = s.id LIMIT 1) AS duration "
           "FROM sessions s JOIN users u ON u.id = s.user_id WHERE 1=1" + where)
    if user_id is not None:
        sql += " AND s.user_id = ?"
        params.append(user_id)
    if channel:
        sql += " AND s.channel = ?"
        params.append(channel)
    if date:
        sql += " AND substr(s.started_at, 1, 10) = ?"
        params.append(date)
    sql += " ORDER BY s.started_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return db.query(sql, params)


@router.get("/sessions/{session_id}")
async def session_detail(session_id: str = Path(max_length=64), person: Person = Depends(current_person)) -> dict:
    row = _own_session(person, session_id)
    row["turns"] = sessions.session_turns(session_id)
    row["tool_calls"] = db.query(
        "SELECT tool_name, payload, result, is_error, ts FROM tool_calls WHERE session_id = ? ORDER BY id",
        (session_id,),
    )
    row["recordings"] = db.query(
        "SELECT id, duration, created_at FROM recordings WHERE session_id = ?", (session_id,)
    )
    return row


@router.post("/sessions/{session_id}/end")
async def end_session_route(session_id: str = Path(max_length=64), person: Person = Depends(admin_only)) -> dict:
    """Admin kill switch: hang up a call (via Twilio) or close a stuck/unwanted session."""
    row = _own_session(person, session_id)
    if row["status"] != "active":
        raise HTTPException(status_code=409, detail="Session is not active.")
    if row["channel"] == "call":
        from orchestrator.call_handler import end_call
        result = await end_call(session_id, by_user_id=person.id)
    else:
        sessions.end_session(session_id, status="terminated")
        db.log_activity("message", "Telegram session closed from dashboard", user_id=person.id, session_id=session_id)
        result = {"local": "session closed"}
    return {"ok": True, **result}


class CallBody(Strict):
    user_id: int = Field(ge=1)
    opening_line: str = Field(default="", max_length=300)
    reason: str = Field(default="", max_length=300)


@router.post("/calls")
async def place_call(body: CallBody, person: Person = Depends(admin_only)) -> dict:
    """Admin: have the assistant phone a registered person (e.g. to test the line)."""
    from orchestrator.call_handler import place_outbound_call
    target = registry.get_person(body.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Unknown person.")
    line = body.opening_line or f"Hi {target.display_name.split()[0]}, it's {settings.assistant_name}. Can you hear me?"
    try:
        call_sid = await place_outbound_call(body.user_id, opening_line=line,
                                             reason=body.reason or "Placed from the dashboard.",
                                             requested_by=person.id)
    except (ValueError, PermissionError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # Twilio errors. 400, not 502: Cloudflare replaces 5xx bodies with its own page.
        logging.getLogger(__name__).exception("Outbound call failed")
        raise HTTPException(status_code=400, detail=f"Carrier error: {str(e)[:300]}")
    return {"ok": True, "call_sid": call_sid}


# --- recordings --------------------------------------------------------------------------

@router.get("/recordings/tree")
async def recordings_tree(person: Person = Depends(current_person)) -> list[dict]:
    """People -> dates -> session counts, for the by-user/by-date browser."""
    where, params = _scope(person)
    rows = db.query(
        "SELECT s.user_id, u.display_name, substr(s.started_at, 1, 10) AS day, "
        "COUNT(*) AS sessions, SUM(s.channel = 'call') AS calls "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE 1=1" + where +
        " GROUP BY s.user_id, day ORDER BY u.display_name, day DESC",
        params,
    )
    tree: dict[int, dict] = {}
    for r in rows:
        node = tree.setdefault(r["user_id"], {"user_id": r["user_id"], "display_name": r["display_name"], "days": []})
        node["days"].append({"day": r["day"], "sessions": r["sessions"], "calls": r["calls"]})
    return list(tree.values())


@router.get("/recordings/{recording_id}/audio")
async def recording_audio(recording_id: int, person: Person = Depends(current_person)) -> FileResponse:
    rec = db.query_one("SELECT session_id, file_path FROM recordings WHERE id = ?", (recording_id,))
    if not rec:
        raise HTTPException(status_code=404)
    _own_session(person, rec["session_id"])
    try:
        path = recordings.resolve_safe(rec["file_path"])
    except PermissionError:
        raise HTTPException(status_code=404)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file missing on disk")
    return FileResponse(path, media_type="audio/wav", filename=f"{rec['session_id']}.wav",
                        headers={"Cache-Control": "private, no-store"})


# --- tasks -------------------------------------------------------------------------------

class TaskBody(Strict):
    title: str = Field(min_length=1, max_length=200)
    schedule: str = Field(default="", max_length=200)
    next_run_at: AwareDatetime | None = None


@router.get("/tasks")
async def tasks(person: Person = Depends(current_person)) -> dict:
    out: dict = {"local": db.query("SELECT * FROM tasks WHERE status = 'active' ORDER BY COALESCE(next_run_at, created_at)")}
    if person.is_admin and settings.n8n_enabled:
        from orchestrator.tools import n8n_tool
        try:
            wfs = await n8n_tool.list_active_workflows()
            out["n8n"] = [{"id": w.get("id"), "name": w.get("name"), "updatedAt": w.get("updatedAt"),
                           "triggers": _trigger_summary(w)} for w in wfs]
        except Exception as e:  # noqa: BLE001
            out["n8n_error"] = str(e)[:200]
    return out


def _trigger_summary(workflow: dict) -> list[str]:
    out = []
    for n in workflow.get("nodes", []) or []:
        t = n.get("type", "")
        if "scheduleTrigger" in t or "cron" in t.lower():
            out.append(f"schedule: {n.get('parameters', {}).get('rule', n.get('parameters', {}))}"[:160])
        elif "webhook" in t.lower():
            out.append(f"webhook: /{n.get('parameters', {}).get('path', '')}")
    return out


@router.post("/tasks")
async def add_task(body: TaskBody, person: Person = Depends(admin_only)) -> dict:
    tid = db.execute(
        "INSERT INTO tasks(title, schedule, source, next_run_at, created_at) VALUES (?,?, 'local', ?, ?)",
        (body.title, body.schedule, body.next_run_at.isoformat() if body.next_run_at else None, db.now_iso()),
    )
    return {"id": tid}


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int, person: Person = Depends(admin_only)) -> dict:
    db.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (task_id,))
    return {"ok": True}


# --- activity / health / registry --------------------------------------------------------

@router.get("/activity")
async def activity(person: Person = Depends(current_person), limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    where, params = ("", []) if person.is_admin else (" AND a.user_id = ?", [person.id])
    return db.query(
        "SELECT a.id, a.ts, a.kind, a.summary, a.session_id, u.display_name AS user FROM activity a "
        "LEFT JOIN users u ON u.id = a.user_id WHERE 1=1" + where + " ORDER BY a.id DESC LIMIT ?",
        params + [limit],
    )


@router.get("/health")
async def get_health(person: Person = Depends(current_person)) -> list[dict]:
    return db.query("SELECT service, ok, detail, checked_at FROM health ORDER BY service")


@router.post("/health/refresh")
async def refresh_health(person: Person = Depends(admin_only)) -> list[dict]:
    await health.check_all()
    return db.query("SELECT service, ok, detail, checked_at FROM health ORDER BY service")


@router.get("/overview")
async def overview(person: Person = Depends(current_person)) -> dict:
    where, params = _scope(person)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    counts = db.query_one(
        "SELECT SUM(status = 'active') AS active, "
        "SUM(substr(started_at,1,10) = ? AND channel = 'call') AS calls_today, "
        "SUM(substr(started_at,1,10) = ? AND channel = 'telegram') AS chats_today "
        "FROM sessions s WHERE 1=1" + where,
        [today, today] + params,
    ) or {}
    blocked = db.query_one(
        "SELECT COUNT(*) AS n FROM access_attempts WHERE resolved = 0 AND substr(ts,1,10) = ?", (today,)
    )
    return {
        "active": counts.get("active") or 0,
        "calls_today": counts.get("calls_today") or 0,
        "chats_today": counts.get("chats_today") or 0,
        "blocked_today": (blocked or {}).get("n", 0) if person.is_admin else None,
        "channels": {
            "telegram": settings.telegram_enabled,
            "phone": settings.twilio_enabled and settings.elevenlabs_enabled,
            "n8n": settings.n8n_enabled,
        },
        "model": settings.claude_model,
    }


@router.get("/people")
async def people(person: Person = Depends(admin_only)) -> list[dict]:
    rows = db.query("SELECT id, display_name, role, active, created_at FROM users ORDER BY id")
    for r in rows:
        r["identifiers"] = registry.identifiers_for(r["id"])
    return rows


@router.get("/access-attempts")
async def access_attempts(person: Person = Depends(admin_only), limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return db.query(
        "SELECT ts, channel, raw_identifier, resolved, detail FROM access_attempts ORDER BY id DESC LIMIT ?",
        (limit,),
    )
