"""Search past conversations (all channels) with SQLite FTS5.

`transcripts_fts` is an external-content FTS5 index over `transcripts`, kept in sync by triggers
(db.SCHEMA) and rebuilt once on start if it's empty. English stemming (porter), so "invoices"
finds "invoice".

Who can find what:
- everyone: their own conversations (phone, Telegram, web) and the calls Alfred made on their
  behalf (scheduled_calls.requested_by), including what the other person said;
- admins: everyone's, optionally narrowed to one person.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db
from .config import settings
from .identity.registry import Person

MAX_RESULTS = 10
CONTEXT_TURNS = 1          # turns before/after each hit
_WORD = re.compile(r"[\w'’-]+", re.UNICODE)


class SearchError(ValueError):
    """Message is safe to show to the user / the model."""


def _fts_query(text: str, mode: str) -> str:
    """User words -> a safe FTS5 query (every term quoted, prefix-matched); no FTS syntax leaks in."""
    words = [w.strip("'’-") for w in _WORD.findall(text or "")]
    words = [w for w in words if len(w) >= 2][:12]
    if not words:
        raise SearchError("Give me a word or two to search for.")
    return f" {mode} ".join(f'"{w}"*' for w in words)


def _local(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(ZoneInfo(settings.timezone)).strftime("%a %d %b %Y %H:%M")


def matching_sessions(query: str, where: str = "", params: list | None = None, limit: int = 200) -> dict[str, str]:
    """Archive search: {session_id: best snippet} for sessions whose transcript matches, best first.
    `where` is extra SQL on `s` (sessions) / `t` (transcripts), starting with " AND"."""
    def run(mode: str) -> list[dict]:
        return db.query(
            "SELECT t.session_id, snippet(transcripts_fts, 0, '«', '»', '…', 16) AS snippet "
            "FROM transcripts_fts JOIN transcripts t ON t.id = transcripts_fts.rowid "
            "JOIN sessions s ON s.id = t.session_id "
            f"WHERE transcripts_fts MATCH ?{where} AND t.speaker IN ('user', 'assistant') "
            "ORDER BY bm25(transcripts_fts) LIMIT 2000", (_fts_query(query, mode), *(params or [])))
    out: dict[str, str] = {}
    for r in run("AND") or run("OR"):
        if r["session_id"] not in out:
            out[r["session_id"]] = r["snippet"]
            if len(out) >= limit:
                break
    return out


def search(person: Person, query: str, *, about: Person | None = None, channel: str | None = None,
           days: int | None = None, limit: int = 8) -> list[dict]:
    if channel not in (None, "", "call", "telegram", "web"):
        raise SearchError("Channel must be call, telegram or web.")
    limit = max(1, min(int(limit or 8), MAX_RESULTS))
    where, params = [], []
    if person.is_admin:
        if about is not None:
            where.append("(s.user_id = ? OR sc.requested_by = ?)")
            params += [about.id, about.id]
    else:
        # Own sessions, plus calls made on this person's behalf. `about` narrows within that.
        where.append("(s.user_id = ? OR sc.requested_by = ?)")
        params += [person.id, person.id]
        if about is not None and about.id != person.id:
            where.append("s.user_id = ?")
            params.append(about.id)
    if channel:
        where.append("s.channel = ?")
        params.append(channel)
    if days:
        where.append("t.ts >= ?")
        params.append((datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat(timespec="seconds"))
    extra = (" AND " + " AND ".join(where)) if where else ""

    def run(mode: str) -> list[dict]:
        return db.query(
            "SELECT t.id, t.session_id, t.turn_index, t.speaker, t.ts, s.channel, s.direction, s.user_id, "
            "u.display_name AS person_name, r.display_name AS requested_by_name, "
            "snippet(transcripts_fts, 0, '«', '»', '…', 24) AS snippet "
            "FROM transcripts_fts JOIN transcripts t ON t.id = transcripts_fts.rowid "
            "JOIN sessions s ON s.id = t.session_id JOIN users u ON u.id = s.user_id "
            "LEFT JOIN scheduled_calls sc ON sc.session_id = s.id LEFT JOIN users r ON r.id = sc.requested_by "
            f"WHERE transcripts_fts MATCH ?{extra} AND t.speaker IN ('user', 'assistant') "
            "ORDER BY bm25(transcripts_fts), t.ts DESC LIMIT ?",
            (_fts_query(query, mode), *params, limit))

    rows = run("AND") or run("OR")      # all words if possible, otherwise any of them
    out = []
    for r in rows:
        ctx = db.query("SELECT turn_index, speaker, text FROM transcripts WHERE session_id = ? AND turn_index "
                       "BETWEEN ? AND ? ORDER BY turn_index",
                       (r["session_id"], r["turn_index"] - CONTEXT_TURNS, r["turn_index"] + CONTEXT_TURNS))
        other = r["person_name"]
        out.append({
            "when": _local(r["ts"]),
            "channel": r["channel"] + (f" ({r['direction']})" if r["channel"] == "call" and r["direction"] else ""),
            "conversation_with": other,
            **({"call_requested_by": r["requested_by_name"]} if r["requested_by_name"] else {}),
            "match": r["snippet"],
            "context": [{"speaker": settings.assistant_name if c["speaker"] == "assistant" else other,
                         "text": c["text"][:400]} for c in ctx],
            "session_id": r["session_id"],
        })
    return out
