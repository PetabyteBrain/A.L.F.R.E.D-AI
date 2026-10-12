"""Call screening (admin): recent screened calls and the blocked-numbers list (orchestrator/screening.py);
outside calls (admins + cofounders): list and cancel (orchestrator/external_calls.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from orchestrator import db, external_calls, screening
from orchestrator.config import settings
from orchestrator.identity import registry
from orchestrator.identity.registry import Person

from .routes import current_person, requires

router = APIRouter(prefix="/api/screening")


class Number(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    number: str = Field(min_length=4, max_length=30)


def _view() -> dict:
    return {
        "enabled": settings.call_screening,
        "blocked": screening.blocked(),
        "recent": db.query("SELECT id, ts, caller, session_id, summary FROM screened_calls ORDER BY id DESC LIMIT 20"),
    }


@router.get("")
async def get_screening(person: Person = Depends(requires("settings.manage"))) -> dict:
    return _view()


@router.post("/block")
async def block(body: Number, person: Person = Depends(requires("settings.manage"))) -> dict:
    number = registry.normalize_phone(body.number)
    if not screening._NUMBER.match(number):
        raise HTTPException(status_code=400, detail="That isn't a valid phone number.")
    screening.block(number, person)
    return _view()


@router.post("/unblock")
async def unblock(body: Number, person: Person = Depends(requires("settings.manage"))) -> dict:
    screening.unblock(registry.normalize_phone(body.number), person)
    return _view()


# --- outside calls (external_calls.py): list + cancel; new ones are asked for in chat ------------

outside = APIRouter(prefix="/api/external-calls")


def _caller_roles(person: Person = Depends(current_person)) -> Person:
    if not person.can("calls.outside"):
        raise HTTPException(status_code=403)
    return person


@outside.get("")
async def list_outside(person: Person = Depends(_caller_roles)) -> list[dict]:
    return external_calls.list_for(person)


@outside.delete("/{call_id}")
async def cancel_outside(call_id: int = Path(ge=1), person: Person = Depends(_caller_roles)) -> dict:
    try:
        return external_calls.cancel(call_id, person)
    except external_calls.ExternalCallError as e:
        raise HTTPException(status_code=400, detail=str(e))
