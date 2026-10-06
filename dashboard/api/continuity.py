"""Profile switch for cross-channel continuity (orchestrator/continuity.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from orchestrator import continuity, db
from orchestrator.identity.registry import Person

from .routes import current_person

router = APIRouter(prefix="/api/continuity")


class Switch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: bool


@router.get("")
async def get_continuity(person: Person = Depends(current_person)) -> dict:
    return {"on": continuity.enabled(person.id), "hours": int(continuity.WINDOW.total_seconds() // 3600)}


@router.put("")
async def put_continuity(body: Switch, person: Person = Depends(current_person)) -> dict:
    on = continuity.set_enabled(person.id, body.on)
    db.log_activity("system", f"Profile: {person.display_name} switched cross-channel continuity {'on' if on else 'off'}",
                    person.id)
    return {"on": on, "hours": int(continuity.WINDOW.total_seconds() // 3600)}
