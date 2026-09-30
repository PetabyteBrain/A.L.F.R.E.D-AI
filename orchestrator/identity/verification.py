"""Confirmation codes for new phone numbers and Telegram IDs.

A new (or changed) phone/Telegram connection doesn't count until its owner proves control of it:

- phone: Alfred calls the number and reads out a 6-digit code, which is typed into the dashboard;
- telegram: the dashboard shows a code, which is sent to the bot from that Telegram account
  (a t.me deep link does it in one tap).

Codes are stored as HMACs, expire after 10 minutes and allow 5 wrong tries. Sending is rate
limited per connection and per requester, because each code call costs money and rings a phone.
An admin can also mark a connection verified by hand (logged).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from .. import db
from ..config import settings
from . import registry
from .registry import VERIFY_TYPES, Person, RegistryError

log = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_AFTER = timedelta(seconds=60)
MAX_SENDS_PER_CONNECTION = 5      # per 24 h
MAX_SENDS_PER_REQUESTER = 10      # per 24 h
CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")

_key = (settings.session_secret or secrets.token_hex(32)).encode()


def _hash(identifier_id: int, code: str) -> str:
    return hmac.new(_key, f"{identifier_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def pending(identifier_id: int) -> dict | None:
    row = db.query_one("SELECT expires_at, attempts, sent_at FROM identifier_verifications WHERE identifier_id = ?",
                       (identifier_id,))
    if not row or datetime.fromisoformat(row["expires_at"]) < _now() or row["attempts"] >= MAX_ATTEMPTS:
        return None
    return row


def _check_rate(ident: dict, requester: Person) -> None:
    current = db.query_one("SELECT sent_at FROM identifier_verifications WHERE identifier_id = ?", (ident["id"],))
    if current and datetime.fromisoformat(current["sent_at"]) > _now() - RESEND_AFTER:
        raise RegistryError("A code was just sent. Wait a minute before asking for another one.")
    since = _iso(_now() - timedelta(hours=24))
    db.execute("DELETE FROM verification_sends WHERE ts < ?", (since,))
    n = db.query_one("SELECT COUNT(*) AS n FROM verification_sends WHERE identifier_value = ?", (ident["value"],))["n"]
    if n >= MAX_SENDS_PER_CONNECTION:
        raise RegistryError("Too many codes for this connection today. Try again tomorrow.")
    n = db.query_one("SELECT COUNT(*) AS n FROM verification_sends WHERE requested_by = ?", (requester.id,))["n"]
    if n >= MAX_SENDS_PER_REQUESTER:
        raise RegistryError("Too many verification codes requested today. Try again tomorrow.")


def _spoken(code: str) -> str:
    return ", ".join(code)  # digit by digit, with a small pause between


async def _call_with_code(number: str, code: str) -> None:
    if not settings.twilio_enabled:
        raise RegistryError("Telephony isn't configured, so the code can't be phoned through.")
    from ..call_handler import _twilio_client
    name = escape(settings.assistant_name)
    say = '<Say voice="Polly.Brian" language="en-GB">'
    twiml = (
        f"<Response><Pause length=\"1\"/>{say}Good day. This is {name}, with a verification code for the "
        f"dashboard. Your code is: {_spoken(code)}.</Say><Pause length=\"1\"/>{say}Once more: {_spoken(code)}."
        f"</Say><Pause length=\"1\"/>{say}If you weren't expecting this call, simply hang up. Goodbye.</Say></Response>"
    )

    def _create():
        return _twilio_client().calls.create(
            to=number, from_=registry.normalize_phone(settings.twilio_phone_number), twiml=twiml, timeout=30)

    try:
        await asyncio.to_thread(_create)
    except Exception as e:  # noqa: BLE001
        log.warning("Verification call to %s failed: %s", number, e)
        raise RegistryError("The verification call couldn't be placed. Check the number and try again.") from e


async def start(identifier_id: int, requester: Person) -> dict:
    """Create a fresh code and deliver it. Returns what the dashboard needs to show."""
    ident = registry.get_identifier(identifier_id)
    if not ident or ident["type"] not in VERIFY_TYPES:
        raise RegistryError("This connection doesn't need verifying.")
    if ident["verified_at"]:
        raise RegistryError("This connection is already verified.")
    _check_rate(ident, requester)

    code = f"{secrets.randbelow(10**6):06d}"
    if ident["type"] == "phone" and requester.id != ident["user_id"]:
        from .. import quiet
        until = quiet.quiet_until(ident["user_id"])
        if until:
            raise RegistryError(f"They're in their quiet hours until {until.astimezone(quiet._tz()):%H:%M}. "
                                "Try again then, or let them verify it themselves.")
    if ident["type"] == "phone":
        await _call_with_code(ident["value"], code)
    now = _now()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO identifier_verifications(identifier_id, code_hash, expires_at, attempts, requested_by, sent_at) "
            "VALUES (?,?,?,0,?,?) ON CONFLICT(identifier_id) DO UPDATE SET code_hash = excluded.code_hash, "
            "expires_at = excluded.expires_at, attempts = 0, requested_by = excluded.requested_by, "
            "sent_at = excluded.sent_at",
            (identifier_id, _hash(identifier_id, code), _iso(now + CODE_TTL), requester.id, _iso(now)))
        conn.execute("INSERT INTO verification_sends(identifier_value, requested_by, ts) VALUES (?,?,?)",
                     (ident["value"], requester.id, _iso(now)))
    db.log_activity("access", f"Verification code {'called through' if ident['type'] == 'phone' else 'issued'} "
                    f"for a {ident['type']} of #{ident['user_id']} (by {requester.display_name})",
                    user_id=ident["user_id"])

    out = {"method": "call" if ident["type"] == "phone" else "telegram", "expires_at": _iso(now + CODE_TTL)}
    if ident["type"] == "telegram":
        # Shown on the dashboard; proving control means sending it *from* that Telegram account.
        from .. import telegram_handler
        bot = telegram_handler.bot_username()
        out.update(code=code, bot=bot, link=f"https://t.me/{bot}?start={code}" if bot else None)
    return out


def check(identifier_id: int, code: str) -> bool:
    """Phone flow: the code typed into the dashboard."""
    code = (code or "").strip()
    if not pending(identifier_id):
        raise RegistryError("No valid code is pending. Request a new one.")
    ok = re.fullmatch(r"\d{6}", code) is not None and hmac.compare_digest(
        _hash(identifier_id, code),
        db.query_one("SELECT code_hash FROM identifier_verifications WHERE identifier_id = ?",
                     (identifier_id,))["code_hash"])
    if not ok:
        db.execute("UPDATE identifier_verifications SET attempts = attempts + 1 WHERE identifier_id = ?",
                   (identifier_id,))
        left = MAX_ATTEMPTS - db.query_one("SELECT attempts FROM identifier_verifications WHERE identifier_id = ?",
                                           (identifier_id,))["attempts"]
        raise RegistryError("That code isn't right." + (f" {left} tries left." if left > 0 else
                                                          " No tries left; request a new code."))
    registry.mark_verified(identifier_id)
    ident = registry.get_identifier(identifier_id)
    db.log_activity("access", f"Verified a {ident['type']} of #{ident['user_id']}", user_id=ident["user_id"])
    return True


def claim_telegram(telegram_id: str, text: str) -> tuple[str, Person | None]:
    """Telegram flow: a message from an unregistered/unverified ID.

    Returns ("verified", person) if it carried the right code, ("wrong", None) if a code is
    pending for this ID but the message didn't match it, and ("none", None) otherwise, in which
    case the sender must get no reply at all.
    """
    row = db.query_one("SELECT id FROM user_identifiers WHERE type = 'telegram' AND value = ? "
                       "AND verified_at IS NULL", (str(telegram_id),))
    if not row or not pending(row["id"]):
        return "none", None
    m = CODE_RE.search(text or "")
    if not m:  # e.g. a plain "hi": point them at the code without using up a try
        return "wrong", None
    try:
        check(row["id"], m.group(1))
    except RegistryError:
        return "wrong", None
    ident = registry.get_identifier(row["id"])
    return "verified", registry.get_person(ident["user_id"])
