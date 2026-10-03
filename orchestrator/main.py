"""Alfred (the "alfred" service) entrypoint: one FastAPI process hosting every channel, the dashboard and background jobs.

    uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from dashboard.api.chat import router as chat_router
from dashboard.api.reminders import router as reminders_router
from dashboard.api.briefing import router as briefing_router
from dashboard.api.memory import router as memory_router
from dashboard.api.quiet import router as quiet_router
from dashboard.api.meetings import router as meetings_router
from dashboard.api.google import router as google_router
from dashboard.api.people import router as people_router
from dashboard.api.twofa import router as twofa_router
from dashboard.api.voice import router as voice_router
from dashboard.api.privacy import router as privacy_router
from dashboard.api.audit import router as audit_router
from dashboard.api.screening import outside as outside_router
from dashboard.api.screening import router as screening_router
from dashboard.api.routes import router as dashboard_router
from dashboard.api.security import SecurityMiddleware

from . import (briefing, call_handler, db, health, homelab, n8n_callback, reminders, scheduler, sessions, tasks,
               telegram_handler)
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
    log.info("%s up — telegram=%s phone=%s n8n=%s model=%s",
             settings.assistant_name, settings.telegram_enabled, settings.twilio_enabled and settings.speech_enabled,
             settings.n8n_enabled, settings.claude_model)
    try:
        yield
    finally:
        for task in (bg, sched, remind, brief, follow, lab):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await telegram_handler.stop()
        db.log_activity("system", f"{settings.assistant_name} stopped")


app = FastAPI(title="Alfred", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

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
app.include_router(dashboard_router)
app.include_router(people_router)
app.include_router(twofa_router)
app.include_router(voice_router)
app.include_router(privacy_router)
app.include_router(audit_router)
app.include_router(screening_router)
app.include_router(outside_router)
app.include_router(google_router)
app.include_router(chat_router)
app.include_router(reminders_router)
app.include_router(briefing_router)
app.include_router(memory_router)
app.include_router(quiet_router)
app.include_router(meetings_router)
def _legal(name: str) -> FileResponse:
    path = WEB_DIR / "legal" / f"{name}.html"
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="text/html", headers={"Cache-Control": "public, max-age=3600"})


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
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})  # new builds rename assets


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
async def dashboard_app() -> FileResponse:
    path = WEB_DIR / "index.html"
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Dashboard not built: run `npm run build` in dashboard/web.")
    return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-cache"})


# Public legal pages at clean URLs (also linked from Google's OAuth consent screen).
@app.get("/privacy-policy", include_in_schema=False)
async def privacy_policy() -> FileResponse:
    return _legal("privacy-policy")


@app.get("/terms-of-service", include_in_schema=False)
async def terms_of_service() -> FileResponse:
    return _legal("terms-of-service")


if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
