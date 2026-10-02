"""Voice notes on request (Telegram).

- One reply: the person asks ("answer by voice", "send that as a voice note"); the model calls
  `reply_by_voice` and that turn's reply goes out as a voice note with the text.
- Voice mode: "voice replies for the next hour" / "back to text" (`voice_mode` tool or Profile).
  While it is on, every Telegram reply and every proactive message (reminder, briefing, call
  report, task/meeting updates) also comes as a voice note. Sign-in codes never do.
- What is spoken: the text without Markdown, links or code; long texts are summarised for the
  ear by the model first, and the full text is always sent alongside.
- Speech comes from the active provider (Jetson Kokoro), falling back to ElevenLabs if the
  Jetson fails mid-request. Metered as TTS (and the summary as a small Claude call).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import db, usage
from .config import settings

log = logging.getLogger(__name__)

SPOKEN_MAX = 700          # longer texts are summarised for speaking
CAPTION_MAX = 900         # Telegram caption limit is 1024 incl. markup
MAX_MINUTES = 7 * 24 * 60

_requested: set[str] = set()   # session ids whose current reply should be a voice note


class VoiceError(ValueError):
    pass


# --- one-off request ---------------------------------------------------------------------

def request_for(session_id: str | None) -> None:
    if session_id:
        _requested.add(session_id)


def pop_request(session_id: str) -> bool:
    if session_id in _requested:
        _requested.discard(session_id)
        return True
    return False


# --- voice mode --------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row(user_id: int) -> dict | None:
    row = db.query_one("SELECT until FROM voice_mode WHERE user_id = ?", (user_id,))
    if row and row["until"] and datetime.fromisoformat(row["until"]) <= _now():
        db.execute("DELETE FROM voice_mode WHERE user_id = ?", (user_id,))
        return None
    return row


def is_on(user_id: int) -> bool:
    return _row(user_id) is not None


def set_mode(user_id: int, on: bool, minutes: int | None = None) -> dict:
    if not on:
        db.execute("DELETE FROM voice_mode WHERE user_id = ?", (user_id,))
        return describe(user_id)
    if minutes is not None and not 1 <= minutes <= MAX_MINUTES:
        raise VoiceError("Voice mode can be set for 1 minute up to 7 days, or until switched off.")
    until = (_now() + timedelta(minutes=minutes)).isoformat() if minutes else None
    db.execute("INSERT INTO voice_mode(user_id, until, updated_at) VALUES (?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET until = excluded.until, updated_at = excluded.updated_at",
               (user_id, until, _now().isoformat()))
    return describe(user_id)


def describe(user_id: int) -> dict:
    from .identity import registry
    row = _row(user_id)
    out = {"on": row is not None, "until": None, "until_local": None,
           "telegram_linked": bool(registry.telegram_for(user_id)), "speech_available": settings.speech_enabled}
    if row and row["until"]:
        local = datetime.fromisoformat(row["until"]).astimezone(ZoneInfo(settings.timezone))
        out["until"] = row["until"]
        out["until_local"] = local.strftime("%a %d %b %H:%M")
    return out


def prompt_line(user_id: int, channel: str) -> str | None:
    if channel == "telegram" and is_on(user_id):
        return ("Voice mode is on for this person: your Telegram replies are also sent as voice notes, so write "
                "them to be spoken (natural sentences, brief, no tables or code unless asked).")
    return None


# --- what gets spoken --------------------------------------------------------------------

_CODE_BLOCK = re.compile(r"```.*?```", re.S)
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_URL = re.compile(r"https?://\S+")
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", re.M)


def plain(md: str) -> str:
    t = _CODE_BLOCK.sub(" (the code is in the message) ", md)
    t = _LINK.sub(r"\1", t)
    t = _URL.sub("the link in the message", t)
    t = _TABLE_RULE.sub("", t)
    t = re.sub(r"^[ \t]*#{1,6}[ \t]+", "", t, flags=re.M)        # headings
    t = re.sub(r"^[ \t]*>[ \t]?", "", t, flags=re.M)              # quotes
    t = re.sub(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+", "", t, flags=re.M)  # list markers
    t = t.replace("|", ", ")
    t = re.sub(r"(\*\*|__|\*|_|`|~~)", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = "\n".join(line.strip() for line in t.split("\n"))
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


async def spoken_text(text: str, *, user_id: int | None, session_id: str | None) -> str:
    """Plain speakable text; summarised by the model when it would be too long to listen to."""
    spoken = plain(text)
    if len(spoken) <= SPOKEN_MAX:
        return spoken
    from . import agent_core
    try:
        resp = await agent_core.client().messages.create(
            model=settings.claude_model, max_tokens=300,
            system=(f"You are {settings.assistant_name}, a butler. Turn the message into a short spoken summary "
                    "(at most 4 sentences, about 60 words) to be read aloud as a voice note. Plain sentences, no "
                    "Markdown, URLs or lists. Keep the key facts, times and names. Say the full text follows in "
                    "writing only if details were left out."),
            messages=[{"role": "user", "content": text}])
        usage.record_claude(getattr(resp, "usage", None), kind="voice_summary", user_id=user_id, session_id=session_id)
        summary = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        if summary:
            return summary
    except Exception:
        log.exception("Voice summary failed; speaking the start instead")
    cut = spoken[:SPOKEN_MAX]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:end + 1] if end > 200 else cut) + " The rest is in the message."


async def synthesize(text: str, *, user_id: int | None, session_id: str | None) -> bytes:
    """Ogg/Opus voice note from the active provider, with ElevenLabs as the fallback."""
    from .stt_tts.base import active_name, speech_module
    provider = active_name()
    try:
        audio = await speech_module().synthesize_voice_note(text)
    except Exception:
        if provider != "jetson" or not settings.elevenlabs_enabled:
            raise
        log.warning("Jetson voice note failed; falling back to ElevenLabs", exc_info=True)
        from .stt_tts import elevenlabs
        audio, provider = await elevenlabs.synthesize_voice_note(text), "elevenlabs"
    usage.record_tts(len(text), user_id=user_id, session_id=session_id, provider=provider)
    return audio
