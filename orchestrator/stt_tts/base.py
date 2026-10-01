"""STT/TTS interface — the swap point between hosted (ElevenLabs) and self-hosted (Jetson) speech.

Audio on both sides is 8 kHz mu-law, the native Twilio Media Streams format, so a provider
never needs to know about Twilio and the call handler never needs to know about a provider.

STT_TTS_PROVIDER=jetson uses the Jetson speech server (speech_server/); if it's unreachable and
ElevenLabs keys are configured, calls and voice notes fall back to ElevenLabs automatically.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import AsyncIterator, Awaitable, Callable

log = logging.getLogger(__name__)

TextCallback = Callable[[str], Awaitable[None]]


class STTStream(ABC):
    """One streaming recognition session (one per call)."""

    def __init__(self, on_partial: TextCallback, on_final: TextCallback):
        self.on_partial = on_partial
        self.on_final = on_final

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def send_audio(self, ulaw_8k: bytes) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class TTS(ABC):
    @abstractmethod
    def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield 8 kHz mu-law audio chunks for `text` as they are synthesised."""

    async def health(self) -> tuple[bool, str]:
        return True, "ok"


class Provider:
    name: str
    stt_factory: Callable[[TextCallback, TextCallback], STTStream]
    tts: TTS

    def __init__(self, name: str, stt_factory, tts: TTS):
        self.name, self.stt_factory, self.tts = name, stt_factory, tts


def active_name() -> str:
    """Which provider handles speech right now ("jetson" or "elevenlabs")."""
    from ..config import settings
    if settings.stt_tts_provider == "jetson" and settings.speech_url:
        from . import jetson
        if jetson.is_up() or not settings.elevenlabs_enabled:
            return "jetson"
        return "elevenlabs"
    return "elevenlabs"


def get_provider() -> Provider:
    name = active_name()
    if name == "jetson":
        from .jetson import JetsonSTT, JetsonTTS
        return Provider("jetson", JetsonSTT, JetsonTTS())
    from ..config import settings
    if settings.stt_tts_provider == "jetson":
        log.warning("Jetson speech unreachable; falling back to ElevenLabs")
    from .elevenlabs import ElevenLabsSTT, ElevenLabsTTS
    return Provider("elevenlabs", ElevenLabsSTT, ElevenLabsTTS())


def speech_module():
    """Module with transcribe_file / synthesize_voice_note for the active provider."""
    if active_name() == "jetson":
        from . import jetson
        return jetson
    from . import elevenlabs
    return elevenlabs
