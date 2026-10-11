"""Session lifecycle, transcript turns, per-(channel,user) conversation history, live state.

Conversation context is scoped per (channel, user): history is rebuilt from every
transcript that user has on that channel, so a Telegram thread continues across
sessions while phone calls keep their own separate thread.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db
from .config import settings

log = logging.getLogger(__name__)


@dataclass
class LiveState:
    """In-memory view of a session in progress, for the dashboard's live panel."""
    session_id: str
    user_id: int
    channel: str
    busy: bool = False          # agent currently thinking / speaking
    speaking: bool = False      # call: TTS audio playing
    partial: str = ""           # call: current partial STT text
    extra: dict[str, Any] = field(default_factory=dict)


LIVE: dict[str, LiveState] = {}


def new_session_id(channel: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    prefix = {"telegram": "tg", "call": "call", "web": "web", "email": "mail"}.get(channel, channel)
    return f"{prefix}-{stamp}-{secrets.token_hex(3)}"


def start_session(user_id: int, channel: str, direction: str = "inbound", external_id: str | None = None) -> str:
    sid = new_session_id(channel)
    now = db.now_iso()
    db.execute(
        "INSERT INTO sessions(id, user_id, channel, direction, external_id, started_at, last_activity_at, status) "
        "VALUES (?,?,?,?,?,?,?, 'active')",
        (sid, user_id, channel, direction, external_id, now, now),
    )
    LIVE[sid] = LiveState(session_id=sid, user_id=user_id, channel=channel)
    db.log_activity(channel if channel == "call" else "message",
                    f"{ {'call': 'Call', 'telegram': 'Telegram session', 'web': 'Web chat', 'email': 'Email conversation'}.get(channel, channel)} "
                    f"started ({direction})",
                    user_id=user_id, session_id=sid)
    return sid


def touch(session_id: str) -> None:
    db.execute("UPDATE sessions SET last_activity_at = ? WHERE id = ?", (db.now_iso(), session_id))


def end_session(session_id: str, status: str = "ended") -> None:
    row = db.query_one("SELECT user_id, channel, status FROM sessions WHERE id = ?", (session_id,))
    LIVE.pop(session_id, None)
    if not row or row["status"] != "active":
        return
    db.execute("UPDATE sessions SET ended_at = ?, status = ? WHERE id = ?", (db.now_iso(), status, session_id))
    db.log_activity("call" if row["channel"] == "call" else "message",
                    f"Session {status}", user_id=row["user_id"], session_id=session_id)
    # Persist the transcript file next to any audio (storage layer owns the layout).
    from .storage import recordings
    try:
        recordings.write_transcript(session_id)
    except Exception:  # never let storage problems break session teardown
        log.exception("Failed writing transcript for %s", session_id)


def add_turn(session_id: str, speaker: str, text: str) -> None:
    text = text.strip()
    if not text:
        return
    with db.tx() as conn:
        idx = conn.execute(
            "SELECT COALESCE(MAX(turn_index), -1) + 1 FROM transcripts WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO transcripts(session_id, turn_index, speaker, text, ts) VALUES (?,?,?,?,?)",
            (session_id, idx, speaker, text, db.now_iso()),
        )
        conn.execute("UPDATE sessions SET last_activity_at = ? WHERE id = ?", (db.now_iso(), session_id))


def history(user_id: int, channel: str, exclude_session: str | None = None, limit: int | None = None) -> list[dict]:
    """Last N user/assistant turns for this (channel, user), oldest first, API-ready."""
    limit = limit or settings.history_turns
    reset = db.query_one("SELECT after_id FROM context_resets WHERE user_id = ? AND channel = ?", (user_id, channel))
    rows = db.query(
        "SELECT t.speaker, t.text FROM transcripts t JOIN sessions s ON s.id = t.session_id "
        "WHERE s.user_id = ? AND s.channel = ? AND t.speaker IN ('user','assistant') "
        "AND (? IS NULL OR s.id != ?) AND t.id > ? "
        "ORDER BY t.ts DESC, t.id DESC LIMIT ?",
        (user_id, channel, exclude_session, exclude_session, reset["after_id"] if reset else 0, limit),
    )
    rows.reverse()
    msgs: list[dict] = []
    for r in rows:
        if msgs and msgs[-1]["role"] == r["speaker"]:
            msgs[-1]["content"] += "\n" + r["text"]
        else:
            msgs.append({"role": r["speaker"], "content": r["text"]})
    while msgs and msgs[0]["role"] != "user":  # API requires the first turn to be the user
        msgs.pop(0)
    return msgs


def session_turns(session_id: str) -> list[dict]:
    return db.query(
        "SELECT turn_index, speaker, text, ts FROM transcripts WHERE session_id = ? ORDER BY turn_index",
        (session_id,),
    )


# --- Telegram sessions: opened on first message, closed after idle timeout ----------------

def telegram_session_for(user_id: int, chat_id: str) -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=settings.telegram_session_idle_minutes)).isoformat(
        timespec="seconds"
    )
    row = db.query_one(
        "SELECT id FROM sessions WHERE user_id = ? AND channel = 'telegram' AND status = 'active' "
        "AND last_activity_at >= ? ORDER BY started_at DESC LIMIT 1",
        (user_id, cutoff),
    )
    if row:
        if row["id"] not in LIVE:
            LIVE[row["id"]] = LiveState(session_id=row["id"], user_id=user_id, channel="telegram")
        return row["id"]
    return start_session(user_id, "telegram", external_id=chat_id)


def web_session_for(user_id: int) -> str:
    """Dashboard chat: reuse the open session while it's active, like Telegram."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=settings.telegram_session_idle_minutes)).isoformat(
        timespec="seconds"
    )
    row = db.query_one(
        "SELECT id FROM sessions WHERE user_id = ? AND channel = 'web' AND status = 'active' "
        "AND last_activity_at >= ? ORDER BY started_at DESC LIMIT 1",
        (user_id, cutoff),
    )
    if row:
        if row["id"] not in LIVE:
            LIVE[row["id"]] = LiveState(session_id=row["id"], user_id=user_id, channel="web")
        return row["id"]
    return start_session(user_id, "web")


def email_session_for(user_id: int, thread: str) -> str:
    """Email: one session per mail conversation (thread = the first message's Message-ID)."""
    row = db.query_one("SELECT id FROM sessions WHERE user_id = ? AND channel = 'email' AND external_id = ? "
                       "ORDER BY started_at DESC LIMIT 1", (user_id, thread[:300]))
    if row:
        db.execute("UPDATE sessions SET status = 'active', ended_at = NULL WHERE id = ?", (row["id"],))
        return row["id"]
    return start_session(user_id, "email", external_id=thread[:300])


def reset_context(user_id: int, channel: str) -> None:
    """Start a fresh conversation: earlier turns stay stored but are no longer sent to the model."""
    for row in db.query("SELECT id FROM sessions WHERE user_id = ? AND channel = ? AND status = 'active'",
                        (user_id, channel)):
        end_session(row["id"])
    last = db.query_one("SELECT COALESCE(MAX(id), 0) AS id FROM transcripts")["id"]
    db.execute("INSERT INTO context_resets(user_id, channel, after_id, ts) VALUES (?,?,?,?) "
               "ON CONFLICT(user_id, channel) DO UPDATE SET after_id = excluded.after_id, ts = excluded.ts",
               (user_id, channel, last, db.now_iso()))


def close_idle_sessions() -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=settings.telegram_session_idle_minutes)).isoformat(
        timespec="seconds"
    )
    for row in db.query(
        "SELECT id FROM sessions WHERE channel IN ('telegram', 'web', 'email') AND status = 'active' AND last_activity_at < ?",
        (cutoff,)
    ):
        end_session(row["id"])


def mark_orphans_on_startup() -> None:
    """Calls can't survive a restart; close anything left 'active' from a previous run."""
    for row in db.query("SELECT id, channel FROM sessions WHERE status = 'active'"):
        if row["channel"] == "call":
            end_session(row["id"], status="failed")
