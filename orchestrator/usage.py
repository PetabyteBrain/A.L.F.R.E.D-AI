"""API usage metering: every billable call is recorded in `api_usage` for the Usage tab.

What's measurable per provider:
- Anthropic: exact tokens from each response's `usage` (input / output / cache read / cache write).
- ElevenLabs: characters sent to TTS, seconds of audio streamed to STT (plan-dependent price).
- Twilio: call minutes per call here; the real month-to-date bill comes from Twilio's Usage API.
- Google Calendar / n8n: request counts (no per-request cost).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from . import db
from .config import settings

log = logging.getLogger(__name__)


def _insert(provider: str, kind: str, *, model: str = "", input_tokens: int = 0, output_tokens: int = 0,
            cache_read: int = 0, cache_write: int = 0, units: float = 0, unit: str = "", cost: float = 0,
            user_id: int | None = None, session_id: str | None = None, account: str | None = None) -> None:
    try:
        db.execute(
            "INSERT INTO api_usage(ts, provider, kind, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, units, unit, cost_usd, user_id, session_id, account) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (db.now_iso(), provider, kind, model, input_tokens, output_tokens, cache_read, cache_write,
             units, unit, round(cost, 6), user_id, session_id, account),
        )
    except Exception:  # metering must never break the actual work
        log.exception("usage insert failed")


def record_claude(usage, *, kind: str, user_id: int | None = None, session_id: str | None = None,
                  model: str | None = None) -> None:
    if usage is None:
        return
    i = getattr(usage, "input_tokens", 0) or 0
    o = getattr(usage, "output_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    from . import ai_config
    model = model or settings.claude_model
    p_in, p_out, p_cr, p_cw = ai_config.price(model)
    cost = (i * p_in + o * p_out + cr * p_cr + cw * p_cw) / 1_000_000
    account = ai_config.key_name()
    _insert("anthropic", kind, model=model, input_tokens=i, output_tokens=o,
            cache_read=cr, cache_write=cw, cost=cost, user_id=user_id, session_id=session_id, account=account)
    searches = getattr(getattr(usage, "server_tool_use", None), "web_search_requests", 0) or 0
    if searches:
        _insert("anthropic", "web_search", model="web_search", units=searches, unit="searches",
                cost=searches * settings.price_web_search_per_1k / 1000, user_id=user_id, session_id=session_id,
                account=account)


def record_tts(chars: int, *, user_id: int | None, session_id: str | None, provider: str = "elevenlabs") -> None:
    if provider == "jetson":   # self-hosted: count it, no per-use cost
        _insert("jetson", "tts", model="kokoro", units=chars, unit="chars", cost=0.0, user_id=user_id,
                session_id=session_id)
        return
    _insert("elevenlabs", "tts", model=settings.elevenlabs_tts_model, units=chars, unit="chars",
            cost=chars / 1000 * settings.price_tts_per_1k_chars, user_id=user_id, session_id=session_id)


def record_stt(seconds: float, *, user_id: int | None, session_id: str | None, provider: str = "elevenlabs") -> None:
    if provider == "jetson":
        _insert("jetson", "stt", model="whisper", units=round(seconds, 1), unit="seconds", cost=0.0,
                user_id=user_id, session_id=session_id)
        return
    _insert("elevenlabs", "stt", model=settings.elevenlabs_stt_model, units=round(seconds, 1), unit="seconds",
            cost=seconds / 3600 * settings.price_stt_per_hour, user_id=user_id, session_id=session_id)


def record_call_minutes(seconds: float, *, direction: str, user_id: int | None, session_id: str | None) -> None:
    _insert("twilio", f"voice-{direction}", units=round(seconds, 1), unit="seconds",
            user_id=user_id, session_id=session_id)


def record_request(provider: str, kind: str, *, user_id: int | None = None) -> None:
    _insert(provider, kind, units=1, unit="requests", user_id=user_id)


# --- reporting ---------------------------------------------------------------------------

_twilio_cache: dict = {"at": 0.0, "data": None}


async def twilio_month_to_date() -> dict | None:
    """Actual Twilio spend this month via the Usage Records API (cached 10 min)."""
    if not settings.twilio_enabled:
        return None
    if _twilio_cache["data"] is not None and time.monotonic() - _twilio_cache["at"] < 600:
        return _twilio_cache["data"]

    def _fetch() -> dict:
        from twilio.rest import Client
        c = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        out = {"total": None, "currency": "USD", "calls_minutes": None, "calls_count": None, "calls_price": None,
               "phone_numbers_price": None}
        for rec in c.usage.records.this_month.list(category="totalprice", limit=1):
            out["total"], out["currency"] = float(rec.price or 0), (rec.price_unit or "usd").upper()
        for rec in c.usage.records.this_month.list(category="calls", limit=1):
            out["calls_minutes"] = float(rec.usage or 0)
            out["calls_count"] = int(float(rec.count or 0))
            out["calls_price"] = float(rec.price or 0)
        for rec in c.usage.records.this_month.list(category="phonenumbers", limit=1):
            out["phone_numbers_price"] = float(rec.price or 0)
        return out

    try:
        data = await asyncio.to_thread(_fetch)
    except Exception as e:  # noqa: BLE001
        log.warning("Twilio usage fetch failed: %s", e)
        data = {"error": str(e)[:200]}
    _twilio_cache.update(at=time.monotonic(), data=data)
    return data


def report(days: int) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    q = db.query

    claude = q(
        "SELECT kind, model, COUNT(*) AS requests, SUM(input_tokens) AS input, SUM(output_tokens) AS output, "
        "SUM(cache_read_tokens) AS cache_read, SUM(cache_write_tokens) AS cache_write, SUM(cost_usd) AS cost "
        "FROM api_usage WHERE provider = 'anthropic' AND ts >= ? GROUP BY kind, model ORDER BY cost DESC", (since,))
    claude_daily = q(
        "SELECT substr(ts,1,10) AS day, SUM(input_tokens + cache_read_tokens + cache_write_tokens) AS input, "
        "SUM(output_tokens) AS output, SUM(cost_usd) AS cost FROM api_usage "
        "WHERE provider = 'anthropic' AND ts >= ? GROUP BY day ORDER BY day", (since,))
    eleven = q(
        "SELECT kind, SUM(units) AS units, MAX(unit) AS unit, COUNT(*) AS requests, SUM(cost_usd) AS cost "
        "FROM api_usage WHERE provider = 'elevenlabs' AND ts >= ? GROUP BY kind", (since,))
    voice = q(
        "SELECT kind, COUNT(*) AS calls, SUM(units) AS seconds FROM api_usage "
        "WHERE provider = 'twilio' AND ts >= ? GROUP BY kind", (since,))
    requests = q(
        "SELECT provider, kind, SUM(units) AS requests FROM api_usage "
        "WHERE provider IN ('google', 'n8n') AND ts >= ? GROUP BY provider, kind", (since,))
    by_user = q(
        "SELECT COALESCE(u.display_name, '(deleted)') AS user, SUM(a.input_tokens + a.cache_read_tokens + "
        "a.cache_write_tokens) AS input, SUM(a.output_tokens) AS output, SUM(a.cost_usd) AS cost "
        "FROM api_usage a LEFT JOIN users u ON u.id = a.user_id WHERE a.ts >= ? "
        "GROUP BY a.user_id ORDER BY cost DESC", (since,))
    by_account = q(
        "SELECT COALESCE(account, 'primary') AS account, SUM(cost_usd) AS cost, COUNT(*) AS requests FROM api_usage "
        "WHERE provider = 'anthropic' AND ts >= ? GROUP BY COALESCE(account, 'primary') ORDER BY cost DESC", (since,))
    total = sum((r["cost"] or 0) for r in claude) + sum((r["cost"] or 0) for r in eleven)
    return {
        "days": days,
        "since": since,
        "estimated_cost_usd": round(total, 4),
        "anthropic": claude,
        "anthropic_daily": claude_daily,
        "elevenlabs": eleven,
        "elevenlabs_priced": bool(settings.price_tts_per_1k_chars or settings.price_stt_per_hour),
        "voice": voice,
        "requests": requests,
        "by_user": by_user,
        "by_account": by_account,
        "prices": {
            "claude_input_per_mtok": settings.price_claude_input,
            "claude_output_per_mtok": settings.price_claude_output,
            "claude_cache_read_per_mtok": settings.price_claude_cache_read,
            "claude_cache_write_per_mtok": settings.price_claude_cache_write,
        },
    }
