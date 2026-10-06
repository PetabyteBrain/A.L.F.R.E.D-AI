"""Dashboard chat: talk to the assistant from the website (channel "web").

Same Agent Core, role-gated tools and per-(channel, user) memory as Telegram; only people whose
role has dashboard access can use it. A per-person rate limit keeps a stuck client or script
from running up API costs. `/stream` sends the reply as Server-Sent Events while it's written.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import agent_core, db, sessions
from orchestrator.identity.registry import Person

from . import dashboard_guide, security
from .routes import current_person

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat")

KEEPALIVE_S = 15          # comment line while a slow tool runs, so proxies don't drop the stream
_running: set[asyncio.Task] = set()

chat_throttle = security.Throttle(window_s=300, max_failures=20)  # 20 messages / 5 min / person


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=4000)
    page: str | None = Field(default=None, max_length=20)   # set by the avatar helper: the tab it was opened on


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


def _begin(body: ChatMessage, person: Person) -> str:
    key = f"chat:{person.id}"
    if chat_throttle.blocked(key):
        raise HTTPException(status_code=429, detail="That's a lot of messages at once. Give it a few minutes.")
    chat_throttle.fail(key)  # counts every message, not just failures
    session_id = sessions.web_session_for(person.id)
    db.log_activity("message", f"Web chat from {person.display_name}: {body.message[:80]}", person.id, session_id)
    return session_id


@router.post("")
async def chat_send(body: ChatMessage, person: Person = Depends(current_person)) -> dict:
    session_id = _begin(body, person)
    reply = await agent_core.respond(person, "web", session_id, body.message)
    return {"reply": reply, "session_id": session_id, "ts": db.now_iso()}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def chat_stream(body: ChatMessage, person: Person = Depends(current_person)) -> StreamingResponse:
    """Events: {type: delta, text} while writing, {type: tool, name} before a tool runs (the text so
    far was a preamble), then {type: done, reply, ts} with the final stored reply, or {type: error}."""
    session_id = _begin(body, person)
    queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

    async def on_event(kind: str, data: str) -> None:
        queue.put_nowait((kind, data))

    async def run() -> None:
        # Runs independently of the HTTP response: if the browser goes away mid-reply the exchange
        # still finishes and is saved, so it shows up in the history on the next visit.
        try:
            extra = dashboard_guide.context(person, body.page) if body.page in dashboard_guide.PAGES else None
            queue.put_nowait(("done", await agent_core.respond(person, "web", session_id, body.message,
                                                               extra_system=extra, on_event=on_event)))
        except Exception:
            log.exception("Web chat reply failed")
            queue.put_nowait(("error", "Something went wrong on my side. Please try again."))

    task = asyncio.create_task(run())
    _running.add(task)
    task.add_done_callback(_running.discard)

    async def events():
        while True:
            try:
                kind, data = await asyncio.wait_for(queue.get(), KEEPALIVE_S)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if kind == "delta":
                yield _sse({"type": "delta", "text": data})
            elif kind == "tool":
                yield _sse({"type": "tool", "name": data})
            elif kind == "done":
                yield _sse({"type": "done", "reply": data, "session_id": session_id, "ts": db.now_iso()})
                return
            else:
                yield _sse({"type": "error", "detail": data})
                return

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/reset")
async def chat_reset(person: Person = Depends(current_person)) -> dict:
    sessions.reset_context(person.id, "web")
    return {"ok": True}
