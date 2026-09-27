"""Periodic health probes for external services; results land in the `health` table."""
from __future__ import annotations

import asyncio
import logging

import anthropic

from . import call_handler, db, sessions, telegram_handler
from .config import settings
from .tools import n8n_tool

log = logging.getLogger(__name__)

INTERVAL = 300  # seconds


async def _claude() -> tuple[bool, str]:
    if not settings.anthropic_api_key:
        return False, "not configured"
    try:
        from .agent_core import client
        m = await client().models.retrieve(settings.claude_model)
        return True, f"{m.id} reachable"
    except anthropic.AuthenticationError:
        return False, "authentication failed"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


async def _elevenlabs() -> tuple[bool, str]:
    if not settings.elevenlabs_enabled:
        return False, "not configured"
    from .stt_tts.base import get_provider
    return await get_provider().tts.health()


async def _n8n() -> tuple[bool, str]:
    if not settings.n8n_enabled:
        return False, "not configured"
    return await n8n_tool.health()


PROBES = {
    "claude": _claude,
    "telegram": telegram_handler.health,
    "twilio": call_handler.health,
    "elevenlabs": _elevenlabs,
    "n8n": _n8n,
}


async def check_all() -> None:
    results = await asyncio.gather(*(p() for p in PROBES.values()), return_exceptions=True)
    for name, res in zip(PROBES, results):
        if isinstance(res, Exception):
            db.set_health(name, False, str(res)[:200])
        else:
            db.set_health(name, *res)


async def background_loop() -> None:
    """Health probes + housekeeping (closing idle Telegram sessions)."""
    while True:
        try:
            sessions.close_idle_sessions()
            await check_all()
        except Exception:
            log.exception("Health loop iteration failed")
        await asyncio.sleep(INTERVAL)
