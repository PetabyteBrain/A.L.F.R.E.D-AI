"""STT/TTS interface — the swap point between hosted (ElevenLabs) and self-hosted (Jetson) speech.

Audio on both sides is 8 kHz mu-law, the native Twilio Media Streams format, so a provider
never needs to know about Twilio and the call handler never needs to know about a provider.
To add the Jetson backend: implement these two classes in stt_tts/local.py and register it
in `get_provider()`; set STT_TTS_PROVIDER=local.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Awaitable, Callable

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


def get_provider() -> Provider:
    from ..config import settings

    if settings.stt_tts_provider == "elevenlabs":
        from .elevenlabs import ElevenLabsSTT, ElevenLabsTTS
        return Provider("elevenlabs", ElevenLabsSTT, ElevenLabsTTS())
    raise ValueError(f"Unknown STT_TTS_PROVIDER '{settings.stt_tts_provider}'")
