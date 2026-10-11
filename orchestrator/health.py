"""Periodic health probes for external services; results land in the `health` table."""
from __future__ import annotations

import asyncio
import logging

import anthropic

from . import ai_config, call_handler, db, sessions, telegram_handler
from .config import settings
from .tools import n8n_tool

log = logging.getLogger(__name__)

INTERVAL = 300  # seconds


async def _claude() -> tuple[bool, str]:
    if not ai_config.configured():
        return False, "not configured"
    try:
        from .agent_core import client
        m = await client().models.retrieve(ai_config.current()["model"])
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


async def _backup_nas() -> tuple[bool, str]:
    from . import backup_nas, nas
    if not nas.enabled():
        return False, "NAS not configured"
    return backup_nas.health_now()


async def _searxng() -> tuple[bool, str]:
    from . import market_analysis
    return await market_analysis.searx_health()


async def _updates() -> tuple[bool, str]:
    from . import update_check
    return update_check.health_now()


async def _nas() -> tuple[bool, str]:
    from . import nas
    if not nas.enabled():
        return False, "not configured"
    return await asyncio.to_thread(nas.health)


async def _n8n() -> tuple[bool, str]:
    if not settings.n8n_enabled:
        return False, "not configured"
    return await n8n_tool.health()


async def _email() -> tuple[bool, str]:
    from . import mail
    return await mail.health()


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
    "backup_nas": _backup_nas,
    "restore_drill": _restore_drill,
    "updates": _updates,
    "searxng": _searxng,
    "nas": _nas,
    "n8n": _n8n,
    "email": _email,
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
    from . import uploads
    uploads.prune()                     # Telegram files nobody saved (1 h)
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
            from . import budgets, recording_archive, retention
            retention.daily()
            await asyncio.to_thread(recording_archive.daily)
            from . import mail
            await asyncio.to_thread(mail.daily)     # archive retries + mail retention (once a day)
            from . import agenda
            await agenda.sync_all()             # reminders follow their calendar events
            await budgets.check()
            from . import update_check
            await update_check.monthly()        # 1st of the month: report only
        except Exception:
            log.exception("Health loop iteration failed")
        await asyncio.sleep(INTERVAL)
