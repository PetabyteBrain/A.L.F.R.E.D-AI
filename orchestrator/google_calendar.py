"""Per-user Google Calendar link (OAuth 2.0 authorization-code flow with PKCE) + Calendar API calls.

Security model:
- Scope is limited to `calendar.events` (+ `openid email` to show which account is linked).
- `state` is random, single-use, bound to the person who started the flow, valid 10 minutes;
  only its hash is stored. PKCE (S256) protects the code exchange.
- Refresh/access tokens are encrypted at rest with Fernet (TOKEN_ENCRYPTION_KEY).
- Each person's tools only ever touch their own linked calendar.
- Unlinking revokes the grant at Google and deletes the tokens.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from cryptography.fernet import Fernet, InvalidToken

from . import db, usage
from .config import settings

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/calendar/v3"
EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
SCOPES = f"openid email {EVENTS_SCOPE}"
STATE_TTL = timedelta(minutes=10)


class GoogleError(RuntimeError):
    """Message is safe to show to the user / the model."""


def redirect_uri() -> str:
    return f"{settings.public_base_url}/api/google/callback"


def _fernet() -> Fernet:
    if not settings.token_encryption_key:
        raise GoogleError("Google linking isn't configured (TOKEN_ENCRYPTION_KEY missing).")
    return Fernet(settings.token_encryption_key.encode())


def _enc(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def _dec(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        raise GoogleError("Stored Google credentials can't be decrypted (key changed?). Please relink.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- HTTP (isolated so tests can replace it) --------------------------------------------------

async def _http(method: str, url: str, **kw) -> httpx.Response:
    async with httpx.AsyncClient(timeout=20) as c:
        return await c.request(method, url, **kw)


# --- link / unlink ---------------------------------------------------------------------------

def is_linked(user_id: int) -> bool:
    return bool(db.query_one("SELECT 1 FROM google_accounts WHERE user_id = ?", (user_id,)))


def status(user_id: int) -> dict:
    row = db.query_one("SELECT email, linked_at, calendar_id FROM google_accounts WHERE user_id = ?", (user_id,))
    return {"available": settings.google_enabled, "linked": bool(row), **(row or {})}


def start_link(user_id: int) -> str:
    if not settings.google_enabled:
        raise GoogleError("Google Calendar isn't configured on this server yet.")
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    cutoff = (_now() - STATE_TTL).isoformat()
    db.execute("DELETE FROM oauth_states WHERE created_at < ? OR user_id = ?", (cutoff, user_id))
    db.execute("INSERT INTO oauth_states(state_hash, user_id, code_verifier, created_at) VALUES (?,?,?,?)",
               (hashlib.sha256(state.encode()).hexdigest(), user_id, _enc(verifier), _now().isoformat()))
    return AUTH_URL + "?" + urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",           # always return a refresh token
        "include_granted_scopes": "false",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def _email_from_id_token(id_token: str | None) -> str:
    """The id_token came straight from Google's token endpoint over TLS; we only read the email."""
    if not id_token or id_token.count(".") != 2:
        return ""
    payload = id_token.split(".")[1]
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return ""
    return str(data.get("email", ""))[:200]


async def finish_link(state: str, code: str) -> int:
    """OAuth callback. Returns the user id the calendar was linked to."""
    if not state or not code:
        raise GoogleError("Google didn't return an authorisation code.")
    sh = hashlib.sha256(state.encode()).hexdigest()
    row = db.query_one("SELECT user_id, code_verifier, created_at FROM oauth_states WHERE state_hash = ?", (sh,))
    db.execute("DELETE FROM oauth_states WHERE state_hash = ?", (sh,))  # single use, whatever happens next
    if not row or datetime.fromisoformat(row["created_at"]) < _now() - STATE_TTL:
        raise GoogleError("This link request expired or was already used. Start again from your profile.")
    r = await _http("POST", TOKEN_URL, data={
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
        "code_verifier": _dec(row["code_verifier"]),
    })
    if r.status_code != 200:
        log.warning("Google token exchange failed: %s %s", r.status_code, r.text[:300])
        raise GoogleError("Google refused the link request. Try again.")
    tok = r.json()
    if EVENTS_SCOPE not in tok.get("scope", "").split():
        raise GoogleError("Calendar access wasn't granted. Tick the calendar permission when Google asks.")
    if not tok.get("refresh_token"):
        raise GoogleError("Google didn't return a refresh token. Remove Alfred's access in your Google "
                          "account's security settings and link again.")
    expires = (_now() + timedelta(seconds=int(tok.get("expires_in", 3600)) - 60)).isoformat()
    db.execute(
        "INSERT INTO google_accounts(user_id, email, refresh_token_enc, access_token_enc, access_expires_at, scopes, "
        "linked_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET email=excluded.email, "
        "refresh_token_enc=excluded.refresh_token_enc, access_token_enc=excluded.access_token_enc, "
        "access_expires_at=excluded.access_expires_at, scopes=excluded.scopes, linked_at=excluded.linked_at",
        (row["user_id"], _email_from_id_token(tok.get("id_token")), _enc(tok["refresh_token"]),
         _enc(tok["access_token"]), expires, tok.get("scope", ""), db.now_iso()),
    )
    return row["user_id"]


async def disconnect(user_id: int) -> None:
    row = db.query_one("SELECT refresh_token_enc FROM google_accounts WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM google_accounts WHERE user_id = ?", (user_id,))
    if row:
        try:
            await _http("POST", REVOKE_URL, data={"token": _dec(row["refresh_token_enc"])})
        except Exception:  # noqa: BLE001 — the local tokens are gone either way
            log.warning("Google token revoke failed for user %s", user_id)


async def _access_token(user_id: int) -> str:
    row = db.query_one("SELECT * FROM google_accounts WHERE user_id = ?", (user_id,))
    if not row:
        raise GoogleError("No Google Calendar is linked. Link it on the dashboard under Profile.")
    if row["access_token_enc"] and row["access_expires_at"] and datetime.fromisoformat(row["access_expires_at"]) > _now():
        return _dec(row["access_token_enc"])
    r = await _http("POST", TOKEN_URL, data={
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "refresh_token": _dec(row["refresh_token_enc"]),
        "grant_type": "refresh_token",
    })
    if r.status_code != 200:
        if "invalid_grant" in r.text:
            db.execute("DELETE FROM google_accounts WHERE user_id = ?", (user_id,))
            raise GoogleError("Google access was revoked or expired. Please link the calendar again.")
        raise GoogleError(f"Google token refresh failed ({r.status_code}).")
    tok = r.json()
    expires = (_now() + timedelta(seconds=int(tok.get("expires_in", 3600)) - 60)).isoformat()
    db.execute("UPDATE google_accounts SET access_token_enc = ?, access_expires_at = ? WHERE user_id = ?",
               (_enc(tok["access_token"]), expires, user_id))
    return tok["access_token"]


async def _api(user_id: int, method: str, path: str, **kw) -> dict:
    token = await _access_token(user_id)
    cal = (db.query_one("SELECT calendar_id FROM google_accounts WHERE user_id = ?", (user_id,)) or {}).get(
        "calendar_id", "primary")
    r = await _http(method, f"{API}/calendars/{cal}{path}", headers={"Authorization": f"Bearer {token}"}, **kw)
    usage.record_request("google", "calendar", user_id=user_id)
    if r.status_code == 404:
        raise GoogleError("That event wasn't found.")
    if r.status_code >= 400:
        raise GoogleError(f"Google Calendar error {r.status_code}: {r.text[:200]}")
    return r.json() if r.content else {}


# --- calendar operations -----------------------------------------------------------------------

def _tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def _when(value: str) -> dict:
    """ISO date -> all-day; ISO datetime (naive = local tz) -> timed."""
    v = (value or "").strip()
    if len(v) == 10:
        datetime.strptime(v, "%Y-%m-%d")
        return {"date": v}
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise GoogleError(f"Couldn't read the time '{value}'. Use ISO format, e.g. 2026-10-02T14:00.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    return {"dateTime": dt.isoformat(), "timeZone": settings.timezone}


def _rfc3339(value: str) -> str:
    w = _when(value)
    if "date" in w:
        return datetime.strptime(w["date"], "%Y-%m-%d").replace(tzinfo=_tz()).isoformat()
    return w["dateTime"]


def _compact(e: dict) -> dict:
    start, end = e.get("start", {}), e.get("end", {})
    return {
        "id": e.get("id"),
        "summary": e.get("summary", "(no title)"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "all_day": "date" in start,
        "location": e.get("location", ""),
        "description": (e.get("description") or "")[:300],
        "attendees": [a.get("email") for a in e.get("attendees", [])][:10],
    }


async def list_events(user_id: int, start: str, end: str, query: str = "", max_results: int = 25) -> list[dict]:
    params = {"timeMin": _rfc3339(start), "timeMax": _rfc3339(end), "singleEvents": "true",
              "orderBy": "startTime", "maxResults": max(1, min(int(max_results), 100)), "timeZone": settings.timezone}
    if query:
        params["q"] = query[:100]
    data = await _api(user_id, "GET", "/events", params=params)
    return [_compact(e) for e in data.get("items", [])]


async def create_event(user_id: int, summary: str, start: str, end: str, description: str = "",
                       location: str = "") -> dict:
    body = {"summary": summary[:300], "start": _when(start), "end": _when(end)}
    if description:
        body["description"] = description[:4000]
    if location:
        body["location"] = location[:300]
    return _compact(await _api(user_id, "POST", "/events", json=body))


def _event_id(event_id: str) -> str:
    """Event ids go into the URL path, so only Google's id alphabet is accepted."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,1024}", str(event_id or "")):
        raise GoogleError("That doesn't look like a calendar event id.")
    return event_id


async def update_event(user_id: int, event_id: str, **fields) -> dict:
    event_id = _event_id(event_id)
    body: dict = {}
    for key in ("summary", "description", "location"):
        if fields.get(key) is not None:
            body[key] = str(fields[key])[:4000]
    for key in ("start", "end"):
        if fields.get(key):
            body[key] = _when(fields[key])
    if not body:
        raise GoogleError("Nothing to change.")
    return _compact(await _api(user_id, "PATCH", f"/events/{event_id}", json=body))


async def delete_event(user_id: int, event_id: str) -> None:
    event_id = _event_id(event_id)
    await _api(user_id, "DELETE", f"/events/{event_id}")
