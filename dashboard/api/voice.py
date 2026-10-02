"""Voice mode: the signed-in person's Telegram replies also as voice notes (orchestrator/voice_replies.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import db, voice_replies
from orchestrator.identity.registry import Person

from .routes import current_person

router = APIRouter(prefix="/api/voice-mode")


class Mode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool
    minutes: int | None = Field(default=None, ge=1, le=voice_replies.MAX_MINUTES)


@router.get("")
async def get_mode(person: Person = Depends(current_person)) -> dict:
    return voice_replies.describe(person.id)


@router.put("")
async def put_mode(body: Mode, person: Person = Depends(current_person)) -> dict:
    if body.on and not voice_replies.describe(person.id)["telegram_linked"]:
        raise HTTPException(status_code=400, detail="Voice notes go out on Telegram: add and verify a Telegram connection first.")
    try:
        out = voice_replies.set_mode(person.id, body.on, body.minutes)
    except voice_replies.VoiceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.log_activity("system", f"Profile: {person.display_name} switched voice mode {'on' if body.on else 'off'}",
                    user_id=person.id)
    return out
