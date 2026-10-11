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
    verified_at   TEXT,                       -- phone/telegram only count once verified (code check)
    UNIQUE (type, value)
);

-- Reminders (orchestrator/reminders.py): one row per occurrence.
CREATE TABLE IF NOT EXISTS reminders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- who gets reminded
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    text         TEXT NOT NULL,
    due_at       TEXT NOT NULL,                     -- UTC ISO
    recurrence   TEXT CHECK (recurrence IN ('daily','weekdays','weekly','monthly')),
    channel      TEXT NOT NULL DEFAULT 'telegram' CHECK (channel IN ('telegram','call')),
    escalate     INTEGER NOT NULL DEFAULT 0,        -- Telegram not acknowledged in 10 min -> phone call
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','sending','sent','done','cancelled','failed')),
    sent_at      TEXT,
    acked_at     TEXT,
    escalated_at TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at);

-- Long-term memory notes (orchestrator/memory.py); only used in the owner's own conversations.
CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);

-- Small key/value store for settings edited on the dashboard (e.g. default quiet hours).
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT
);

-- Per-person quiet hours (orchestrator/quiet.py): follow the default, own window, or off.
CREATE TABLE IF NOT EXISTS quiet_hours (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    mode       TEXT NOT NULL DEFAULT 'default' CHECK (mode IN ('default','custom','off')),
    start      TEXT,
    end        TEXT,
    updated_at TEXT
);

-- Meeting scheduling (orchestrator/meetings.py)
CREATE TABLE IF NOT EXISTS meetings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    organizer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    duration_min INTEGER NOT NULL,
    slots_json   TEXT NOT NULL,                     -- proposed starts, UTC ISO
    status       TEXT NOT NULL DEFAULT 'proposed',  -- proposed|booking|booked|no_match|cancelled|expired
    booked_start TEXT,
    event_id     TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meeting_invites (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id    INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel       TEXT NOT NULL,                    -- telegram | call
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | answered | closed
    accepted_json TEXT NOT NULL DEFAULT '[]',       -- 0-based slot indexes that work
    answered_at   TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (meeting_id, user_id)
);

-- Daily briefing settings per person (orchestrator/briefing.py).
CREATE TABLE IF NOT EXISTS briefings (
    user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enabled      INTEGER NOT NULL DEFAULT 0,
    time_local   TEXT NOT NULL DEFAULT '07:30',
    days         TEXT NOT NULL DEFAULT 'daily' CHECK (days IN ('daily','weekdays')),
    channel      TEXT NOT NULL DEFAULT 'telegram' CHECK (channel IN ('telegram','call')),
    location     TEXT,                              -- weather place (name only is sent to Open-Meteo)
    lat          REAL,
    lon          REAL,
    last_sent_on TEXT,                              -- local date of the last scheduled briefing
    updated_at   TEXT
);

-- One pending confirmation code per phone/Telegram connection (see identity/verification.py).
CREATE TABLE IF NOT EXISTS identifier_verifications (
    identifier_id INTEGER PRIMARY KEY REFERENCES user_identifiers(id) ON DELETE CASCADE,
    code_hash     TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    requested_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    sent_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verification_sends (   -- rate limit for code calls/messages
    identifier_value TEXT NOT NULL,
    requested_by     INTEGER,
    ts               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    channel    TEXT NOT NULL CHECK (channel IN ('call','telegram','web','email')),
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

-- Full-text index over transcripts for search_conversations (orchestrator/search.py).
CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
    text, content='transcripts', content_rowid='id', tokenize='porter unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS transcripts_fts_ai AFTER INSERT ON transcripts BEGIN
    INSERT INTO transcripts_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS transcripts_fts_ad AFTER DELETE ON transcripts BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS transcripts_fts_au AFTER UPDATE OF text ON transcripts BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO transcripts_fts(rowid, text) VALUES (new.id, new.text);
END;

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

-- Two-factor sign-in for the dashboard (orchestrator/identity/mfa.py).
CREATE TABLE IF NOT EXISTS user_mfa (
    user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    totp_secret_enc   TEXT,                   -- Fernet-encrypted authenticator secret
    totp_confirmed_at TEXT,                   -- NULL = set-up started but not confirmed yet
    totp_last_step    INTEGER NOT NULL DEFAULT 0,  -- last accepted 30 s step (no code reuse)
    telegram_enabled  INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mfa_backup_codes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL,
    used_at   TEXT
);
CREATE TABLE IF NOT EXISTS voice_mode (       -- Telegram replies also as voice notes (voice_replies.py)
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    until      TEXT,                          -- UTC ISO; NULL = until switched off
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nas_recent (          -- recently viewed / edited files in the explorer
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope   TEXT NOT NULL,
    path    TEXT NOT NULL,
    action  TEXT NOT NULL,                      -- viewed | edited
    at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nas_recent_user ON nas_recent(user_id, action);
CREATE TABLE IF NOT EXISTS event_categories (   -- category of a Google Calendar event in the agenda
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    category TEXT NOT NULL,
    PRIMARY KEY (user_id, event_id)
);
CREATE TABLE IF NOT EXISTS nas_folders (       -- each person's folder in the personal NAS share (nas.py)
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    folder     TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS external_calls (    -- task calls to numbers outside the registry (external_calls.py)
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    requested_by INTEGER REFERENCES users(id) ON DELETE CASCADE,
    number       TEXT NOT NULL,
    contact      TEXT NOT NULL,
    task         TEXT NOT NULL,
    share        TEXT NOT NULL DEFAULT '',
    run_at       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending', -- pending|dialing|finishing|done|no_answer|failed|cancelled
    attempts     INTEGER NOT NULL DEFAULT 0,
    session_id   TEXT,
    result       TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS screened_calls (    -- unknown callers Alfred took a message from (screening.py)
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    caller     TEXT NOT NULL,
    session_id TEXT,
    summary    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_screened_caller ON screened_calls(caller, ts);
CREATE TABLE IF NOT EXISTS data_requests (    -- "delete my data" (privacy.py); an admin confirms
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope       TEXT NOT NULL,                -- history | account
    note        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | rejected (done requests vanish with the data)
    created_at  TEXT NOT NULL,
    decided_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    decided_at  TEXT
);
CREATE TABLE IF NOT EXISTS mfa_challenges (   -- password was right, second factor pending
    token_hash   TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at   TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    tg_code_hash TEXT,
    tg_sent_at   TEXT,
    tg_sends     INTEGER NOT NULL DEFAULT 0
);

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

CREATE TABLE IF NOT EXISTS context_resets (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel  TEXT NOT NULL,
    after_id INTEGER NOT NULL,                -- only transcript rows with id > after_id count as context
    ts       TEXT NOT NULL,
    PRIMARY KEY (user_id, channel)
);

-- Alfred's own email (orchestrator/mail.py): every message in and out; the .eml itself is archived on the NAS.
CREATE TABLE IF NOT EXISTS emails (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    direction    TEXT NOT NULL CHECK (direction IN ('in','out')),
    kind         TEXT NOT NULL,              -- in: request|reply|inquiry|unverified|automated|spam; out: sent|reply|auto_reply|notice
    status       TEXT NOT NULL DEFAULT 'received',  -- received|done|ignored|failed (in); sent|failed (out)
    scope        TEXT NOT NULL DEFAULT 'company',   -- personal (a registered person's mail) | company
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,  -- whose mail it is (sender, requester)
    message_id   TEXT,
    in_reply_to  TEXT,
    thread_id    INTEGER,                    -- first email of the conversation
    from_addr    TEXT NOT NULL DEFAULT '',
    from_name    TEXT NOT NULL DEFAULT '',
    to_addrs     TEXT NOT NULL DEFAULT '[]', -- JSON lists
    cc_addrs     TEXT NOT NULL DEFAULT '[]',
    subject      TEXT NOT NULL DEFAULT '',
    body_text    TEXT NOT NULL DEFAULT '',
    own_text     TEXT NOT NULL DEFAULT '',   -- inbound: the sender's own words, without the quoted thread
    attachments  TEXT NOT NULL DEFAULT '[]', -- [{name, size, type}]
    auth         TEXT NOT NULL DEFAULT '{}', -- spf/dkim/dmarc/spam verdicts of inbound mail
    summary      TEXT NOT NULL DEFAULT '',
    provider_id  TEXT UNIQUE,                -- inbound provider's id (duplicate webhooks)
    delete_url   TEXT,                       -- provider copy, removed once archived
    spool_path   TEXT,                       -- local .eml until it is on the NAS
    nas_path     TEXT,                       -- personal:<folder>/... or company:...
    error        TEXT,
    created_at   TEXT NOT NULL,
    processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_emails_msgid ON emails(message_id);
CREATE INDEX IF NOT EXISTS idx_emails_user ON emails(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_emails_status ON emails(status, direction);

-- Email templates (orchestrator/email_templates.py): layouts (banner, colours, signature, footer) and reusable texts.
CREATE TABLE IF NOT EXISTS mail_templates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL CHECK (kind IN ('layout','text')),
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    active       INTEGER NOT NULL DEFAULT 1,
    archived     INTEGER NOT NULL DEFAULT 0,
    builtin      TEXT,                       -- company | alfred | inquiry_ack for the seeded ones
    company      INTEGER NOT NULL DEFAULT 0, -- layout: mail in the company's name (admins/cofounders, company mail)
    style        TEXT NOT NULL DEFAULT '{}', -- layout colours and font (JSON)
    from_name    TEXT NOT NULL DEFAULT '',
    signature_md TEXT NOT NULL DEFAULT '',
    footer_md    TEXT NOT NULL DEFAULT '',
    banner       BLOB,                       -- uploaded banner (PNG/JPEG); NULL = built-in file or none
    banner_type  TEXT,
    subject      TEXT NOT NULL DEFAULT '',   -- text templates
    body_md      TEXT NOT NULL DEFAULT '',
    layout_id    INTEGER REFERENCES mail_templates(id) ON DELETE SET NULL,
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- Attachments of stored emails (orchestrator/mail_attachments.py). The content stays inside the archived .eml
-- (same retention as the mail); this is the index: order, name, type, size, checksum, risk flag.
CREATE TABLE IF NOT EXISTS email_attachments (
    email_id   INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,             -- 1-based, order in the message
    name       TEXT NOT NULL,
    mime       TEXT NOT NULL DEFAULT '',
    size       INTEGER NOT NULL DEFAULT 0,
    sha256     TEXT NOT NULL DEFAULT '',
    flagged    TEXT NOT NULL DEFAULT '',     -- why it's risky ('' = fine): never previewed or filed
    saved_to   TEXT NOT NULL DEFAULT '',     -- last place it was filed (scope:path)
    PRIMARY KEY (email_id, idx)
);

-- Mail groups (an email is in at most one) and tags (any number), per person for personal mail and shared by
-- the team for company mail; plus each viewer's own read / starred / archived state (orchestrator/mail_labels.py).
CREATE TABLE IF NOT EXISTS mail_labels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL CHECK (kind IN ('group','tag')),
    scope      TEXT NOT NULL CHECK (scope IN ('personal','company')),
    user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,   -- owner of a personal label; NULL for company
    name       TEXT NOT NULL,
    color      TEXT NOT NULL DEFAULT 'slate',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS email_labels (
    email_id INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    label_id INTEGER NOT NULL REFERENCES mail_labels(id) ON DELETE CASCADE,
    PRIMARY KEY (email_id, label_id)
);
CREATE TABLE IF NOT EXISTS email_state (
    email_id INTEGER NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    read_at  TEXT,
    starred  INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (email_id, user_id)
);

-- Matters Alfred can't or may not decide, passed to the admins and cofounders (orchestrator/escalation.py).
CREATE TABLE IF NOT EXISTS escalations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,              -- email | telegram | web | call
    email_id     INTEGER REFERENCES emails(id) ON DELETE SET NULL,
    requester_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    requester    TEXT NOT NULL DEFAULT '',   -- name / address as shown to the team
    topic        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    urgency      TEXT NOT NULL DEFAULT 'normal',  -- low | normal | high | critical
    status       TEXT NOT NULL DEFAULT 'open',    -- open | handled
    notified     TEXT NOT NULL DEFAULT '[]',      -- [{user_id, name, via}]
    task_id      INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    note         TEXT NOT NULL DEFAULT '',
    handled_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    handled_at   TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_escalations_status ON escalations(status, created_at);

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


def _migrate_sessions_channel_check() -> None:
    """Older databases allow fewer channels (('call','telegram') before the web chat, no 'email' before
    Alfred's mailbox). SQLite can't alter a CHECK constraint, so rebuild the table (official 12-step
    procedure), with foreign keys off for the swap and verified afterwards."""
    import re
    conn = sqlite3.connect(settings.db_path, timeout=10)
    try:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone()
        if not row or "'email'" in row[0]:
            return
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        conn.execute(re.sub(r"CHECK \(channel IN \([^)]*\)\)", "CHECK (channel IN ('call','telegram','web','email'))",
                            re.sub(r'CREATE TABLE\s+["`]?sessions["`]?', "CREATE TABLE sessions_new", row[0], count=1)))
        conn.execute("INSERT INTO sessions_new SELECT * FROM sessions")
        conn.execute("DROP TABLE sessions")
        conn.execute("ALTER TABLE sessions_new RENAME TO sessions")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, channel, started_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)")
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"foreign key check failed after migration: {problems[:3]}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK") if conn.in_transaction else None
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.close()


def _migrate_identifier_verified_at() -> None:
    """Connections that existed before verification was introduced were already in use: keep
    them working by marking them verified."""
    conn = sqlite3.connect(settings.db_path, timeout=10)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_identifiers'").fetchone():
            return
        cols = {r[1] for r in conn.execute("PRAGMA table_info(user_identifiers)")}
        if "verified_at" in cols:
            return
        with conn:
            conn.execute("ALTER TABLE user_identifiers ADD COLUMN verified_at TEXT")
            conn.execute("UPDATE user_identifiers SET verified_at = ?", (now_iso(),))
    finally:
        conn.close()


# Columns added after tables already existed in production: (table, column, declaration).
ADDED_COLUMNS = [
    ("api_usage", "account", "TEXT"),                              # ai_config key name: primary | secondary
    ("users", "cross_channel", "INTEGER NOT NULL DEFAULT 1"),         # continuity.py: recent turns from other channels
    ("scheduled_calls", "urgent", "INTEGER NOT NULL DEFAULT 0"),      # skips the target's quiet hours
    ("tasks", "owner_user_id", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
    ("tasks", "requested_by", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
    ("tasks", "due_at", "TEXT"),
    ("tasks", "notes", "TEXT NOT NULL DEFAULT ''"),
    ("tasks", "follow_up", "TEXT NOT NULL DEFAULT 'none'"),          # none | telegram | call
    ("tasks", "follow_up_at", "TEXT"),
    ("tasks", "follow_ups", "INTEGER NOT NULL DEFAULT 0"),
    ("tasks", "last_follow_up_at", "TEXT"),
    ("meeting_invites", "send_after", "TEXT"),                               # queued until quiet hours end
    ("google_accounts", "share_availability", "INTEGER NOT NULL DEFAULT 0"),   # opt-in free/busy for meetings
    ("tasks", "completed_at", "TEXT"),
    ("tasks", "completed_by", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
    ("tasks", "result", "TEXT"),
    ("recordings", "deleted_at", "TEXT"),        # audio removed by the retention policy (transcript kept)
    # Reminders tied to Google Calendar events + categories (agenda.py)
    ("reminders", "event_id", "TEXT"),
    ("reminders", "event_offset_min", "INTEGER"),
    ("reminders", "link_state", "TEXT NOT NULL DEFAULT 'none'"),   # none | linked | event_deleted | duplicate
    ("reminders", "link_note", "TEXT"),
    ("reminders", "category", "TEXT NOT NULL DEFAULT 'reminder'"),
    ("tasks", "category", "TEXT NOT NULL DEFAULT 'task'"),
    ("dashboard_sessions", "view_as", "TEXT"),   # admin previewing the dashboard as another role
    ("roles", "can_email", "INTEGER NOT NULL DEFAULT 1"),                # may write to Alfred by email
    ("user_identifiers", "preferred", "INTEGER NOT NULL DEFAULT 0"),     # the email address Alfred writes to
    ("emails", "layout_id", "INTEGER"),                                  # email_templates: layout and text used
    ("emails", "template_id", "INTEGER"),
]


def _add_missing_columns() -> None:
    conn = sqlite3.connect(settings.db_path, timeout=10)
    try:
        with conn:
            for table, col, decl in ADDED_COLUMNS:
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                if cols and col not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    finally:
        conn.close()


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    _migrate_sessions_channel_check()
    _migrate_identifier_verified_at()
    with tx() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT OR IGNORE INTO roles(name, description, can_telegram, can_call, can_dashboard) "
            "VALUES (?,?,?,?,?)",
            DEFAULT_ROLES,
        )
    _add_missing_columns()
    _build_search_index()


def _build_search_index() -> None:
    """Fill the transcript search index once (existing databases, or after a restore)."""
    with tx() as conn:
        indexed = conn.execute("SELECT COUNT(*) FROM transcripts_fts_docsize").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM transcripts").fetchone()[0]
        if indexed != total:
            conn.execute("INSERT INTO transcripts_fts(transcripts_fts) VALUES ('rebuild')")


def execute(sql: str, params: tuple | list = ()) -> int:
    """Run a write; returns lastrowid."""
    with tx() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid or 0


def execute_count(sql: str, params: tuple | list = ()) -> int:
    """Run a write; returns the number of affected rows (for atomic claim/compare-and-set)."""
    with tx() as conn:
        return conn.execute(sql, params).rowcount


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


def get_setting(key: str, default: Any = None) -> Any:
    """JSON value from app_settings."""
    row = query_one("SELECT value FROM app_settings WHERE key = ?", (key,))
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value: Any) -> None:
    execute("INSERT INTO app_settings(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated_at = excluded.updated_at", (key, json.dumps(value), now_iso()))


def set_health(service: str, ok: bool, detail: str = "") -> None:
    execute(
        "INSERT INTO health(service, ok, detail, checked_at) VALUES (?,?,?,?) "
        "ON CONFLICT(service) DO UPDATE SET ok=excluded.ok, detail=excluded.detail, "
        "checked_at=excluded.checked_at",
        (service, int(ok), detail[:300], now_iso()),
    )
