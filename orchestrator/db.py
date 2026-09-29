"""SQLite persistence: registry, sessions, transcripts, recordings, tool calls, activity, tasks.

One short-lived connection per call keeps this safe to use from async handlers and
background threads alike; at this scale the overhead is negligible.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    name         TEXT PRIMARY KEY,
    description  TEXT NOT NULL DEFAULT '',
    can_telegram INTEGER NOT NULL DEFAULT 1,
    can_call     INTEGER NOT NULL DEFAULT 1,
    can_dashboard INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    role         TEXT NOT NULL REFERENCES roles(name),
    active       INTEGER NOT NULL DEFAULT 1,
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_identifiers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type          TEXT NOT NULL CHECK (type IN ('telegram','phone','email','dashboard')),
    value         TEXT NOT NULL,
    password_hash TEXT,                       -- only for type='dashboard'
    UNIQUE (type, value)
);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    channel    TEXT NOT NULL CHECK (channel IN ('call','telegram')),
    direction  TEXT NOT NULL DEFAULT 'inbound',
    external_id TEXT,                         -- Twilio CallSid / Telegram chat id
    started_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    ended_at   TEXT,
    status     TEXT NOT NULL DEFAULT 'active' -- active | ended | failed
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, channel, started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS transcripts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    turn_index INTEGER NOT NULL,
    speaker    TEXT NOT NULL CHECK (speaker IN ('user','assistant','system')),
    text       TEXT NOT NULL,
    ts         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transcripts_session ON transcripts(session_id, turn_index);

CREATE TABLE IF NOT EXISTS recordings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    file_path  TEXT NOT NULL,                 -- relative to STORAGE_ROOT
    duration   REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT REFERENCES sessions(id),
    user_id    INTEGER REFERENCES users(id),
    tool_name  TEXT NOT NULL,
    payload    TEXT NOT NULL,
    result     TEXT,
    is_error   INTEGER NOT NULL DEFAULT 0,
    ts         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_attempts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    channel        TEXT NOT NULL,
    raw_identifier TEXT NOT NULL,
    resolved       INTEGER NOT NULL,
    detail         TEXT NOT NULL DEFAULT '',
    ts             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind     TEXT NOT NULL,                   -- message | call | tool | access | system | n8n
    user_id  INTEGER REFERENCES users(id),
    session_id TEXT,
    summary  TEXT NOT NULL,
    ts       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity(ts);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    schedule    TEXT NOT NULL DEFAULT '',     -- free text or cron expression
    source      TEXT NOT NULL DEFAULT 'local',-- local | n8n
    external_id TEXT,
    next_run_at TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS n8n_callbacks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id TEXT,
    session_id  TEXT,
    payload     TEXT NOT NULL,
    ts          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dashboard_sessions (
    token_hash   TEXT PRIMARY KEY,            -- HMAC of the cookie token; the token itself is never stored
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL,               -- absolute lifetime cap
    ip           TEXT NOT NULL DEFAULT '',
    user_agent   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_dash_sessions_user ON dashboard_sessions(user_id);

CREATE TABLE IF NOT EXISTS scheduled_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    target_user_id INTEGER NOT NULL REFERENCES users(id),
    requested_by   INTEGER REFERENCES users(id),
    run_at         TEXT NOT NULL,             -- UTC ISO; "now" for immediate calls
    purpose        TEXT NOT NULL DEFAULT '',  -- what to ask/tell; empty = plain call, no report
    opening_line   TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'pending', -- pending|dialing|done|no_answer|failed|cancelled
    attempts       INTEGER NOT NULL DEFAULT 0,
    session_id     TEXT,
    result         TEXT,                      -- outcome summary reported to the requester
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sched_due ON scheduled_calls(status, run_at);

CREATE TABLE IF NOT EXISTS api_usage (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                 TEXT NOT NULL,
    provider           TEXT NOT NULL,         -- anthropic | elevenlabs | twilio | google | n8n
    kind               TEXT NOT NULL,         -- chat | call | summary | tts | stt | voice | calendar | api
    model              TEXT NOT NULL DEFAULT '',
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    units              REAL NOT NULL DEFAULT 0, -- chars / seconds / requests, see unit
    unit               TEXT NOT NULL DEFAULT '',
    cost_usd           REAL NOT NULL DEFAULT 0,
    user_id            INTEGER REFERENCES users(id),
    session_id         TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON api_usage(ts);

CREATE TABLE IF NOT EXISTS google_accounts (
    user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    email             TEXT NOT NULL DEFAULT '',
    refresh_token_enc TEXT NOT NULL,          -- Fernet-encrypted
    access_token_enc  TEXT,
    access_expires_at TEXT,
    scopes            TEXT NOT NULL DEFAULT '',
    calendar_id       TEXT NOT NULL DEFAULT 'primary',
    linked_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_states (
    state_hash    TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_verifier TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS health (
    service    TEXT PRIMARY KEY,
    ok         INTEGER NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL
);
"""

DEFAULT_ROLES = [
    # name, description, telegram, call, dashboard
    ("admin", "Full access to every tool and channel.", 1, 1, 1),
    ("cofounder", "Just under admin: all tools except n8n workflow management; own dashboard.", 1, 1, 1),
    ("friend", "Chat and call; only tools explicitly granted to 'friend'.", 1, 1, 0),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with tx() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT OR IGNORE INTO roles(name, description, can_telegram, can_call, can_dashboard) "
            "VALUES (?,?,?,?,?)",
            DEFAULT_ROLES,
        )


def execute(sql: str, params: tuple | list = ()) -> int:
    """Run a write; returns lastrowid."""
    with tx() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid or 0


def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def query_one(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


# --- small domain helpers used across modules -------------------------------------------

def log_activity(kind: str, summary: str, user_id: int | None = None, session_id: str | None = None) -> None:
    execute(
        "INSERT INTO activity(kind, user_id, session_id, summary, ts) VALUES (?,?,?,?,?)",
        (kind, user_id, session_id, summary[:500], now_iso()),
    )


def log_tool_call(
    tool_name: str,
    payload: dict,
    result: Any,
    is_error: bool,
    session_id: str | None,
    user_id: int | None,
) -> None:
    execute(
        "INSERT INTO tool_calls(session_id, user_id, tool_name, payload, result, is_error, ts) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            session_id,
            user_id,
            tool_name,
            json.dumps(payload, ensure_ascii=False),
            result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str),
            int(is_error),
            now_iso(),
        ),
    )


def set_health(service: str, ok: bool, detail: str = "") -> None:
    execute(
        "INSERT INTO health(service, ok, detail, checked_at) VALUES (?,?,?,?) "
        "ON CONFLICT(service) DO UPDATE SET ok=excluded.ok, detail=excluded.detail, "
        "checked_at=excluded.checked_at",
        (service, int(ok), detail[:300], now_iso()),
    )
