"""Builtin tool handlers. Signature: async def handler(ctx, **tool_input) -> str | dict | list."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from .. import db
from ..config import settings

Handler = Callable[..., Awaitable[Any]]
HANDLERS: dict[str, Handler] = {}


def handler(fn: Handler) -> Handler:
    HANDLERS[fn.__name__] = fn
    return fn


@handler
async def current_time(ctx) -> str:
    now = datetime.now(ZoneInfo(settings.timezone))
    return now.strftime(f"%A %d %B %Y, %H:%M ({settings.timezone})")


@handler
async def list_tasks(ctx) -> dict:
    local = db.query(
        "SELECT id, title, schedule, next_run_at, status FROM tasks WHERE status = 'active' ORDER BY id"
    )
    out: dict[str, Any] = {"local_tasks": local}
    if settings.n8n_enabled:
        from . import n8n_tool
        try:
            out["active_n8n_workflows"] = [
                {"id": w.get("id"), "name": w.get("name")} for w in await n8n_tool.list_active_workflows()
            ]
        except Exception as e:  # noqa: BLE001
            out["n8n_error"] = str(e)[:200]
    return out


@handler
async def add_task(ctx, title: str, schedule: str = "", next_run_at: str | None = None) -> dict:
    tid = db.execute(
        "INSERT INTO tasks(title, schedule, source, next_run_at, created_at) VALUES (?,?, 'local', ?, ?)",
        (title, schedule, next_run_at, db.now_iso()),
    )
    db.log_activity("system", f"Task added: {title}", user_id=ctx.person.id, session_id=ctx.session_id)
    return {"id": tid, "title": title, "schedule": schedule}


@handler
async def list_active_sessions(ctx) -> list[dict]:
    from ..sessions import LIVE
    rows = db.query(
        "SELECT s.id, s.channel, s.direction, s.started_at, u.display_name FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.status = 'active' ORDER BY s.started_at"
    )
    for r in rows:
        live = LIVE.get(r["id"])
        r["busy"] = bool(live and live.busy)
    return rows


@handler
async def recent_activity(ctx, limit: int = 15) -> list[dict]:
    return db.query(
        "SELECT a.ts, a.kind, a.summary, u.display_name AS user FROM activity a "
        "LEFT JOIN users u ON u.id = a.user_id ORDER BY a.id DESC LIMIT ?",
        (max(1, min(int(limit), 50)),),
    )


@handler
async def list_people(ctx) -> list[dict]:
    people = db.query("SELECT id, display_name, role FROM users WHERE active = 1 ORDER BY id")
    for p in people:
        types = db.query("SELECT DISTINCT type FROM user_identifiers WHERE user_id = ? AND verified_at IS NOT NULL",
                         (p["id"],))
        p["reachable_via"] = sorted(t["type"] for t in types if t["type"] in ("telegram", "phone"))
    return people


@handler
async def send_telegram_message(ctx, user_id: int, text: str) -> str:
    from ..telegram_handler import send_to_user
    await send_to_user(int(user_id), text, note=f"sent on behalf of {ctx.person.display_name}")
    return "Sent."


@handler
async def set_reminder(ctx, text: str, when: str, recurrence: str = "none", channel: str = "telegram",
                       escalate: bool = False, person: str = "") -> dict:
    from .. import reminders
    try:
        r = reminders.create(created_by=ctx.person, text=text, when=when, target_ref=person or None,
                             recurrence=recurrence, channel=channel, escalate=escalate)
    except reminders.ReminderError as e:
        return {"error": str(e)}
    return {"reminder_id": r["id"], "for": r["person_name"], "at_local_time": r["due_local"],
            "recurrence": r["recurrence"] or "once", "channel": r["channel"], "escalate": r["escalate"]}


@handler
async def list_reminders(ctx, include_finished: bool = False) -> list[dict]:
    from .. import reminders
    return [{"id": r["id"], "for": r["person_name"], "text": r["text"], "at_local_time": r["due_local"],
             "recurrence": r["recurrence"] or "once", "channel": r["channel"], "status": r["status"]}
            for r in reminders.list_for(ctx.person, include_done=include_finished, limit=30)]


@handler
async def update_reminder(ctx, reminder_id: int, action: str, snooze_minutes: int = 10) -> dict:
    from .. import reminders
    try:
        if action == "cancel":
            r = reminders.cancel(int(reminder_id), ctx.person)
        elif action in ("done", "snooze"):
            r = reminders.acknowledge(int(reminder_id), ctx.person,
                                      snooze_minutes=(snooze_minutes or 10) if action == "snooze" else 0)
        else:
            return {"error": "action must be done, snooze or cancel"}
    except reminders.ReminderError as e:
        return {"error": str(e)}
    return {"reminder_id": r["id"], "status": r["status"], "at_local_time": r["due_local"]}


@handler
async def place_call(ctx, person: str, purpose: str = "", opening_line: str = "") -> dict:
    from .. import scheduler
    try:
        row = await scheduler.request_call(person, requested_by=ctx.person, purpose=purpose, opening_line=opening_line)
    except scheduler.ScheduleError as e:
        return {"error": str(e)}
    return {"status": row["status"], "calling": row["target_name"], "request_id": row["id"],
            "note": "The outcome will be reported back when the call ends." if purpose and row["target_user_id"] != ctx.person.id
            else ""}


@handler
async def schedule_call(ctx, person: str, when: str, purpose: str = "", opening_line: str = "") -> dict:
    from .. import scheduler
    try:
        row = await scheduler.request_call(person, requested_by=ctx.person, purpose=purpose, when=when,
                                           opening_line=opening_line)
    except scheduler.ScheduleError as e:
        return {"error": str(e)}
    return {"scheduled": True, "request_id": row["id"], "calling": row["target_name"],
            "at_local_time": row["run_at_local"], "status": row["status"]}


@handler
async def list_scheduled_calls(ctx, include_finished: bool = False) -> list[dict]:
    from .. import scheduler
    rows = scheduler.list_for(ctx.person, include_done=include_finished, limit=25)
    return [{k: r[k] for k in ("id", "target_name", "requester_name", "run_at_local", "purpose", "status",
                               "attempts", "result")} for r in rows]


@handler
async def cancel_scheduled_call(ctx, request_id: int) -> dict:
    from .. import scheduler
    try:
        row = scheduler.cancel(int(request_id), ctx.person)
    except scheduler.ScheduleError as e:
        return {"error": str(e)}
    return {"cancelled": True, "call_to": row["target_name"], "was_at": row["run_at_local"]}


@handler
async def end_call(ctx) -> str:
    from ..call_handler import request_hangup
    if ctx.channel != "call" or not ctx.session_id or not request_hangup(ctx.session_id):
        return "There's no live call to end."
    return ("The line will be closed right after your current reply. In that reply, briefly confirm what "
            "was agreed or answered (one sentence), then say goodbye.")


# --- Google Calendar (the current person's own linked calendar only) -------------------------

async def _cal(fn, *args, **kwargs):
    from .. import google_calendar
    try:
        return await fn(*args, **kwargs)
    except google_calendar.GoogleError as e:
        return {"error": str(e)}


@handler
async def calendar_list_events(ctx, start: str, end: str, query: str = "", max_results: int = 25):
    from .. import google_calendar
    return await _cal(google_calendar.list_events, ctx.person.id, start, end, query, max_results)


@handler
async def calendar_create_event(ctx, summary: str, start: str, end: str, description: str = "", location: str = ""):
    from .. import google_calendar
    return await _cal(google_calendar.create_event, ctx.person.id, summary, start, end, description, location)


@handler
async def calendar_update_event(ctx, event_id: str, summary: str | None = None, start: str | None = None,
                                end: str | None = None, description: str | None = None, location: str | None = None):
    from .. import google_calendar
    return await _cal(google_calendar.update_event, ctx.person.id, event_id, summary=summary, start=start, end=end,
                      description=description, location=location)


@handler
async def calendar_delete_event(ctx, event_id: str, confirmed_by_user: bool = False):
    from .. import google_calendar
    if not confirmed_by_user:
        return {"error": "Ask the person to confirm deleting this specific event first."}
    result = await _cal(google_calendar.delete_event, ctx.person.id, event_id)
    return result or {"deleted": event_id}
