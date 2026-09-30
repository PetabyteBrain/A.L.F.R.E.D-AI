"""Daily briefing settings for the signed-in person, plus "send one now"."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import briefing
from orchestrator.identity.registry import Person

from . import security
from .routes import current_person

router = APIRouter(prefix="/api/briefing")

send_throttle = security.Throttle(window_s=3600, max_failures=5)   # "send now": 5 per hour per person


class BriefingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    enabled: bool | None = None
    time_local: str | None = Field(default=None, max_length=5)
    days: Literal["daily", "weekdays"] | None = None
    channel: Literal["telegram", "call"] | None = None
    location: str | None = Field(default=None, max_length=80)


def _view(user_id: int) -> dict:
    s = briefing.get_settings(user_id)
    return {k: s[k] for k in ("enabled", "time_local", "days", "channel", "location")}


@router.get("")
async def get_briefing(person: Person = Depends(current_person)) -> dict:
    return _view(person.id)


@router.put("")
async def put_briefing(body: BriefingSettings, person: Person = Depends(current_person)) -> dict:
    try:
        await briefing.update_settings(person.id, enabled=body.enabled, time_local=body.time_local, days=body.days,
                                       channel=body.channel, location=body.location)
    except briefing.BriefingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _view(person.id)


@router.post("/send")
async def send_now(person: Person = Depends(current_person)) -> dict:
    key = f"briefing:{person.id}"
    if send_throttle.blocked(key):
        raise HTTPException(status_code=429, detail="That's enough briefings for one hour.")
    send_throttle.fail(key)
    try:
        channel = await briefing.send(person)
    except briefing.BriefingError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"sent": True, "channel": channel}
