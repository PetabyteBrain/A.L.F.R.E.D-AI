"""Dashboard security primitives.

- Server-side sessions: the cookie holds a random 256-bit token; the DB stores only its HMAC.
  Logout, password changes, disabling a person or changing their role revoke sessions
  immediately. Sliding idle timeout + absolute lifetime.
- CSRF: SameSite=Strict cookie, plus every state-changing /api request must carry the custom
  header `X-Requested-With: alfred` (cross-site pages can't set it without a CORS preflight,
  which we never grant) and, when present, an Origin matching this site.
- Security headers on every response (strict CSP, no framing, no sniffing, HSTS over HTTPS).
- Client IP for rate limiting only trusts proxy headers from TRUSTED_PROXY_IPS.
- Login throttling per client IP *and* per username (so rotating IPs doesn't help).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from orchestrator import db
from orchestrator.config import settings

log = logging.getLogger(__name__)

COOKIE_NAME = "__Host-alfred" if settings.cookie_secure else "alfred"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "alfred"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_secret = settings.session_secret.encode() if settings.session_secret else None
if _secret is None or len(_secret) < 32:
    if _secret is not None:
        log.warning("DASHBOARD_SESSION_SECRET is shorter than 32 chars — using a random one instead.")
    else:
        log.warning("DASHBOARD_SESSION_SECRET not set — using a random one (logins reset on restart).")
    _secret = secrets.token_bytes(32)


# --- client IP ---------------------------------------------------------------------------

def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "?"
    if peer in settings.trusted_proxy_ips:
        forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
        forwarded = forwarded.split(",")[0].strip()
        if forwarded:
            return forwarded[:64]
    return peer


# --- sessions ----------------------------------------------------------------------------

def _hash(token: str) -> str:
    return hmac.new(_secret, token.encode(), hashlib.sha256).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_session(user_id: int, request: Request) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    db.execute(
        "INSERT INTO dashboard_sessions(token_hash, user_id, created_at, last_seen_at, expires_at, ip, user_agent) "
        "VALUES (?,?,?,?,?,?,?)",
        (_hash(token), user_id, now.isoformat(), now.isoformat(),
         (now + timedelta(hours=settings.session_max_hours)).isoformat(),
         client_ip(request), request.headers.get("user-agent", "")[:200]),
    )
    return token


def session_user_id(request: Request) -> int | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 100:
        return None
    th = _hash(token)
    row = db.query_one("SELECT user_id, last_seen_at, expires_at FROM dashboard_sessions WHERE token_hash = ?", (th,))
    if not row:
        return None
    now = _now()
    idle_cutoff = now - timedelta(minutes=settings.session_idle_minutes)
    if datetime.fromisoformat(row["expires_at"]) < now or datetime.fromisoformat(row["last_seen_at"]) < idle_cutoff:
        db.execute("DELETE FROM dashboard_sessions WHERE token_hash = ?", (th,))
        return None
    # Only write last_seen at most once a minute (the live view polls every 2 s).
    if (now - datetime.fromisoformat(row["last_seen_at"])).total_seconds() > 60:
        db.execute("UPDATE dashboard_sessions SET last_seen_at = ? WHERE token_hash = ?", (now.isoformat(), th))
    return row["user_id"]


def destroy_session(request: Request) -> None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        db.execute("DELETE FROM dashboard_sessions WHERE token_hash = ?", (_hash(token),))


def revoke_user_sessions(user_id: int) -> None:
    db.execute("DELETE FROM dashboard_sessions WHERE user_id = ?", (user_id,))


def prune_sessions() -> None:
    cutoff = (_now() - timedelta(minutes=settings.session_idle_minutes)).isoformat()
    db.execute("DELETE FROM dashboard_sessions WHERE expires_at < ? OR last_seen_at < ?", (_now().isoformat(), cutoff))


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=settings.session_max_hours * 3600,
        path="/", secure=settings.cookie_secure, httponly=True, samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=settings.cookie_secure, httponly=True, samesite="strict")


# --- login throttling --------------------------------------------------------------------

class Throttle:
    """Sliding-window failure counter keyed by arbitrary strings."""

    def __init__(self, window_s: int, max_failures: int):
        self.window, self.max = window_s, max_failures
        self._hits: dict[str, deque] = defaultdict(deque)

    def _trim(self, key: str) -> deque:
        q = self._hits[key]
        now = time.monotonic()
        while q and now - q[0] > self.window:
            q.popleft()
        if not q:
            self._hits.pop(key, None)
            q = self._hits[key]
        return q

    def blocked(self, key: str) -> bool:
        return len(self._trim(key)) >= self.max

    def fail(self, key: str) -> None:
        self._trim(key).append(time.monotonic())
        if len(self._hits) > 10_000:  # bound memory under a spray of junk keys
            for k in list(self._hits)[:5_000]:
                self._hits.pop(k, None)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


ip_throttle = Throttle(window_s=900, max_failures=10)
user_throttle = Throttle(window_s=900, max_failures=5)


# --- middleware --------------------------------------------------------------------------

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


def _allowed_origins(request: Request) -> set[str]:
    origins = set()
    if settings.public_base_url:
        u = urlsplit(settings.public_base_url)
        origins.add(f"{u.scheme}://{u.netloc}")
    host = request.headers.get("host")
    if host:
        origins.add(f"{request.url.scheme}://{host}")
        origins.add(f"https://{host}")
    return origins


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and request.method in UNSAFE_METHODS:
            if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
                return JSONResponse({"detail": "Missing CSRF header"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin not in _allowed_origins(request):
                log.warning("Blocked cross-origin %s %s from %s", request.method, path, origin)
                return JSONResponse({"detail": "Bad origin"}, status_code=403)

        response = await call_next(request)

        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if not path.startswith("/twilio/"):
            h.setdefault("Content-Security-Policy", CSP)
        if settings.cookie_secure:
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if path.startswith("/api/"):
            h.setdefault("Cache-Control", "no-store")
        elif path in ("/", "/index.html"):
            h["Cache-Control"] = "no-cache"
        return response
