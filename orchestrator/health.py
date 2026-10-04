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
    from .stt_tts.elevenlabs import ElevenLabsTTS
    return await ElevenLabsTTS().health()


async def _jetson_speech() -> tuple[bool, str]:
    if settings.stt_tts_provider != "jetson" or not settings.speech_url:
        return False, "not configured"
    from .stt_tts.jetson import JetsonTTS
    return await JetsonTTS().health()


async def _backup() -> tuple[bool, str]:
    from . import backup
    return backup.health_now()


async def _restore_drill() -> tuple[bool, str]:
    from . import backup
    return backup.drill_health()


async def _nas() -> tuple[bool, str]:
    from . import nas
    if not nas.enabled():
        return False, "not configured"
    return await asyncio.to_thread(nas.health)


async def _n8n() -> tuple[bool, str]:
    if not settings.n8n_enabled:
        return False, "not configured"
    return await n8n_tool.health()


async def _google() -> tuple[bool, str]:
    from . import google_calendar
    return await google_calendar.health()


PROBES = {
    "claude": _claude,
    "telegram": telegram_handler.health,
    "twilio": call_handler.health,
    "elevenlabs": _elevenlabs,
    "jetson_speech": _jetson_speech,
    "backup": _backup,
    "restore_drill": _restore_drill,
    "nas": _nas,
    "n8n": _n8n,
    "google": _google,
}


async def check_all() -> None:
    results = await asyncio.gather(*(p() for p in PROBES.values()), return_exceptions=True)
    for name, res in zip(PROBES, results):
        if isinstance(res, Exception):
            db.set_health(name, False, str(res)[:200])
        else:
            db.set_health(name, *res)


def _housekeeping() -> None:
    from dashboard.api.security import prune_sessions
    prune_sessions()
    # Blocked-access spam must not grow the DB forever.
    db.execute("DELETE FROM access_attempts WHERE ts < strftime('%Y-%m-%dT%H:%M:%S', 'now', '-90 days')")
    # Sign-in and security events are the audit trail (exportable on the Log tab): kept a year.
    db.execute("DELETE FROM activity WHERE kind = 'access' AND ts < strftime('%Y-%m-%dT%H:%M:%S', 'now', '-365 days')")


async def background_loop() -> None:
    """Health probes + housekeeping (closing idle Telegram sessions)."""
    while True:
        try:
            sessions.close_idle_sessions()
            _housekeeping()
            await check_all()
            from . import budgets, retention
            retention.daily()
            await budgets.check()
        except Exception:
            log.exception("Health loop iteration failed")
        await asyncio.sleep(INTERVAL)
