"""Identity & Access Layer.

Every inbound event resolves to a Person *before* it reaches Agent Core. Anything that
doesn't resolve (or resolves to an inactive person / a role without access to that
channel) is dropped and recorded in `access_attempts` — the caller never gets a reply.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from .. import db
from ..config import settings

log = logging.getLogger(__name__)

IDENTIFIER_TYPES = ("telegram", "phone", "email", "dashboard")
CHANNEL_PERMISSION = {"telegram": "can_telegram", "call": "can_call", "dashboard": "can_dashboard"}
DEFAULT_COUNTRY_CODE = os.environ.get("DEFAULT_COUNTRY_CODE", "41")

_hasher = PasswordHasher()


@dataclass(frozen=True)
class Person:
    id: int
    display_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


# --- normalisation -----------------------------------------------------------------------

def normalize_phone(raw: str) -> str:
    """Best-effort E.164. Swiss-local numbers ('079 000 01 00') become +41790000100."""
    s = re.sub(r"[\s().\-/]", "", raw.strip())
    if s.startswith("00"):
        s = "+" + s[2:]
    elif s.startswith("0"):
        s = f"+{DEFAULT_COUNTRY_CODE}" + s[1:]
    elif not s.startswith("+") and s.isdigit():
        s = "+" + s
    return s


def normalize(id_type: str, value: str) -> str:
    value = str(value).strip()
    if id_type == "phone":
        return normalize_phone(value)
    if id_type in ("email", "dashboard"):
        return value.lower()
    return value  # telegram numeric ID


# --- resolution --------------------------------------------------------------------------

def _record_attempt(channel: str, raw: str, resolved: bool, detail: str = "") -> None:
    db.execute(
        "INSERT INTO access_attempts(channel, raw_identifier, resolved, detail, ts) VALUES (?,?,?,?,?)",
        (channel, raw[:200], int(resolved), detail[:200], db.now_iso()),
    )
    if not resolved:
        db.log_activity("access", f"Blocked {channel} access from {raw[:80]} ({detail or 'unknown'})")


def lookup(id_type: str, value: str) -> Person | None:
    """Pure lookup — no permission check, no logging."""
    row = db.query_one(
        "SELECT u.id, u.display_name, u.role FROM user_identifiers i "
        "JOIN users u ON u.id = i.user_id WHERE i.type = ? AND i.value = ? AND u.active = 1",
        (id_type, normalize(id_type, value)),
    )
    return Person(**row) if row else None


def role_allows_channel(role: str, channel: str) -> bool:
    col = CHANNEL_PERMISSION[channel]
    row = db.query_one(f"SELECT {col} AS ok FROM roles WHERE name = ?", (role,))
    return bool(row and row["ok"])


def resolve(channel: str, id_type: str, value: str, *, record_success: bool = False) -> Person | None:
    """Resolve an inbound identifier for a channel. Returns None → caller must drop silently."""
    person = lookup(id_type, value)
    if person is None:
        _record_attempt(channel, f"{id_type}:{value}", False, "unregistered")
        return None
    if not role_allows_channel(person.role, channel):
        _record_attempt(channel, f"{id_type}:{value}", False, f"role '{person.role}' has no {channel} access")
        return None
    if record_success:
        _record_attempt(channel, f"{id_type}:{value}", True, person.display_name)
    return person


def get_person(user_id: int) -> Person | None:
    row = db.query_one("SELECT id, display_name, role FROM users WHERE id = ? AND active = 1", (user_id,))
    return Person(**row) if row else None


def identifiers_for(user_id: int) -> list[dict]:
    return db.query("SELECT id, type, value FROM user_identifiers WHERE user_id = ? ORDER BY type", (user_id,))


def phone_for(user_id: int) -> str | None:
    row = db.query_one("SELECT value FROM user_identifiers WHERE user_id = ? AND type = 'phone' LIMIT 1", (user_id,))
    return row["value"] if row else None


# --- dashboard passwords -----------------------------------------------------------------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_dashboard_login(username: str, password: str) -> Person | None:
    uname = normalize("dashboard", username)
    row = db.query_one(
        "SELECT u.id, u.display_name, u.role, i.password_hash FROM user_identifiers i "
        "JOIN users u ON u.id = i.user_id WHERE i.type = 'dashboard' AND i.value = ? AND u.active = 1",
        (uname,),
    )
    # Always run a verify so timing doesn't reveal whether the username exists.
    stored = (row or {}).get("password_hash") or _DUMMY_HASH
    try:
        ok = _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        ok = False
    if not row or not ok:
        _record_attempt("dashboard", f"dashboard:{uname}", False, "bad credentials")
        return None
    person = Person(id=row["id"], display_name=row["display_name"], role=row["role"])
    if not role_allows_channel(person.role, "dashboard"):
        _record_attempt("dashboard", f"dashboard:{uname}", False, f"role '{person.role}' has no dashboard access")
        return None
    _record_attempt("dashboard", f"dashboard:{uname}", True, person.display_name)
    return person


_DUMMY_HASH = _hasher.hash("not-a-real-password")


# --- admin mutations (used by admin_cli and the dashboard) -------------------------------

def add_user(display_name: str, role: str, notes: str = "") -> int:
    if not db.query_one("SELECT 1 FROM roles WHERE name = ?", (role,)):
        raise ValueError(f"Unknown role '{role}'. Create it first.")
    return db.execute(
        "INSERT INTO users(display_name, role, notes, created_at) VALUES (?,?,?,?)",
        (display_name, role, notes, db.now_iso()),
    )


def add_identifier(user_id: int, id_type: str, value: str, password_hash: str | None = None) -> int:
    if id_type not in IDENTIFIER_TYPES:
        raise ValueError(f"type must be one of {IDENTIFIER_TYPES}")
    if id_type == "telegram" and not str(value).lstrip("-").isdigit():
        raise ValueError("Telegram identifier must be the numeric user ID, not the @username.")
    return db.execute(
        "INSERT INTO user_identifiers(user_id, type, value, password_hash) VALUES (?,?,?,?)",
        (user_id, id_type, normalize(id_type, value), password_hash),
    )


def upsert_identifier(user_id: int, id_type: str, value: str, password_hash: str | None = None) -> None:
    value = normalize(id_type, value)
    existing = db.query_one("SELECT id, user_id FROM user_identifiers WHERE type = ? AND value = ?", (id_type, value))
    if existing:
        db.execute(
            "UPDATE user_identifiers SET user_id = ?, password_hash = COALESCE(?, password_hash) WHERE id = ?",
            (user_id, password_hash, existing["id"]),
        )
    else:
        add_identifier(user_id, id_type, value, password_hash)


def bootstrap_admin() -> None:
    """Seed/refresh the admin person from .env so the registry is never empty."""
    admin = db.query_one("SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
    if admin:
        user_id = admin["id"]
        db.execute("UPDATE users SET display_name = ? WHERE id = ?", (settings.admin_display_name, user_id))
    else:
        user_id = add_user(settings.admin_display_name, "admin", notes="seeded from .env")
        log.info("Seeded admin user id=%s", user_id)

    if settings.admin_telegram_id:
        upsert_identifier(user_id, "telegram", settings.admin_telegram_id)
    if settings.admin_phone:
        upsert_identifier(user_id, "phone", settings.admin_phone)
    if settings.admin_email:
        upsert_identifier(user_id, "email", settings.admin_email)
    if settings.admin_dashboard_username and settings.admin_password_hash:
        if not settings.admin_password_hash.startswith("$argon2"):
            log.error("DASHBOARD_ADMIN_PASSWORD_HASH is not an Argon2 hash (wrap it in single quotes in .env) — ignored")
            return
        existing = db.query_one(
            "SELECT password_hash FROM user_identifiers WHERE type = 'dashboard' AND value = ?",
            (normalize("dashboard", settings.admin_dashboard_username),),
        )
        if not existing or existing["password_hash"] != settings.admin_password_hash:
            upsert_identifier(user_id, "dashboard", settings.admin_dashboard_username, settings.admin_password_hash)
            db.execute("DELETE FROM dashboard_sessions WHERE user_id = ?", (user_id,))
