"""Self-hosted speech on the Jetson Orin (speech_server/ in this repo): Whisper STT + Kokoro TTS.

Same 8 kHz mu-law contract as the ElevenLabs provider, so the call handler doesn't change.
The server only does voice-activity detection plus a final transcript per utterance; it sends
`speech_start` as soon as the caller is clearly talking, which we pass on as a partial so
barge-in (talking over Alfred) still works.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import AsyncIterator

import httpx
import websockets

from .. import db
from ..config import settings
from .base import STTStream, TTS, TextCallback

log = logging.getLogger(__name__)

BATCH_BYTES = 800            # 100 ms of 8 kHz mu-law per websocket message
SPEAKING_PARTIAL = "[speaking…]"
_health = {"ok": True, "at": 0.0}   # last known reachability, for provider fallback


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.speech_token}"} if settings.speech_token else {}


def mark(ok: bool, detail: str = "") -> None:
    _health.update(ok=ok, at=time.monotonic())
    db.set_health("jetson_speech", ok, detail or ("ok" if ok else "unreachable"))


def is_up() -> bool:
    return _health["ok"]


class JetsonSTT(STTStream):
    def __init__(self, on_partial: TextCallback, on_final: TextCallback):
        super().__init__(on_partial, on_final)
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._buf = bytearray()

    async def start(self) -> None:
        url = settings.speech_url.replace("http", "ws", 1).rstrip("/") + "/v1/stt/stream"
        try:
            self._ws = await websockets.connect(url, additional_headers=_headers(), max_size=2**20, open_timeout=5)
        except Exception as e:  # noqa: BLE001
            mark(False, f"stt connect failed: {str(e)[:120]}")
            raise
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if msg.get("type") == "speech_start":
                    await self.on_partial(SPEAKING_PARTIAL)
                elif msg.get("type") == "final" and msg.get("text", "").strip():
                    await self.on_final(msg["text"].strip())
        except websockets.ConnectionClosed:
            pass
        except Exception:  # noqa: BLE001
            log.exception("Jetson STT reader failed")

    async def send_audio(self, ulaw_8k: bytes) -> None:
        self._buf.extend(ulaw_8k)
        if len(self._buf) >= BATCH_BYTES and self._ws is not None:
            chunk, self._buf = bytes(self._buf), bytearray()
            with contextlib.suppress(websockets.ConnectionClosed):
                await self._ws.send(chunk)

    async def close(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._reader:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader


class JetsonTTS(TTS):
    async def stream(self, text: str) -> AsyncIterator[bytes]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=3.0)) as c:
            async with c.stream("POST", f"{settings.speech_url.rstrip('/')}/v1/tts", headers=_headers(),
                                json={"text": text, "format": "ulaw_8000"}) as r:
                if r.status_code >= 400:
                    mark(False, f"tts HTTP {r.status_code}")
                    raise RuntimeError(f"Jetson TTS {r.status_code}: {(await r.aread())[:200]!r}")
                async for chunk in r.aiter_bytes():
                    if chunk:
                        yield chunk

    async def health(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{settings.speech_url.rstrip('/')}/health")
            d = r.json()
            ok = r.status_code == 200 and d.get("ok")
            detail = (f"{d.get('device')} · {d.get('stt_model', '').split('/')[-1]} · voice {d.get('voice')}"
                      + (f" · stt {d['stt_median_ms']} ms" if d.get("stt_median_ms") else "")
                      + (f" · tts {d['tts_first_median_ms']} ms" if d.get("tts_first_median_ms") else ""))
            mark(bool(ok), detail if ok else "loading models")
            return bool(ok), detail if ok else "loading models"
        except Exception as e:  # noqa: BLE001
            mark(False, str(e)[:120])
            return False, f"unreachable: {str(e)[:120]}"


async def transcribe_file(data: bytes, filename: str = "voice.ogg", mime: str = "audio/ogg") -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=3.0)) as c:
        r = await c.post(f"{settings.speech_url.rstrip('/')}/v1/stt", headers=_headers(),
                         files={"file": (filename, data, mime)})
    if r.status_code >= 400:
        raise RuntimeError(f"Jetson STT {r.status_code}: {r.text[:200]}")
    j = r.json()
    return (j.get("text") or "").strip(), j.get("language") or "en"


async def synthesize_voice_note(text: str) -> bytes:
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=3.0)) as c:
        r = await c.post(f"{settings.speech_url.rstrip('/')}/v1/tts", headers=_headers(),
                         json={"text": text, "format": "ogg_opus"})
    if r.status_code >= 400:
        raise RuntimeError(f"Jetson TTS {r.status_code}: {r.text[:200]}")
    return r.content
