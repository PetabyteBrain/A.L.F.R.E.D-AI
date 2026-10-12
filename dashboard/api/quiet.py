"""Quiet hours: the signed-in person's own setting, and the household default (admin edits it)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import db, quiet
from orchestrator.identity.registry import Person

from .routes import current_person, requires

router = APIRouter(prefix="/api/quiet-hours")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Own(Strict):
    mode: Literal["default", "custom", "off"]
    start: str | None = Field(default=None, max_length=5)
    end: str | None = Field(default=None, max_length=5)


class Default(Strict):
    enabled: bool
    start: str = Field(max_length=5)
    end: str = Field(max_length=5)


@router.get("")
async def get_own(person: Person = Depends(current_person)) -> dict:
    return quiet.describe(person.id)


@router.put("")
async def put_own(body: Own, person: Person = Depends(current_person)) -> dict:
    try:
        quiet.set_person(person.id, mode=body.mode, start=body.start, end=body.end)
    except quiet.QuietError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return quiet.describe(person.id)


@router.put("/default")
async def put_default(body: Default, person: Person = Depends(requires("settings.manage"))) -> dict:
    try:
        value = quiet.set_default(enabled=body.enabled, start=body.start, end=body.end)
    except quiet.QuietError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.log_activity("system", f"{person.display_name} set the default quiet hours to "
                    + (f"{value['start']}-{value['end']}" if value["enabled"] else "off"), user_id=person.id)
    return quiet.describe(person.id)
