"""Reminders on the dashboard: list, create (own, or for others where the role allows it), cancel.

Same rules as Alfred's reminder tools; everything is checked in orchestrator/reminders.py.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import reminders
from orchestrator.identity.registry import Person

from .routes import current_person

router = APIRouter(prefix="/api/reminders")


class ReminderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=500)
    when: str = Field(min_length=10, max_length=40)
    recurrence: Literal["none", "daily", "weekdays", "weekly", "monthly"] = "none"
    channel: Literal["telegram", "call"] = "telegram"
    escalate: bool = False
    person_id: int | None = Field(default=None, ge=1)


@router.get("")
async def list_reminders(include_done: bool = Query(default=False),
                         person: Person = Depends(current_person)) -> list[dict]:
    return reminders.list_for(person, include_done=include_done, limit=100, everyone=True)


@router.post("")
async def create_reminder(body: ReminderCreate, person: Person = Depends(current_person)) -> dict:
    try:
        return reminders.create(created_by=person, text=body.text, when=body.when, target_ref=body.person_id,
                                recurrence=body.recurrence, channel=body.channel, escalate=body.escalate)
    except reminders.ReminderError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{reminder_id}")
async def cancel_reminder(reminder_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    try:
        return reminders.cancel(reminder_id, person)
    except reminders.ReminderError as e:
        raise HTTPException(status_code=400, detail=str(e))
