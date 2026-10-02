"""Two-factor sign-in for the dashboard.

Methods (a person can have both):
- Authenticator app: standard TOTP (RFC 6238, SHA-1, 6 digits, 30 s), ±1 step of clock drift,
  each step accepted once. The secret is encrypted at rest with TOKEN_ENCRYPTION_KEY.
- Telegram: a 6-digit code sent to the person's verified Telegram account, valid 5 minutes.
- Backup codes: 10 single-use codes, shown once, stored only as hashes. Generated when the first
  method is switched on.

Sign-in: a correct password for someone with 2FA does not create a session; it creates a
challenge (random token, 5 minutes, 5 wrong codes max). Only a correct second factor turns the
challenge into a session.

Locked out (lost phone and backup codes)? Another admin can reset it on the People page, or on
the server:  docker compose exec alfred python -m orchestrator.identity.mfa reset <username>
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken

from .. import db
from ..config import settings
from .registry import RegistryError

ISSUER = "Alfred"
STEP = 30
DIGITS = 6
CHALLENGE_MINUTES = 5
MAX_ATTEMPTS = 5
TG_RESEND_SECONDS = 30
TG_MAX_SENDS = 3
BACKUP_CODES = 10
_BACKUP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o, 1/l/i


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _fernet() -> Fernet:
    if not settings.token_encryption_key:
        raise RegistryError("Authenticator apps need TOKEN_ENCRYPTION_KEY to be set on the server.")
    return Fernet(settings.token_encryption_key.encode())


# --- TOTP --------------------------------------------------------------------------------

def totp_at(secret_b32: str, step: int) -> str:
    key = base64.b32decode(secret_b32.upper() + "=" * (-len(secret_b32) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def _match_step(secret_b32: str, code: str, now: float | None = None) -> int | None:
    current = int((time.time() if now is None else now) // STEP)
    for step in (current, current - 1, current + 1):
        if hmac.compare_digest(totp_at(secret_b32, step), code):
            return step
    return None


def _clean(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


# --- state -------------------------------------------------------------------------------

def _row(user_id: int) -> dict | None:
    return db.query_one("SELECT * FROM user_mfa WHERE user_id = ?", (user_id,))


def status(user_id: int) -> dict:
    from . import registry
    row = _row(user_id) or {}
    totp = bool(row.get("totp_confirmed_at"))
    telegram = bool(row.get("telegram_enabled")) and bool(registry.telegram_for(user_id))
    left = db.query_one("SELECT COUNT(*) AS n FROM mfa_backup_codes WHERE user_id = ? AND used_at IS NULL",
                        (user_id,))["n"]
    return {"enabled": totp or telegram, "totp": totp, "telegram": telegram,
            "telegram_available": bool(registry.telegram_for(user_id)), "backup_codes_left": left}


def enabled(user_id: int) -> bool:
    return status(user_id)["enabled"]


def required_for(role: str) -> bool:
    return role in settings.mfa_required_roles


def _upsert(user_id: int, **cols) -> None:
    if not _row(user_id):
        db.execute("INSERT INTO user_mfa(user_id, updated_at) VALUES (?,?)", (user_id, _now().isoformat()))
    sets = ", ".join(f"{k} = ?" for k in cols)
    db.execute(f"UPDATE user_mfa SET {sets}, updated_at = ? WHERE user_id = ?",
               (*cols.values(), _now().isoformat(), user_id))


def new_backup_codes(user_id: int) -> list[str]:
    codes = ["".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(10)) for _ in range(BACKUP_CODES)]
    with db.tx() as conn:
        conn.execute("DELETE FROM mfa_backup_codes WHERE user_id = ?", (user_id,))
        conn.executemany("INSERT INTO mfa_backup_codes(user_id, code_hash) VALUES (?,?)",
                         [(user_id, _sha(f"{user_id}:{c}")) for c in codes])
    return [f"{c[:5]}-{c[5:]}" for c in codes]


def _maybe_first_codes(user_id: int, was_enabled: bool) -> list[str] | None:
    return new_backup_codes(user_id) if not was_enabled else None


# --- set-up ------------------------------------------------------------------------------

def start_totp(user_id: int, account: str) -> dict:
    """New (unconfirmed) secret; returns what the authenticator app needs."""
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    _upsert(user_id, totp_secret_enc=_fernet().encrypt(secret.encode()).decode(),
            totp_confirmed_at=None, totp_last_step=0)
    label = quote(f"{ISSUER}:{account}")
    uri = f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"
    return {"secret": secret, "uri": uri, "qr": _qr(uri)}


def _qr(uri: str) -> str | None:
    try:
        import segno
    except ImportError:
        return None
    return segno.make(uri, error="m").svg_data_uri(scale=5, border=2)


def _secret(row: dict) -> str:
    try:
        return _fernet().decrypt(row["totp_secret_enc"].encode()).decode()
    except InvalidToken as e:
        raise RegistryError("The authenticator secret can't be read (encryption key changed?). Set it up again.") from e


def confirm_totp(user_id: int, code: str) -> list[str] | None:
    """Turns the app on once it produced a valid code. Returns new backup codes if 2FA was off."""
    row = _row(user_id)
    if not row or not row.get("totp_secret_enc"):
        raise RegistryError("Start the authenticator set-up first.")
    if row.get("totp_confirmed_at"):
        raise RegistryError("The authenticator app is already active.")
    step = _match_step(_secret(row), _clean(code))
    if step is None:
        raise RegistryError("That code doesn't match. Check the phone's time and try the newest code.")
    was = enabled(user_id)
    _upsert(user_id, totp_confirmed_at=_now().isoformat(), totp_last_step=step)
    return _maybe_first_codes(user_id, was)


def set_telegram(user_id: int, on: bool) -> list[str] | None:
    from . import registry
    if on and not registry.telegram_for(user_id):
        raise RegistryError("Add and verify a Telegram connection first.")
    was = enabled(user_id)
    _upsert(user_id, telegram_enabled=1 if on else 0)
    return _maybe_first_codes(user_id, was) if on else None


def disable_totp(user_id: int) -> None:
    _upsert(user_id, totp_secret_enc=None, totp_confirmed_at=None, totp_last_step=0)


def after_change(user_id: int) -> None:
    """Drop leftover backup codes once no method is on any more."""
    if not enabled(user_id):
        db.execute("DELETE FROM mfa_backup_codes WHERE user_id = ?", (user_id,))


def reset(user_id: int) -> None:
    with db.tx() as conn:
        conn.execute("DELETE FROM user_mfa WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM mfa_backup_codes WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM mfa_challenges WHERE user_id = ?", (user_id,))


# --- sign-in challenges ------------------------------------------------------------------

def create_challenge(user_id: int) -> str:
    db.execute("DELETE FROM mfa_challenges WHERE expires_at < ?", (_now().isoformat(),))
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO mfa_challenges(token_hash, user_id, expires_at) VALUES (?,?,?)",
               (_sha(token), user_id, (_now() + timedelta(minutes=CHALLENGE_MINUTES)).isoformat()))
    return token


def _challenge(token: str) -> dict:
    row = db.query_one("SELECT * FROM mfa_challenges WHERE token_hash = ?", (_sha(token),)) if len(token) < 100 else None
    if not row or datetime.fromisoformat(row["expires_at"]) < _now():
        raise RegistryError("The sign-in expired. Enter your password again.")
    if row["attempts"] >= MAX_ATTEMPTS:
        db.execute("DELETE FROM mfa_challenges WHERE token_hash = ?", (row["token_hash"],))
        raise RegistryError("Too many wrong codes. Enter your password again.")
    return row


def challenge_user(token: str) -> int:
    return _challenge(token)["user_id"]


async def send_telegram_code(token: str) -> None:
    from .. import telegram_handler
    row = _challenge(token)
    if not status(row["user_id"])["telegram"]:
        raise RegistryError("Telegram codes aren't switched on for this account.")
    if row["tg_sends"] >= TG_MAX_SENDS:
        raise RegistryError("No more codes for this sign-in. Enter your password again.")
    if row["tg_sent_at"] and (_now() - datetime.fromisoformat(row["tg_sent_at"])).total_seconds() < TG_RESEND_SECONDS:
        raise RegistryError("A code was just sent. Wait a few seconds before asking for another.")
    code = f"{secrets.randbelow(10 ** DIGITS):0{DIGITS}d}"
    db.execute("UPDATE mfa_challenges SET tg_code_hash = ?, tg_sent_at = ?, tg_sends = tg_sends + 1 "
               "WHERE token_hash = ?", (_sha(f"{row['token_hash']}:{code}"), _now().isoformat(), row["token_hash"]))
    try:
        # record=False: the code never enters the conversation history (or the model's context).
        await telegram_handler.send_to_user(
            row["user_id"], f"Your dashboard sign-in code: `{code}`\nValid for {CHALLENGE_MINUTES} minutes. "
            "If you didn't just try to sign in, change your password.", note="sign-in code",
            record=False, silent=False)
    except Exception as e:
        raise RegistryError("The code couldn't be sent on Telegram right now. Use your app or a backup code.") from e


def verify(token: str, code: str) -> int:
    """Checks a code against every active method; returns the user id or raises RegistryError."""
    row = _challenge(token)
    user_id, code = row["user_id"], _clean(code)
    if _check(row, user_id, code):
        db.execute("DELETE FROM mfa_challenges WHERE token_hash = ?", (row["token_hash"],))
        return user_id
    db.execute("UPDATE mfa_challenges SET attempts = attempts + 1 WHERE token_hash = ?", (row["token_hash"],))
    left = MAX_ATTEMPTS - row["attempts"] - 1
    raise RegistryError("Wrong code." + (f" {left} tries left." if left > 0 else " Enter your password again."))


def _check(challenge: dict, user_id: int, code: str) -> bool:
    st = status(user_id)
    if len(code) == DIGITS and code.isdigit():
        if st["telegram"] and challenge["tg_code_hash"] and \
                hmac.compare_digest(challenge["tg_code_hash"], _sha(f"{challenge['token_hash']}:{code}")):
            return True
        if st["totp"]:
            return check_totp(user_id, code)
        return False
    if len(code) == 10:
        hit = db.query_one("SELECT id FROM mfa_backup_codes WHERE user_id = ? AND code_hash = ? AND used_at IS NULL",
                           (user_id, _sha(f"{user_id}:{code}")))
        if hit:
            db.execute("UPDATE mfa_backup_codes SET used_at = ? WHERE id = ?", (_now().isoformat(), hit["id"]))
            return True
    return False


def check_totp(user_id: int, code: str) -> bool:
    """Valid, not-yet-used authenticator code (also used to confirm sensitive changes)."""
    row = _row(user_id)
    if not row or not row.get("totp_confirmed_at"):
        return False
    step = _match_step(_secret(row), _clean(code))
    if step is None or step <= row["totp_last_step"]:
        return False
    _upsert(user_id, totp_last_step=step)
    return True


# --- CLI: emergency reset ----------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "reset":
        sys.exit("usage: python -m orchestrator.identity.mfa reset <dashboard username>")
    db.init_db()
    hit = db.query_one("SELECT user_id FROM user_identifiers WHERE type = 'dashboard' AND value = ?",
                       (sys.argv[2].strip().lower(),))
    if not hit:
        sys.exit("No dashboard login with that username.")
    reset(hit["user_id"])
    db.log_activity("access", "Two-factor sign-in reset from the server console", user_id=hit["user_id"])
    print("Two-factor sign-in removed. The person can sign in with the password and set it up again.")
