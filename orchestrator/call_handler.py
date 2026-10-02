"""Telephony: Twilio voice webhooks + Media Streams WebSocket <-> STT / Agent Core / TTS.

Flow (inbound):
  Twilio POST /twilio/voice  -> caller ID resolved against the registry
     unknown  -> call screening (screening.py): Alfred takes a message for the owner, or
                 <Reject reason="busy"/> if screening is off / the number is withheld, blocked
                 or over its daily limit (never answered, nothing revealed, no cost)
     known    -> <Connect><Stream wss://.../twilio/stream> with a one-time token
  Twilio WS /twilio/stream   -> mu-law frames -> STT; committed utterances -> Agent Core
                                replies -> TTS -> mu-law frames back to Twilio
Outbound calls use the same stream endpoint via /twilio/voice/outbound.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from . import agent_core, db, sessions, usage
from .config import settings
from .identity import registry
from .identity.registry import Person
from .storage.recordings import CallRecorder
from .stt_tts.base import Provider, get_provider

log = logging.getLogger(__name__)
router = APIRouter()

PENDING_TTL = 120  # seconds a stream token stays valid after the webhook answered
FILLER = "One moment."   # said when a tool runs before anything else was said this turn

_SENTENCE_END = re.compile(r"(?<=[.!?…:;])\s+")
MIN_SENTENCE = 12        # don't split off tiny fragments ("Mr.", "OK.")
MAX_UNSPOKEN = 220       # a very long sentence is split at a comma/space rather than waited for


def split_speakable(text: str) -> tuple[list[str], str]:
    """Split streamed text into complete sentences ready for TTS and the unfinished remainder."""
    ready, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        if m.start() - start >= MIN_SENTENCE:
            ready.append(text[start:m.start()])
            start = m.end()
    rest = text[start:]
    if len(rest) > MAX_UNSPOKEN:
        cut = max(rest.rfind(", ", 0, MAX_UNSPOKEN), rest.rfind(" ", 0, MAX_UNSPOKEN))
        if cut > MIN_SENTENCE:
            ready.append(rest[:cut + 1])
            rest = rest[cut + 1:].lstrip()
    return ready, rest


@dataclass
class PendingCall:
    session_id: str
    user_id: int
    token: str
    opening_line: str
    extra_system: str | None = None
    created: float = field(default_factory=time.monotonic)
    amd: bool = False                     # Twilio answering-machine detection running (outbound)
    voicemail: dict | None = None         # who/why, to compose a message if a mailbox answers
    answered_by: str | None = None        # AMD verdict that arrived before the media stream did
    screening: str | None = None          # caller's number: an unknown caller, Alfred takes a message


# Twilio AnsweredBy values (async AMD with DetectMessageEnd): human | machine_end_beep |
# machine_end_silence | machine_end_other | fax | unknown.
MAILBOX = ("machine_end_beep", "machine_end_silence", "machine_end_other")
AMD_WAIT_S = 35   # never hold the conversation longer than this waiting for a verdict


_pending: dict[str, PendingCall] = {}
_stream_connected: set[str] = set()
ACTIVE_CALLS: dict[str, "CallSession"] = {}


# --- helpers -----------------------------------------------------------------------------

def _twiml(body: str) -> Response:
    return Response(content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>',
                    media_type="application/xml")


def _stream_twiml(p: PendingCall) -> Response:
    url = f"{settings.public_ws_base}/twilio/stream"
    return _twiml(
        f"<Connect><Stream url={quoteattr(url)}>"
        f"<Parameter name=\"session_id\" value={quoteattr(p.session_id)}/>"
        f"<Parameter name=\"token\" value={quoteattr(p.token)}/>"
        f"</Stream></Connect>"
    )


async def _validated_form(request: Request) -> dict[str, str]:
    form = {k: str(v) for k, v in (await request.form()).items()}
    if settings.twilio_validate_signature:
        from twilio.request_validator import RequestValidator
        url = f"{settings.public_base_url}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"
        sig = request.headers.get("X-Twilio-Signature", "")
        if not RequestValidator(settings.twilio_auth_token).validate(url, form, sig):
            log.warning("Rejected Twilio webhook with bad signature (%s)", request.url.path)
            raise HTTPException(status_code=403)
    return form


def _gc_pending() -> None:
    now = time.monotonic()
    for sid, p in list(_pending.items()):
        if now - p.created > PENDING_TTL:
            _pending.pop(sid, None)
            if sid not in _stream_connected:
                sessions.end_session(sid, status="failed")


def _call_finished(session_id: str, outcome: str) -> None:
    """Tell the scheduler a call ended (it reports outcomes / schedules retries)."""
    from . import scheduler
    asyncio.create_task(scheduler.on_call_finished(session_id, outcome))


# Every call is recorded (audio + transcript), so every call opens by saying so.
RECORDING_NOTICE = "Just so you know, this call is recorded."


def with_recording_notice(line: str) -> str:
    line = line.strip()
    return line if RECORDING_NOTICE in line else f"{line} {RECORDING_NOTICE}".strip()


def _greeting(person: Person) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    hour = datetime.now(ZoneInfo(settings.timezone)).hour
    part = "morning" if 5 <= hour < 12 else "afternoon" if hour < 18 else "evening"
    who = "sir" if person.is_admin else person.display_name.split()[0]
    return f"Good {part}, {who}. {settings.assistant_name} here. {RECORDING_NOTICE} How may I be of service?"


# --- webhooks ----------------------------------------------------------------------------

@router.post("/twilio/voice")
async def inbound_voice(request: Request) -> Response:
    form = await _validated_form(request)
    _gc_pending()
    caller = form.get("From", "")
    person = registry.resolve("call", "phone", caller)
    if person is None:
        from . import screening
        boss = screening.accept(caller)
        if boss is None:
            log.info("Rejected call from unregistered number %s", caller)
            return _twiml('<Reject reason="busy"/>')
        sid = sessions.start_session(boss.id, "call", "screening", external_id=form.get("CallSid"))
        number = screening.start(sid, caller)
        p = PendingCall(sid, boss.id, secrets.token_urlsafe(24), screening.greeting(boss), screening=number)
        _pending[sid] = p
        log.info("Screening a call from unknown number %s", number)
        return _stream_twiml(p)
    if not (settings.speech_enabled and settings.public_base_url):
        log.error("Call from %s but ElevenLabs/PUBLIC_BASE_URL not configured", person.display_name)
        return _twiml('<Reject reason="busy"/>')

    sid = sessions.start_session(person.id, "call", "inbound", external_id=form.get("CallSid"))
    p = PendingCall(sid, person.id, secrets.token_urlsafe(24), _greeting(person))
    _pending[sid] = p
    return _stream_twiml(p)


@router.post("/twilio/voice/outbound")
async def outbound_voice(request: Request, session_id: str) -> Response:
    await _validated_form(request)
    p = _pending.get(session_id)
    if p is None:
        return _twiml("<Hangup/>")
    p.created = time.monotonic()  # the clock starts when they pick up
    return _stream_twiml(p)


@router.post("/twilio/amd")
async def amd_result(request: Request, session_id: str) -> Response:
    """Async answering-machine detection verdict for an outbound call."""
    form = await _validated_form(request)
    answered_by = form.get("AnsweredBy", "unknown")
    log.info("AMD for %s: %s", session_id, answered_by)
    call = ACTIVE_CALLS.get(session_id)
    if call is not None:
        asyncio.create_task(call.on_amd(answered_by))
    elif session_id in _pending:
        _pending[session_id].answered_by = answered_by
    return Response(status_code=204)


@router.post("/twilio/status")
async def call_status(request: Request, session_id: str) -> Response:
    form = await _validated_form(request)
    status = form.get("CallStatus", "")
    if status in ("completed", "busy", "no-answer", "failed", "canceled") and session_id not in _stream_connected:
        was_pending = _pending.pop(session_id, None) is not None
        sessions.end_session(session_id, status="ended" if status == "completed" else "failed")
        if was_pending:  # never reached the media stream: nobody (or voicemail) answered
            _call_finished(session_id, "no_answer" if status in ("busy", "no-answer", "canceled", "completed")
                           else "failed")
    return Response(status_code=204)


# --- outbound ----------------------------------------------------------------------------

def _twilio_client():
    from twilio.rest import Client
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


async def place_outbound_call(user_id: int, *, opening_line: str, reason: str = "",
                              requested_by: int | None = None, request_id: int | None = None,
                              extra_system: str | None = None, voicemail: dict | None = None) -> str:
    if not (settings.twilio_enabled and settings.speech_enabled and settings.public_base_url):
        raise RuntimeError("Telephony isn't fully configured (Twilio, ElevenLabs, PUBLIC_BASE_URL).")
    person = registry.get_person(user_id)
    number = registry.phone_for(user_id)
    if person is None or not number:
        raise ValueError(f"User {user_id} has no verified phone number.")
    if not registry.role_allows_channel(person.role, "call"):
        raise PermissionError(f"Role '{person.role}' has no call access.")

    sid = sessions.start_session(person.id, "call", "outbound")
    if request_id is not None:
        db.execute("UPDATE scheduled_calls SET session_id = ? WHERE id = ?", (sid, request_id))
    extra = extra_system or (f"This is an outbound call you placed. Reason: {reason}" if reason
                             else "This is an outbound call you placed.")
    amd = settings.voicemail_detection
    _pending[sid] = PendingCall(sid, person.id, secrets.token_urlsafe(24), with_recording_notice(opening_line),
                                extra, amd=amd, voicemail={"target": person.display_name, **(voicemail or {})})
    q = urlencode({"session_id": sid})
    amd_kw = dict(machine_detection="DetectMessageEnd", async_amd="true",
                  async_amd_status_callback=f"{settings.public_base_url}/twilio/amd?{q}",
                  async_amd_status_callback_method="POST") if amd else {}

    def _create():
        return _twilio_client().calls.create(
            to=number,
            from_=registry.normalize_phone(settings.twilio_phone_number),  # Twilio needs E.164
            url=f"{settings.public_base_url}/twilio/voice/outbound?{q}",
            status_callback=f"{settings.public_base_url}/twilio/status?{q}",
            status_callback_event=["completed"],
            timeout=30,
            **amd_kw,
        )

    try:
        call = await asyncio.to_thread(_create)
    except Exception:
        _pending.pop(sid, None)
        sessions.end_session(sid, status="failed")
        raise
    db.execute("UPDATE sessions SET external_id = ? WHERE id = ?", (call.sid, sid))
    db.log_activity("call", f"Outbound call to {person.display_name}", user_id=requested_by, session_id=sid)
    return call.sid


async def health() -> tuple[bool, str]:
    if not settings.twilio_enabled:
        return False, "not configured"
    try:
        acct = await asyncio.to_thread(lambda: _twilio_client().api.v2010.accounts(settings.twilio_account_sid).fetch())
        return acct.status == "active", f"account {acct.status}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


async def end_call(session_id: str, *, by_user_id: int | None = None, status: str = "terminated",
                   reason: str = "from dashboard") -> dict:
    """Hang up a call from the dashboard: Twilio first, then force-close locally if needed.

    Works for live calls, outbound calls still ringing, and "stuck" sessions whose call is
    already gone but whose DB row is still active (e.g. after a crash or network drop).
    """
    row = db.query_one("SELECT channel, status, external_id, user_id FROM sessions WHERE id = ?", (session_id,))
    if not row or row["channel"] != "call":
        raise ValueError("Not a call session.")
    result: dict = {"twilio": "skipped", "local": "none"}

    if row["external_id"] and settings.twilio_enabled:
        def _hangup() -> str:
            calls = _twilio_client().calls(row["external_id"])
            current = calls.fetch().status
            if current in ("queued", "ringing"):
                calls.update(status="canceled")
                return "canceled"
            if current == "in-progress":
                calls.update(status="completed")
                return "completed"
            return f"already {current}"
        try:
            result["twilio"] = await asyncio.to_thread(_hangup)
        except Exception as e:  # noqa: BLE001 — still force-close locally below
            log.exception("Twilio hangup failed for %s", session_id)
            result["twilio"] = f"error: {str(e)[:120]}"

    call = ACTIVE_CALLS.get(session_id)
    if call is not None:
        call.end_status = status
        with contextlib.suppress(Exception):
            await call.ws.close(code=1000)
        for _ in range(50):  # give the stream handler up to 5 s to tear down cleanly
            if session_id not in ACTIVE_CALLS:
                break
            await asyncio.sleep(0.1)
        if session_id in ACTIVE_CALLS:
            await call.close()
        result["local"] = "stream closed"

    _pending.pop(session_id, None)
    _stream_connected.discard(session_id)
    still_active = db.query_one("SELECT 1 FROM sessions WHERE id = ? AND status = 'active'", (session_id,))
    if still_active:
        sessions.end_session(session_id, status=status)
        result["local"] = "session closed" if result["local"] == "none" else result["local"]
        if call is None:
            _call_finished(session_id, status)
    db.log_activity("call", f"Call {'ended' if status == 'ended' else 'terminated'} {reason} "
                            f"(twilio: {result['twilio']})", user_id=by_user_id, session_id=session_id)
    return result


def request_hangup(session_id: str) -> bool:
    """end_call tool: hang up once the current reply has been spoken."""
    call = ACTIVE_CALLS.get(session_id)
    if call is None:
        return False
    call.hangup_requested = True
    if not call.speaking:  # the goodbye has already been played
        asyncio.create_task(call._hangup())
    else:
        asyncio.create_task(call._hangup_fallback())
    return True


async def voicemail_message(person: Person, info: dict) -> str:
    """A short, speakable voicemail from the call's purpose (one small Claude call; plain fallback)."""
    first = "sir" if person.is_admin else person.display_name.split()[0]
    requester, purpose = info.get("requester"), (info.get("purpose") or "").strip()
    name = settings.assistant_name
    on_behalf = f", calling on behalf of {requester.split()[0]}" if requester else ""
    fallback = (f"Hello {first}, this is {name}{on_behalf}. "
                + ("I'll try you again a little later." if purpose else "Nothing urgent; I'll try again later.")
                + " Goodbye.")
    if not purpose or not settings.anthropic_api_key:
        return fallback
    try:
        from .agent_core import client
        resp = await asyncio.wait_for(client().messages.create(
            model=settings.claude_model, max_tokens=150,
            system=(f"You are {name}, a courteous British butler, leaving a voicemail. Write ONLY the message "
                    "to be spoken: at most 2 short sentences, under 45 words, no lists or symbols. Greet them, "
                    "say who you are" + (f" and that you call on behalf of {requester}" if requester else "")
                    + ", state the matter briefly as a request or note (not as an instruction to yourself), "
                    "say you'll try again later or they may call back, and say goodbye."),
            messages=[{"role": "user", "content": f"Recipient: {first}. Matter: {purpose}"}]), timeout=6)
        usage.record_claude(getattr(resp, "usage", None), kind="voicemail", user_id=person.id)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return text or fallback
    except Exception:  # noqa: BLE001
        log.exception("Voicemail composition failed; using the plain message")
        return fallback


# --- media stream ------------------------------------------------------------------------

class CallSession:
    """One live call: glues Twilio audio, STT, Agent Core and TTS together."""

    BARGE_IN_MIN_CHARS = 6

    def __init__(self, ws: WebSocket, stream_sid: str, pending: PendingCall, person: Person, provider: Provider):
        self.ws = ws
        self.stream_sid = stream_sid
        self.session_id = pending.session_id
        self.person = person
        self.provider = provider
        self.opening_line = pending.opening_line
        self.extra_system = pending.extra_system
        self.recorder = CallRecorder(self.session_id)
        self.utterances: asyncio.Queue[str] = asyncio.Queue()
        self.stt = provider.stt_factory(self._on_partial, self._on_final)
        self._send_lock = asyncio.Lock()
        self._speak_task: asyncio.Task | None = None
        self._say: asyncio.Queue[str] = asyncio.Queue()
        self._tts_active = False
        self._turn_id = 0
        self._worker: asyncio.Task | None = None
        self._mark_seq = 0
        self._pending_mark: str | None = None
        self.live = sessions.LIVE.get(self.session_id)
        self._closed = False
        self.end_status = "ended"
        self.outcome: str | None = None          # reported to the scheduler instead of end_status
        self.hangup_requested = False
        self._hanging_up = False
        # Outbound + AMD: hold the conversation until Twilio says human or mailbox.
        self.voicemail_info = pending.voicemail
        self._amd_done = asyncio.Event()
        if not pending.amd:
            self._amd_done.set()
        self._early_verdict = pending.answered_by
        self.left_voicemail = False
        self.who = person.display_name             # for the log

    # lifecycle
    async def start(self) -> None:
        try:
            await self.stt.start()
        except Exception:
            # Jetson speech down at call start: carry on with ElevenLabs if it's configured.
            if self.provider.name != "jetson" or not settings.elevenlabs_enabled:
                raise
            log.exception("Jetson STT unavailable; this call uses ElevenLabs")
            from .stt_tts.elevenlabs import ElevenLabsSTT, ElevenLabsTTS
            self.provider = Provider("elevenlabs", ElevenLabsSTT, ElevenLabsTTS())
            self.stt = self.provider.stt_factory(self._on_partial, self._on_final)
            await self.stt.start()
        self._worker = asyncio.create_task(self._work())
        sessions.add_turn(self.session_id, "assistant", self.opening_line)
        self._speak(self.opening_line)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        ACTIVE_CALLS.pop(self.session_id, None)
        for t in (self._worker, self._speak_task):
            if t:
                t.cancel()
        await self.stt.close()
        duration = self.recorder.close()
        db.log_activity("call", f"Call with {self.who} ended ({duration:.0f}s)",
                        self.person.id, self.session_id)
        sessions.end_session(self.session_id, status=self.end_status)
        direction = (db.query_one("SELECT direction FROM sessions WHERE id = ?", (self.session_id,)) or {}).get(
            "direction", "inbound")
        usage.record_stt(duration, user_id=self.person.id, session_id=self.session_id, provider=self.provider.name)
        usage.record_call_minutes(duration, direction=direction, user_id=self.person.id, session_id=self.session_id)
        _call_finished(self.session_id, self.outcome or self.end_status)

    # inbound events
    async def on_audio(self, ulaw: bytes) -> None:
        self.recorder.caller_audio(ulaw)
        await self.stt.send_audio(ulaw)

    def on_mark(self, name: str) -> None:
        if name == self._pending_mark:
            self._pending_mark = None
            if not self.speaking:
                self._set_speaking(False)
                if self.hangup_requested:
                    asyncio.create_task(self._hangup())

    async def _hangup(self) -> None:
        if self._hanging_up:
            return
        self._hanging_up = True
        await asyncio.sleep(0.4)  # let the last syllable leave the phone line
        await end_call(self.session_id, status="ended", reason=f"by {settings.assistant_name}")

    async def _hangup_fallback(self) -> None:
        """If no playback mark ever arrives (e.g. TTS error), don't leave the line open."""
        await asyncio.sleep(25)
        if not self._closed:
            await self._hangup()

    async def _on_partial(self, text: str) -> None:
        if self.live:
            self.live.partial = text
        if self.speaking and len(text.strip()) >= self.BARGE_IN_MIN_CHARS:
            await self.interrupt()

    async def _on_final(self, text: str) -> None:
        if self.live:
            self.live.partial = ""
        if self.speaking:
            await self.interrupt()
        await self.utterances.put(text)

    # agent loop
    async def _work(self) -> None:
        if self._early_verdict:
            await self.on_amd(self._early_verdict)
        while True:
            text = await self.utterances.get()
            if not self._amd_done.is_set():
                # Might be a mailbox greeting: don't answer it until Twilio has decided.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._amd_done.wait(), AMD_WAIT_S)
                self._amd_done.set()
            if self.left_voicemail:
                continue                    # the mailbox's own words; we've left our message
            while not self.utterances.empty():  # merge anything said while we were busy
                text += " " + self.utterances.get_nowait()
            await self._turn(text)

    # voicemail
    async def on_amd(self, answered_by: str) -> None:
        if self._amd_done.is_set() and answered_by not in MAILBOX:
            return
        if answered_by == "fax":
            self.outcome = "no_answer"
            self._amd_done.set()
            await self._hangup()
            return
        if answered_by not in MAILBOX:          # human / unknown: carry on as a normal call
            self._amd_done.set()
            return
        # The mailbox greeting has finished (beep or silence): leave a short message, then hang up.
        self.left_voicemail = True
        self._amd_done.set()
        await self.interrupt()
        text = await voicemail_message(self.person, self.voicemail_info or {})
        sessions.add_turn(self.session_id, "assistant", text)
        db.log_activity("call", f"Reached {self.person.display_name}'s voicemail; left a message", self.person.id,
                        self.session_id)
        self.outcome = "voicemail"
        self.hangup_requested = True
        self._speak(text)
        asyncio.create_task(self._hangup_fallback())

    async def _turn(self, text: str) -> None:
        """One exchange. Claude's reply is streamed and spoken sentence by sentence, so the caller
        hears the first sentence while the rest is still being written."""
        turn = self._turn_id = self._turn_id + 1
        heard_at = time.monotonic()
        buf, state = [], {"spoken": False, "first": None}

        def say(part: str) -> None:
            part = part.strip()
            if part and turn == self._turn_id:           # a barge-in makes this turn stale
                if state["first"] is None:
                    state["first"] = time.monotonic() - heard_at
                state["spoken"] = True
                self._enqueue(part)

        async def on_event(kind: str, data: str) -> None:
            if kind == "delta":
                buf.append(data)
                ready, rest = split_speakable("".join(buf))
                buf[:] = [rest] if rest else []
                for sentence in ready:
                    say(sentence)
            elif kind == "tool":
                say("".join(buf))                         # e.g. "Let me check." before the tool runs
                buf.clear()
                if not state["spoken"]:
                    say(FILLER)

        try:
            reply = await agent_core.respond(self.person, "call", self.session_id, text,
                                             extra_system=self.extra_system, on_event=on_event)
        except Exception:
            log.exception("Agent failure during call")
            reply = "Sorry, something went wrong on my side."
        if not state["spoken"]:
            say(reply)                                    # nothing streamed (e.g. an error message)
        else:
            say("".join(buf))
        if state["first"] is not None:
            log.info("Call %s: first words queued %.2fs after the caller finished", self.session_id, state["first"])
            if self.live:
                self.live.extra["latency_s"] = round(state["first"], 2)

    # speaking: sentences queue up and are played in order by one player task
    @property
    def speaking(self) -> bool:
        return self._tts_active or not self._say.empty() or self._pending_mark is not None

    def _set_speaking(self, value: bool) -> None:
        if self.live:
            self.live.speaking = value

    def _speak(self, text: str) -> None:
        self._enqueue(text)

    def _enqueue(self, text: str) -> None:
        self._say.put_nowait(text)
        self._set_speaking(True)
        if self._speak_task is None or self._speak_task.done():
            self._speak_task = asyncio.create_task(self._player())

    async def _player(self) -> None:
        while not self._say.empty():
            text = self._say.get_nowait()
            self._tts_active = True
            try:
                await self._speak_run(text)
            finally:
                self._tts_active = False

    async def _speak_run(self, text: str) -> None:
        usage.record_tts(len(text), user_id=self.person.id, session_id=self.session_id, provider=self.provider.name)
        try:
            async for chunk in self.provider.tts.stream(text):
                self.recorder.agent_audio(chunk)
                await self._send({"event": "media", "streamSid": self.stream_sid,
                                  "media": {"payload": base64.b64encode(chunk).decode()}})
            # Twilio echoes the mark once everything before it has played; the last one ends "speaking".
            self._mark_seq += 1
            self._pending_mark = f"reply-{self._mark_seq}"
            await self._send({"event": "mark", "streamSid": self.stream_sid, "mark": {"name": self._pending_mark}})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("TTS failed")
            if self._say.empty():
                self._set_speaking(False)

    async def interrupt(self) -> None:
        self._turn_id += 1                                # drop whatever the current turn still streams
        while not self._say.empty():
            self._say.get_nowait()
        if self._speak_task and not self._speak_task.done():
            self._speak_task.cancel()
        self._tts_active = False
        self._pending_mark = None
        self.recorder.clear_agent()
        self._set_speaking(False)
        await self._send({"event": "clear", "streamSid": self.stream_sid})

    async def _send(self, msg: dict) -> None:
        async with self._send_lock:
            with contextlib.suppress(Exception):
                await self.ws.send_text(json.dumps(msg))


class ScreeningCall(CallSession):
    """An unknown caller: no Agent Core, no tools. Alfred only takes a message (screening.py),
    hangs up after a goodbye, a few exchanges or MAX_SECONDS, then reports to the admins."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.caller = args[2].screening
        self.who = f"unknown caller {self.caller}"
        self.turns = 0
        self._limit: asyncio.Task | None = None

    async def start(self) -> None:
        await super().start()
        self._limit = asyncio.create_task(self._time_limit())

    async def _time_limit(self) -> None:
        from . import screening
        await asyncio.sleep(screening.MAX_SECONDS)
        if not self._closed and not self.hangup_requested:
            self._say_last("I'm afraid I have to end the call here. I'll pass your message on. Goodbye.")

    def _say_last(self, text: str) -> None:
        sessions.add_turn(self.session_id, "assistant", text)
        self.hangup_requested = True
        self._speak(text)
        asyncio.create_task(self._hangup_fallback())

    async def _turn(self, text: str) -> None:
        from . import screening
        if self.hangup_requested:
            return
        self.turns += 1
        sessions.add_turn(self.session_id, "user", text)
        reply, done = await screening.next_line(self.session_id, self.person, self.turns)
        if done or self.turns >= screening.MAX_TURNS:
            self._say_last(reply)
        else:
            sessions.add_turn(self.session_id, "assistant", reply)
            self._speak(reply)

    async def close(self) -> None:
        if self._closed:
            return
        if self._limit:
            self._limit.cancel()
        await super().close()
        from . import screening
        asyncio.create_task(screening.report(self.session_id, self.caller, self.person))


@router.websocket("/twilio/stream")
async def media_stream(ws: WebSocket) -> None:
    await ws.accept()
    call: CallSession | None = None
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            event = msg.get("event")
            if event == "media" and call is not None:
                if msg["media"].get("track", "inbound") == "inbound":
                    await call.on_audio(base64.b64decode(msg["media"]["payload"]))
            elif event == "start":
                start = msg["start"]
                params = start.get("customParameters", {})
                pending = _pending.pop(params.get("session_id", ""), None)
                if pending is None or not hmac.compare_digest(pending.token, params.get("token", "")):
                    log.warning("Media stream with invalid/expired token — closing")
                    await ws.close(code=1008)
                    return
                person = registry.get_person(pending.user_id)
                if person is None:
                    await ws.close(code=1008)
                    return
                _stream_connected.add(pending.session_id)
                cls = ScreeningCall if pending.screening else CallSession
                call = cls(ws, start["streamSid"], pending, person, get_provider())
                ACTIVE_CALLS[pending.session_id] = call
                await call.start()
                db.log_activity("call", f"Call connected with {call.who}", person.id, pending.session_id)
            elif event == "mark" and call is not None:
                call.on_mark(msg.get("mark", {}).get("name", ""))
            elif event == "stop":
                break
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Media stream error")
    finally:
        if call is not None:
            _stream_connected.discard(call.session_id)
            await call.close()
