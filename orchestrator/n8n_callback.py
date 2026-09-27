"""POST /n8n/callback — long-running n8n workflows report results here instead of making
the agent wait. The result is phrased by Agent Core and delivered to the person over Telegram.

Expected body (anything extra is kept):
    {"correlation_id": "...", "session_id": "...", "result": <any JSON>, "message": "optional text"}
Header: X-Alfred-Secret: <N8N_CALLBACK_SECRET>
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Request

from . import agent_core, db, sessions
from .config import settings
from .identity import registry

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/n8n/callback")
async def n8n_callback(request: Request) -> dict:
    secret = request.headers.get("X-Alfred-Secret", "")
    if not settings.n8n_callback_secret or not hmac.compare_digest(secret, settings.n8n_callback_secret):
        raise HTTPException(status_code=404)  # look like nothing is here
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="JSON body required")

    session_id = body.get("session_id")
    db.execute(
        "INSERT INTO n8n_callbacks(correlation_id, session_id, payload, ts) VALUES (?,?,?,?)",
        (body.get("correlation_id"), session_id, json.dumps(body, ensure_ascii=False)[:100_000], db.now_iso()),
    )
    db.log_activity("n8n", f"n8n callback {body.get('correlation_id', '')}", session_id=session_id)
    asyncio.create_task(_deliver(body))
    return {"ok": True}


async def _deliver(body: dict) -> None:
    session_id = body.get("session_id")
    row = db.query_one("SELECT user_id FROM sessions WHERE id = ?", (session_id,)) if session_id else None
    if not row:
        return
    person = registry.get_person(row["user_id"])
    if person is None or not settings.telegram_enabled:
        return
    from .telegram_handler import send_to_user
    try:
        tg = db.query_one("SELECT value FROM user_identifiers WHERE user_id = ? AND type = 'telegram'", (person.id,))
        if not tg:
            return
        tg_session = sessions.telegram_session_for(person.id, tg["value"])
        note = json.dumps({k: body.get(k) for k in ("correlation_id", "message", "result")}, ensure_ascii=False)[:6000]
        reply = await agent_core.respond(
            person, "telegram", tg_session,
            f"[System: an n8n workflow you started earlier has finished. Tell {person.display_name} "
            f"the outcome briefly.]\n{note}",
            record_user_turn=False,
        )
        await send_to_user(person.id, reply, note="n8n result", record=False)
    except Exception:
        log.exception("Failed delivering n8n callback")
