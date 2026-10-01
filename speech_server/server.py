"""Alfred's speech server for the Jetson Orin Nano: English speech recognition and synthesis.

- STT: Whisper (Hugging Face transformers, fp16 on the GPU) + Silero VAD for end-of-speech.
- TTS: Kokoro-82M with a British male voice (default `bm_george`), GPU.

Endpoints (all need `Authorization: Bearer $SPEECH_TOKEN`):
  WS   /v1/stt/stream      binary 8 kHz mu-law frames in (phone audio);
                           JSON out: {"type":"speech_start"} once speech is clearly under way,
                           {"type":"final","text":...} after the caller pauses.
  POST /v1/stt             multipart `file` (any format ffmpeg reads) -> {"text": ...}
  POST /v1/tts             {"text", "format": "ulaw_8000"|"ogg_opus"|"wav"} -> audio (ulaw streamed per sentence)
  GET  /health             models, device, timings (no auth: no content, tailnet only)
"""
from __future__ import annotations

import asyncio
import audioop
import io
import json
import logging
import os
import subprocess
import threading
import time
import wave

import numpy as np
import torch
from fastapi import FastAPI, File, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from scipy.signal import resample_poly

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("speech")

TOKEN = os.environ.get("SPEECH_TOKEN", "")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "openai/whisper-large-v3-turbo")
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "bm_george")
KOKORO_SPEED = float(os.environ.get("KOKORO_SPEED", "1.0"))
SILENCE_MS = int(os.environ.get("VAD_SILENCE_MS", "500"))       # pause that ends an utterance
SPEECH_START_MS = int(os.environ.get("VAD_SPEECH_START_MS", "250"))
SPECULATE_MS = int(os.environ.get("VAD_SPECULATE_MS", "250"))   # start transcribing this far into a pause
VAD_THRESHOLD = float(os.environ.get("VAD_THRESHOLD", "0.5"))
MAX_UTTERANCE_S = 30
PREROLL_MS = 200
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32

app = FastAPI(title="Alfred speech", docs_url=None, redoc_url=None, openapi_url=None)
_gpu = threading.Lock()                 # one model call at a time on the GPU
_stats: dict = {"stt_ms": [], "tts_first_ms": []}


def _auth(value: str | None) -> None:
    if TOKEN and value != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _track(key: str, ms: float) -> None:
    _stats[key] = (_stats[key] + [round(ms)])[-50:]


# --- models --------------------------------------------------------------------------------

class Whisper:
    def __init__(self, name: str):
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor
        self.processor = AutoProcessor.from_pretrained(name)
        # Load straight onto the GPU: on Jetson CPU and GPU share the same RAM, so a CPU copy first
        # would double the peak memory.
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(
            name, torch_dtype=DTYPE, low_cpu_mem_usage=True, attn_implementation="sdpa",
            device_map=DEVICE).eval()
        import gc
        gc.collect()
        self.multilingual = getattr(self.model.generation_config, "is_multilingual", False)

    def __call__(self, audio16k: np.ndarray) -> str:
        if audio16k.size < 1600:                               # < 0.1 s
            return ""
        feats = self.processor(audio16k, sampling_rate=16000, return_tensors="pt").input_features
        kw = {"language": "en", "task": "transcribe"} if self.multilingual else {}
        with _gpu, torch.inference_mode():
            ids = self.model.generate(feats.to(DEVICE, DTYPE), max_new_tokens=160, num_beams=1, **kw)
        return self.processor.batch_decode(ids, skip_special_tokens=True)[0].strip()


class Kokoro:
    SR = 24000

    def __init__(self, voice: str):
        from kokoro import KModel, KPipeline
        self.model = KModel(repo_id="hexgrad/Kokoro-82M").to(DEVICE).eval()
        # lang 'b' = British English pronunciation (misaki G2P)
        self.pipe = KPipeline(lang_code="b", repo_id="hexgrad/Kokoro-82M", model=self.model)
        self.voice = voice

    def sentences(self, text: str):
        """Yield float32 24 kHz audio per sentence-sized chunk as it is synthesised."""
        for result in self.pipe(text, voice=self.voice, speed=KOKORO_SPEED, split_pattern=r"(?<=[.!?…])\s+|\n+"):
            audio = result.audio
            if audio is None:
                continue
            yield audio.detach().float().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio, np.float32)

    def synth_iter(self, text: str):
        it = self.sentences(text)
        while True:
            with _gpu:
                try:
                    chunk = next(it)
                except StopIteration:
                    return
            yield chunk


class Vad:
    """Silero VAD on 32 ms frames of 16 kHz audio (CPU; it's tiny)."""

    def __init__(self):
        from silero_vad import load_silero_vad
        self.model = load_silero_vad()

    def prob(self, frame16k: np.ndarray) -> float:
        with torch.inference_mode():
            return float(self.model(torch.from_numpy(frame16k), 16000).item())

    def reset(self) -> None:
        self.model.reset_states()


WHISPER: Whisper | None = None
KOKORO: Kokoro | None = None


@app.on_event("startup")
def load_models() -> None:
    global WHISPER, KOKORO
    t = time.monotonic()
    torch.set_num_threads(2)
    WHISPER = Whisper(WHISPER_MODEL)
    KOKORO = Kokoro(KOKORO_VOICE)
    # Warm up kernels so the first call isn't slow.
    WHISPER(np.zeros(16000, np.float32))
    for _ in KOKORO.synth_iter("Good evening."):
        pass
    log.info("Models ready on %s in %.1fs (stt=%s, voice=%s)", DEVICE, time.monotonic() - t, WHISPER_MODEL, KOKORO_VOICE)


# --- audio helpers ---------------------------------------------------------------------------

def ulaw8k_to_f32_16k(data: bytes) -> np.ndarray:
    pcm = np.frombuffer(audioop.ulaw2lin(data, 2), dtype=np.int16).astype(np.float32) / 32768.0
    return resample_poly(pcm, 2, 1).astype(np.float32)


def f32_24k_to_ulaw8k(audio: np.ndarray) -> bytes:
    pcm = np.clip(resample_poly(audio, 1, 3), -1.0, 1.0)
    return audioop.lin2ulaw((pcm * 32767).astype(np.int16).tobytes(), 2)


def ffmpeg(args: list[str], data: bytes) -> bytes:
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", *args], input=data, capture_output=True,
                       timeout=60)
    if p.returncode:
        raise HTTPException(status_code=400, detail=f"audio conversion failed: {p.stderr[:200]!r}")
    return p.stdout


def wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


# --- endpoints -----------------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None  # noqa: E731
    return {"ok": WHISPER is not None and KOKORO is not None, "device": DEVICE, "stt_model": WHISPER_MODEL,
            "voice": KOKORO_VOICE, "stt_median_ms": med(_stats["stt_ms"]),
            "tts_first_median_ms": med(_stats["tts_first_ms"])}


class TtsBody(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    format: str = Field(default="ulaw_8000", pattern="^(ulaw_8000|ogg_opus|wav)$")


@app.post("/v1/tts")
async def tts(body: TtsBody, authorization: str | None = Header(default=None)):
    _auth(authorization)
    text = " ".join(body.text.split())
    if body.format == "ulaw_8000":
        def gen():
            t = time.monotonic()
            first = True
            for chunk in KOKORO.synth_iter(text):
                if first:
                    _track("tts_first_ms", (time.monotonic() - t) * 1000)
                    first = False
                yield f32_24k_to_ulaw8k(chunk)
        return StreamingResponse(gen(), media_type="audio/basic")
    audio = await asyncio.to_thread(lambda: np.concatenate(list(KOKORO.synth_iter(text)) or [np.zeros(1, np.float32)]))
    wav = wav_bytes(audio, Kokoro.SR)
    if body.format == "wav":
        return Response(wav, media_type="audio/wav")
    ogg = await asyncio.to_thread(ffmpeg, ["-f", "wav", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "48k",
                                           "-f", "ogg", "pipe:1"], wav)
    return Response(ogg, media_type="audio/ogg")


@app.post("/v1/stt")
async def stt_file(file: UploadFile = File(...), authorization: str | None = Header(default=None)) -> dict:
    _auth(authorization)
    data = await file.read()
    if len(data) > 25_000_000:
        raise HTTPException(status_code=413, detail="file too large")
    pcm = await asyncio.to_thread(ffmpeg, ["-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"], data)
    audio = np.frombuffer(pcm, dtype=np.float32)
    t = time.monotonic()
    # Long clips: transcribe in 30 s windows.
    parts = [await asyncio.to_thread(WHISPER, audio[i:i + 16000 * 30]) for i in range(0, len(audio), 16000 * 30)]
    _track("stt_ms", (time.monotonic() - t) * 1000)
    return {"text": " ".join(p for p in parts if p).strip(), "language": "en", "seconds": round(len(audio) / 16000, 1)}


@app.websocket("/v1/stt/stream")
async def stt_stream(ws: WebSocket) -> None:
    if TOKEN and ws.headers.get("authorization") != f"Bearer {TOKEN}":
        await ws.close(code=1008)
        return
    await ws.accept()
    vad = Vad()
    frame = 512                                   # 32 ms at 16 kHz
    pending = np.zeros(0, np.float32)             # not yet VAD-checked
    preroll: list[np.ndarray] = []
    speech: list[np.ndarray] = []
    in_speech, started, speech_ms, silence_ms = False, False, 0, 0
    # Speculative transcription: start Whisper early in the pause; if the caller carries on
    # talking the result is discarded, otherwise it's (nearly) ready when the pause is confirmed.
    spec: asyncio.Task | None = None

    def transcribe_task(audio: np.ndarray) -> asyncio.Task:
        async def run() -> tuple[str, float]:
            t = time.monotonic()
            text = await asyncio.to_thread(WHISPER, audio)
            return text, (time.monotonic() - t) * 1000
        return asyncio.create_task(run())

    async def finish() -> None:
        nonlocal speech, in_speech, started, speech_ms, silence_ms, spec
        audio = np.concatenate(speech) if speech else np.zeros(0, np.float32)
        task, spec = spec, None
        speech, in_speech, started, speech_ms, silence_ms = [], False, False, 0, 0
        vad.reset()
        if audio.size < 16000 * 0.3:              # < 0.3 s: a cough or click, ignore
            return
        t = time.monotonic()
        text, ms = await (task or transcribe_task(audio))
        _track("stt_ms", ms)
        log.info("utterance %.1fs -> final %.0f ms after pause confirmed (whisper %.0f ms)",
                 audio.size / 16000, (time.monotonic() - t) * 1000, ms)
        if text and not _hallucination(text):
            await ws.send_text(json.dumps({"type": "final", "text": text}))

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            data = msg.get("bytes")
            if not data:
                continue
            pending = np.concatenate([pending, ulaw8k_to_f32_16k(data)])
            while pending.size >= frame:
                f, pending = pending[:frame], pending[frame:]
                p = vad.prob(f)
                if in_speech:
                    speech.append(f)
                    if p >= VAD_THRESHOLD:
                        speech_ms += 32
                        silence_ms = 0
                        spec = None                   # they kept talking: drop the early guess
                        if not started and speech_ms >= SPEECH_START_MS:
                            started = True
                            await ws.send_text(json.dumps({"type": "speech_start"}))
                    else:
                        silence_ms += 32
                        if spec is None and silence_ms >= SPECULATE_MS and speech_ms >= 300:
                            spec = transcribe_task(np.concatenate(speech))
                        if silence_ms >= SILENCE_MS or len(speech) * 32 >= MAX_UTTERANCE_S * 1000:
                            await finish()
                elif p >= VAD_THRESHOLD:
                    in_speech, speech_ms, silence_ms = True, 32, 0
                    speech = preroll + [f]
                    preroll = []
                else:
                    preroll = (preroll + [f])[-(PREROLL_MS // 32):]
    except WebSocketDisconnect:
        pass


_HALLUCINATIONS = {"thanks for watching!", "thanks for watching.", "thank you for watching.",
                   "thank you for watching!", "you", "you.", "."}


def _hallucination(text: str) -> bool:
    """Phrases Whisper invents on noise/silence (a real "thank you" is kept)."""
    return text.strip().lower() in _HALLUCINATIONS
