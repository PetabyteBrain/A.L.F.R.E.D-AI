"""Audio + transcript storage, organised per user and date:

    {STORAGE_ROOT}/{user_id}/{YYYY-MM-DD}/{session_id}/audio.wav
    {STORAGE_ROOT}/{user_id}/{YYYY-MM-DD}/{session_id}/transcript.json

STORAGE_ROOT is the only thing that changes when this moves to the NAS.
"""
from __future__ import annotations

import json
import logging
import wave
from array import array
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import db
from ..config import settings

log = logging.getLogger(__name__)

SAMPLE_RATE = 8000  # Twilio media streams are 8 kHz mu-law


def _build_ulaw_table() -> array:
    table = array("h")
    for byte in range(256):
        u = ~byte & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        table.append(-sample if sign else sample)
    return table


ULAW_TO_PCM16 = _build_ulaw_table()
ULAW_SILENCE = 0xFF


def ulaw_to_pcm16(data: bytes) -> array:
    return array("h", (ULAW_TO_PCM16[b] for b in data))


def root() -> Path:
    return settings.storage_root


def session_dir(session_id: str) -> Path:
    row = db.query_one("SELECT user_id, started_at FROM sessions WHERE id = ?", (session_id,))
    if not row:
        raise KeyError(session_id)
    started = datetime.fromisoformat(row["started_at"]).astimezone(ZoneInfo(settings.timezone))
    path = root() / str(row["user_id"]) / started.strftime("%Y-%m-%d") / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_safe(relative: str) -> Path:
    """Map a stored relative path to disk, refusing anything that escapes STORAGE_ROOT."""
    base = root().resolve()
    target = (base / relative).resolve()
    if base != target and base not in target.parents:
        raise PermissionError("path escapes storage root")
    return target


def write_transcript(session_id: str) -> Path:
    s = db.query_one(
        "SELECT s.*, u.display_name FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = ?", (session_id,)
    )
    if not s:
        raise KeyError(session_id)
    turns = db.query(
        "SELECT turn_index, speaker, text, ts FROM transcripts WHERE session_id = ? ORDER BY turn_index", (session_id,)
    )
    tools = db.query(
        "SELECT tool_name, payload, result, is_error, ts FROM tool_calls WHERE session_id = ? ORDER BY id",
        (session_id,),
    )
    doc = {
        "session_id": session_id,
        "user": {"id": s["user_id"], "display_name": s["display_name"]},
        "channel": s["channel"],
        "direction": s["direction"],
        "started_at": s["started_at"],
        "ended_at": s["ended_at"],
        "status": s["status"],
        "turns": turns,
        "tool_calls": tools,
    }
    path = session_dir(session_id) / "transcript.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class CallRecorder:
    """Writes a stereo 16-bit WAV while a call runs: left = caller, right = the assistant.

    Twilio sends inbound frames continuously in real time, so inbound frames are the clock:
    for every chunk the caller sends, we pull the same number of bytes from the playback
    buffer (whatever the assistant is currently "saying"), keeping both channels aligned.
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.path = session_dir(session_id) / "audio.wav"
        self._wav = wave.open(str(self.path), "wb")
        self._wav.setnchannels(2)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)
        self._agent = bytearray()
        self._frames = 0
        self._closed = False

    def agent_audio(self, ulaw: bytes) -> None:
        self._agent.extend(ulaw)

    def clear_agent(self) -> None:
        """Barge-in: Twilio dropped queued playback, so drop it from the mirror too."""
        self._agent.clear()

    def caller_audio(self, ulaw: bytes) -> None:
        if self._closed:
            return
        n = len(ulaw)
        agent = bytes(self._agent[:n])
        del self._agent[:n]
        if len(agent) < n:
            agent += bytes([ULAW_SILENCE]) * (n - len(agent))
        left, right = ulaw_to_pcm16(ulaw), ulaw_to_pcm16(agent)
        inter = array("h", [0]) * (2 * n)
        inter[0::2] = left
        inter[1::2] = right
        self._wav.writeframes(inter.tobytes())
        self._frames += n

    def close(self) -> float:
        if self._closed:
            return 0.0
        self._closed = True
        self._wav.close()
        duration = self._frames / SAMPLE_RATE
        rel = self.path.relative_to(root()).as_posix()
        db.execute(
            "INSERT INTO recordings(session_id, file_path, duration, created_at) VALUES (?,?,?,?)",
            (self.session_id, rel, round(duration, 2), db.now_iso()),
        )
        return duration
