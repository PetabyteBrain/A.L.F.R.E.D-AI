"""Alfred (the "alfred" service) entrypoint: one FastAPI process hosting every channel, the dashboard and background jobs.

    uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from dashboard.api.chat import router as chat_router
from dashboard.api.reminders import router as reminders_router
from dashboard.api.briefing import router as briefing_router
from dashboard.api.memory import router as memory_router
from dashboard.api.quiet import router as quiet_router
from dashboard.api.meetings import router as meetings_router
from dashboard.api.mail_templates import router as mail_templates_router
from dashboard.api.mail import router as mail_router
from dashboard.api.google import router as google_router
from dashboard.api.people import router as people_router
from dashboard.api.twofa import router as twofa_router
from dashboard.api.voice import router as voice_router
from dashboard.api.privacy import router as privacy_router
from dashboard.api.audit import router as audit_router
from dashboard.api.screening import outside as outside_router
from dashboard.api.screening import router as screening_router
from dashboard.api.notes import router as notes_router
from dashboard.api.agenda import router as agenda_router
from dashboard.api.viewas import router as viewas_router
from dashboard.api.settings_admin import router as settings_router
from dashboard.api.access import router as access_router
from dashboard.api.crm import actions as crm_actions_router, router as crm_router
from dashboard.api.billing import router as billing_router
from dashboard.api.team import approvals_router, router as team_router
from dashboard.api.continuity import router as continuity_router
from dashboard.api.routes import router as dashboard_router
from dashboard.api.security import SecurityMiddleware

from . import (briefing, call_handler, db, health, homelab, mail_inbound, n8n_callback, reminders, scheduler,
               sessions, tasks, telegram_handler)
from .config import settings
from .identity import registry
from .tools import registry as tool_registry

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
for noisy in ("httpx", "httpx2", "telegram.ext", "websockets", "twilio.http_client"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("alfred")

WEB_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    registry.bootstrap_admin()
    sessions.mark_orphans_on_startup()
    tool_registry.load()
    from . import email_templates
    email_templates.ensure_builtin()
    db.log_activity("system", f"{settings.assistant_name} started")

    if settings.twilio_enabled and not settings.twilio_validate_signature:
        log.warning("TWILIO_VALIDATE_SIGNATURE=false — anyone can forge call webhooks. Testing only!")
    if not settings.cookie_secure:
        log.warning("DASHBOARD_COOKIE_SECURE=false — dashboard cookie can travel over plain HTTP. LAN testing only!")

    await telegram_handler.start()
    bg = asyncio.create_task(health.background_loop())
    sched = asyncio.create_task(scheduler.background_loop())
    remind = asyncio.create_task(reminders.background_loop())
    brief = asyncio.create_task(briefing.background_loop())
    follow = asyncio.create_task(tasks.background_loop())
    lab = asyncio.create_task(homelab.background_loop())
    inbox = asyncio.create_task(mail_inbound.background_loop())
    from . import crm_proactive
    crm_jobs = asyncio.create_task(crm_proactive.background_loop())
    log.info("%s up — telegram=%s phone=%s email=%s n8n=%s model=%s",
             settings.assistant_name, settings.telegram_enabled, settings.twilio_enabled and settings.speech_enabled,
             settings.mail_enabled, settings.n8n_enabled, settings.claude_model)
    try:
        yield
    finally:
        for task in (bg, sched, remind, brief, follow, lab, inbox, crm_jobs):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await telegram_handler.stop()
        db.log_activity("system", f"{settings.assistant_name} stopped")


app = FastAPI(title="Alfred", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

# Files in public/ keep a fixed name (no build hash), so pages link them with ?v=<content hash>:
# a changed file gets a new URL and no browser or Cloudflare copy can be stale.
_FIXED_NAME_ASSETS = ("/legal/legal.css", "/theme-boot.js")
_asset_versions: dict[str, tuple[float, str]] = {}


def _asset_version(url: str) -> str:
    f = WEB_DIR / url.lstrip("/")
    try:
        mtime = f.stat().st_mtime
    except OSError:
        return ""
    cached = _asset_versions.get(url)
    if not cached or cached[0] != mtime:
        cached = (mtime, hashlib.sha256(f.read_bytes()).hexdigest()[:10])
        _asset_versions[url] = cached
    return cached[1]


# Browser-tab icons: one version over all of them. The served page carries it in <meta name="icon-version">,
# and theme-boot.js / themes.setFavicon put it on the skin's icon URL, so a browser never reuses an old icon.
_ICONS = ("/favicon.svg", "/favicon-stark.svg", "/favicon-batcave.svg", "/favicon-matrix.svg", "/favicon-alfred.svg")


def _icon_version() -> str:
    return hashlib.sha256("".join(_asset_version(u) for u in _ICONS).encode()).hexdigest()[:10]


# Operator details live in .env, not in the repo: the public pages (homepage, legal pages, 404,
# robots.txt, ai.txt, tdmrep.json) carry {{PLACEHOLDERS}} that are filled in when they're served.
def _site_values() -> dict[str, str]:
    from urllib.parse import urlsplit
    host = urlsplit(settings.public_base_url).netloc if settings.public_base_url else ""
    return {"ASSISTANT_NAME": settings.assistant_name, "OPERATOR_NAME": settings.operator_name,
            "OPERATOR_LOCATION": settings.operator_location, "CONTACT_EMAIL": settings.contact_email,
            "SITE_URL": settings.public_base_url or "", "SITE_HOST": host or "this site",
            "COMPANY_NAME": settings.company_name}


def _fill(text: str, escape=html.escape) -> str:
    for key, value in _site_values().items():
        text = text.replace("{{" + key + "}}", escape(value))
    return text


def _versioned(html_text: str) -> str:
    html = _fill(html_text)
    for url in _FIXED_NAME_ASSETS:
        v = _asset_version(url)
        if v:
            html = html.replace(f'"{url}"', f'"{url}?v={v}"')
    iv = _icon_version()
    for url in _ICONS:
        html = html.replace(f'"{url}"', f'"{url}?v={iv}"')
    return html.replace('<link rel="icon"', f'<meta name="icon-version" content="{iv}">\n  <link rel="icon"', 1)


def _page(path: Path, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(_versioned(path.read_text(encoding="utf-8")), status_code=status_code,
                        headers={"Cache-Control": "no-cache"})


# Machine endpoints keep JSON errors; any other unknown address gets the styled "page not found" page.
_MACHINE_PATHS = ("/api/", "/twilio/", "/n8n/", "/mail/", "/internal/", "/healthz")


@app.exception_handler(StarletteHTTPException)
async def _http_errors(request: Request, exc: StarletteHTTPException) -> Response:
    path = request.url.path
    if exc.status_code == 404 and request.method in ("GET", "HEAD") and not path.startswith(_MACHINE_PATHS):
        page = WEB_DIR / "not-found.html"
        if page.is_file():
            return _page(page, status_code=404)
    return await http_exception_handler(request, exc)

app.add_middleware(SecurityMiddleware)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"ok": True}


# Components the external watchdog (infra/watchdog, on another Pi) reports on when they fail.
WATCHDOG_COMPONENTS = ("claude", "telegram", "twilio", "jetson_speech")


@app.get("/internal/watchdog", include_in_schema=False)
async def watchdog(request: Request) -> Response:
    """For the external watchdog only: component up/down flags (no details) and how old the last
    health check is (a stuck health loop means Alfred is hung). Needs WATCHDOG_TOKEN; 404 without."""
    import hmac
    import json as _json
    from datetime import datetime, timezone
    given = request.headers.get("x-watchdog-token", "")
    if not settings.watchdog_token or not hmac.compare_digest(given.encode(), settings.watchdog_token.encode()):
        return Response(status_code=404)
    rows = {r["service"]: r for r in db.query("SELECT service, ok, checked_at FROM health")}
    newest = max((r["checked_at"] for r in rows.values()), default=None)
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(newest)).total_seconds() if newest else None
    body = {"ok": True, "health_age_s": round(age) if age is not None else None,
            "components": {c: bool(rows[c]["ok"]) for c in WATCHDOG_COMPONENTS if c in rows}}
    return Response(_json.dumps(body), media_type="application/json", headers={"Cache-Control": "no-store"})


app.include_router(call_handler.router)
app.include_router(n8n_callback.router)
app.include_router(mail_inbound.router)
app.include_router(dashboard_router)
app.include_router(people_router)
app.include_router(twofa_router)
app.include_router(voice_router)
app.include_router(privacy_router)
app.include_router(audit_router)
app.include_router(screening_router)
app.include_router(outside_router)
app.include_router(notes_router)
app.include_router(agenda_router)
app.include_router(viewas_router)
app.include_router(settings_router)
app.include_router(access_router)
app.include_router(crm_router)
app.include_router(crm_actions_router)
app.include_router(billing_router)
app.include_router(team_router)
app.include_router(approvals_router)
app.include_router(continuity_router)
app.include_router(google_router)
app.include_router(chat_router)
app.include_router(reminders_router)
app.include_router(briefing_router)
app.include_router(memory_router)
app.include_router(quiet_router)
app.include_router(meetings_router)
app.include_router(mail_templates_router)     # before mail_router: /api/mail/templates vs /api/mail/{email_id}
app.include_router(mail_router)
def _legal(name: str) -> Response:
    path = WEB_DIR / "legal" / f"{name}.html"
    if not path.is_file():
        raise HTTPException(status_code=404)
    return _page(path)


# Public pages: "/" explains the app without a login (required by Google's OAuth review);
# the dashboard single-page app lives at /app.
@app.get("/", include_in_schema=False)
async def home() -> Response:
    path = WEB_DIR / "home.html"
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Site not built")
    html = path.read_text(encoding="utf-8")
    token = settings.google_site_verification
    if token and re.fullmatch(r"[A-Za-z0-9_-]{10,100}", token):
        # Search Console "HTML tag" ownership proof.
        html = html.replace("</head>", f'  <meta name="google-site-verification" content="{token}">\n</head>', 1)
    return HTMLResponse(_versioned(html), headers={"Cache-Control": "no-cache"})  # new builds rename assets


@app.get("/google{code}.html", include_in_schema=False)
async def google_site_verification_file(code: str) -> Response:
    """Search Console "HTML file" ownership proof, configured via GOOGLE_SITE_VERIFICATION_FILE."""
    name = f"google{code}.html"
    if not settings.google_site_verification_file or name != settings.google_site_verification_file \
            or not re.fullmatch(r"google[0-9a-f]{8,64}\.html", name):
        raise HTTPException(status_code=404)
    return PlainTextResponse(f"google-site-verification: {name}")


@app.get("/app", include_in_schema=False)
@app.get("/app/", include_in_schema=False)
async def dashboard_app() -> Response:
    path = WEB_DIR / "index.html"
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Dashboard not built: run `npm run build` in dashboard/web.")
    return _page(path)


# Public legal pages at clean URLs (also linked from Google's OAuth consent screen).
@app.get("/privacy-policy", include_in_schema=False)
async def privacy_policy() -> Response:
    return _legal("privacy-policy")


@app.get("/terms-of-service", include_in_schema=False)
async def terms_of_service() -> Response:
    return _legal("terms-of-service")


@app.get("/legal/{name}.html", include_in_schema=False)
async def legal_file(name: str) -> Response:
    """The raw files hold placeholders: always send people to the filled-in pages."""
    if name not in ("privacy-policy", "terms-of-service"):
        raise HTTPException(status_code=404)
    return RedirectResponse(f"/{name}", status_code=308)


def _public_text(rel: str, media_type: str, escape) -> Response:
    path = WEB_DIR / rel
    if not path.is_file():
        raise HTTPException(status_code=404)
    return Response(_fill(path.read_text(encoding="utf-8"), escape), media_type=media_type,
                    headers={"Cache-Control": "no-cache"})


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt() -> Response:
    return _public_text("robots.txt", "text/plain; charset=utf-8", str)


@app.get("/ai.txt", include_in_schema=False)
async def ai_txt() -> Response:
    return _public_text("ai.txt", "text/plain; charset=utf-8", str)


@app.get("/.well-known/tdmrep.json", include_in_schema=False)
async def tdmrep_json() -> Response:
    return _public_text(".well-known/tdmrep.json", "application/json", lambda v: json.dumps(v)[1:-1])


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
