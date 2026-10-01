"""Benchmark on the Jetson: Whisper models on phone-quality audio + Kokoro voices.

docker run --rm --runtime nvidia -v ~/alfred-speech/models:/models -v ~/alfred-speech/out:/out \
    alfred-speech python3 bench.py
"""
import audioop
import os
import time

import numpy as np
import torch
from scipy.signal import resample_poly

os.environ.setdefault("HF_HOME", "/models/hf")
import server  # noqa: E402

SENTENCES = [
    "Could you call Max tomorrow at nine and ask whether the invoice went out?",
    "Remind me every weekday at half past eight to take my vitamins.",
    "What's on my calendar on Friday afternoon?",
    "Cancel the reminder about the plants, please.",
]
VOICES = ["bm_george", "bm_fable", "bm_lewis", "bm_daniel"]
WHISPERS = os.environ.get("BENCH_WHISPERS", "openai/whisper-large-v3-turbo,openai/whisper-small.en").split(",")
SKIP_TTS = os.environ.get("BENCH_SKIP_TTS") == "1"


def phone(audio24k: np.ndarray) -> np.ndarray:
    """24 kHz TTS audio -> 8 kHz mu-law (as Twilio delivers it) -> back to 16 kHz float."""
    pcm = (np.clip(resample_poly(audio24k, 1, 3), -1, 1) * 32767).astype(np.int16).tobytes()
    return server.ulaw8k_to_f32_16k(audioop.lin2ulaw(pcm, 2))


def words(s: str) -> list[str]:
    return "".join(c.lower() if c.isalnum() or c == " " else " " for c in s).split()


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / max(1, len(r))


def main():
    os.makedirs("/out", exist_ok=True)
    k = server.Kokoro("bm_george")
    for _ in k.synth_iter("Warming up."):
        pass
    print("\n== TTS (Kokoro, GPU)")
    for v in VOICES:
        k.voice = v
        t = time.monotonic()
        chunks = list(k.synth_iter(SENTENCES[0]))
        ms = (time.monotonic() - t) * 1000
        audio = np.concatenate(chunks)
        with open(f"/out/voice_{v}.wav", "wb") as f:
            f.write(server.wav_bytes(np.concatenate(list(k.synth_iter(
                "Good evening, sir. Alfred here. I've moved your meeting with Max to Friday at ten, "
                "and I'll remind you an hour before. Will there be anything else?"))), 24000))
        print(f"{v:10s} {ms:6.0f} ms for {len(audio) / 24000:.1f} s of speech (RTF {ms / 1000 / (len(audio) / 24000):.2f})")
    k.voice = "bm_george"
    clips = [(s, phone(np.concatenate(list(k.synth_iter(s))))) for s in SENTENCES]

    print("\n== STT on 8 kHz phone audio")
    for name in WHISPERS:
        t = time.monotonic()
        w = server.Whisper(name)
        load = time.monotonic() - t
        w(np.zeros(16000, np.float32))
        times, errs = [], []
        for ref, audio in clips:
            t = time.monotonic()
            hyp = w(audio)
            times.append((time.monotonic() - t) * 1000)
            errs.append(wer(ref, hyp))
        mem = torch.cuda.max_memory_allocated() / 1e9 if torch.cuda.is_available() else 0
        print(f"{name:36s} load {load:5.1f}s  median {sorted(times)[len(times) // 2]:5.0f} ms  "
              f"max {max(times):5.0f} ms  WER {np.mean(errs) * 100:4.1f}%  peak GPU {mem:.1f} GB  e.g. {hyp!r}")
        del w
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()


if __name__ == "__main__":
    main()
