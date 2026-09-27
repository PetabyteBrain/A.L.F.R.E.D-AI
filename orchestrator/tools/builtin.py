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
        types = db.query("SELECT DISTINCT type FROM user_identifiers WHERE user_id = ?", (p["id"],))
        p["reachable_via"] = sorted(t["type"] for t in types if t["type"] in ("telegram", "phone"))
    return people


@handler
async def send_telegram_message(ctx, user_id: int, text: str) -> str:
    from ..telegram_handler import send_to_user
    await send_to_user(int(user_id), text, note=f"sent on behalf of {ctx.person.display_name}")
    return "Sent."


@handler
async def place_call(ctx, user_id: int, opening_line: str, reason: str = "") -> dict:
    from ..call_handler import place_outbound_call
    call_sid = await place_outbound_call(int(user_id), opening_line=opening_line, reason=reason,
                                         requested_by=ctx.person.id)
    return {"status": "dialing", "call_sid": call_sid}
