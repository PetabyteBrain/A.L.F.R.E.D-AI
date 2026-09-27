"""ElevenLabs Scribe v2 Realtime (STT, WebSocket) + Flash TTS (HTTP streaming), both mu-law 8 kHz."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from typing import AsyncIterator
from urllib.parse import urlencode

import httpx
import websockets

from .. import db
from ..config import settings
from .base import STTStream, TTS, TextCallback

log = logging.getLogger(__name__)

API = "https://api.elevenlabs.io"
STT_WS = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
BATCH_BYTES = 800  # 100 ms of 8 kHz mu-law per websocket message


class ElevenLabsSTT(STTStream):
    def __init__(self, on_partial: TextCallback, on_final: TextCallback):
        super().__init__(on_partial, on_final)
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._buf = bytearray()

    async def start(self) -> None:
        params = {
            "model_id": settings.elevenlabs_stt_model,
            "audio_format": "ulaw_8000",
            "commit_strategy": "vad",
            "vad_silence_threshold_secs": "0.6",
        }
        if settings.stt_language:
            params["language_code"] = settings.stt_language
        self._ws = await websockets.connect(
            f"{STT_WS}?{urlencode(params)}",
            additional_headers={"xi-api-key": settings.elevenlabs_api_key},
            max_size=2**22,
        )
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                kind = msg.get("message_type", "")
                if kind == "partial_transcript":
                    if msg.get("text"):
                        await self.on_partial(msg["text"])
                elif kind.startswith("committed_transcript"):
                    if msg.get("text", "").strip():
                        await self.on_final(msg["text"].strip())
                elif kind == "session_started":
                    db.set_health("elevenlabs_stt", True, "session ok")
                elif "error" in kind or kind in ("quota_exceeded", "rate_limited"):
                    log.error("ElevenLabs STT %s: %s", kind, msg.get("error"))
                    db.set_health("elevenlabs_stt", False, f"{kind}: {msg.get('error', '')}")
        except websockets.ConnectionClosed:
            pass
        except Exception:
            log.exception("STT reader crashed")

    async def send_audio(self, ulaw_8k: bytes) -> None:
        if self._ws is None:
            return
        self._buf.extend(ulaw_8k)
        if len(self._buf) < BATCH_BYTES:
            return
        chunk, self._buf = bytes(self._buf), bytearray()
        with contextlib.suppress(websockets.ConnectionClosed):
            await self._ws.send(json.dumps({
                "message_type": "input_audio_chunk",
                "audio_base_64": base64.b64encode(chunk).decode(),
                "commit": False,
                "sample_rate": 8000,
            }))

    async def close(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader


class ElevenLabsTTS(TTS):
    async def stream(self, text: str) -> AsyncIterator[bytes]:
        url = f"{API}/v1/text-to-speech/{settings.elevenlabs_voice_id}/stream"
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as c:
            async with c.stream(
                "POST",
                url,
                params={"output_format": "ulaw_8000"},
                headers={"xi-api-key": settings.elevenlabs_api_key},
                json={"text": text, "model_id": settings.elevenlabs_tts_model},
            ) as r:
                if r.status_code >= 400:
                    body = (await r.aread())[:300]
                    db.set_health("elevenlabs_tts", False, f"HTTP {r.status_code}")
                    raise RuntimeError(f"ElevenLabs TTS {r.status_code}: {body!r}")
                db.set_health("elevenlabs_tts", True, "ok")
                async for chunk in r.aiter_bytes():
                    if chunk:
                        yield chunk

    async def health(self) -> tuple[bool, str]:
        if not settings.elevenlabs_api_key:
            return False, "not configured"
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{API}/v1/user/subscription", headers={"xi-api-key": settings.elevenlabs_api_key})
            if r.status_code >= 400:
                return False, f"HTTP {r.status_code}"
            d = r.json()
            return True, f"{d.get('tier', '?')}: {d.get('character_count', '?')}/{d.get('character_limit', '?')} chars"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:200]
