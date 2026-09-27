"""Alfred entrypoint: one FastAPI process hosting every channel, the dashboard and background jobs.

    uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from dashboard.api.routes import router as dashboard_router

from . import call_handler, db, health, n8n_callback, sessions, telegram_handler
from .config import settings
from .identity import registry
from .tools import registry as tool_registry

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
for noisy in ("httpx", "httpx2", "telegram.ext", "websockets"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("alfred")

WEB_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    registry.bootstrap_admin()
    sessions.mark_orphans_on_startup()
    tool_registry.load()
    db.log_activity("system", "Alfred started")

    await telegram_handler.start()
    bg = asyncio.create_task(health.background_loop())
    log.info("Alfred up — telegram=%s phone=%s n8n=%s model=%s",
             settings.telegram_enabled, settings.twilio_enabled and settings.elevenlabs_enabled,
             settings.n8n_enabled, settings.claude_model)
    try:
        yield
    finally:
        bg.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await bg
        await telegram_handler.stop()
        db.log_activity("system", "Alfred stopped")


app = FastAPI(title="Alfred", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

_secret = settings.session_secret
if not _secret:
    _secret = secrets.token_urlsafe(32)
    log.warning("DASHBOARD_SESSION_SECRET not set — using a random one (logins reset on restart).")

app.add_middleware(
    SessionMiddleware,
    secret_key=_secret,
    session_cookie="alfred_session",
    max_age=60 * 60 * 12,
    same_site="strict",
    https_only=settings.cookie_secure,
)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"ok": True}


app.include_router(call_handler.router)
app.include_router(n8n_callback.router)
app.include_router(dashboard_router)
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
