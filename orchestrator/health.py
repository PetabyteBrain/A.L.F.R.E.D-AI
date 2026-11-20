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
# Network-dependent components the external watchdog alerts on. A short network/DNS hiccup (a minute
# or so) used to fail one probe, which then counted as "down" until the next run 5 minutes later and
# set off a watchdog alert plus "back to normal". Now a failure is only recorded if it is still
# failing when re-checked after RECHECK_S (both re-checks must fail).
CONFIRM = ("claude", "telegram", "twilio", "jetson_speech")
RECHECK_S = (30, 60)  # waits before the first and second re-check


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


def _as_result(res) -> tuple[bool, str]:
    if isinstance(res, Exception):
        return False, str(res)[:200]
    return res


async def check_all() -> None:
    results = await asyncio.gather(*(p() for p in PROBES.values()), return_exceptions=True)
    final = {name: _as_result(res) for name, res in zip(PROBES, results)}
    # Everything that doesn't need confirming is recorded right away.
    for name, res in final.items():
        if name not in CONFIRM or res[0]:
            db.set_health(name, *res)
    failing = [n for n in CONFIRM if n in final and not final[n][0]]
    for wait in RECHECK_S:
        if not failing:
            break
        await asyncio.sleep(wait)
        again = await asyncio.gather(*(PROBES[n]() for n in failing), return_exceptions=True)
        for name, res in zip(failing, again):
            res = _as_result(res)
            if res[0]:
                log.info("health: %s failed once (%s) but recovered on re-check", name, final[name][1])
                db.set_health(name, *res)
            final[name] = res
        failing = [n for n in failing if not final[n][0]]
    for name in failing:
        log.warning("health: %s still failing after re-checks: %s", name, final[name][1])
        db.set_health(name, *final[name])


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
