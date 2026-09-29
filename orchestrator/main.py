"""Alfred (the "alfred" service) entrypoint: one FastAPI process hosting every channel, the dashboard and background jobs.

    uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from dashboard.api.google import router as google_router
from dashboard.api.people import router as people_router
from dashboard.api.routes import router as dashboard_router
from dashboard.api.security import SecurityMiddleware

from . import call_handler, db, health, n8n_callback, scheduler, sessions, telegram_handler
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
    log.info("%s up — telegram=%s phone=%s n8n=%s model=%s",
             settings.assistant_name, settings.telegram_enabled, settings.twilio_enabled and settings.elevenlabs_enabled,
             settings.n8n_enabled, settings.claude_model)
    try:
        yield
    finally:
        bg.cancel()
        sched.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await bg
        with contextlib.suppress(asyncio.CancelledError):
            await sched
        await telegram_handler.stop()
        db.log_activity("system", f"{settings.assistant_name} stopped")


app = FastAPI(title="Alfred", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(SecurityMiddleware)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"ok": True}


app.include_router(call_handler.router)
app.include_router(n8n_callback.router)
app.include_router(dashboard_router)
app.include_router(people_router)
app.include_router(google_router)
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
else:
    @app.get("/", include_in_schema=False)
    async def no_frontend() -> PlainTextResponse:
        return PlainTextResponse("Dashboard not built: run `npm run build` in dashboard/web.", status_code=503)
