"""Dashboard API: auth, live sessions, history, recordings, tasks, activity, health, registry.

Admins see everything; other roles with dashboard access only see their own sessions,
recordings and activity.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import db, health, sessions
from orchestrator.config import settings
from orchestrator.identity import mfa, registry
from orchestrator.identity.registry import Person
from orchestrator.storage import recordings

from . import security

router = APIRouter(prefix="/api")

# --- auth --------------------------------------------------------------------------------


# Reachable before a role that requires two-factor sign-in has set it up.
_SETUP_PATHS = ("/api/me", "/api/profile")


def current_person(request: Request) -> Person:
    uid = security.session_user_id(request)
    person = registry.get_person(uid) if uid else None
    if person is None or not registry.role_allows_channel(person.role, "dashboard"):
        raise HTTPException(status_code=401)
    if mfa.required_for(person.role) and not request.url.path.startswith(_SETUP_PATHS)             and not mfa.enabled(person.id):
        raise HTTPException(status_code=403, detail="Set up two-factor sign-in on your Profile first.")
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
    if mfa.enabled(person.id):
        # Password right: no session yet, only a short-lived challenge for the second factor.
        st = mfa.status(person.id)
        return {"mfa_required": True, "challenge": mfa.create_challenge(person.id),
                "methods": {"totp": st["totp"], "telegram": st["telegram"]}}
    security.ip_throttle.reset(ip)
    security.user_throttle.reset(uname)
    security.destroy_session(request)  # no session fixation: always a fresh token
    security.set_session_cookie(response, security.create_session(person.id, request))
    db.log_activity("access", f"Dashboard login from {ip}", user_id=person.id)
    return _me_view(person)


def _me_view(person: Person) -> dict:
    return {"id": person.id, "display_name": person.display_name, "role": person.role,
            "assistant_name": settings.assistant_name, "can_request_calls": _may_request_calls(person),
            "mfa_setup_required": mfa.required_for(person.role) and not mfa.enabled(person.id)}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    security.destroy_session(request)
    security.clear_session_cookie(response)
    return {"ok": True}


@router.get("/session")
async def session_state(request: Request) -> dict:
    """Public, minimal: lets the homepage swap "Sign in" for "Open Alfred". Never errors."""
    uid = security.session_user_id(request)
    person = registry.get_person(uid) if uid else None
    if person is None or not registry.role_allows_channel(person.role, "dashboard"):
        return {"authenticated": False}
    return {"authenticated": True, "first_name": person.display_name.split()[0]}


@router.get("/me")
async def me(person: Person = Depends(current_person)) -> dict:
    return _me_view(person)


# --- helpers -----------------------------------------------------------------------------

def _scope(person: Person, alias: str = "s") -> tuple[str, list]:
    return ("", []) if person.is_admin else (f" AND {alias}.user_id = ?", [person.id])


def _seconds_since(iso: str) -> int:
    return int((datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds())


def _own_session(person: Person, session_id: str) -> dict:
    row = db.query_one(
        "SELECT s.*, u.display_name, (SELECT c.caller FROM screened_calls c WHERE c.session_id = s.id) AS screened_caller "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?", (session_id,)
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
    channel: str | None = Query(default=None, pattern="^(call|telegram|web)$"),
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_from: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> list[dict]:
    """Archive list. Filters: person, channel, a day or a date range (local dates), and `q`, a
    full-text search over the transcripts (best matches first, each with a highlighted snippet)."""
    where, params = _scope(person)
    if user_id is not None:
        where += " AND s.user_id = ?"
        params.append(user_id)
    if channel:
        where += " AND s.channel = ?"
        params.append(channel)
    if date:                                   # a day from the tree (grouped the same way)
        where += " AND substr(s.started_at, 1, 10) = ?"
        params.append(date)
    tz = ZoneInfo(settings.timezone)
    if date_from:
        where += " AND s.started_at >= ?"
        params.append(datetime.fromisoformat(date_from).replace(tzinfo=tz).astimezone(timezone.utc).isoformat())
    if date_to:
        where += " AND s.started_at < ?"
        params.append((datetime.fromisoformat(date_to).replace(tzinfo=tz) + timedelta(days=1))
                      .astimezone(timezone.utc).isoformat())
    hits: dict[str, str] | None = None
    if q and q.strip():
        from orchestrator import search
        try:
            hits = search.matching_sessions(q, where, list(params), limit=offset + limit)
        except search.SearchError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not hits:
            return []
        ids = list(hits)[offset:offset + limit]
        where, params = f" AND s.id IN ({','.join('?' * len(ids))})", ids
        limit, offset = len(ids), 0
    sql = ("SELECT s.id, s.user_id, s.channel, s.direction, s.started_at, s.ended_at, s.status, u.display_name, "
           "(SELECT COUNT(*) FROM transcripts t WHERE t.session_id = s.id) AS turns, "
           "(SELECT r.id FROM recordings r WHERE r.session_id = s.id AND r.deleted_at IS NULL LIMIT 1) AS recording_id, "
           "(SELECT r.duration FROM recordings r WHERE r.session_id = s.id LIMIT 1) AS duration, "
           "(SELECT MAX(r.deleted_at) FROM recordings r WHERE r.session_id = s.id) AS audio_deleted_at, "
           "(SELECT c.caller FROM screened_calls c WHERE c.session_id = s.id) AS screened_caller "
           "FROM sessions s JOIN users u ON u.id = s.user_id WHERE 1=1" + where)
    sql += " ORDER BY s.started_at DESC LIMIT ? OFFSET ?"
    rows = db.query(sql, params + [limit, offset])
    if hits is not None:
        rank = {sid: i for i, sid in enumerate(hits)}
        rows.sort(key=lambda r: rank[r["id"]])
        for r in rows:
            r["match"] = hits[r["id"]]
    return rows


@router.get("/sessions/{session_id}")
async def session_detail(session_id: str = Path(max_length=64), person: Person = Depends(current_person)) -> dict:
    row = _own_session(person, session_id)
    row["turns"] = sessions.session_turns(session_id)
    row["tool_calls"] = db.query(
        "SELECT tool_name, payload, result, is_error, ts FROM tool_calls WHERE session_id = ? ORDER BY id",
        (session_id,),
    )
    row["recordings"] = db.query(
        "SELECT id, duration, created_at, deleted_at FROM recordings WHERE session_id = ?", (session_id,)
    )
    row["retention_days"] = settings.recording_retention_days
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
    purpose: str = Field(default="", max_length=600)
    when: str | None = Field(default=None, max_length=40)  # ISO local time; empty = now
    opening_line: str = Field(default="", max_length=300)
    urgent: bool = False  # ring even during the target's quiet hours


def _may_request_calls(person: Person) -> bool:
    """Same rule as Alfred's place_call tool (roles listed in tools/registry.yaml)."""
    from orchestrator.tools import registry as tools
    if not tools._TOOLS:
        tools.load()
    spec = tools._TOOLS.get("place_call")
    return bool(spec and spec.allowed_for(person.role))


@router.post("/calls")
async def place_call(body: CallBody, person: Person = Depends(current_person)) -> dict:
    """Call a registered person now or later, optionally with a purpose Alfred reports back on."""
    from orchestrator import scheduler
    if not _may_request_calls(person):
        raise HTTPException(status_code=403)
    if registry.get_person(body.user_id) is None:
        raise HTTPException(status_code=404, detail="Unknown person.")
    try:
        row = await scheduler.request_call(body.user_id, requested_by=person, purpose=body.purpose,
                                           when=body.when, opening_line=body.opening_line, urgent=body.urgent)
    except scheduler.ScheduleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if row["status"] == "failed":  # 400, not 5xx: Cloudflare replaces 5xx bodies with its own page
        raise HTTPException(status_code=400, detail=row["result"] or "The call failed.")
    return {"ok": True, "request": row}


@router.get("/calls/targets")
async def call_targets(person: Person = Depends(current_person)) -> list[dict]:
    """People a caller may ask Alfred to phone: active, with a phone and a role allowed on calls."""
    if not _may_request_calls(person):
        return []
    rows = db.query(
        "SELECT DISTINCT u.id, u.display_name FROM users u JOIN user_identifiers i ON i.user_id = u.id "
        "JOIN roles r ON r.name = u.role WHERE u.active = 1 AND i.type = 'phone' AND i.verified_at IS NOT NULL "
        "AND r.can_call = 1 "
        "ORDER BY u.display_name")
    return rows


@router.get("/calls/scheduled")
async def scheduled_calls(person: Person = Depends(current_person), include_done: bool = True) -> list[dict]:
    from orchestrator import scheduler
    return scheduler.list_for(person, include_done=include_done, limit=100)


@router.delete("/calls/scheduled/{request_id}")
async def cancel_scheduled_call(request_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    from orchestrator import scheduler
    try:
        return scheduler.cancel(request_id, person)
    except scheduler.ScheduleError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
async def recording_audio(recording_id: int, person: Person = Depends(current_person)) -> Response:
    rec = db.query_one("SELECT session_id, file_path, deleted_at FROM recordings WHERE id = ?", (recording_id,))
    if not rec:
        raise HTTPException(status_code=404)
    _own_session(person, rec["session_id"])
    if rec["deleted_at"]:
        raise HTTPException(status_code=410, detail="The audio was deleted after the retention period.")
    from orchestrator import nas, recording_archive
    if recording_archive.is_nas(rec["file_path"]):          # older recordings live on the NAS
        import asyncio
        try:
            data = await asyncio.to_thread(recording_archive.read_bytes, rec["file_path"])
        except (nas.NasError, PermissionError) as e:
            raise HTTPException(status_code=404, detail=f"Audio not available: {e}")
        return Response(data, media_type="audio/wav", headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{rec["session_id"]}.wav"'})
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
    owner_id: int | None = Field(default=None, ge=1)
    due: str | None = Field(default=None, max_length=40)            # ISO local
    follow_up: Literal["none", "telegram", "call"] = "none"
    notes: str = Field(default="", max_length=500)


class TaskDone(Strict):
    result: str = Field(default="", max_length=500)


@router.get("/tasks")
async def tasks(person: Person = Depends(current_person), include_done: bool = Query(default=False)) -> dict:
    from orchestrator import tasks as task_mod
    out: dict = {"local": task_mod.list_for(person, include_done=include_done)}
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
async def add_task(body: TaskBody, person: Person = Depends(current_person)) -> dict:
    from orchestrator import tasks as task_mod
    try:
        return task_mod.create(created_by=person, title=body.title, owner_ref=body.owner_id, due=body.due,
                               follow_up=body.follow_up, notes=body.notes, schedule=body.schedule)
    except task_mod.TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/tasks/{task_id}/done")
async def complete_task(body: TaskDone, task_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    from orchestrator import tasks as task_mod
    try:
        return task_mod.complete(task_id, person, body.result)
    except task_mod.TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    from orchestrator import tasks as task_mod
    try:
        return task_mod.cancel(task_id, person)
    except task_mod.TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- activity / health / registry --------------------------------------------------------

@router.get("/activity")
async def activity(person: Person = Depends(current_person), limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    where, params = ("", []) if person.is_admin else (" AND a.user_id = ?", [person.id])
    return db.query(
        "SELECT a.id, a.ts, a.kind, a.summary, a.session_id, u.display_name AS user FROM activity a "
        "LEFT JOIN users u ON u.id = a.user_id WHERE 1=1" + where + " ORDER BY a.id DESC LIMIT ?",
        params + [limit],
    )


def _health_rows(person: Person) -> list[dict]:
    """Alfred's own systems for everyone with dashboard access; homelab machines for admins only."""
    from orchestrator import homelab
    rows = db.query("SELECT service, ok, detail, checked_at FROM health ORDER BY service")
    return homelab.host_rows_visible(rows, person.is_admin)


@router.get("/health")
async def get_health(person: Person = Depends(current_person)) -> list[dict]:
    return _health_rows(person)


@router.post("/health/refresh")
async def refresh_health(person: Person = Depends(admin_only)) -> list[dict]:
    from orchestrator import homelab
    await health.check_all()
    if homelab.enabled():
        await homelab.poll_and_alert()
    return _health_rows(person)


@router.get("/overview")
async def overview(person: Person = Depends(current_person)) -> dict:
    where, params = _scope(person)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    counts = db.query_one(
        "SELECT SUM(status = 'active') AS active, "
        "SUM(substr(started_at,1,10) = ? AND channel = 'call') AS calls_today, "
        "SUM(substr(started_at,1,10) = ? AND channel IN ('telegram', 'web')) AS chats_today "
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
            "phone": settings.twilio_enabled and settings.speech_enabled,
            "n8n": settings.n8n_enabled,
            "google": settings.google_enabled,
        },
        "model": settings.claude_model,
    }


@router.get("/usage")
async def usage_report(person: Person = Depends(admin_only), days: int = Query(default=30, ge=1, le=366)) -> dict:
    """Metered API usage (tokens, characters, minutes, requests) + Twilio's own month-to-date bill."""
    from orchestrator import usage
    data = usage.report(days)
    data["twilio_month"] = await usage.twilio_month_to_date()
    return data


@router.get("/access-attempts")
async def access_attempts(person: Person = Depends(admin_only), limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    return db.query(
        "SELECT ts, channel, raw_identifier, resolved, detail FROM access_attempts ORDER BY id DESC LIMIT ?",
        (limit,),
    )
