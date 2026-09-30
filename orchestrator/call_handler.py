"""Telephony: Twilio voice webhooks + Media Streams WebSocket <-> STT / Agent Core / TTS.

Flow (inbound):
  Twilio POST /twilio/voice  -> caller ID resolved against the registry
     unknown  -> <Reject reason="busy"/>  (never answered, nothing revealed, no cost)
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


@dataclass
class PendingCall:
    session_id: str
    user_id: int
    token: str
    opening_line: str
    extra_system: str | None = None
    created: float = field(default_factory=time.monotonic)


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
        log.info("Rejected call from unregistered number %s", caller)
        return _twiml('<Reject reason="busy"/>')
    if not (settings.elevenlabs_enabled and settings.public_base_url):
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
                              extra_system: str | None = None) -> str:
    if not (settings.twilio_enabled and settings.elevenlabs_enabled and settings.public_base_url):
        raise RuntimeError("Telephony isn't fully configured (Twilio, ElevenLabs, PUBLIC_BASE_URL).")
    person = registry.get_person(user_id)
    number = registry.phone_for(user_id)
    if person is None or not number:
        raise ValueError(f"User {user_id} is not registered with a phone number.")
    if not registry.role_allows_channel(person.role, "call"):
        raise PermissionError(f"Role '{person.role}' has no call access.")

    sid = sessions.start_session(person.id, "call", "outbound")
    if request_id is not None:
        db.execute("UPDATE scheduled_calls SET session_id = ? WHERE id = ?", (sid, request_id))
    extra = extra_system or (f"This is an outbound call you placed. Reason: {reason}" if reason
                             else "This is an outbound call you placed.")
    _pending[sid] = PendingCall(sid, person.id, secrets.token_urlsafe(24), with_recording_notice(opening_line),
                                extra)
    q = urlencode({"session_id": sid})

    def _create():
        return _twilio_client().calls.create(
            to=number,
            from_=registry.normalize_phone(settings.twilio_phone_number),  # Twilio needs E.164
            url=f"{settings.public_base_url}/twilio/voice/outbound?{q}",
            status_callback=f"{settings.public_base_url}/twilio/status?{q}",
            status_callback_event=["completed"],
            timeout=30,
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
    asyncio.create_task(call._hangup_fallback())
    return True


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
        self._worker: asyncio.Task | None = None
        self._mark_seq = 0
        self._pending_mark: str | None = None
        self.live = sessions.LIVE.get(self.session_id)
        self._closed = False
        self.end_status = "ended"
        self.hangup_requested = False
        self._hanging_up = False

    # lifecycle
    async def start(self) -> None:
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
        db.log_activity("call", f"Call with {self.person.display_name} ended ({duration:.0f}s)",
                        self.person.id, self.session_id)
        sessions.end_session(self.session_id, status=self.end_status)
        direction = (db.query_one("SELECT direction FROM sessions WHERE id = ?", (self.session_id,)) or {}).get(
            "direction", "inbound")
        usage.record_stt(duration, user_id=self.person.id, session_id=self.session_id)
        usage.record_call_minutes(duration, direction=direction, user_id=self.person.id, session_id=self.session_id)
        _call_finished(self.session_id, self.end_status)

    # inbound events
    async def on_audio(self, ulaw: bytes) -> None:
        self.recorder.caller_audio(ulaw)
        await self.stt.send_audio(ulaw)

    def on_mark(self, name: str) -> None:
        if name == self._pending_mark:
            self._pending_mark = None
            self._set_speaking(False)
            if self.hangup_requested and not self.speaking:
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
        while True:
            text = await self.utterances.get()
            while not self.utterances.empty():  # merge anything said while we were busy
                text += " " + self.utterances.get_nowait()
            try:
                reply = await agent_core.respond(self.person, "call", self.session_id, text,
                                                 extra_system=self.extra_system)
            except Exception:
                log.exception("Agent failure during call")
                reply = "Sorry, something went wrong on my side."
            self._speak(reply)

    # speaking
    @property
    def speaking(self) -> bool:
        return (self._speak_task is not None and not self._speak_task.done()) or self._pending_mark is not None

    def _set_speaking(self, value: bool) -> None:
        if self.live:
            self.live.speaking = value

    def _speak(self, text: str) -> None:
        if self._speak_task and not self._speak_task.done():
            self._speak_task.cancel()
        self._speak_task = asyncio.create_task(self._speak_run(text))

    async def _speak_run(self, text: str) -> None:
        self._set_speaking(True)
        usage.record_tts(len(text), user_id=self.person.id, session_id=self.session_id)
        try:
            async for chunk in self.provider.tts.stream(text):
                self.recorder.agent_audio(chunk)
                await self._send({"event": "media", "streamSid": self.stream_sid,
                                  "media": {"payload": base64.b64encode(chunk).decode()}})
            self._mark_seq += 1
            self._pending_mark = f"reply-{self._mark_seq}"
            await self._send({"event": "mark", "streamSid": self.stream_sid, "mark": {"name": self._pending_mark}})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("TTS failed")
            self._set_speaking(False)

    async def interrupt(self) -> None:
        if self._speak_task and not self._speak_task.done():
            self._speak_task.cancel()
        self._pending_mark = None
        self.recorder.clear_agent()
        self._set_speaking(False)
        await self._send({"event": "clear", "streamSid": self.stream_sid})

    async def _send(self, msg: dict) -> None:
        async with self._send_lock:
            with contextlib.suppress(Exception):
                await self.ws.send_text(json.dumps(msg))


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
                call = CallSession(ws, start["streamSid"], pending, person, get_provider())
                ACTIVE_CALLS[pending.session_id] = call
                await call.start()
                db.log_activity("call", f"Call connected with {person.display_name}", person.id, pending.session_id)
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
