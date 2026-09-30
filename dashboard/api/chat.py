"""Dashboard chat: talk to the assistant from the website (channel "web").

Same Agent Core, role-gated tools and per-(channel, user) memory as Telegram; only people whose
role has dashboard access can use it. A per-person rate limit keeps a stuck client or script
from running up API costs.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import agent_core, db, sessions
from orchestrator.identity.registry import Person

from . import security
from .routes import current_person

router = APIRouter(prefix="/api/chat")

chat_throttle = security.Throttle(window_s=300, max_failures=20)  # 20 messages / 5 min / person


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=4000)


def _turns(user_id: int, limit: int) -> list[dict]:
    reset = db.query_one("SELECT after_id FROM context_resets WHERE user_id = ? AND channel = 'web'", (user_id,))
    rows = db.query(
        "SELECT t.speaker, t.text, t.ts FROM transcripts t JOIN sessions s ON s.id = t.session_id "
        "WHERE s.user_id = ? AND s.channel = 'web' AND t.speaker IN ('user', 'assistant') AND t.id > ? "
        "ORDER BY t.id DESC LIMIT ?",
        (user_id, reset["after_id"] if reset else 0, limit),
    )
    rows.reverse()
    return rows


@router.get("/history")
async def chat_history(person: Person = Depends(current_person),
                       limit: int = Query(default=80, ge=1, le=300)) -> list[dict]:
    return _turns(person.id, limit)


@router.post("")
async def chat_send(body: ChatMessage, person: Person = Depends(current_person)) -> dict:
    key = f"chat:{person.id}"
    if chat_throttle.blocked(key):
        raise HTTPException(status_code=429, detail="That's a lot of messages at once. Give it a few minutes.")
    chat_throttle.fail(key)  # counts every message, not just failures
    session_id = sessions.web_session_for(person.id)
    db.log_activity("message", f"Web chat from {person.display_name}: {body.message[:80]}", person.id, session_id)
    reply = await agent_core.respond(person, "web", session_id, body.message)
    return {"reply": reply, "session_id": session_id, "ts": db.now_iso()}


@router.post("/reset")
async def chat_reset(person: Person = Depends(current_person)) -> dict:
    sessions.reset_context(person.id, "web")
    return {"ok": True}
