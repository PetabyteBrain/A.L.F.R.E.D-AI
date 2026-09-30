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
# Connections that let someone reach (or be reached by) the assistant only count once their
# owner proved control of them with a confirmation code (identity/verification.py).
VERIFY_TYPES = ("phone", "telegram")
# The web chat lives in the dashboard, so it follows dashboard access.
CHANNEL_PERMISSION = {"telegram": "can_telegram", "call": "can_call", "dashboard": "can_dashboard",
                      "web": "can_dashboard"}
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
        "JOIN users u ON u.id = i.user_id "
        "WHERE i.type = ? AND i.value = ? AND u.active = 1 AND i.verified_at IS NOT NULL",
        (id_type, normalize(id_type, value)),
    )
    return Person(**row) if row else None


def is_pending_verification(id_type: str, value: str) -> bool:
    return bool(db.query_one(
        "SELECT 1 FROM user_identifiers WHERE type = ? AND value = ? AND verified_at IS NULL",
        (id_type, normalize(id_type, value))))


def role_allows_channel(role: str, channel: str) -> bool:
    col = CHANNEL_PERMISSION[channel]
    row = db.query_one(f"SELECT {col} AS ok FROM roles WHERE name = ?", (role,))
    return bool(row and row["ok"])


def resolve(channel: str, id_type: str, value: str, *, record_success: bool = False) -> Person | None:
    """Resolve an inbound identifier for a channel. Returns None → caller must drop silently."""
    person = lookup(id_type, value)
    if person is None:
        detail = "not yet verified" if is_pending_verification(id_type, value) else "unregistered"
        _record_attempt(channel, f"{id_type}:{value}", False, detail)
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
    return db.query("SELECT id, type, value, verified_at FROM user_identifiers WHERE user_id = ? ORDER BY type",
                    (user_id,))


def _verified_value(user_id: int, id_type: str) -> str | None:
    row = db.query_one("SELECT value FROM user_identifiers WHERE user_id = ? AND type = ? "
                       "AND verified_at IS NOT NULL ORDER BY id LIMIT 1", (user_id, id_type))
    return row["value"] if row else None


def phone_for(user_id: int) -> str | None:
    """The number Alfred may call: verified ones only."""
    return _verified_value(user_id, "phone")


def telegram_for(user_id: int) -> str | None:
    """The Telegram ID Alfred may message: verified ones only."""
    return _verified_value(user_id, "telegram")


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

USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")
MIN_PASSWORD = 10


class RegistryError(ValueError):
    """User-facing validation/conflict error (message is safe to show)."""


def validate_identifier(id_type: str, value: str) -> str:
    """Normalise and validate an identifier; returns the stored form or raises RegistryError."""
    if id_type not in IDENTIFIER_TYPES:
        raise RegistryError(f"Type must be one of: {', '.join(IDENTIFIER_TYPES)}.")
    raw = str(value or "").strip()
    if not raw or len(raw) > 254:
        raise RegistryError("Value is empty or too long.")
    v = normalize(id_type, raw)
    if id_type == "telegram" and not re.fullmatch(r"\d{4,15}", v):
        raise RegistryError("Telegram needs the numeric user ID (e.g. from @userinfobot), not the @username.")
    if id_type == "phone" and not PHONE_RE.fullmatch(v):
        raise RegistryError("Phone number not recognised. Use e.g. 079 000 01 00 or +41 79 000 01 00.")
    if id_type == "email" and not EMAIL_RE.fullmatch(v):
        raise RegistryError("That doesn't look like an email address.")
    if id_type == "dashboard" and not USERNAME_RE.fullmatch(v):
        raise RegistryError("Username: 3-32 characters, lowercase letters, digits, dot, dash or underscore.")
    return v


def validate_password(password: str) -> None:
    if not password or len(password) < MIN_PASSWORD:
        raise RegistryError(f"Password must be at least {MIN_PASSWORD} characters.")
    if len(password) > 200:
        raise RegistryError("Password is too long.")


def validate_name(name: str) -> str:
    name = " ".join(str(name or "").split())
    if not 1 <= len(name) <= 80:
        raise RegistryError("Name must be 1-80 characters.")
    return name


def role_exists(role: str) -> bool:
    return bool(db.query_one("SELECT 1 FROM roles WHERE name = ?", (role,)))


def active_admin_count(excluding: int | None = None) -> int:
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1 AND (? IS NULL OR id != ?)",
        (excluding, excluding),
    )
    return row["n"] if row else 0


def add_user(display_name: str, role: str, notes: str = "") -> int:
    if not role_exists(role):
        raise RegistryError(f"Unknown role '{role}'. Create it first.")
    return db.execute(
        "INSERT INTO users(display_name, role, notes, created_at) VALUES (?,?,?,?)",
        (validate_name(display_name), role, (notes or "")[:500], db.now_iso()),
    )


def update_user(user_id: int, *, display_name: str | None = None, role: str | None = None,
                active: bool | None = None, notes: str | None = None) -> dict:
    """Admin edit. Returns {'access_changed': bool} so callers know to revoke sessions."""
    row = db.query_one("SELECT id, role, active FROM users WHERE id = ?", (user_id,))
    if not row:
        raise RegistryError("Unknown person.")
    if role is not None and not role_exists(role):
        raise RegistryError(f"Unknown role '{role}'.")
    losing_admin = row["role"] == "admin" and row["active"] and (
        (role is not None and role != "admin") or active is False)
    if losing_admin and active_admin_count(excluding=user_id) == 0:
        raise RegistryError("This is the last active admin. Make someone else admin first.")
    sets, params = [], []
    if display_name is not None:
        sets.append("display_name = ?")
        params.append(validate_name(display_name))
    if role is not None:
        sets.append("role = ?")
        params.append(role)
    if active is not None:
        sets.append("active = ?")
        params.append(int(active))
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes[:500])
    if sets:
        db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", (*params, user_id))
    access_changed = (role is not None and role != row["role"]) or (
        active is not None and int(active) != row["active"])
    return {"access_changed": access_changed}


def delete_user(user_id: int) -> None:
    row = db.query_one("SELECT role, active FROM users WHERE id = ?", (user_id,))
    if not row:
        raise RegistryError("Unknown person.")
    if row["role"] == "admin" and row["active"] and active_admin_count(excluding=user_id) == 0:
        raise RegistryError("This is the last active admin and can't be deleted.")
    if db.query_one("SELECT 1 FROM sessions WHERE user_id = ? LIMIT 1", (user_id,)):
        raise RegistryError("This person has call/chat history. Disable them instead, so the records are kept.")
    with db.tx() as conn:
        # Keep log lines, just detach them from the deleted person.
        conn.execute("UPDATE activity SET user_id = NULL WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE tool_calls SET user_id = NULL WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE api_usage SET user_id = NULL WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM scheduled_calls WHERE target_user_id = ? OR requested_by = ?", (user_id, user_id))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def _identifier_conflict(id_type: str, value: str, exclude_id: int | None = None) -> None:
    clash = db.query_one(
        "SELECT id FROM user_identifiers WHERE type = ? AND value = ? AND (? IS NULL OR id != ?)",
        (id_type, value, exclude_id, exclude_id))
    if clash:
        raise RegistryError(f"That {id_type} is already linked to a registered person.")


def add_identifier(user_id: int, id_type: str, value: str, password_hash: str | None = None,
                   *, verified: bool = False) -> int:
    """New phone/Telegram connections start unverified unless `verified` (trusted CLI)."""
    v = validate_identifier(id_type, value)
    _identifier_conflict(id_type, v)
    if id_type == "dashboard" and db.query_one(
            "SELECT 1 FROM user_identifiers WHERE user_id = ? AND type = 'dashboard'", (user_id,)):
        raise RegistryError("This person already has a dashboard login.")
    trusted = verified or id_type not in VERIFY_TYPES
    return db.execute(
        "INSERT INTO user_identifiers(user_id, type, value, password_hash, verified_at) VALUES (?,?,?,?,?)",
        (user_id, id_type, v, password_hash, db.now_iso() if trusted else None),
    )


def get_identifier(identifier_id: int) -> dict | None:
    return db.query_one("SELECT id, user_id, type, value, verified_at FROM user_identifiers WHERE id = ?",
                        (identifier_id,))


def mark_verified(identifier_id: int) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE user_identifiers SET verified_at = ? WHERE id = ?", (db.now_iso(), identifier_id))
        conn.execute("DELETE FROM identifier_verifications WHERE identifier_id = ?", (identifier_id,))


def update_identifier(identifier_id: int, value: str) -> dict:
    ident = get_identifier(identifier_id)
    if not ident:
        raise RegistryError("Unknown connection.")
    v = validate_identifier(ident["type"], value)
    _identifier_conflict(ident["type"], v, exclude_id=identifier_id)
    if v == ident["value"]:
        return ident
    if ident["type"] in VERIFY_TYPES:  # a new number/ID has to be confirmed again
        with db.tx() as conn:
            conn.execute("UPDATE user_identifiers SET value = ?, verified_at = NULL WHERE id = ?", (v, identifier_id))
            conn.execute("DELETE FROM identifier_verifications WHERE identifier_id = ?", (identifier_id,))
        return {**ident, "value": v, "verified_at": None}
    db.execute("UPDATE user_identifiers SET value = ? WHERE id = ?", (v, identifier_id))
    return {**ident, "value": v}


def delete_identifier(identifier_id: int) -> dict:
    ident = get_identifier(identifier_id)
    if not ident:
        raise RegistryError("Unknown connection.")
    db.execute("DELETE FROM user_identifiers WHERE id = ?", (identifier_id,))
    return ident


def set_dashboard_login(user_id: int, username: str, password: str | None) -> None:
    """Create or change a person's dashboard login (one per person)."""
    uname = validate_identifier("dashboard", username)
    existing = db.query_one("SELECT id FROM user_identifiers WHERE user_id = ? AND type = 'dashboard'", (user_id,))
    _identifier_conflict("dashboard", uname, exclude_id=existing["id"] if existing else None)
    if password is not None:
        validate_password(password)
    if existing:
        if password is not None:
            db.execute("UPDATE user_identifiers SET value = ?, password_hash = ? WHERE id = ?",
                       (uname, hash_password(password), existing["id"]))
        else:
            db.execute("UPDATE user_identifiers SET value = ? WHERE id = ?", (uname, existing["id"]))
    else:
        if password is None:
            raise RegistryError("A new dashboard login needs a password.")
        db.execute("INSERT INTO user_identifiers(user_id, type, value, password_hash, verified_at) "
                   "VALUES (?,?,?,?,?)", (user_id, "dashboard", uname, hash_password(password), db.now_iso()))


def check_password(user_id: int, password: str) -> bool:
    row = db.query_one(
        "SELECT password_hash FROM user_identifiers WHERE user_id = ? AND type = 'dashboard'", (user_id,))
    try:
        return bool(row and row["password_hash"] and _hasher.verify(row["password_hash"], password or ""))
    except (VerificationError, InvalidHashError):
        return False


def upsert_identifier(user_id: int, id_type: str, value: str, password_hash: str | None = None) -> None:
    """CLI/bootstrap only (trusted, so verified): attach or re-attach an identifier."""
    value = normalize(id_type, value)
    existing = db.query_one("SELECT id, user_id FROM user_identifiers WHERE type = ? AND value = ?", (id_type, value))
    if existing:
        db.execute(
            "UPDATE user_identifiers SET user_id = ?, password_hash = COALESCE(?, password_hash), "
            "verified_at = COALESCE(verified_at, ?) WHERE id = ?",
            (user_id, password_hash, db.now_iso(), existing["id"]),
        )
    else:
        db.execute("INSERT INTO user_identifiers(user_id, type, value, password_hash, verified_at) VALUES (?,?,?,?,?)",
                   (user_id, id_type, value, password_hash, db.now_iso()))


def bootstrap_admin() -> None:
    """First start only: seed the admin from .env so the registry is never empty.

    Once an admin exists, the registry (edited on the dashboard) is the source of truth and
    .env values are no longer re-applied, so UI edits survive restarts.
    """
    if db.query_one("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"):
        return
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
        upsert_identifier(user_id, "dashboard", settings.admin_dashboard_username, settings.admin_password_hash)
