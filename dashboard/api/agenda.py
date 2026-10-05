"""The agenda on the dashboard (orchestrator/agenda.py): calendar events, reminders, tasks and calls in
one view, categories, and reminders tied to calendar events. Always the signed-in person's own."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import agenda, reminders
from orchestrator.identity.registry import Person

from .routes import current_person

router = APIRouter(prefix="/api")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ItemChange(Strict):
    kind: Literal["event", "reminder", "task"]
    id: str = Field(min_length=1, max_length=1100)
    title: str | None = Field(default=None, max_length=300)
    start: str | None = Field(default=None, max_length=40)
    category: str | None = Field(default=None, max_length=20)


class LinkBody(Strict):
    event_id: str = Field(min_length=1, max_length=1100)
    minutes_before: int = Field(default=15, ge=0, le=10080)


class EventReminder(LinkBody):
    channel: Literal["telegram", "call"] = "telegram"


class NewItem(Strict):
    mode: Literal["reminder", "task", "calendar"]
    title: str = Field(min_length=1, max_length=300)
    start: str = Field(min_length=10, max_length=40)
    category: str = Field(default="", max_length=20)
    duration_min: int = Field(default=60, ge=5, le=1440)
    channel: Literal["telegram", "call"] = "telegram"
    recurrence: Literal["", "daily", "weekdays", "weekly", "monthly"] = ""
    also_calendar: bool = False
    minutes_before: int = Field(default=15, ge=0, le=10080)
    follow_up: Literal["none", "telegram", "call"] = "none"


def _fail(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


@router.get("/agenda")
async def get_agenda(start: str = Query(max_length=40), end: str = Query(max_length=40),
                     person: Person = Depends(current_person)) -> dict:
    try:
        return await agenda.items(person, start, end)
    except (agenda.AgendaError, ValueError) as e:
        raise _fail(e)


@router.patch("/agenda")
async def change_item(body: ItemChange, person: Person = Depends(current_person)) -> dict:
    try:
        return await agenda.update(person, body.kind, body.id, title=body.title or None, start=body.start or None,
                                   category=body.category or None)
    except (agenda.AgendaError, reminders.ReminderError, ValueError) as e:
        raise _fail(e)


@router.post("/agenda")
async def create_item(body: NewItem, person: Person = Depends(current_person)) -> dict:
    try:
        return await agenda.create(person, body.mode, title=body.title, start=body.start, category=body.category,
                                   duration_min=body.duration_min, channel=body.channel,
                                   recurrence=body.recurrence or None, also_calendar=body.also_calendar,
                                   minutes_before=body.minutes_before, follow_up=body.follow_up)
    except (agenda.AgendaError, reminders.ReminderError, ValueError) as e:
        raise _fail(e)


@router.post("/agenda/remind")
async def remind_for_event(body: EventReminder, person: Person = Depends(current_person)) -> dict:
    try:
        return await agenda.remind_for_event(person, body.event_id, body.minutes_before, body.channel)
    except (agenda.AgendaError, reminders.ReminderError) as e:
        raise _fail(e)


@router.post("/agenda/sync")
async def sync(person: Person = Depends(current_person)) -> dict:
    return await agenda.sync_person(person.id)


@router.post("/reminders/{reminder_id}/link")
async def link(body: LinkBody, reminder_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    try:
        return await agenda.link(person, reminder_id, body.event_id, body.minutes_before)
    except (agenda.AgendaError, reminders.ReminderError) as e:
        raise _fail(e)


@router.post("/reminders/{reminder_id}/unlink")
async def unlink(reminder_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    try:
        return agenda.unlink(person, reminder_id)
    except reminders.ReminderError as e:
        raise _fail(e)


@router.post("/reminders/{reminder_id}/dismiss")
async def dismiss(reminder_id: int = Path(ge=1), person: Person = Depends(current_person)) -> dict:
    try:
        return agenda.dismiss(person, reminder_id)
    except (agenda.AgendaError, reminders.ReminderError) as e:
        raise _fail(e)
